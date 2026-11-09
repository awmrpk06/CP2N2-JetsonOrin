"""Evaluate backend-matching quality on a curated task suite."""

from __future__ import annotations

from pydantic import ValidationError

from evaluation.common import RESULTS_DIR, save_csv, save_json
from evaluation.plots import save_bar_chart

from demos.common import build_default_orchestrator, make_chemical_task, make_edge_task, make_wetware_task
from core.task_model import OutputPreference, TaskKind, TaskRequest
from descriptors.capability_schema import SignalModality


def evaluate() -> dict:
    orchestrator = build_default_orchestrator()

    test_cases = [
        {
            "name": "edge_classification",
            "task": make_edge_task(task_id="match-edge"),
            "expected_backend": "edge-backend",
        },
        {
            "name": "wetware_temporal_inference",
            "task": make_wetware_task(task_id="match-wetware"),
            "expected_backend": "wetware-backend",
        },
        {
            "name": "chemical_sensing",
            "task": make_chemical_task(task_id="match-chemical", input_level=1.6),
            "expected_backend": "chemical-backend",
        },
        {
            "name": "edge_monitoring",
            "task": TaskRequest(
                task_id="match-edge-monitor",
                task_kind=TaskKind.MONITORING,
                summary="Fast telemetry-aware edge monitoring task.",
                required_input_modalities=[SignalModality.DIGITAL_VECTOR],
                preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
                latency_budget_ms=25.0,
                continuous_monitoring_required=True,
                metadata={"input_vector": [0.2, 0.2, 0.4, 0.9]},
            ),
            "expected_backend": "edge-backend",
        },
        {
            "name": "wetware_control",
            "task": TaskRequest(
                task_id="match-wetware-control",
                task_kind=TaskKind.CONTROL,
                summary="Closed-loop spike-based control task.",
                required_input_modalities=[SignalModality.SPIKES],
                preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
                latency_budget_ms=100.0,
                continuous_monitoring_required=True,
                human_supervision_available=True,
                metadata={"stimulation_strength": 0.7, "observation_window_ms": 150.0},
            ),
            "expected_backend": "wetware-backend",
        },
        {
            "name": "unsupported_control_only",
            "task": TaskRequest(
                task_id="match-none",
                task_kind=TaskKind.OPTIMIZATION,
                summary="Control-only optimization request unsupported by current backends.",
                required_input_modalities=[SignalModality.CONTROL_SIGNAL],
                preferred_output=OutputPreference.SCORE,
                latency_budget_ms=500.0,
                metadata={"control_mode": "external"},
            ),
            "expected_backend": None,
        },
    ]

    rows: list[dict] = []
    correct = 0
    for case in test_cases:
        report = orchestrator.plan_task(case["task"])
        best = report.best_candidate()
        predicted_backend = best.backend_id if best is not None else None
        matched = predicted_backend == case["expected_backend"]
        if matched:
            correct += 1

        top_score = best.score if best is not None else 0.0
        second_score = 0.0
        if len(report.candidates) > 1:
            second_score = report.candidates[1].score

        rows.append(
            {
                "case": case["name"],
                "expected_backend": case["expected_backend"],
                "predicted_backend": predicted_backend,
                "correct": matched,
                "top_score": round(top_score, 6),
                "second_score": round(second_score, 6),
                "score_margin": round(top_score - second_score, 6),
            }
        )

    accuracy = correct / len(test_cases)
    payload = {"accuracy": accuracy, "cases": rows}
    save_json(RESULTS_DIR / "matching_results.json", payload)
    save_csv(RESULTS_DIR / "matching_results.csv", rows)

    labels = [row["case"] for row in rows]
    values = [1.0 if row["correct"] else 0.0 for row in rows]
    save_bar_chart(
        labels=labels,
        values=values,
        title="Matching correctness on curated task suite",
        ylabel="Correct prediction (1=yes, 0=no)",
        output_path=RESULTS_DIR / "matching_accuracy_bar_chart.png",
    )

    return payload


def main() -> None:
    payload = evaluate()
    print("Matching evaluation complete.")
    print(f"Accuracy: {payload['accuracy']:.3f}")
    for row in payload["cases"]:
        print(
            f"{row['case']}: expected={row['expected_backend']}, "
            f"predicted={row['predicted_backend']}, correct={row['correct']}, "
            f"score_margin={row['score_margin']}"
        )


if __name__ == "__main__":
    main()
