"""Deterministic, non-LLM BioPattern Gate client for control-plane evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from applications.biopattern_gate.control_plane_contract import BACKEND_ID
from mcp_surface.service import MCPControlSurface


@dataclass(frozen=True)
class BioPatternGateTranscript:
    """Machine-readable lifecycle transcript and audit correlation references."""

    steps: tuple[dict[str, Any], ...]
    result_summary: dict[str, Any]
    lifecycle_history: tuple[dict[str, Any], ...]

    @property
    def audit_request_ids(self) -> tuple[str, ...]:
        return tuple(
            str(step["request_id"])
            for step in self.steps
            if step.get("request_id")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_kind": "deterministic_non_llm",
            "resource_id": BACKEND_ID,
            "steps": list(self.steps),
            "audit_request_ids": list(self.audit_request_ids),
            "result_summary": self.result_summary,
            "lifecycle_history": list(self.lifecycle_history),
            "raw_substrate_output_exposed": False,
        }


class BioPatternGateDeterministicClient:
    """Run the exact E3 lifecycle without model planning or free-form input."""

    def __init__(self, surface: MCPControlSurface) -> None:
        self.surface = surface

    def run(self) -> BioPatternGateTranscript:
        steps: list[dict[str, Any]] = []

        self._call(
            steps,
            "discover",
            "discover_resources",
            {"include_unavailable": True, "limit": 100},
        )
        dry_run = self._call(
            steps,
            "dry_run",
            "prepare_assay",
            {
                "resource_id": BACKEND_ID,
                "preset_id": "pattern_gate_v1",
                "dry_run": True,
            },
        )
        if not dry_run["plan"]["admissible_and_feasible"]:
            raise RuntimeError("BioPattern Gate E3 dry run was not admissible")

        reservation = self._call(
            steps,
            "reserve",
            "reserve_resource",
            {"resource_id": BACKEND_ID, "ttl_seconds": 60},
        )
        prepared = self._call(
            steps,
            "prepare",
            "prepare_assay",
            {
                "resource_id": BACKEND_ID,
                "preset_id": "pattern_gate_v1",
                "dry_run": False,
                "lease_id": reservation["lease_id"],
                "expected_lease_version": reservation["lease_version"],
            },
        )
        run_id = prepared["run_id"]
        self._call(
            steps,
            "status_prepared",
            "get_run_status",
            {"run_id": run_id},
        )
        self._call(
            steps,
            "run",
            "run_assay",
            {"run_id": run_id, "idempotency_key": f"e3-{run_id}"},
        )
        self._call(
            steps,
            "status_terminal",
            "get_run_status",
            {"run_id": run_id},
        )
        result = self._call(
            steps,
            "result",
            "get_result_summary",
            {"run_id": run_id},
        )

        snapshot = self.surface.orchestrator.registry.lifecycle_store.snapshot(
            BACKEND_ID
        )
        lease = self.surface.orchestrator.registry.lease_store.current(BACKEND_ID)
        if snapshot.state != "ready" or lease is not None:
            raise RuntimeError("control plane did not release the E3 resource")
        correlation_id = str(result["summary"]["orchestration_correlation_id"])
        lifecycle_history = tuple(
            transition.model_dump(mode="json")
            for transition in (
                self.surface.orchestrator.registry.lifecycle_store.history(
                    resource_id=BACKEND_ID,
                    correlation_id=correlation_id,
                )
            )
        )
        steps.append(
            {
                "stage": "automatic_release",
                "tool": None,
                "request_id": None,
                "ok": True,
                "result": {
                    "resource_id": BACKEND_ID,
                    "lifecycle_state": snapshot.state,
                    "lease_present": False,
                    "release_semantics": "orchestrator_finalization",
                },
            }
        )
        return BioPatternGateTranscript(
            steps=tuple(steps),
            result_summary=result["summary"],
            lifecycle_history=lifecycle_history,
        )

    def _call(
        self,
        steps: list[dict[str, Any]],
        stage: str,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.surface.invoke(tool, arguments)
        steps.append(
            {
                "stage": stage,
                "tool": tool,
                "request_id": response.request_id,
                "ok": response.ok,
                "result": response.result,
                "error": (
                    response.error.model_dump(mode="json")
                    if response.error is not None
                    else None
                ),
            }
        )
        if not response.ok:
            raise RuntimeError(response.model_dump_json())
        return response.result

