"""Constrained service layer behind the CP²N² MCP protocol server."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from core.errors import ControlPlaneError, ControlPlaneErrorCode, ControlPlaneException
from core.orchestrator import (
    ControlPlaneActionResult,
    OrchestrationRunResult,
    CP2N2Orchestrator,
)
from descriptors.capability_schema import SubstrateClass
from descriptors.resource_contract import (
    PhysicalNeuralResourceContract,
    ResourceLifecycleState,
    RuntimeKind,
)
from mcp_surface.approvals import (
    ApprovalDenied,
    ApprovalRequirement,
    ApprovalVerifier,
    DenyAllApprovalVerifier,
)
from mcp_surface.audit import JsonlHashChainAuditTrail
from mcp_surface.auth import AuthorizationDenied, Scope, StaticAuthorizer
from mcp_surface.catalog import ASSAY_PRESETS, get_preset
from mcp_surface.models import (
    AbortRunInput,
    DescribeResourceInput,
    DiscoverResourcesInput,
    GetResultSummaryInput,
    GetRunStatusInput,
    MCPPrincipal,
    PrepareAssayInput,
    ReleaseResourceInput,
    RenewLeaseInput,
    ReserveResourceInput,
    RunAssayInput,
    RunState,
    SurfaceError,
    SurfaceErrorCode,
    ToolResponse,
)
from mcp_surface.records import AssayRunRecord, RunRecordStore


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    required_scope: Scope
    read_only: bool
    destructive: bool
    idempotent: bool


TOOL_SPECS: dict[str, ToolSpec] = {
    "discover_resources": ToolSpec(
        "discover_resources",
        "List sanitized CP²N² resource summaries and compatible assay presets.",
        DiscoverResourcesInput,
        Scope.RESOURCES_READ,
        True,
        False,
        True,
    ),
    "describe_resource": ToolSpec(
        "describe_resource",
        "Describe one resource without exposing low-level control primitives.",
        DescribeResourceInput,
        Scope.RESOURCES_READ,
        True,
        False,
        True,
    ),
    "reserve_resource": ToolSpec(
        "reserve_resource",
        "Acquire a bounded exclusive lease for one resource.",
        ReserveResourceInput,
        Scope.LEASES_WRITE,
        False,
        False,
        False,
    ),
    "renew_lease": ToolSpec(
        "renew_lease",
        "Renew an owned lease within the server-enforced TTL bound.",
        RenewLeaseInput,
        Scope.LEASES_WRITE,
        False,
        False,
        False,
    ),
    "prepare_assay": ToolSpec(
        "prepare_assay",
        "Validate a server-owned assay preset; dry_run performs no commitment.",
        PrepareAssayInput,
        Scope.ASSAYS_PREPARE,
        False,
        False,
        False,
    ),
    "run_assay": ToolSpec(
        "run_assay",
        "Execute exactly one previously prepared assay under its existing lease.",
        RunAssayInput,
        Scope.ASSAYS_EXECUTE,
        False,
        True,
        False,
    ),
    "get_run_status": ToolSpec(
        "get_run_status",
        "Read the state of one assay run owned by the principal.",
        GetRunStatusInput,
        Scope.RESOURCES_READ,
        True,
        False,
        True,
    ),
    "abort_run": ToolSpec(
        "abort_run",
        "Abort one owned prepared or running assay and reconcile the resource.",
        AbortRunInput,
        Scope.RUNS_ABORT,
        False,
        True,
        True,
    ),
    "get_result_summary": ToolSpec(
        "get_result_summary",
        "Return a sanitized high-level summary without raw substrate output.",
        GetResultSummaryInput,
        Scope.RESOURCES_READ,
        True,
        False,
        True,
    ),
    "release_resource": ToolSpec(
        "release_resource",
        "Release an unused owned lease; active work must be aborted first.",
        ReleaseResourceInput,
        Scope.LEASES_WRITE,
        False,
        False,
        False,
    ),
}


class SurfaceCallFailure(RuntimeError):
    def __init__(
        self,
        code: SurfaceErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.surface_error = SurfaceError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(message)


class MCPControlSurface:
    """Exact, auditable tool boundary presented to agents."""

    def __init__(
        self,
        *,
        orchestrator: CP2N2Orchestrator,
        principal: MCPPrincipal,
        audit_trail: JsonlHashChainAuditTrail,
        approval_verifier: ApprovalVerifier | None = None,
        run_store: RunRecordStore | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.principal = principal
        self.audit_trail = audit_trail
        self.authorizer = StaticAuthorizer(principal)
        self.approval_verifier = approval_verifier or DenyAllApprovalVerifier()
        self.run_store = run_store or RunRecordStore()

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> ToolResponse:
        """Validate, authorize, execute, and audit one delivered MCP tool call."""
        request_id = str(uuid4())
        raw_arguments = arguments if isinstance(arguments, dict) else {}
        self.audit_trail.record(
            request_id=request_id,
            principal_id=self.principal.principal_id,
            tool=tool_name,
            phase="received",
            outcome="pending",
            arguments=raw_arguments,
        )
        try:
            spec = TOOL_SPECS.get(tool_name)
            if spec is None:
                raise SurfaceCallFailure(
                    SurfaceErrorCode.INVALID_REQUEST,
                    f"Unknown MCP tool '{tool_name}'.",
                )
            if not isinstance(arguments, dict):
                raise SurfaceCallFailure(
                    SurfaceErrorCode.INVALID_REQUEST,
                    "Tool arguments must be a JSON object.",
                )
            try:
                validated = spec.input_model.model_validate(arguments)
            except ValidationError as exc:
                issues = [
                    {
                        "location": ".".join(str(item) for item in error["loc"]),
                        "type": error["type"],
                        "message": error["msg"],
                    }
                    for error in exc.errors(include_input=False, include_url=False)
                ]
                raise SurfaceCallFailure(
                    SurfaceErrorCode.INVALID_REQUEST,
                    "Arguments do not match the constrained tool schema.",
                    details={"issues": issues},
                ) from exc

            self.authorizer.require(spec.required_scope)
            handler = getattr(self, f"_handle_{tool_name}")
            result = handler(validated)
            response = ToolResponse(
                ok=True,
                request_id=request_id,
                tool=tool_name,
                result=result,
            )
            self._audit_completion(response, raw_arguments)
            return response
        except AuthorizationDenied as exc:
            response = self._failure(
                request_id,
                tool_name,
                SurfaceError(
                    code=SurfaceErrorCode.UNAUTHORIZED,
                    message=str(exc),
                ),
            )
        except ApprovalDenied as exc:
            response = self._failure(
                request_id,
                tool_name,
                SurfaceError(
                    code=SurfaceErrorCode.APPROVAL_REQUIRED,
                    message=str(exc),
                ),
            )
        except SurfaceCallFailure as exc:
            response = self._failure(
                request_id,
                tool_name,
                exc.surface_error,
            )
        except (ControlPlaneException, KeyError) as exc:
            if isinstance(exc, ControlPlaneException):
                surface_error = self._from_control_error(exc.error)
            else:
                surface_error = SurfaceError(
                    code=SurfaceErrorCode.RESOURCE_NOT_FOUND,
                    message=str(exc),
                )
            response = self._failure(request_id, tool_name, surface_error)
        except Exception:
            response = self._failure(
                request_id,
                tool_name,
                SurfaceError(
                    code=SurfaceErrorCode.INTERNAL_ERROR,
                    message="The constrained MCP surface could not complete the request.",
                ),
            )
        self._audit_completion(response, raw_arguments)
        return response

    def _handle_discover_resources(
        self,
        request: DiscoverResourcesInput,
    ) -> dict[str, Any]:
        resources = []
        for contract in self.orchestrator.registry.list_resource_contracts():
            view = self._resource_view(contract)
            if request.include_unavailable or view["lifecycle_state"] in {
                "ready",
                "reserved",
            }:
                resources.append(view)
        resources.sort(key=lambda item: item["resource_id"])
        return {
            "resources": resources[: request.limit],
            "count": min(len(resources), request.limit),
            "available_tools_are_constrained": True,
        }

    def _handle_describe_resource(
        self,
        request: DescribeResourceInput,
    ) -> dict[str, Any]:
        contract = self.orchestrator.registry.resource_contract_for(
            request.resource_id
        )
        return self._resource_view(contract, detailed=True)

    def _handle_reserve_resource(
        self,
        request: ReserveResourceInput,
    ) -> dict[str, Any]:
        result = self.orchestrator.reserve_backend(
            request.resource_id,
            owner_id=self.principal.principal_id,
            ttl_ms=request.ttl_seconds * 1000.0,
            expected_state_version=request.expected_state_version,
            idempotency_key=request.idempotency_key,
        )
        self._require_action_success(result)
        assert result.lease is not None
        return {
            "resource_id": result.resource_id,
            "lease_id": result.lease.lease_id,
            "lease_version": result.lease.version,
            "expires_at": result.lease.expires_at.isoformat(),
            "lifecycle": result.final_lifecycle.model_dump(mode="json")
            if result.final_lifecycle
            else None,
        }

    def _handle_renew_lease(
        self,
        request: RenewLeaseInput,
    ) -> dict[str, Any]:
        result = self.orchestrator.renew_lease(
            request.resource_id,
            lease_id=str(request.lease_id),
            owner_id=self.principal.principal_id,
            ttl_ms=request.ttl_seconds * 1000.0,
            expected_lease_version=request.expected_lease_version,
        )
        self._require_action_success(result)
        assert result.lease is not None
        return {
            "resource_id": result.resource_id,
            "lease_id": result.lease.lease_id,
            "lease_version": result.lease.version,
            "expires_at": result.lease.expires_at.isoformat(),
        }

    def _handle_prepare_assay(
        self,
        request: PrepareAssayInput,
    ) -> dict[str, Any]:
        descriptor = self.orchestrator.registry.get_adapter(
            request.resource_id
        ).describe()
        preset = get_preset(request.preset_id)
        if not preset.is_compatible(descriptor):
            raise SurfaceCallFailure(
                SurfaceErrorCode.POLICY_DENIED,
                (
                    f"Preset '{request.preset_id}' is not compatible with "
                    f"resource '{request.resource_id}'."
                ),
            )

        run_id = str(uuid4())
        lease_id = None if request.lease_id is None else str(request.lease_id)
        task = preset.build_task(
            run_id=run_id,
            principal_id=self.principal.principal_id,
            resource_id=request.resource_id,
            lease_id=lease_id,
            expected_lease_version=request.expected_lease_version,
        )
        report = self.orchestrator.plan_task(task)
        candidate = report.best_candidate()
        contract = self.orchestrator.registry.resource_contract_for(
            request.resource_id
        )
        approval_required = self._requires_human_approval(contract)
        plan = {
            "resource_id": request.resource_id,
            "preset": preset.public_dict(),
            "admissible_and_feasible": candidate is not None,
            "selection_report": report.model_dump(mode="json"),
            "human_approval_required_for_execution": approval_required,
        }
        if request.dry_run:
            return {
                "dry_run": True,
                "resource_committed": False,
                "run_created": False,
                "plan": plan,
            }
        if candidate is None:
            raise SurfaceCallFailure(
                SurfaceErrorCode.POLICY_DENIED,
                "The selected resource did not pass admission and feasibility.",
                details={
                    "selection_report": report.model_dump(mode="json"),
                },
            )

        assert lease_id is not None
        lease = self.orchestrator.registry.lease_store.validate(
            lease_id,
            request.resource_id,
            self.principal.principal_id,
            expected_version=request.expected_lease_version,
        )
        lifecycle = self.orchestrator.registry.lifecycle_store.snapshot(
            request.resource_id
        )
        if (
            ResourceLifecycleState(lifecycle.state)
            != ResourceLifecycleState.RESERVED
        ):
            raise SurfaceCallFailure(
                SurfaceErrorCode.INVALID_STATE,
                "Assay preparation requires a RESERVED resource.",
            )
        record = AssayRunRecord(
            run_id=run_id,
            owner_id=self.principal.principal_id,
            resource_id=request.resource_id,
            preset_id=str(request.preset_id),
            lease_id=lease.lease_id,
            lease_version=lease.version,
            approval_required=approval_required,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            task=task,
        )
        self.run_store.add(record)
        return {
            "dry_run": False,
            "resource_committed": True,
            "run_created": True,
            "run_id": run_id,
            "state": RunState.PREPARED.value,
            "plan": plan,
        }

    def _handle_run_assay(
        self,
        request: RunAssayInput,
    ) -> dict[str, Any]:
        run_id = str(request.run_id)
        record = self._owned_record(run_id)
        if RunState(record.state) != RunState.PREPARED:
            raise SurfaceCallFailure(
                SurfaceErrorCode.INVALID_STATE,
                f"Run '{run_id}' is '{record.state}', not prepared.",
            )
        self.orchestrator.registry.lease_store.validate(
            record.lease_id,
            record.resource_id,
            self.principal.principal_id,
            expected_version=record.lease_version,
        )
        if record.approval_required:
            requirement = ApprovalRequirement(
                run_id=record.run_id,
                resource_id=record.resource_id,
                preset_id=record.preset_id,
                principal_id=record.owner_id,
            )
            self.approval_verifier.verify_and_consume(
                request.approval_token,
                requirement,
            )

        running = self.run_store.mark_running(run_id)
        task = running.task.model_copy(
            update={
                "idempotency_key": (
                    request.idempotency_key or f"mcp-run:{run_id}"
                )
            }
        )
        try:
            result = self.orchestrator.execute_task(task)
        except Exception as exc:
            self.run_store.mark_failed(
                run_id,
                summary={
                    "success": False,
                    "error_code": "INTERNAL_ERROR",
                    "message": "Execution raised before a normalized result was returned.",
                },
            )
            raise SurfaceCallFailure(
                SurfaceErrorCode.INTERNAL_ERROR,
                "Assay execution failed before a normalized result was returned.",
            ) from exc
        summary = self._summarize_result(record, result)
        finished = self.run_store.finish(
            run_id,
            result=result,
            summary=summary,
        )
        return {
            "run_id": run_id,
            "state": RunState(finished.state).value,
            "summary": summary,
        }

    def _handle_get_run_status(
        self,
        request: GetRunStatusInput,
    ) -> dict[str, Any]:
        record = self._owned_record(str(request.run_id))
        lifecycle = self.orchestrator.registry.lifecycle_store.snapshot(
            record.resource_id
        )
        return {
            "run_id": record.run_id,
            "resource_id": record.resource_id,
            "preset_id": record.preset_id,
            "state": RunState(record.state).value,
            "approval_required": record.approval_required,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "resource_lifecycle": lifecycle.model_dump(mode="json"),
        }

    def _handle_abort_run(
        self,
        request: AbortRunInput,
    ) -> dict[str, Any]:
        run_id = str(request.run_id)
        record = self._owned_record(run_id)
        state = RunState(record.state)
        if state in {RunState.SUCCEEDED, RunState.FAILED, RunState.ABORTED}:
            return {"run_id": run_id, "state": state.value, "already_terminal": True}
        if state == RunState.PREPARED:
            result = self.orchestrator.release_lease(
                record.resource_id,
                lease_id=record.lease_id,
                owner_id=self.principal.principal_id,
            )
        else:
            result = self.orchestrator.abort_backend(
                record.resource_id,
                lease_id=record.lease_id,
                owner_id=self.principal.principal_id,
            )
        self._require_action_success(result)
        aborted = self.run_store.mark_aborted(run_id)
        return {
            "run_id": run_id,
            "state": RunState(aborted.state).value,
            "resource_lifecycle": result.final_lifecycle.model_dump(mode="json")
            if result.final_lifecycle
            else None,
        }

    def _handle_get_result_summary(
        self,
        request: GetResultSummaryInput,
    ) -> dict[str, Any]:
        record = self._owned_record(str(request.run_id))
        if RunState(record.state) not in {
            RunState.SUCCEEDED,
            RunState.FAILED,
            RunState.ABORTED,
        }:
            raise SurfaceCallFailure(
                SurfaceErrorCode.INVALID_STATE,
                "A result summary is available only after the run is terminal.",
            )
        return {
            "run_id": record.run_id,
            "state": RunState(record.state).value,
            "summary": dict(record.summary),
            "raw_substrate_output_exposed": False,
        }

    def _handle_release_resource(
        self,
        request: ReleaseResourceInput,
    ) -> dict[str, Any]:
        result = self.orchestrator.release_lease(
            request.resource_id,
            lease_id=str(request.lease_id),
            owner_id=self.principal.principal_id,
            expected_state_version=request.expected_state_version,
        )
        self._require_action_success(result)
        self.run_store.abort_prepared_for_lease(
            owner_id=self.principal.principal_id,
            resource_id=request.resource_id,
            lease_id=str(request.lease_id),
        )
        return {
            "resource_id": request.resource_id,
            "released": True,
            "resource_lifecycle": result.final_lifecycle.model_dump(mode="json")
            if result.final_lifecycle
            else None,
        }

    def _resource_view(
        self,
        contract: PhysicalNeuralResourceContract,
        *,
        detailed: bool = False,
    ) -> dict[str, Any]:
        descriptor = contract.capabilities
        presets = [
            preset.public_dict()
            for preset in ASSAY_PRESETS.values()
            if preset.is_compatible(descriptor)
        ]
        view: dict[str, Any] = {
            "resource_id": descriptor.backend_id,
            "display_name": descriptor.display_name,
            "substrate_class": str(descriptor.capability.substrate_class),
            "runtime_kind": str(contract.evidence.runtime_kind),
            "evidence_level": str(contract.evidence.evidence_level),
            "lifecycle_state": (
                contract.state.lifecycle.value
                if contract.state.lifecycle is not None
                else "unknown"
            ),
            "health_state": (
                contract.state.health.value
                if contract.state.health is not None
                else "unknown"
            ),
            "locality": str(descriptor.policy.locality),
            "human_approval_required_for_real_execution": (
                self._requires_human_approval(contract)
            ),
            "compatible_presets": presets,
        }
        if detailed:
            view.update(
                {
                    "supported_task_types": list(
                        descriptor.capability.supported_task_types
                    ),
                    "input_modalities": sorted(
                        {str(item.modality) for item in descriptor.input_contracts}
                    ),
                    "typical_latency_ms": descriptor.timing.typical_latency_ms,
                    "telemetry_fields": sorted(contract.telemetry),
                    "agent_editable_physical_parameters": [],
                    "lease_required_for_execution": True,
                }
            )
        return view

    @staticmethod
    def _requires_human_approval(
        contract: PhysicalNeuralResourceContract,
    ) -> bool:
        return (
            RuntimeKind(contract.evidence.runtime_kind)
            == RuntimeKind.PHYSICAL_HARDWARE
            and SubstrateClass(contract.capabilities.capability.substrate_class)
            == SubstrateClass.WETWARE
        )

    def _owned_record(self, run_id: str) -> AssayRunRecord:
        try:
            record = self.run_store.get(run_id)
        except KeyError as exc:
            raise SurfaceCallFailure(
                SurfaceErrorCode.RESOURCE_NOT_FOUND,
                f"Run '{run_id}' does not exist.",
            ) from exc
        if record.owner_id != self.principal.principal_id:
            raise SurfaceCallFailure(
                SurfaceErrorCode.UNAUTHORIZED,
                "The authenticated principal does not own this run.",
            )
        return record

    def _summarize_result(
        self,
        record: AssayRunRecord,
        result: OrchestrationRunResult,
    ) -> dict[str, Any]:
        invocation = result.invocation
        error = result.error
        contract = self.orchestrator.registry.resource_contract_for(
            record.resource_id
        )
        summary = {
            "success": result.success,
            "selected_resource_id": result.decision.selected_backend_id,
            "preset_id": record.preset_id,
            "runtime_kind": str(contract.evidence.runtime_kind),
            "evidence_level": str(contract.evidence.evidence_level),
            "used_fallback": result.decision.used_fallback,
            "execution_latency_ms": (
                invocation.execution_latency_ms if invocation else None
            ),
            "confidence": invocation.confidence if invocation else None,
            "error_code": error.code if error else None,
            "error_message": error.message if error else None,
            "validation_failure_count": len(result.validation_failures),
            "raw_output_included": False,
            "orchestration_correlation_id": result.correlation_id,
        }
        if record.preset_id == "pattern_gate_v1":
            allowed_application_fields = {
                "application_id",
                "application_run_id",
                "application_status",
                "config_sha256",
                "decoder_sha256",
                "application_source_sha256",
                "runtime_kind",
                "evidence_level",
                "biological_claim",
                "trial_count",
                "scored_trial_count",
                "sham_trial_count",
                "pipeline_assertion_accuracy",
            }
            payload = invocation.output_payload if invocation else {}
            application = payload.get("application_summary", {})
            if isinstance(application, dict):
                summary["application"] = {
                    key: application[key]
                    for key in allowed_application_fields
                    if key in application
                }
            adapter = self.orchestrator.registry.get_adapter(record.resource_id)
            summary["artifact_references"] = [
                {
                    "artifact_id": item.artifact_id,
                    "kind": item.kind,
                    "uri": item.uri,
                    "media_type": item.media_type,
                    "metadata": dict(item.metadata),
                }
                for item in adapter.list_artifacts()
            ]
        return summary

    def _require_action_success(self, result: ControlPlaneActionResult) -> None:
        if result.success:
            return
        if result.error is None:
            raise SurfaceCallFailure(
                SurfaceErrorCode.INTERNAL_ERROR,
                "Control-plane action failed without a normalized error.",
            )
        raise self.from_control_error(result.error)

    @classmethod
    def _from_control_error(cls, error: ControlPlaneError) -> SurfaceError:
        code_map = {
            ControlPlaneErrorCode.RESOURCE_NOT_FOUND: SurfaceErrorCode.RESOURCE_NOT_FOUND,
            ControlPlaneErrorCode.POLICY_DENIED: SurfaceErrorCode.POLICY_DENIED,
            ControlPlaneErrorCode.INADMISSIBLE: SurfaceErrorCode.POLICY_DENIED,
            ControlPlaneErrorCode.RESOURCE_BUSY: SurfaceErrorCode.CONFLICT,
            ControlPlaneErrorCode.LEASE_EXPIRED: SurfaceErrorCode.CONFLICT,
            ControlPlaneErrorCode.LEASE_NOT_FOUND: SurfaceErrorCode.CONFLICT,
            ControlPlaneErrorCode.STATE_VERSION_CONFLICT: SurfaceErrorCode.CONFLICT,
            ControlPlaneErrorCode.INVALID_STATE_TRANSITION: SurfaceErrorCode.INVALID_STATE,
        }
        return SurfaceError(
            code=code_map.get(
                ControlPlaneErrorCode(error.code),
                SurfaceErrorCode.POLICY_DENIED,
            ),
            message=error.message,
            retryable=error.retryable,
            details=dict(error.details),
        )

    @classmethod
    def from_control_error(cls, error: ControlPlaneError) -> SurfaceCallFailure:
        mapped = cls._from_control_error(error)
        return SurfaceCallFailure(
            SurfaceErrorCode(mapped.code),
            mapped.message,
            retryable=mapped.retryable,
            details=mapped.details,
        )

    @staticmethod
    def _failure(
        request_id: str,
        tool_name: str,
        error: SurfaceError,
    ) -> ToolResponse:
        return ToolResponse(
            ok=False,
            request_id=request_id,
            tool=tool_name,
            error=error,
        )

    def _audit_completion(
        self,
        response: ToolResponse,
        arguments: dict[str, Any],
    ) -> None:
        self.audit_trail.record(
            request_id=response.request_id,
            principal_id=self.principal.principal_id,
            tool=response.tool,
            phase="completed",
            outcome="success" if response.ok else "denied",
            arguments=arguments,
            details=(
                {}
                if response.error is None
                else {"error_code": str(response.error.code)}
            ),
        )
