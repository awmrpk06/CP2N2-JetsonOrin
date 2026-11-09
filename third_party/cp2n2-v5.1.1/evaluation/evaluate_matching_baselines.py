"""Evaluate CP²N² matching against simpler baseline selectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from evaluation.common import PROJECT_ROOT, RESULTS_DIR, save_csv, save_json

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.fault_injecting_adapter import FaultInjectingAdapter, FaultProfile
from adapters.wetware_adapter import WetwareAdapter
from core.matcher import BackendMatcher
from core.orchestrator import CP2N2Orchestrator
from core.task_model import SelectionPolicy, TaskRequest
from demos.common import (
    build_extended_orchestrator,
    make_chemical_task,
    make_edge_task,
    make_remote_edge_monitoring_task,
    make_wetware_task,
)
from remote.service_controller import start_remote_edge_service


@dataclass
class CaseSpec:
    name: str
    expected_backend: str | None
    task_builder: Callable[[], TaskRequest]
    orchestrator_builder: Callable[[str], object]


def _clean_orchestrator(remote_base_url: str):
    return build_extended_orchestrator(remote_base_url=remote_base_url)


def _drifted_edge_orchestrator(remote_base_url: str):
    orchestrator = build_extended_orchestrator(remote_base_url=remote_base_url)
    orchestrator.register_adapter(
        FaultInjectingAdapter(
            EdgeAdapter(),
            FaultProfile(override_telemetry={"drift_score": 0.97, "health_status": "degraded"}),
        ),
        overwrite=True,
    )
    return orchestrator


def _stale_chemical_orchestrator(remote_base_url: str):
    orchestrator = build_extended_orchestrator(remote_base_url=remote_base_url)
    orchestrator.register_adapter(
        FaultInjectingAdapter(
            ChemicalAdapter(),
            FaultProfile(override_telemetry={"age_of_information_ms": 4000.0}),
        ),
        overwrite=True,
    )
    return orchestrator


def _select_with_policy(
    task: TaskRequest,
    orchestrator,
    policy: SelectionPolicy,
) -> str | None:
    planned_task = task.model_copy(update={"selection_policy": policy})
    best = orchestrator.plan_task(planned_task).best_candidate()
    return best.backend_id if best is not None else None


def _selector(policy: SelectionPolicy):
    return lambda task, orchestrator: _select_with_policy(
        task,
        orchestrator,
        policy,
    )


def _select_static_priority(task: TaskRequest, orchestrator) -> str | None:
    """Apply one fixed order independent of task preferences."""
    static_matcher = BackendMatcher(
        static_priority=[
            "remote-edge-backend",
            "edge-backend",
            "wetware-backend",
            "chemical-backend",
        ]
    )
    static_orchestrator = CP2N2Orchestrator(
        registry=orchestrator.registry,
        matcher=static_matcher,
    )
    return _select_with_policy(
        task,
        static_orchestrator,
        SelectionPolicy.STATIC_PRIORITY,
    )


def evaluate() -> dict:
    service = start_remote_edge_service(PROJECT_ROOT)
    try:
        cases = [
            CaseSpec(
                name="edge_low_latency",
                expected_backend="edge-backend",
                task_builder=lambda: make_edge_task(task_id="baseline-edge-local"),
                orchestrator_builder=_clean_orchestrator,
            ),
            CaseSpec(
                name="remote_fog_monitoring",
                expected_backend="remote-edge-backend",
                task_builder=lambda: make_remote_edge_monitoring_task(task_id="baseline-remote-fog"),
                orchestrator_builder=_clean_orchestrator,
            ),
            CaseSpec(
                name="wetware_supervised",
                expected_backend="wetware-backend",
                task_builder=lambda: make_wetware_task(task_id="baseline-wetware", human_supervision_available=True),
                orchestrator_builder=_clean_orchestrator,
            ),
            CaseSpec(
                name="chemical_fresh",
                expected_backend="chemical-backend",
                task_builder=lambda: make_chemical_task(task_id="baseline-chemical", max_twin_age_ms=2000.0),
                orchestrator_builder=_clean_orchestrator,
            ),
            CaseSpec(
                name="edge_primary_drifted",
                expected_backend="remote-edge-backend",
                task_builder=lambda: make_remote_edge_monitoring_task(task_id="baseline-drifted-edge"),
                orchestrator_builder=_drifted_edge_orchestrator,
            ),
            CaseSpec(
                name="chemical_stale_twin",
                expected_backend=None,
                task_builder=lambda: make_chemical_task(
                    task_id="baseline-stale-chemical",
                    max_twin_age_ms=1000.0,
                    required_telemetry_fields=["age_of_information_ms"],
                ),
                orchestrator_builder=_stale_chemical_orchestrator,
            ),
            CaseSpec(
                name="wetware_without_supervision",
                expected_backend=None,
                task_builder=lambda: make_wetware_task(task_id="baseline-unsupervised", human_supervision_available=False),
                orchestrator_builder=_clean_orchestrator,
            ),
        ]

        selectors = {
            "static_priority": _select_static_priority,
            "constraint_based": _selector(SelectionPolicy.CONSTRAINT_BASED),
            "random_admissible": _selector(SelectionPolicy.RANDOM_ADMISSIBLE),
            "weighted_comparison": _selector(
                SelectionPolicy.WEIGHTED_COMPARISON
            ),
            "cp2n2_lexicographic": _selector(
                SelectionPolicy.LEXICOGRAPHIC
            ),
        }

        rows: list[dict] = []
        summary: dict[str, dict[str, float | int]] = {}
        for selector_name, selector in selectors.items():
            correct = 0
            unsupported = 0
            for case in cases:
                orchestrator = case.orchestrator_builder(service.base_url)
                task = case.task_builder()
                predicted_backend = selector(task, orchestrator)
                matched = predicted_backend == case.expected_backend
                correct += int(matched)
                if predicted_backend is None:
                    unsupported += 1
                rows.append(
                    {
                        "selector": selector_name,
                        "case": case.name,
                        "expected_backend": case.expected_backend,
                        "predicted_backend": predicted_backend,
                        "correct": matched,
                    }
                )
            summary[selector_name] = {
                "correct": correct,
                "total": len(cases),
                "accuracy": round(correct / len(cases), 6),
                "returned_none": unsupported,
            }

        payload = {"summary": summary, "cases": rows}
        save_json(RESULTS_DIR / "matching_baselines_results.json", payload)
        save_csv(RESULTS_DIR / "matching_baselines_results.csv", rows)
        return payload
    finally:
        service.stop()


def main() -> None:
    payload = evaluate()
    print("Matching baseline evaluation complete.")
    for selector, stats in payload["summary"].items():
        print(
            f"{selector}: accuracy={stats['accuracy']:.3f} "
            f"({stats['correct']}/{stats['total']})"
        )


if __name__ == "__main__":
    main()
