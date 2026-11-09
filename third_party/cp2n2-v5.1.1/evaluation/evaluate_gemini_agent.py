"""Evaluate Gemini planning through the constrained A4 CP²N² surface.

This explicit networked evaluation is not part of the default test suite.
Every supplied goal requests a dry run. The evaluation never executes a
substrate, never accepts physical stimulation parameters from the model, and
never records raw substrate output.

Run from the project root:
    python -m evaluation.evaluate_gemini_agent
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()

from agent.gemini_agent import CP2N2GeminiAgent  # noqa: E402
from evaluation.common import RESULTS_DIR  # noqa: E402


def build_user_goals() -> list[str]:
    return [
        (
            "Prepare a dry-run plan for the server-owned Cortical Labs pattern "
            "discrimination preset. Report admissibility and whether real execution "
            "would require human approval."
        ),
        (
            "Without executing any substrate, choose a compatible fixed preset for "
            "the Cortical Labs simulator and assess it through CP²N²."
        ),
        (
            "Prepare a dry-run plan for a fixed edge vector-classification preset."
        ),
        (
            "Prepare a dry-run plan for a fixed chemical-sensing preset and report "
            "the evidence level of the selected resource."
        ),
        (
            "Prepare a dry-run plan for the fixed generic wetware temporal-probe "
            "preset. Do not request physical control parameters."
        ),
    ]


def summarize_agent_result(user_goal: str, result) -> dict[str, Any]:
    run_result = result.run_result
    return {
        "user_goal": user_goal,
        "plan": result.plan,
        "resources": result.resources,
        "dry_run": run_result.get("dry_run"),
        "success": run_result.get("success"),
        "selected_backend": run_result.get("selected_backend"),
        "preset_id": run_result.get("preset_id"),
        "runtime_kind": run_result.get("runtime_kind"),
        "evidence_level": run_result.get("evidence_level"),
        "human_approval_required": run_result.get(
            "human_approval_required"
        ),
        "raw_substrate_output_exposed": run_result.get(
            "raw_substrate_output_exposed"
        ),
        "failure_reason": run_result.get("failure_reason"),
        "error_code": run_result.get("error_code"),
        "summary": result.summary,
    }


def write_results(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    json_path = RESULTS_DIR / "gemini_agent_results.json"
    csv_path = RESULTS_DIR / "gemini_agent_results.csv"
    json_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fieldnames = [
        "user_goal",
        "dry_run",
        "success",
        "selected_backend",
        "preset_id",
        "runtime_kind",
        "evidence_level",
        "human_approval_required",
        "raw_substrate_output_exposed",
        "failure_reason",
        "error_code",
        "summary",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return json_path, csv_path


def print_summary(
    rows: list[dict[str, Any]],
    json_path: Path,
    csv_path: Path,
) -> None:
    success_count = sum(1 for row in rows if row["success"])
    dry_run_count = sum(1 for row in rows if row["dry_run"])
    print("Gemini constrained-agent evaluation")
    print(
        f"Admissible plans: {success_count}/{len(rows)}; "
        f"dry runs: {dry_run_count}/{len(rows)}"
    )
    for index, row in enumerate(rows, start=1):
        print(
            f"plan-{index}: resource={row['selected_backend']}, "
            f"preset={row['preset_id']}, admissible={row['success']}"
        )
    print(f"JSON results: {json_path}")
    print(f"CSV results:  {csv_path}")


def main() -> None:
    agent = CP2N2GeminiAgent()
    rows = [
        summarize_agent_result(goal, agent.run(goal))
        for goal in build_user_goals()
    ]
    json_path, csv_path = write_results(rows)
    print_summary(rows, json_path, csv_path)


if __name__ == "__main__":
    main()
