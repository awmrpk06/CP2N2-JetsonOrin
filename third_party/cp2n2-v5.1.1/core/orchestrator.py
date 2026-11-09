"""Lifecycle-, lease-, and evidence-aware CP²N² orchestrator."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from adapters.base_adapter import (
    AdapterInvocationResult,
    AdapterPreparationResult,
    BaseAdapter,
)
from core.errors import (
    ControlPlaneError,
    ControlPlaneErrorCode,
    ControlPlaneException,
)
from core.execution import run_with_timeout
from core.leases import ResourceLease
from core.lifecycle import LifecycleSnapshot, LifecycleTransition
from core.matcher import BackendMatcher, MatchCandidate, MatchReport
from core.task_model import TaskRequest
from core.twin_registry import TwinRegistry
from descriptors.capability_schema import ResetMode
from descriptors.resource_contract import (
    ContractAdmissionResult,
    ResourceLifecycleState,
    RuntimeKind,
    assess_contract_admission,
)


ACTIVE_STATES = {
    ResourceLifecycleState.RESERVED,
    ResourceLifecycleState.PREPARING,
    ResourceLifecycleState.RUNNING,
    ResourceLifecycleState.VALIDATING,
    ResourceLifecycleState.COOLDOWN,
    ResourceLifecycleState.DEGRADED,
}


class OrchestrationDecision(BaseModel):
    """Selection result returned before or during execution."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    selected_backend_id: str | None = None
    selected_score: float | None = None
    used_fallback: bool = False
    ranked_candidates: list[MatchCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OrchestrationRunResult(BaseModel):
    """End-to-end result of a lifecycle-managed task execution."""

    model_config = ConfigDict(extra="forbid")

    decision: OrchestrationDecision
    correlation_id: str | None = None
    preparation: AdapterPreparationResult | None = None
    invocation: AdapterInvocationResult | None = None
    telemetry_before: dict[str, float | int | str | bool | None] = Field(
        default_factory=dict
    )
    telemetry_after: dict[str, float | int | str | bool | None] = Field(
        default_factory=dict
    )
    contract_admission: ContractAdmissionResult | None = None
    lease: ResourceLease | None = None
    lifecycle_history: list[LifecycleTransition] = Field(default_factory=list)
    final_lifecycle: LifecycleSnapshot | None = None
    recovery_actions: list[str] = Field(default_factory=list)
    validation_failures: list[str] = Field(default_factory=list)
    error: ControlPlaneError | None = None
    idempotent_replay: bool = False
    success: bool = False
    failure_reason: str | None = None


class ControlPlaneActionResult(BaseModel):
    """Result of an explicit abort or reconciliation request."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str
    correlation_id: str
    success: bool
    lease: ResourceLease | None = None
    final_lifecycle: LifecycleSnapshot | None = None
    actions: list[str] = Field(default_factory=list)
    error: ControlPlaneError | None = None


class CP2N2Orchestrator:
    """Coordinates discovery, leases, lifecycle, invocation, and recovery."""

    def __init__(
        self,
        registry: TwinRegistry | None = None,
        matcher: BackendMatcher | None = None,
    ) -> None:
        self._registry = registry or TwinRegistry()
        self._matcher = matcher or BackendMatcher()

    @property
    def registry(self) -> TwinRegistry:
        """Expose the underlying registry."""
        return self._registry

    def register_adapter(self, adapter: BaseAdapter, *, overwrite: bool = False) -> None:
        """Register one backend adapter with the control plane."""
        self._registry.register(adapter=adapter, overwrite=overwrite)

    def discover_backends(self) -> list[dict]:
        """Return v3.0-compatible descriptors for all known backends."""
        return [
            descriptor.to_public_dict()
            for descriptor in self._registry.list_descriptors()
        ]

    def discover_resource_contracts(self) -> list[dict]:
        """Return contracts overlaid with control-plane lifecycle state."""
        return [
            contract.model_dump(mode="json")
            for contract in self._registry.list_resource_contracts()
        ]

    def discover_adapter_capabilities(self) -> list[dict]:
        """Return A5 control-adapter and substrate-runtime declarations."""

        return [
            declaration.model_dump(mode="json")
            for declaration in (
                self._registry.list_adapter_capability_declarations()
            )
        ]

    def plan_task(self, task: TaskRequest) -> MatchReport:
        """Run admission, feasibility, and selection for the task."""
        descriptors = self._registry.list_descriptors()
        if task.direct_backend_id is not None:
            descriptors = [
                descriptor
                for descriptor in descriptors
                if descriptor.backend_id == task.direct_backend_id
            ]
        runtime_state = self._selection_runtime_snapshot(task)
        return self._matcher.rank_backends(
            task=task,
            descriptors=descriptors,
            runtime_state=runtime_state,
        )

    def _selection_runtime_snapshot(
        self,
        task: TaskRequest,
    ) -> dict[str, dict[str, float | int | str | bool | None]]:
        """Overlay lifecycle, lease, safety, and cost facts for A3 decisions."""
        runtime_state: dict[
            str,
            dict[str, float | int | str | bool | None],
        ] = {}
        for backend_id in self._registry.list_backend_ids():
            runtime: dict[str, float | int | str | bool | None] = {}
            snapshot = self._registry.lifecycle_store.snapshot(backend_id)
            lease = self._registry.lease_store.current(backend_id)
            contract = self._registry.resource_contract_for(backend_id)
            for name, observation in contract.telemetry.items():
                value = observation.value
                if value is None or isinstance(value, (float, int, str, bool)):
                    runtime[name] = value

            runtime["control_plane_lifecycle"] = str(snapshot.state)
            runtime["control_plane_state_version"] = snapshot.version
            runtime["reservable"] = lease is None or (
                task.lease_id is not None
                and lease.lease_id == task.lease_id
                and lease.owner_id == task.client_id
            )

            safety_reasons = self._contract_safety_violations(contract)
            runtime["contract_safety_admissible"] = not safety_reasons
            runtime["contract_safety_reason"] = (
                None if not safety_reasons else "; ".join(safety_reasons)
            )

            if contract.cost is not None and contract.cost.estimated_cost is not None:
                value = contract.cost.estimated_cost.value
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    runtime["estimated_cost"] = float(value)
                    runtime["cost_currency"] = contract.cost.currency
            runtime_state[backend_id] = runtime
        return runtime_state

    @staticmethod
    def _contract_safety_violations(contract) -> list[str]:
        """Return only admission/safety violations, not dynamic feasibility."""
        reasons: list[str] = []
        if contract.evidence.runtime_kind == RuntimeKind.UNKNOWN:
            reasons.append("runtime kind is not attested")
        if contract.safety is None:
            return reasons + ["safety contract is missing"]
        if "invoke" not in contract.safety.permitted_operations:
            reasons.append("operation 'invoke' is not explicitly permitted")
        if contract.safety.human_supervision_required is None:
            reasons.append("human supervision requirement is unknown")
        if contract.safety.exclusive_access_required is None:
            reasons.append("exclusive-access requirement is unknown")
        if contract.evidence.runtime_kind == RuntimeKind.PHYSICAL_HARDWARE:
            if not contract.identity.hardware_id:
                reasons.append("physical hardware identity is missing")
            if contract.evidence.attested_at is None:
                reasons.append("physical runtime attestation is missing")
            if not contract.safety.hard_limits:
                reasons.append("physical hardware has no declared hard limits")
            if contract.safety.emergency_stop_supported is None:
                reasons.append("physical emergency-stop support is unknown")
            if contract.safety.operator_acknowledgement_required is None:
                reasons.append("operator acknowledgement is unknown")
        return reasons

    def execute_task(self, task: TaskRequest) -> OrchestrationRunResult:
        """Execute one task with idempotency, a lease, and bounded phases."""

        correlation_id = task.correlation_id or str(uuid4())
        fingerprint = self._request_fingerprint(task)
        if task.idempotency_key is not None:
            try:
                cached = self._registry.idempotency_store.begin(
                    task.client_id,
                    task.idempotency_key,
                    fingerprint,
                )
            except ControlPlaneException as exc:
                return self._early_error_result(task, correlation_id, exc.error)
            if cached is not None:
                replay = cached.model_copy(deep=True)
                replay.idempotent_replay = True
                replay.decision.notes.append(
                    f"Replayed completed request for idempotency key "
                    f"'{task.idempotency_key}'."
                )
                return replay

        try:
            result = self._execute_task_once(task, correlation_id)
        except Exception:
            if task.idempotency_key is not None:
                self._registry.idempotency_store.abandon(
                    task.client_id,
                    task.idempotency_key,
                )
            raise

        if task.idempotency_key is not None:
            if (
                result.lease is None
                and result.error is not None
                and result.error.retryable
            ):
                self._registry.idempotency_store.abandon(
                    task.client_id,
                    task.idempotency_key,
                )
            else:
                self._registry.idempotency_store.complete(
                    task.client_id,
                    task.idempotency_key,
                    result,
                )
        return result

    def _execute_task_once(
        self,
        task: TaskRequest,
        correlation_id: str,
    ) -> OrchestrationRunResult:
        report = self.plan_task(task)
        decision = OrchestrationDecision(
            task_id=task.task_id,
            ranked_candidates=report.candidates,
        )
        accepted_candidates = report.accepted_candidates()

        if not accepted_candidates:
            early_admission: ContractAdmissionResult | None = None
            safety_violations = [
                violation.split(":", 1)[1].strip()
                for candidate in report.candidates
                for violation in candidate.admission.violations
                if violation.startswith("resource_contract_safety:")
            ]
            if task.direct_backend_id and not self._registry.has_backend(
                task.direct_backend_id
            ):
                error = ControlPlaneError(
                    code=ControlPlaneErrorCode.RESOURCE_NOT_FOUND,
                    message=(
                        f"Directed backend '{task.direct_backend_id}' is not registered."
                    ),
                )
            elif any(
                any(
                    violation.startswith("resource_reservable:")
                    for violation in candidate.feasibility.violations
                )
                for candidate in report.candidates
            ):
                error = ControlPlaneError(
                    code=ControlPlaneErrorCode.RESOURCE_BUSY,
                    message="All otherwise admissible resources are currently reserved.",
                    retryable=True,
                )
            elif safety_violations:
                early_admission = ContractAdmissionResult(
                    admissible=False,
                    reasons=safety_violations,
                )
                error = ControlPlaneError(
                    code=ControlPlaneErrorCode.INADMISSIBLE,
                    message=(
                        "No resource passed contract safety admission: "
                        + "; ".join(safety_violations)
                    ),
                )
            else:
                error = ControlPlaneError(
                    code=ControlPlaneErrorCode.POLICY_DENIED,
                    message="No compatible backend passed matching and policy checks.",
                )
            decision.notes.append(error.message)
            return OrchestrationRunResult(
                decision=decision,
                correlation_id=correlation_id,
                contract_admission=early_admission,
                error=error,
                failure_reason=self._format_error(error),
            )

        last_result: OrchestrationRunResult | None = None
        for index, candidate in enumerate(accepted_candidates):
            decision.selected_backend_id = candidate.backend_id
            decision.selected_score = candidate.score
            decision.used_fallback = index > 0
            if index:
                decision.notes.append(
                    f"Trying fallback candidate '{candidate.backend_id}'."
                )
            else:
                decision.notes.append(
                    f"Selected primary candidate '{candidate.backend_id}'."
                )

            result = self._execute_candidate(
                task,
                candidate,
                decision,
                correlation_id,
            )
            if result.success:
                return result
            last_result = result
            if not task.allow_fallback:
                return result

        assert last_result is not None
        return last_result

    def _execute_candidate(
        self,
        task: TaskRequest,
        candidate: MatchCandidate,
        decision: OrchestrationDecision,
        correlation_id: str,
    ) -> OrchestrationRunResult:
        backend_id = candidate.backend_id
        adapter = self._registry.get_adapter(backend_id)
        owner_id = task.client_id
        lease: ResourceLease | None = None
        telemetry_before: dict[str, float | int | str | bool | None] = {}
        preparation: AdapterPreparationResult | None = None
        admission: ContractAdmissionResult | None = None

        try:
            snapshot = self._registry.lifecycle_store.snapshot(backend_id)
            if (
                task.expected_resource_state_version is not None
                and snapshot.version != task.expected_resource_state_version
            ):
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Resource '{backend_id}' is at state version "
                        f"{snapshot.version}, expected "
                        f"{task.expected_resource_state_version}."
                    ),
                    retryable=True,
                )
            if task.lease_id is not None:
                if (
                    ResourceLifecycleState(snapshot.state)
                    != ResourceLifecycleState.RESERVED
                ):
                    raise ControlPlaneException(
                        ControlPlaneErrorCode.INVALID_STATE_TRANSITION,
                        (
                            f"Pre-acquired lease requires '{backend_id}' to be "
                            f"reserved, but it is '{snapshot.state}'."
                        ),
                    )
                lease = self._registry.lease_store.validate(
                    task.lease_id,
                    backend_id,
                    owner_id,
                    expected_version=task.expected_lease_version,
                )
            else:
                if (
                    ResourceLifecycleState(snapshot.state)
                    != ResourceLifecycleState.READY
                ):
                    raise ControlPlaneException(
                        ControlPlaneErrorCode.RESOURCE_BUSY,
                        (
                            f"Resource '{backend_id}' is in lifecycle state "
                            f"'{snapshot.state}', not 'ready'."
                        ),
                        retryable=True,
                    )
                lease = self._registry.lease_store.acquire(
                    backend_id,
                    owner_id,
                    ttl_ms=task.lease_ttl_ms,
                    idempotency_key=task.idempotency_key,
                )
                self._registry.lifecycle_store.transition(
                    backend_id,
                    ResourceLifecycleState.RESERVED,
                    expected_version=snapshot.version,
                    reason=f"exclusive lease {lease.lease_id} acquired",
                    correlation_id=correlation_id,
                )
        except ControlPlaneException as exc:
            if lease is not None:
                self._release_lease_safely(lease, owner_id)
            decision.notes.append(exc.error.message)
            return self._candidate_error_result(
                decision,
                correlation_id,
                exc.error,
                backend_id=backend_id,
                lease=lease,
            )

        telemetry_outcome = run_with_timeout(
            adapter.collect_telemetry,
            task.preparation_timeout_ms,
        )
        if telemetry_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.TELEMETRY_STALE,
                    message=(
                        f"Pre-execution telemetry timed out for '{backend_id}'."
                    ),
                    retryable=True,
                ),
                uncertain=True,
            )
        if telemetry_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.TELEMETRY_STALE,
                    message=(
                        f"Pre-execution telemetry failed for '{backend_id}': "
                        f"{telemetry_outcome.error}"
                    ),
                    retryable=True,
                ),
                uncertain=True,
            )
        telemetry_before = telemetry_outcome.value or {}
        self._transition(
            backend_id,
            ResourceLifecycleState.PREPARING,
            correlation_id,
            "backend preparation started",
        )
        preparation_outcome = run_with_timeout(
            lambda: adapter.prepare(task),
            task.preparation_timeout_ms,
        )
        if preparation_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.PREPARATION_TIMEOUT,
                    message=(
                        f"Preparation timed out after "
                        f"{task.preparation_timeout_ms:.0f} ms for '{backend_id}'."
                    ),
                    retryable=True,
                    details={"provider_status": "unknown"},
                ),
                telemetry_before=telemetry_before,
                uncertain=True,
            )
        if preparation_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.PREPARATION_FAILED,
                    message=(
                        f"Preparation raised for '{backend_id}': "
                        f"{preparation_outcome.error}"
                    ),
                    retryable=True,
                ),
                telemetry_before=telemetry_before,
            )

        preparation = preparation_outcome.value
        assert preparation is not None
        if not preparation.prepared:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.PREPARATION_FAILED,
                    message=(
                        f"Preparation failed for '{backend_id}': "
                        f"{preparation.details}"
                    ),
                    retryable=True,
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
            )

        try:
            self._registry.lease_store.validate(
                lease.lease_id,
                backend_id,
                owner_id,
                expected_version=lease.version,
            )
        except ControlPlaneException as exc:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                exc.error,
                preparation=preparation,
                telemetry_before=telemetry_before,
            )

        admission_outcome = run_with_timeout(
            lambda: assess_contract_admission(
                adapter.resource_contract(),
                operation="invoke",
            ),
            task.validation_timeout_ms,
        )
        if admission_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.TELEMETRY_STALE,
                    message=f"Contract admission timed out for '{backend_id}'.",
                    retryable=True,
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
                uncertain=True,
            )
        if admission_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.INADMISSIBLE,
                    message=(
                        f"Contract admission failed for '{backend_id}': "
                        f"{admission_outcome.error}"
                    ),
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
            )
        admission = admission_outcome.value
        assert admission is not None
        if not admission.admissible:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.INADMISSIBLE,
                    message=(
                        f"Backend '{backend_id}' is inadmissible: "
                        + "; ".join(admission.reasons)
                    ),
                    details={"reasons": admission.reasons},
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
            )

        self._transition(
            backend_id,
            ResourceLifecycleState.RUNNING,
            correlation_id,
            "substrate invocation started",
        )
        invocation_outcome = run_with_timeout(
            lambda: adapter.invoke(task),
            task.invocation_timeout_ms,
        )
        if invocation_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.EXECUTION_STATUS_UNKNOWN,
                    message=(
                        f"Invocation exceeded {task.invocation_timeout_ms:.0f} ms "
                        f"for '{backend_id}'; completion status is unknown."
                    ),
                    retryable=False,
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
                uncertain=True,
            )
        if invocation_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.INVOCATION_FAILED,
                    message=(
                        f"Invocation failed for '{backend_id}': "
                        f"{invocation_outcome.error}"
                    ),
                    retryable=False,
                    details={"provider_status": "uncertain"},
                ),
                preparation=preparation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
                uncertain=True,
            )

        invocation = invocation_outcome.value
        assert invocation is not None
        try:
            self._registry.lease_store.validate(
                lease.lease_id,
                backend_id,
                owner_id,
                expected_version=lease.version,
            )
        except ControlPlaneException as exc:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                exc.error,
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
            )

        self._transition(
            backend_id,
            ResourceLifecycleState.VALIDATING,
            correlation_id,
            "postcondition validation started",
        )

        def validate() -> tuple[
            dict[str, float | int | str | bool | None],
            list[str],
        ]:
            telemetry = adapter.collect_telemetry()
            return telemetry, self._validate_postconditions(
                task,
                invocation,
                telemetry,
            )

        validation_outcome = run_with_timeout(validate, task.validation_timeout_ms)
        if validation_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.VALIDATION_TIMEOUT,
                    message=(
                        f"Postcondition validation exceeded "
                        f"{task.validation_timeout_ms:.0f} ms for '{backend_id}'."
                    ),
                    retryable=False,
                ),
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
            )
        if validation_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.POSTCONDITION_FAILED,
                    message=(
                        f"Postcondition validation raised for '{backend_id}': "
                        f"{validation_outcome.error}"
                    ),
                ),
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                contract_admission=admission,
            )

        telemetry_after, validation_failures = validation_outcome.value
        validation_error: ControlPlaneError | None = None
        if validation_failures:
            error_code = (
                ControlPlaneErrorCode.TELEMETRY_STALE
                if any(
                    "age_of_information" in failure
                    for failure in validation_failures
                )
                else ControlPlaneErrorCode.POSTCONDITION_FAILED
            )
            validation_error = ControlPlaneError(
                code=error_code,
                message=(
                    f"Postcondition validation failed for '{backend_id}': "
                    + "; ".join(validation_failures)
                ),
                details={"failures": validation_failures},
            )

        self._transition(
            backend_id,
            ResourceLifecycleState.COOLDOWN,
            correlation_id,
            "execution completed; cooldown started",
        )
        recovery_outcome = run_with_timeout(
            lambda: self._maybe_recover(adapter, telemetry_after),
            task.cooldown_timeout_ms,
        )
        if recovery_outcome.timed_out:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.EXECUTION_STATUS_UNKNOWN,
                    message=(
                        f"Cooldown exceeded {task.cooldown_timeout_ms:.0f} ms "
                        f"for '{backend_id}'."
                    ),
                ),
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                telemetry_after=telemetry_after,
                contract_admission=admission,
                validation_failures=validation_failures,
                uncertain=True,
            )
        if recovery_outcome.error is not None:
            return self._fail_active_candidate(
                task,
                decision,
                correlation_id,
                adapter,
                lease,
                ControlPlaneError(
                    code=ControlPlaneErrorCode.RECONCILIATION_FAILED,
                    message=(
                        f"Cooldown/recovery failed for '{backend_id}': "
                        f"{recovery_outcome.error}"
                    ),
                ),
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                telemetry_after=telemetry_after,
                contract_admission=admission,
                validation_failures=validation_failures,
            )

        recovery_actions = recovery_outcome.value or []
        final_lifecycle = self._transition(
            backend_id,
            ResourceLifecycleState.READY,
            correlation_id,
            "cooldown completed",
        )
        self._release_lease_safely(lease, owner_id)

        if validation_error is not None:
            decision.notes.append(validation_error.message)
            return OrchestrationRunResult(
                decision=decision,
                correlation_id=correlation_id,
                preparation=preparation,
                invocation=invocation,
                telemetry_before=telemetry_before,
                telemetry_after=telemetry_after,
                contract_admission=admission,
                lease=lease,
                lifecycle_history=self._history(correlation_id),
                final_lifecycle=final_lifecycle,
                recovery_actions=recovery_actions,
                validation_failures=validation_failures,
                error=validation_error,
                failure_reason=self._format_error(validation_error),
            )

        return OrchestrationRunResult(
            decision=decision,
            correlation_id=correlation_id,
            preparation=preparation,
            invocation=invocation,
            telemetry_before=telemetry_before,
            telemetry_after=telemetry_after,
            contract_admission=admission,
            lease=lease,
            lifecycle_history=self._history(correlation_id),
            final_lifecycle=final_lifecycle,
            recovery_actions=recovery_actions,
            success=True,
        )

    def reserve_backend(
        self,
        backend_id: str,
        *,
        owner_id: str,
        ttl_ms: float = 60_000.0,
        expected_state_version: int | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneActionResult:
        """Acquire an exclusive lease and move READY to RESERVED."""

        correlation = correlation_id or str(uuid4())
        lease: ResourceLease | None = None
        try:
            self._registry.get_adapter(backend_id)
            current = self._registry.lifecycle_store.snapshot(backend_id)
            if (
                expected_state_version is not None
                and current.version != expected_state_version
            ):
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Resource '{backend_id}' is at version {current.version}, "
                        f"expected {expected_state_version}."
                    ),
                    retryable=True,
                )
            if ResourceLifecycleState(current.state) != ResourceLifecycleState.READY:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.RESOURCE_BUSY,
                    f"Resource '{backend_id}' is not ready for reservation.",
                    retryable=True,
                )
            lease = self._registry.lease_store.acquire(
                backend_id,
                owner_id,
                ttl_ms=ttl_ms,
                idempotency_key=idempotency_key,
            )
            final = self._registry.lifecycle_store.transition(
                backend_id,
                ResourceLifecycleState.RESERVED,
                expected_version=current.version,
                reason=f"explicit lease {lease.lease_id} acquired",
                correlation_id=correlation,
            )
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=True,
                lease=lease,
                final_lifecycle=final,
                actions=["Exclusive lease acquired."],
            )
        except ControlPlaneException as exc:
            if lease is not None:
                self._release_lease_safely(lease, owner_id)
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=exc.error,
            )
        except KeyError:
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=ControlPlaneError(
                    code=ControlPlaneErrorCode.RESOURCE_NOT_FOUND,
                    message=f"Backend '{backend_id}' is not registered.",
                ),
            )

    def renew_lease(
        self,
        backend_id: str,
        *,
        lease_id: str,
        owner_id: str,
        ttl_ms: float,
        expected_lease_version: int | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneActionResult:
        """Renew a lease with ownership and version checks."""

        correlation = correlation_id or str(uuid4())
        try:
            renewed = self._registry.lease_store.renew(
                lease_id,
                backend_id,
                owner_id,
                ttl_ms=ttl_ms,
                expected_version=expected_lease_version,
            )
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=True,
                lease=renewed,
                final_lifecycle=self._registry.lifecycle_store.snapshot(backend_id),
                actions=[f"Lease renewed to version {renewed.version}."],
            )
        except ControlPlaneException as exc:
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=exc.error,
            )

    def release_lease(
        self,
        backend_id: str,
        *,
        lease_id: str,
        owner_id: str,
        expected_state_version: int | None = None,
        correlation_id: str | None = None,
    ) -> ControlPlaneActionResult:
        """Release an unused RESERVED lease; active work requires abort."""

        correlation = correlation_id or str(uuid4())
        try:
            lease = self._registry.lease_store.validate(
                lease_id,
                backend_id,
                owner_id,
            )
            current = self._registry.lifecycle_store.snapshot(backend_id)
            if (
                expected_state_version is not None
                and current.version != expected_state_version
            ):
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Resource '{backend_id}' is at version {current.version}, "
                        f"expected {expected_state_version}."
                    ),
                    retryable=True,
                )
            if (
                ResourceLifecycleState(current.state)
                != ResourceLifecycleState.RESERVED
            ):
                raise ControlPlaneException(
                    ControlPlaneErrorCode.POLICY_DENIED,
                    "Only an unused RESERVED lease may be released; abort active work.",
                )
            final = self._registry.lifecycle_store.transition(
                backend_id,
                ResourceLifecycleState.READY,
                expected_version=current.version,
                reason=f"unused lease {lease_id} released",
                correlation_id=correlation,
            )
            self._registry.lease_store.release(
                lease_id,
                backend_id,
                owner_id,
            )
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=True,
                lease=lease,
                final_lifecycle=final,
                actions=["Unused lease released."],
            )
        except ControlPlaneException as exc:
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=exc.error,
            )

    def abort_backend(
        self,
        backend_id: str,
        *,
        lease_id: str,
        owner_id: str,
        correlation_id: str | None = None,
        timeout_ms: float = 5_000.0,
    ) -> ControlPlaneActionResult:
        """Explicitly abort one leased active backend."""

        correlation = correlation_id or str(uuid4())
        try:
            lease = self._registry.lease_store.validate(
                lease_id,
                backend_id,
                owner_id,
            )
            adapter = self._registry.get_adapter(backend_id)
            actions, final = self._abort_and_reconcile(
                adapter,
                lease,
                owner_id,
                correlation,
                timeout_ms=timeout_ms,
                uncertain=True,
            )
            success = ResourceLifecycleState(final.state) in {
                ResourceLifecycleState.READY,
                ResourceLifecycleState.DEGRADED,
            }
            error = None
            if not success:
                error = ControlPlaneError(
                    code=ControlPlaneErrorCode.ABORT_FAILED,
                    message=(
                        f"Abort did not restore '{backend_id}' to a usable state."
                    ),
                    details={"final_state": final.state},
                )
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=success,
                lease=lease,
                final_lifecycle=final,
                actions=actions,
                error=error,
            )
        except ControlPlaneException as exc:
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=exc.error,
            )

    def reconcile_backend(
        self,
        backend_id: str,
        *,
        expected_state_version: int | None = None,
        correlation_id: str | None = None,
        timeout_ms: float = 5_000.0,
    ) -> ControlPlaneActionResult:
        """Reconcile control-plane state with current provider telemetry."""

        correlation = correlation_id or str(uuid4())
        try:
            current = self._registry.lifecycle_store.snapshot(backend_id)
            active_lease = self._registry.lease_store.current(backend_id)
            if active_lease is not None:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.RESOURCE_BUSY,
                    (
                        f"Resource '{backend_id}' has active lease "
                        f"'{active_lease.lease_id}'; abort it before reconciliation."
                    ),
                    retryable=True,
                )
            if (
                expected_state_version is not None
                and current.version != expected_state_version
            ):
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Resource '{backend_id}' is at version {current.version}, "
                        f"expected {expected_state_version}."
                    ),
                    retryable=True,
                )
            adapter = self._registry.get_adapter(backend_id)
            outcome = run_with_timeout(adapter.collect_telemetry, timeout_ms)
            if outcome.timed_out:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.TELEMETRY_STALE,
                    f"Provider telemetry timed out for '{backend_id}'.",
                    retryable=True,
                )
            if outcome.error is not None:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.RECONCILIATION_FAILED,
                    f"Provider telemetry failed for '{backend_id}': {outcome.error}",
                    retryable=True,
                )
            telemetry = outcome.value or {}
            target = self._provider_state_target(telemetry, current)
            if (
                ResourceLifecycleState(current.state) in ACTIVE_STATES
                and ResourceLifecycleState(current.state)
                != ResourceLifecycleState.ABORTING
            ):
                current = self._registry.lifecycle_store.transition(
                    backend_id,
                    ResourceLifecycleState.ABORTING,
                    expected_version=current.version,
                    reason="provider reconciliation after active-state uncertainty",
                    correlation_id=correlation,
                )
            final = self._registry.lifecycle_store.transition(
                backend_id,
                target,
                expected_version=current.version,
                reason="control-plane state reconciled with provider telemetry",
                correlation_id=correlation,
            )
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=True,
                final_lifecycle=final,
                actions=[f"Reconciled provider state to {target.value}."],
            )
        except ControlPlaneException as exc:
            return ControlPlaneActionResult(
                resource_id=backend_id,
                correlation_id=correlation,
                success=False,
                error=exc.error,
            )

    def reset_backend(self, backend_id: str, mode: ResetMode | None = None) -> bool:
        """Reset one unleased backend through its adapter."""
        if self._registry.lease_store.current(backend_id) is not None:
            return False
        adapter = self._registry.get_adapter(backend_id)
        result = adapter.reset(mode=mode)
        if result:
            self.reconcile_backend(backend_id)
        return result

    def recalibrate_backend(self, backend_id: str) -> bool:
        """Recalibrate one unleased backend through its adapter."""
        if self._registry.lease_store.current(backend_id) is not None:
            return False
        adapter = self._registry.get_adapter(backend_id)
        result = adapter.recalibrate()
        if result:
            self.reconcile_backend(backend_id)
        return result

    def _fail_active_candidate(
        self,
        task: TaskRequest,
        decision: OrchestrationDecision,
        correlation_id: str,
        adapter: BaseAdapter,
        lease: ResourceLease,
        error: ControlPlaneError,
        *,
        preparation: AdapterPreparationResult | None = None,
        invocation: AdapterInvocationResult | None = None,
        telemetry_before: dict[str, float | int | str | bool | None] | None = None,
        telemetry_after: dict[str, float | int | str | bool | None] | None = None,
        contract_admission: ContractAdmissionResult | None = None,
        validation_failures: list[str] | None = None,
        uncertain: bool = False,
    ) -> OrchestrationRunResult:
        actions, final = self._abort_and_reconcile(
            adapter,
            lease,
            task.client_id,
            correlation_id,
            timeout_ms=task.abort_timeout_ms,
            uncertain=uncertain,
        )
        decision.notes.append(error.message)
        return OrchestrationRunResult(
            decision=decision,
            correlation_id=correlation_id,
            preparation=preparation,
            invocation=invocation,
            telemetry_before=telemetry_before or {},
            telemetry_after=telemetry_after or {},
            contract_admission=contract_admission,
            lease=lease,
            lifecycle_history=self._history(correlation_id),
            final_lifecycle=final,
            recovery_actions=actions,
            validation_failures=validation_failures or [],
            error=error,
            failure_reason=self._format_error(error),
        )

    def _abort_and_reconcile(
        self,
        adapter: BaseAdapter,
        lease: ResourceLease,
        owner_id: str,
        correlation_id: str,
        *,
        timeout_ms: float,
        uncertain: bool,
    ) -> tuple[list[str], LifecycleSnapshot]:
        backend_id = lease.resource_id
        actions: list[str] = []
        current = self._registry.lifecycle_store.snapshot(backend_id)
        if ResourceLifecycleState(current.state) != ResourceLifecycleState.ABORTING:
            current = self._registry.lifecycle_store.transition(
                backend_id,
                ResourceLifecycleState.ABORTING,
                expected_version=current.version,
                reason="abort requested after incomplete execution phase",
                correlation_id=correlation_id,
            )
        abort_outcome = run_with_timeout(adapter.abort, timeout_ms)
        abort_succeeded = (
            not abort_outcome.timed_out
            and abort_outcome.error is None
            and abort_outcome.value is True
        )
        if abort_outcome.timed_out:
            actions.append("Abort timed out; provider status remains uncertain.")
        elif abort_outcome.error is not None:
            actions.append(f"Abort raised: {abort_outcome.error}")
        elif abort_succeeded:
            actions.append("Provider acknowledged abort.")
        else:
            actions.append("Provider does not confirm abort support.")

        if uncertain and not abort_succeeded:
            target = ResourceLifecycleState.UNREACHABLE
        else:
            telemetry_outcome = run_with_timeout(
                adapter.collect_telemetry,
                timeout_ms,
            )
            if (
                telemetry_outcome.timed_out
                or telemetry_outcome.error is not None
            ):
                target = ResourceLifecycleState.UNREACHABLE
            else:
                target = self._provider_state_target(
                    telemetry_outcome.value or {},
                    current,
                )

        final = self._registry.lifecycle_store.transition(
            backend_id,
            target,
            expected_version=current.version,
            reason="provider state reconciled after abort",
            correlation_id=correlation_id,
        )
        self._release_lease_safely(lease, owner_id)
        actions.append(f"Lease released; lifecycle is {target.value}.")
        return actions, final

    def _transition(
        self,
        backend_id: str,
        state: ResourceLifecycleState,
        correlation_id: str,
        reason: str,
    ) -> LifecycleSnapshot:
        current = self._registry.lifecycle_store.snapshot(backend_id)
        return self._registry.lifecycle_store.transition(
            backend_id,
            state,
            expected_version=current.version,
            reason=reason,
            correlation_id=correlation_id,
        )

    def _release_lease_safely(self, lease: ResourceLease, owner_id: str) -> None:
        try:
            self._registry.lease_store.release(
                lease.lease_id,
                lease.resource_id,
                owner_id,
            )
        except ControlPlaneException:
            pass

    def _candidate_error_result(
        self,
        decision: OrchestrationDecision,
        correlation_id: str,
        error: ControlPlaneError,
        *,
        backend_id: str,
        lease: ResourceLease | None,
    ) -> OrchestrationRunResult:
        final = self._registry.lifecycle_store.snapshot(backend_id)
        return OrchestrationRunResult(
            decision=decision,
            correlation_id=correlation_id,
            lease=lease,
            lifecycle_history=self._history(correlation_id),
            final_lifecycle=final,
            error=error,
            failure_reason=self._format_error(error),
        )

    @staticmethod
    def _early_error_result(
        task: TaskRequest,
        correlation_id: str,
        error: ControlPlaneError,
    ) -> OrchestrationRunResult:
        return OrchestrationRunResult(
            decision=OrchestrationDecision(
                task_id=task.task_id,
                notes=[error.message],
            ),
            correlation_id=correlation_id,
            error=error,
            failure_reason=CP2N2Orchestrator._format_error(error),
        )

    def _history(self, correlation_id: str) -> list[LifecycleTransition]:
        return self._registry.lifecycle_store.history(correlation_id=correlation_id)

    @staticmethod
    def _request_fingerprint(task: TaskRequest) -> str:
        payload = task.model_dump(
            mode="json",
            exclude={"correlation_id"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _format_error(error: ControlPlaneError) -> str:
        return f"{error.code}: {error.message}"

    @staticmethod
    def _provider_state_target(
        telemetry: dict[str, float | int | str | bool | None],
        current: LifecycleSnapshot,
    ) -> ResourceLifecycleState:
        readiness = str(telemetry.get("readiness_state", "")).strip().lower()
        health = str(telemetry.get("health_status", "")).strip().lower()
        if readiness in {"unavailable", "offline", "unreachable"} or health in {
            "offline",
            "unreachable",
        }:
            return ResourceLifecycleState.UNREACHABLE
        if health in {"degraded", "warning"}:
            return ResourceLifecycleState.DEGRADED
        if readiness == "ready" or health in {"ready", "healthy"}:
            return ResourceLifecycleState.READY
        if ResourceLifecycleState(current.state) == ResourceLifecycleState.READY:
            return ResourceLifecycleState.READY
        return ResourceLifecycleState.FAILED

    @staticmethod
    def _validate_postconditions(
        task: TaskRequest,
        invocation: AdapterInvocationResult,
        telemetry_after: dict[str, float | int | str | bool | None],
    ) -> list[str]:
        failures: list[str] = []

        if (
            invocation.confidence is not None
            and invocation.confidence < task.min_confidence
        ):
            failures.append(
                f"confidence {invocation.confidence:.3f} is below required "
                f"threshold {task.min_confidence:.3f}"
            )

        if task.required_telemetry_fields:
            missing = sorted(
                field
                for field in task.required_telemetry_fields
                if field not in telemetry_after
            )
            if missing:
                failures.append(
                    "missing required telemetry fields: " + ", ".join(missing)
                )

        if task.max_twin_age_ms is not None:
            age = telemetry_after.get("age_of_information_ms")
            if not isinstance(age, (int, float)):
                failures.append("age_of_information_ms is required but missing")
            elif age > task.max_twin_age_ms:
                failures.append(
                    f"age_of_information_ms {age:.2f} exceeds bound "
                    f"{task.max_twin_age_ms:.2f}"
                )

        if (
            task.continuous_monitoring_required
            and "health_status" not in telemetry_after
        ):
            failures.append(
                "continuous monitoring required but health_status is missing"
            )

        if telemetry_after.get("health_status") == "offline":
            failures.append("backend transitioned to offline state")

        return failures

    @staticmethod
    def _maybe_recover(
        adapter: BaseAdapter,
        telemetry_after: dict[str, float | int | str | bool | None],
    ) -> list[str]:
        actions: list[str] = []

        drift_score = telemetry_after.get("drift_score")
        if isinstance(drift_score, (int, float)) and drift_score > 0.8:
            if adapter.recalibrate():
                actions.append("Triggered recalibration due to high drift_score.")

        health_status = telemetry_after.get("health_status")
        if health_status == "degraded":
            if adapter.reset():
                actions.append("Triggered reset due to degraded health_status.")

        return actions


# Backward-compatible import for software written against releases published
# before the CP²N² rename. New code should import CP2N2Orchestrator.
PhysMCPOrchestrator = CP2N2Orchestrator
