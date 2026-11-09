"""Evaluate the real Cortical Labs runtime path through CP²N².

This script executes a small number of directed tasks against the
`cortical-labs-backend` and records the most important runtime fields:

- backend latency
- observation latency
- readiness / health
- recording artifact path
- channel / amplitude / observation window

The goal is not a broad benchmark, but a compact end-to-end check that the
real wetware-facing API path remains operational and produces consistent
telemetry.

Run from the project root, for example:

    python -m evaluation.evaluate_cortical_runtime

or, if your import path setup supports it:

    python evaluation/evaluate_cortical_runtime.py
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


def bootstrap_project_root() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()
from evaluation.common import RESULTS_DIR  # noqa: E402

from demos.common import build_live_target_orchestrator, make_cortical_task  # noqa: E402


def make_runtime_task(
    task_id: str,
    *,
    channel: int,
    amplitude: float,
    observation_window_ms: int,
    pre_delay_ms: int = 20,
):
    task = make_cortical_task(task_id=task_id, allow_fallback=False)
    task.metadata["stimulation_pattern"] = {
        "channels": [channel],
        "amplitude": amplitude,
    }
    task.metadata["observation_window_ms"] = observation_window_ms
    task.metadata["pre_delay_ms"] = pre_delay_ms
    return task


def summarize_run(run_result, task) -> dict[str, Any]:
    invocation = run_result.invocation
    payload = invocation.output_payload if invocation is not None else {}
    before = run_result.telemetry_before or {}
    after = run_result.telemetry_after or {}

    recording_artifact = payload.get("recording_artifact") or {}
    raw_backend_metadata = payload.get("raw_backend_metadata") or {}

    return {
        "task_id": task.task_id,
        "success": run_result.success,
        "selected_backend": run_result.decision.selected_backend_id,
        "used_fallback": run_result.decision.used_fallback,
        "failure_reason": run_result.failure_reason,
        "execution_latency_ms": getattr(invocation, "execution_latency_ms", None),
        "confidence": getattr(invocation, "confidence", None),
        "response_fingerprint": payload.get("response_fingerprint"),
        "stim_channel": payload.get("stim_channel"),
        "stim_amplitude_ua": payload.get("stim_amplitude_ua"),
        "observation_window_ms": payload.get("observation_window_ms"),
        "recording_path": after.get("recording_path") or recording_artifact.get("path"),
        "recording_name": recording_artifact.get("name"),
        "backend_latency_ms": after.get("backend_latency_ms"),
        "observation_latency_ms": after.get("observation_latency_ms"),
        "readiness_before": before.get("readiness_state"),
        "health_before": before.get("health_status"),
        "readiness_after": after.get("readiness_state"),
        "health_after": after.get("health_status"),
        "channel_count": after.get("channel_count"),
        "fps": after.get("fps"),
        "drift_score": after.get("drift_score"),
        "age_of_information_ms": after.get("age_of_information_ms"),
        "sdk_available": after.get("sdk_available"),
        "decision_notes": list(run_result.decision.notes or []),
        "validation_failures": list(run_result.validation_failures or []),
        "recovery_actions": list(run_result.recovery_actions or []),
        "system_attributes": raw_backend_metadata.get("system_attributes"),
        "raw_backend_metadata": raw_backend_metadata,
    }


def write_results(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    json_path = RESULTS_DIR / "cortical_runtime_results.json"
    csv_path = RESULTS_DIR / "cortical_runtime_results.csv"

    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_fieldnames = [
        "task_id",
        "success",
        "selected_backend",
        "used_fallback",
        "failure_reason",
        "execution_latency_ms",
        "confidence",
        "response_fingerprint",
        "stim_channel",
        "stim_amplitude_ua",
        "observation_window_ms",
        "recording_path",
        "recording_name",
        "backend_latency_ms",
        "observation_latency_ms",
        "readiness_before",
        "health_before",
        "readiness_after",
        "health_after",
        "channel_count",
        "fps",
        "drift_score",
        "age_of_information_ms",
        "sdk_available",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in csv_fieldnames})

    return json_path, csv_path


def print_summary(rows: list[dict[str, Any]], json_path: Path, csv_path: Path) -> None:
    print()
    print("=" * 80)
    print("Cortical runtime evaluation summary")
    print("=" * 80)

    success_count = sum(1 for r in rows if r["success"])
    print(f"Runs: {len(rows)}")
    print(f"Successful runs: {success_count}/{len(rows)}")

    for row in rows:
        print("-" * 80)
        print(
            f"{row['task_id']}: success={row['success']}, "
            f"backend_latency_ms={row['backend_latency_ms']}, "
            f"observation_latency_ms={row['observation_latency_ms']}, "
            f"recording_path={row['recording_path']}"
        )

    print("-" * 80)
    print(f"JSON results: {json_path}")
    print(f"CSV results:  {csv_path}")


def main() -> None:
    orchestrator = build_live_target_orchestrator(include_cortical_labs=True)

    tasks = [
        make_runtime_task(
            "cortical-run-1",
            channel=1,
            amplitude=0.4,
            observation_window_ms=100,
            pre_delay_ms=20,
        ),
        make_runtime_task(
            "cortical-run-2",
            channel=3,
            amplitude=0.4,
            observation_window_ms=120,
            pre_delay_ms=20,
        ),
        make_runtime_task(
            "cortical-run-3",
            channel=7,
            amplitude=0.5,
            observation_window_ms=140,
            pre_delay_ms=20,
        ),
    ]

    rows: list[dict[str, Any]] = []
    for task in tasks:
        run_result = orchestrator.execute_task(task)
        rows.append(summarize_run(run_result, task))

    json_path, csv_path = write_results(rows)
    print_summary(rows, json_path, csv_path)


if __name__ == "__main__":
    main()
