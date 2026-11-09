"""Explicitly networked dry-run evaluation for the UzL AI-Lab agent.

This evaluates LLM planning only. It never executes a substrate and does not
constitute PNN, CL1, simulator, or physical-hardware evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from agent.ai_lab_agent import (
    AI_LAB_BASE_URL,
    AILabConfiguration,
    OpenAICompatibleAILabClient,
    CP2N2AILabAgent,
)
from agent.constrained_client import build_agent_surface
from evaluation.common import RESULTS_DIR


def build_user_goals() -> list[str]:
    return [
        (
            "Prepare a dry-run plan for the fixed edge vector-classification "
            "preset. Do not execute a substrate."
        ),
        (
            "Prepare a dry-run plan for the fixed chemical-sensing preset and "
            "report the evidence level without executing a substrate."
        ),
    ]


def _catalog_digest(models: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(models), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def evaluate(*, output_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    configuration = AILabConfiguration.from_environment()
    client = OpenAICompatibleAILabClient(configuration)
    models = client.list_models()
    if configuration.model not in models:
        raise RuntimeError(
            f"Configured AI-Lab model {configuration.model!r} is not available"
        )
    budget_before = client.budget_info()

    audit_path = output_dir / "ai_lab_agent_audit.jsonl"
    surface = build_agent_surface(
        principal_id="ai-lab-evaluation-agent",
        audit_path=audit_path,
        include_cortical_labs=False,
    )
    agent = CP2N2AILabAgent(llm=client, surface=surface)

    rows: list[dict[str, Any]] = []
    for index, goal in enumerate(build_user_goals(), start=1):
        started = perf_counter()
        result = agent.run(goal)
        plan_completion = agent.last_plan_completion
        summary_completion = agent.last_summary_completion
        rows.append(
            {
                "case_id": f"ai-lab-dry-run-{index}",
                "user_goal": goal,
                "dry_run": result.run_result.get("dry_run"),
                "success": result.run_result.get("success"),
                "selected_backend": result.run_result.get("selected_backend"),
                "preset_id": result.run_result.get("preset_id"),
                "runtime_kind": result.run_result.get("runtime_kind"),
                "evidence_level": result.run_result.get("evidence_level"),
                "raw_substrate_output_exposed": result.run_result.get(
                    "raw_substrate_output_exposed"
                ),
                "requested_model": configuration.model,
                "plan_response_model": (
                    None
                    if plan_completion is None
                    else plan_completion.response_model
                ),
                "summary_response_model": (
                    None
                    if summary_completion is None
                    else summary_completion.response_model
                ),
                "plan_request_id": (
                    None if plan_completion is None else plan_completion.request_id
                ),
                "summary_request_id": (
                    None
                    if summary_completion is None
                    else summary_completion.request_id
                ),
                "plan_usage": (
                    {}
                    if plan_completion is None
                    else plan_completion.usage
                ),
                "summary_usage": (
                    {}
                    if summary_completion is None
                    else summary_completion.usage
                ),
                "wall_latency_ms": round(
                    (perf_counter() - started) * 1000.0, 6
                ),
                "plan": result.plan,
                "summary": result.summary,
            }
        )

    budget_after = client.budget_info()
    provenance = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_kind": "ai_lab_llm_inference_dry_run",
        "pnn_evidence": False,
        "substrate_executed": False,
        "provider": "University of Lübeck AI-Lab / ITSC",
        "api_kind": "LiteLLM OpenAI-compatible",
        "base_url": AI_LAB_BASE_URL,
        "requested_model": configuration.model,
        "available_model_count": len(models),
        "model_catalog_sha256": _catalog_digest(models),
        "budget_before": budget_before,
        "budget_after": budget_after,
        "cost_statement": (
            "Project-provided access; no direct monetary charge is recorded. "
            "Provider budget units reset weekly."
        ),
        "rate_limit_statement": (
            "No request/token-per-minute limit is published in documentation "
            "version 1.4.1; provider budget limits apply."
        ),
        "data_handling": (
            "No personal, patient, or confidential university data was sent."
        ),
        "audit_verified": surface.audit_trail.verify(),
    }
    payload = {"provenance": provenance, "results": rows}
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "ai_lab_agent_results.json"
    csv_path = output_dir / "ai_lab_agent_results.csv"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "case_id",
        "dry_run",
        "success",
        "selected_backend",
        "preset_id",
        "runtime_kind",
        "evidence_level",
        "raw_substrate_output_exposed",
        "requested_model",
        "plan_response_model",
        "summary_response_model",
        "wall_latency_ms",
        "summary",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name) for name in fieldnames})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-network",
        action="store_true",
        help="Required acknowledgement that this consumes AI-Lab budget units.",
    )
    args = parser.parse_args()
    if not args.confirm_network:
        raise SystemExit(
            "Refusing networked evaluation without --confirm-network"
        )
    payload = evaluate()
    print(
        "AI-Lab dry-run evaluation complete: "
        f"{len(payload['results'])} cases; no substrate execution."
    )


if __name__ == "__main__":
    main()
