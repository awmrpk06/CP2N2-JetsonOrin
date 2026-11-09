"""Shared constrained client used by the Gemini and Ollama demonstrations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from demos.common import build_live_target_orchestrator
from mcp_surface.approvals import ApprovalVerifier
from mcp_surface.audit import JsonlHashChainAuditTrail
from mcp_surface.auth import Scope
from mcp_surface.models import AssayPresetId, MCPPrincipal, ToolResponse
from mcp_surface.service import MCPControlSurface


PLANNING_PROMPT = """You are a planner for the constrained CP²N² service.
Return ONLY valid JSON with this schema:

{
  "action": "prepare_assay",
  "arguments": {
    "resource_id": "<one discovered resource id>",
    "preset_id": "<one compatible server-owned preset id>",
    "dry_run": true
  },
  "rationale": "<short string>"
}

Rules:
- Use action exactly "prepare_assay".
- Choose only a resource and one of its published compatible presets.
- Use dry_run=true unless an operator explicitly requested real execution.
- Never invent or request channels, electrodes, amplitudes, pulse parameters,
  loop counts, fallback, policy changes, lease bypass, or runtime-kind changes.
- Human approval tokens are external operator data and must never appear in the plan.
- Output JSON only.
"""


SUMMARY_PROMPT_TEMPLATE = """Summarize this constrained CP²N² agent result.

User goal:
{user_goal}

Structured plan:
{plan_json}

MCP result:
{result_json}

State clearly whether this was only a dry run, whether the plan was admissible,
which server-owned preset and resource were selected, and whether human approval
would be required. Do not infer physical-hardware evidence.
"""


@dataclass
class AgentResult:
    plan: dict[str, Any]
    resources: list[dict[str, Any]]
    run_result: dict[str, Any]
    summary: str


class AgentPlanArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    resource_id: str = Field(min_length=1, max_length=128)
    preset_id: AssayPresetId
    dry_run: bool = True


class AgentPlan(BaseModel):
    """Only choices an LLM is allowed to make in the demonstration clients."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["prepare_assay"]
    arguments: AgentPlanArguments
    rationale: str = Field(min_length=1, max_length=500)


def build_agent_surface(
    *,
    principal_id: str,
    audit_path: Path,
    include_cortical_labs: bool = True,
    approval_verifier: ApprovalVerifier | None = None,
) -> MCPControlSurface:
    principal = MCPPrincipal(
        principal_id=principal_id,
        scopes=[
            Scope.RESOURCES_READ.value,
            Scope.LEASES_WRITE.value,
            Scope.ASSAYS_PREPARE.value,
            Scope.ASSAYS_EXECUTE.value,
            Scope.RUNS_ABORT.value,
        ],
    )
    return MCPControlSurface(
        orchestrator=build_live_target_orchestrator(
            include_cortical_labs=include_cortical_labs
        ),
        principal=principal,
        audit_trail=JsonlHashChainAuditTrail(audit_path),
        approval_verifier=approval_verifier,
    )


class ConstrainedAgentExecutor:
    """Execute an LLM plan only through the ten-tool MCP service boundary."""

    def __init__(self, surface: MCPControlSurface) -> None:
        self.surface = surface

    def discover_resources(self) -> list[dict[str, Any]]:
        response = self.surface.invoke(
            "discover_resources",
            {"include_unavailable": True, "limit": 100},
        )
        if not response.ok:
            raise RuntimeError(response.model_dump_json())
        return list(response.result["resources"])

    def execute_plan(
        self,
        plan_payload: dict[str, Any],
        *,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        plan = AgentPlan.model_validate(plan_payload)
        args = plan.arguments
        if args.dry_run:
            response = self.surface.invoke(
                "prepare_assay",
                {
                    "resource_id": args.resource_id,
                    "preset_id": args.preset_id,
                    "dry_run": True,
                },
            )
            resource = self.surface.invoke(
                "describe_resource",
                {"resource_id": args.resource_id},
            )
            return self._agent_result(
                response,
                dry_run=True,
                resource=resource.result if resource.ok else None,
            )

        reservation = self.surface.invoke(
            "reserve_resource",
            {
                "resource_id": args.resource_id,
                "ttl_seconds": 60,
            },
        )
        if not reservation.ok:
            return self._agent_result(reservation, dry_run=False)

        lease_id = reservation.result["lease_id"]
        prepared = self.surface.invoke(
            "prepare_assay",
            {
                "resource_id": args.resource_id,
                "preset_id": args.preset_id,
                "dry_run": False,
                "lease_id": lease_id,
                "expected_lease_version": reservation.result["lease_version"],
            },
        )
        if not prepared.ok:
            self.surface.invoke(
                "release_resource",
                {
                    "resource_id": args.resource_id,
                    "lease_id": lease_id,
                },
            )
            return self._agent_result(prepared, dry_run=False)

        executed = self.surface.invoke(
            "run_assay",
            {
                "run_id": prepared.result["run_id"],
                **(
                    {"approval_token": approval_token}
                    if approval_token is not None
                    else {}
                ),
            },
        )
        return self._agent_result(executed, dry_run=False)

    @staticmethod
    def _agent_result(
        response: ToolResponse,
        *,
        dry_run: bool,
        resource: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = dict(response.result)
        summary = result.get("summary", {})
        plan = result.get("plan", {})
        resource = resource or {}
        return {
            "success": response.ok
            and (
                bool(plan.get("admissible_and_feasible"))
                if dry_run
                else bool(summary.get("success"))
            ),
            "dry_run": dry_run,
            "selected_backend": (
                plan.get("resource_id")
                if dry_run
                else summary.get("selected_resource_id")
            ),
            "preset_id": (
                plan.get("preset", {}).get("preset_id")
                if dry_run
                else summary.get("preset_id")
            ),
            "runtime_kind": summary.get("runtime_kind")
            or resource.get("runtime_kind"),
            "evidence_level": summary.get("evidence_level")
            or resource.get("evidence_level"),
            "execution_latency_ms": summary.get("execution_latency_ms"),
            "confidence": summary.get("confidence"),
            "failure_reason": (
                response.error.message if response.error is not None else None
            ),
            "error_code": (
                response.error.code if response.error is not None else None
            ),
            "human_approval_required": plan.get(
                "human_approval_required_for_execution"
            ),
            "raw_substrate_output_exposed": False,
            "mcp_response": response.model_dump(mode="json"),
        }
