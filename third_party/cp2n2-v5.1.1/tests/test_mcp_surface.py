from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from adapters.wetware_adapter import WetwareAdapter
from agent.constrained_client import AgentPlan, ConstrainedAgentExecutor
from core.orchestrator import CP2N2Orchestrator
from demos.common import build_default_orchestrator
from descriptors.resource_contract import (
    EvidenceLevel,
    ObservationSource,
    RuntimeEvidence,
    RuntimeKind,
    SafetyLimit,
)
from mcp_surface.approvals import (
    ApprovalDenied,
    ApprovalRequirement,
    InMemoryHumanApprovalAuthority,
)
from mcp_surface.audit import JsonlHashChainAuditTrail
from mcp_surface.auth import Scope
from mcp_surface.models import MCPPrincipal
from mcp_surface.server import create_mcp_server
from mcp_surface.service import MCPControlSurface, TOOL_SPECS


EXPECTED_TOOLS = {
    "discover_resources",
    "describe_resource",
    "reserve_resource",
    "renew_lease",
    "prepare_assay",
    "run_assay",
    "get_run_status",
    "abort_run",
    "get_result_summary",
    "release_resource",
}


def _principal(
    principal_id: str = "research-agent",
    scopes: list[str] | None = None,
) -> MCPPrincipal:
    return MCPPrincipal(
        principal_id=principal_id,
        scopes=scopes
        or [
            Scope.RESOURCES_READ.value,
            Scope.LEASES_WRITE.value,
            Scope.ASSAYS_PREPARE.value,
            Scope.ASSAYS_EXECUTE.value,
            Scope.RUNS_ABORT.value,
        ],
    )


def _surface(
    tmp_path,
    *,
    orchestrator: CP2N2Orchestrator | None = None,
    principal: MCPPrincipal | None = None,
    approvals=None,
) -> MCPControlSurface:
    return MCPControlSurface(
        orchestrator=orchestrator or build_default_orchestrator(),
        principal=principal or _principal(),
        audit_trail=JsonlHashChainAuditTrail(tmp_path / f"audit-{uuid4()}.jsonl"),
        approval_verifier=approvals,
    )


def _reserve(surface: MCPControlSurface, resource_id: str) -> dict:
    response = surface.invoke(
        "reserve_resource",
        {"resource_id": resource_id, "ttl_seconds": 60},
    )
    assert response.ok, response
    return response.result


def _prepare(
    surface: MCPControlSurface,
    *,
    resource_id: str,
    preset_id: str,
    lease: dict,
) -> dict:
    response = surface.invoke(
        "prepare_assay",
        {
            "resource_id": resource_id,
            "preset_id": preset_id,
            "dry_run": False,
            "lease_id": lease["lease_id"],
            "expected_lease_version": lease["lease_version"],
        },
    )
    assert response.ok, response
    return response.result


def test_exact_tool_set_and_schemas_exclude_unsafe_primitives(tmp_path) -> None:
    surface = _surface(tmp_path)
    server = create_mcp_server(surface)

    async def inspect_tools():
        async with create_connected_server_and_client_session(server) as session:
            return await session.list_tools()

    result = asyncio.run(inspect_tools())
    assert {tool.name for tool in result.tools} == EXPECTED_TOOLS
    assert set(TOOL_SPECS) == EXPECTED_TOOLS

    forbidden = {
        "channel",
        "channels",
        "electrode",
        "amplitude",
        "pulse",
        "repeat_count",
        "loop_count",
        "runtime_kind",
        "selection_policy",
        "allow_fallback",
        "metadata",
        "task",
    }
    for tool in result.tools:
        assert tool.inputSchema.get("additionalProperties") is False
        properties = set(tool.inputSchema.get("properties", {}))
        assert not properties.intersection(forbidden)
        assert tool.annotations is not None
        assert tool.annotations.openWorldHint is False


def test_protocol_audits_malformed_and_unknown_calls(tmp_path) -> None:
    surface = _surface(tmp_path)
    server = create_mcp_server(surface)

    async def call_hostile_inputs():
        async with create_connected_server_and_client_session(server) as session:
            malformed = await session.call_tool(
                "reserve_resource",
                {
                    "resource_id": "edge-backend",
                    "ttl_seconds": 999_999,
                    "channel": 12,
                },
            )
            unknown = await session.call_tool(
                "raw_stimulate",
                {"amplitude": 1000},
            )
            return malformed, unknown

    malformed, unknown = asyncio.run(call_hostile_inputs())
    assert malformed.structuredContent["ok"] is False
    assert (
        malformed.structuredContent["error"]["code"]
        == "INVALID_REQUEST"
    )
    assert unknown.structuredContent["ok"] is False
    assert unknown.structuredContent["error"]["code"] == "INVALID_REQUEST"
    assert len(surface.audit_trail.events()) == 4
    assert surface.audit_trail.verify()


def test_non_object_arguments_are_rejected_and_audited(tmp_path) -> None:
    surface = _surface(tmp_path)

    response = surface.invoke("discover_resources", ["not", "an", "object"])

    assert not response.ok
    assert response.error.code == "INVALID_REQUEST"
    assert len(surface.audit_trail.events()) == 2
    assert surface.audit_trail.verify()


def test_dry_run_has_no_lease_or_lifecycle_side_effect(tmp_path) -> None:
    orchestrator = build_default_orchestrator()
    surface = _surface(tmp_path, orchestrator=orchestrator)
    before = orchestrator.registry.lifecycle_store.snapshot("edge-backend")

    response = surface.invoke(
        "prepare_assay",
        {
            "resource_id": "edge-backend",
            "preset_id": "edge_vector_classification_v1",
            "dry_run": True,
        },
    )

    assert response.ok
    assert response.result["dry_run"] is True
    assert response.result["resource_committed"] is False
    assert response.result["run_created"] is False
    assert orchestrator.registry.lease_store.current("edge-backend") is None
    after = orchestrator.registry.lifecycle_store.snapshot("edge-backend")
    assert after.state == before.state
    assert after.version == before.version


def test_malformed_hostile_plan_cannot_edit_policy_or_physical_parameters(
    tmp_path,
) -> None:
    surface = _surface(tmp_path)
    hostile = surface.invoke(
        "prepare_assay",
        {
            "resource_id": "wetware-backend",
            "preset_id": "wetware_temporal_probe_v1",
            "dry_run": True,
            "channels": [1, 2, 3],
            "amplitude": 999.0,
            "repeat_count": 1_000_000,
            "selection_policy": "weighted_comparison",
            "runtime_kind": "physical_hardware",
            "allow_fallback": True,
        },
    )
    bypass = surface.invoke(
        "prepare_assay",
        {
            "resource_id": "edge-backend",
            "preset_id": "edge_vector_classification_v1",
            "dry_run": False,
        },
    )

    assert not hostile.ok
    assert hostile.error.code == "INVALID_REQUEST"
    assert not bypass.ok
    assert bypass.error.code == "INVALID_REQUEST"
    assert surface.orchestrator.registry.lease_store.current(
        "wetware-backend"
    ) is None
    assert surface.orchestrator.registry.lease_store.current("edge-backend") is None


def test_authorization_is_server_side_and_denial_is_audited(tmp_path) -> None:
    surface = _surface(
        tmp_path,
        principal=_principal(scopes=[Scope.RESOURCES_READ.value]),
    )
    response = surface.invoke(
        "reserve_resource",
        {"resource_id": "edge-backend", "ttl_seconds": 60},
    )

    assert not response.ok
    assert response.error.code == "UNAUTHORIZED"
    assert surface.orchestrator.registry.lease_store.current("edge-backend") is None
    assert [event.outcome for event in surface.audit_trail.events()] == [
        "pending",
        "denied",
    ]


def test_edge_assay_end_to_end_returns_only_sanitized_summary(tmp_path) -> None:
    surface = _surface(tmp_path)
    lease = _reserve(surface, "edge-backend")
    prepared = _prepare(
        surface,
        resource_id="edge-backend",
        preset_id="edge_vector_classification_v1",
        lease=lease,
    )
    run_id = prepared["run_id"]

    status = surface.invoke("get_run_status", {"run_id": run_id})
    assert status.ok
    assert status.result["state"] == "prepared"

    executed = surface.invoke("run_assay", {"run_id": run_id})
    assert executed.ok
    assert executed.result["state"] == "succeeded"
    assert executed.result["summary"]["success"] is True
    assert executed.result["summary"]["raw_output_included"] is False

    summary = surface.invoke("get_result_summary", {"run_id": run_id})
    assert summary.ok
    serialized = summary.model_dump_json()
    assert "output_payload" not in serialized
    assert "recording_path" not in serialized
    assert summary.result["raw_substrate_output_exposed"] is False
    assert surface.orchestrator.registry.lease_store.current("edge-backend") is None
    assert surface.audit_trail.verify()


def test_abort_prepared_run_releases_lease(tmp_path) -> None:
    surface = _surface(tmp_path)
    lease = _reserve(surface, "edge-backend")
    prepared = _prepare(
        surface,
        resource_id="edge-backend",
        preset_id="edge_vector_classification_v1",
        lease=lease,
    )
    aborted = surface.invoke("abort_run", {"run_id": prepared["run_id"]})

    assert aborted.ok
    assert aborted.result["state"] == "aborted"
    assert surface.orchestrator.registry.lease_store.current("edge-backend") is None


def test_release_resource_cancels_prepared_run(tmp_path) -> None:
    surface = _surface(tmp_path)
    lease = _reserve(surface, "edge-backend")
    prepared = _prepare(
        surface,
        resource_id="edge-backend",
        preset_id="edge_vector_classification_v1",
        lease=lease,
    )
    released = surface.invoke(
        "release_resource",
        {
            "resource_id": "edge-backend",
            "lease_id": lease["lease_id"],
        },
    )
    status = surface.invoke(
        "get_run_status",
        {"run_id": prepared["run_id"]},
    )

    assert released.ok
    assert status.ok
    assert status.result["state"] == "aborted"


def test_audit_redacts_approval_token_but_preserves_digest(tmp_path) -> None:
    audit_path = tmp_path / "redaction.jsonl"
    surface = MCPControlSurface(
        orchestrator=build_default_orchestrator(),
        principal=_principal(),
        audit_trail=JsonlHashChainAuditTrail(audit_path),
    )
    token = "operator-secret-token-1234567890"
    response = surface.invoke(
        "run_assay",
        {"run_id": str(uuid4()), "approval_token": token},
    )

    assert not response.ok
    content = audit_path.read_text(encoding="utf-8")
    assert token not in content
    assert "<redacted>" in content
    assert "approval_token_sha256" in content
    assert surface.audit_trail.verify()


class ApprovalRequiredWetwareAdapter(WetwareAdapter):
    """Local test double that exercises the E5 approval gate without hardware."""

    def __init__(self) -> None:
        super().__init__(backend_id="approval-required-wetware")
        self.invoked = False

    def resource_contract(self):
        contract = super().resource_contract()
        evidence = RuntimeEvidence(
            runtime_kind=RuntimeKind.PHYSICAL_HARDWARE,
            evidence_level=EvidenceLevel.E5_PHYSICAL_HARDWARE,
            attestation_method="unit_test_double",
            attested_at=datetime.now(timezone.utc),
            attestation_details={"test_double": True},
        )
        identity = contract.identity.model_copy(
            update={"hardware_id": "unit-test-hardware"}
        )
        safety = contract.safety.model_copy(
            update={
                "hard_limits": {
                    "stimulation_strength": SafetyLimit(
                        minimum=0.0,
                        maximum=1.0,
                        unit="normalized",
                        source=ObservationSource.CONFIGURED,
                        description="Unit-test-only bounded input.",
                    )
                },
                "operator_acknowledgement_required": True,
                "emergency_stop_supported": True,
            }
        )
        return contract.model_copy(
            update={
                "identity": identity,
                "evidence": evidence,
                "safety": safety,
            }
        )

    def invoke(self, task):
        self.invoked = True
        return super().invoke(task)


def test_real_biological_execution_requires_external_one_time_approval(
    tmp_path,
) -> None:
    adapter = ApprovalRequiredWetwareAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    approvals = InMemoryHumanApprovalAuthority()
    surface = _surface(
        tmp_path,
        orchestrator=orchestrator,
        approvals=approvals,
    )
    lease = _reserve(surface, adapter.backend_id())
    prepared = _prepare(
        surface,
        resource_id=adapter.backend_id(),
        preset_id="wetware_temporal_probe_v1",
        lease=lease,
    )
    run_id = prepared["run_id"]

    denied = surface.invoke("run_assay", {"run_id": run_id})
    assert not denied.ok
    assert denied.error.code == "APPROVAL_REQUIRED"
    assert adapter.invoked is False

    requirement = ApprovalRequirement(
        run_id=run_id,
        resource_id=adapter.backend_id(),
        preset_id="wetware_temporal_probe_v1",
        principal_id=surface.principal.principal_id,
    )
    grant = approvals.issue(
        requirement,
        approver_id="responsible-researcher",
    )
    executed = surface.invoke(
        "run_assay",
        {"run_id": run_id, "approval_token": grant.token},
    )

    assert executed.ok
    assert adapter.invoked is True
    try:
        approvals.verify_and_consume(grant.token, requirement)
    except ApprovalDenied:
        pass
    else:
        raise AssertionError("Consumed approval token was accepted twice.")
    replay = surface.invoke(
        "run_assay",
        {"run_id": run_id, "approval_token": grant.token},
    )
    assert not replay.ok
    assert replay.error.code == "INVALID_STATE"


def test_approval_minting_and_low_level_control_are_not_tools() -> None:
    assert "approve_run" not in TOOL_SPECS
    assert "issue_approval" not in TOOL_SPECS
    assert "reset_backend" not in TOOL_SPECS
    assert "recalibrate_backend" not in TOOL_SPECS
    assert "invoke_backend" not in TOOL_SPECS
    assert "raw_stimulate" not in TOOL_SPECS


def test_agent_plan_schema_rejects_hostile_control_fields() -> None:
    hostile_plan = {
        "action": "prepare_assay",
        "arguments": {
            "resource_id": "wetware-backend",
            "preset_id": "wetware_temporal_probe_v1",
            "dry_run": True,
            "channels": [1, 2],
            "amplitude": 999,
            "loop_count": 1_000_000,
            "selection_policy": "agent_owned",
            "runtime_kind": "physical_hardware",
            "approval_token": "agent-minted-approval",
        },
        "rationale": "Attempt to bypass the constrained service.",
    }

    try:
        AgentPlan.model_validate(hostile_plan)
    except ValidationError as exc:
        rejected_fields = {
            error["loc"][-1] for error in exc.errors(include_url=False)
        }
    else:
        raise AssertionError("Hostile agent plan unexpectedly passed validation.")

    assert {
        "channels",
        "amplitude",
        "loop_count",
        "selection_policy",
        "runtime_kind",
        "approval_token",
    }.issubset(rejected_fields)


def test_constrained_agent_dry_run_uses_fixed_preset_without_commitment(
    tmp_path,
) -> None:
    surface = _surface(tmp_path)
    executor = ConstrainedAgentExecutor(surface)

    result = executor.execute_plan(
        {
            "action": "prepare_assay",
            "arguments": {
                "resource_id": "edge-backend",
                "preset_id": "edge_vector_classification_v1",
                "dry_run": True,
            },
            "rationale": "Check admissibility without executing.",
        }
    )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["selected_backend"] == "edge-backend"
    assert result["preset_id"] == "edge_vector_classification_v1"
    assert result["runtime_kind"] == "synthetic_twin"
    assert result["raw_substrate_output_exposed"] is False
    assert surface.orchestrator.registry.lease_store.current("edge-backend") is None
    assert result["mcp_response"]["result"]["run_created"] is False
