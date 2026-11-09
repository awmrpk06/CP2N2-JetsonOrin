"""Evaluate A3 policies on holdout tasks and perturb comparison weights."""

from __future__ import annotations

import random

from adapters.edge_adapter import EdgeAdapter
from core.matcher import BackendMatcher
from core.task_model import SelectionPolicy
from demos.common import make_edge_task
from descriptors.capability_schema import Locality
from evaluation.common import RESULTS_DIR, save_csv, save_json


def _descriptor(backend_id: str, latency_ms: float, locality: Locality):
    descriptor = EdgeAdapter(backend_id=backend_id).describe()
    return descriptor.model_copy(
        update={
            "timing": descriptor.timing.model_copy(
                update={"typical_latency_ms": latency_ms}
            ),
            "policy": descriptor.policy.model_copy(
                update={"locality": locality.value}
            ),
            "telemetry": descriptor.telemetry.model_copy(
                update={"supports_age_of_information": True}
            ),
        }
    )


def _fixture():
    descriptors = [
        _descriptor("fast-remote", 2.0, Locality.CLOUD),
        _descriptor("safe-local", 10.0, Locality.EDGE),
    ]
    runtime = {
        "fast-remote": {
            "health_status": "ready",
            "drift_score": 0.40,
            "age_of_information_ms": 200.0,
            "reservable": True,
            "estimated_cost": 0.1,
            "cost_currency": "eur",
        },
        "safe-local": {
            "health_status": "ready",
            "drift_score": 0.05,
            "age_of_information_ms": 10.0,
            "reservable": True,
            "estimated_cost": 1.0,
            "cost_currency": "eur",
        },
    }
    return descriptors, runtime


def _holdout_cases():
    balanced = make_edge_task(task_id="holdout-balanced")
    balanced.max_twin_age_ms = 1_000.0

    latency_urgent = make_edge_task(task_id="holdout-latency")
    latency_urgent.max_twin_age_ms = 1_000.0
    latency_urgent.selection_policy = SelectionPolicy.LATENCY_FIRST

    locality_cost = make_edge_task(task_id="holdout-cost")
    locality_cost.max_twin_age_ms = 1_000.0
    locality_cost.preferred_locality = None
    locality_cost.selection_policy = SelectionPolicy.LOCALITY_COST_FIRST

    infeasible = make_edge_task(task_id="holdout-infeasible")
    infeasible.latency_budget_ms = 1.0

    return [
        (balanced, "safe-local"),
        (latency_urgent, "fast-remote"),
        (locality_cost, "fast-remote"),
        (infeasible, None),
    ]


def evaluate() -> dict:
    descriptors, runtime = _fixture()
    matcher = BackendMatcher()
    holdout_rows: list[dict] = []
    correct = 0
    for task, expected in _holdout_cases():
        best = matcher.rank_backends(
            task,
            descriptors,
            runtime_state=runtime,
        ).best_candidate()
        predicted = None if best is None else best.backend_id
        matched = predicted == expected
        correct += int(matched)
        holdout_rows.append(
            {
                "task_id": task.task_id,
                "policy": SelectionPolicy(task.selection_policy).value,
                "expected_backend": expected,
                "predicted_backend": predicted,
                "correct": matched,
            }
        )

    rng = random.Random(31)
    sensitivity_rows: list[dict] = []
    base_task = _holdout_cases()[0][0]
    terms = sorted(BackendMatcher.WEIGHTED_HEURISTIC)
    for perturbation in range(32):
        overrides = {
            term: BackendMatcher.WEIGHTED_HEURISTIC[term]
            * rng.uniform(0.5, 1.5)
            for term in terms
        }
        perturbed = BackendMatcher(weighted_overrides=overrides)
        principal = perturbed.rank_backends(
            base_task,
            descriptors,
            runtime_state=runtime,
            policy=SelectionPolicy.LEXICOGRAPHIC,
        ).best_candidate()
        weighted = perturbed.rank_backends(
            base_task,
            descriptors,
            runtime_state=runtime,
            policy=SelectionPolicy.WEIGHTED_COMPARISON,
        ).best_candidate()
        sensitivity_rows.append(
            {
                "perturbation": perturbation,
                "factor_min": round(
                    min(
                        overrides[name] / BackendMatcher.WEIGHTED_HEURISTIC[name]
                        for name in terms
                        if BackendMatcher.WEIGHTED_HEURISTIC[name] != 0
                    ),
                    6,
                ),
                "factor_max": round(
                    max(
                        overrides[name] / BackendMatcher.WEIGHTED_HEURISTIC[name]
                        for name in terms
                        if BackendMatcher.WEIGHTED_HEURISTIC[name] != 0
                    ),
                    6,
                ),
                "principal_backend": principal.backend_id,
                "weighted_backend": weighted.backend_id,
            }
        )

    payload = {
        "holdout": {
            "correct": correct,
            "total": len(holdout_rows),
            "accuracy": correct / len(holdout_rows),
            "cases": holdout_rows,
        },
        "weight_sensitivity": {
            "perturbations": len(sensitivity_rows),
            "principal_unique_selections": sorted(
                {row["principal_backend"] for row in sensitivity_rows}
            ),
            "weighted_unique_selections": sorted(
                {row["weighted_backend"] for row in sensitivity_rows}
            ),
            "runs": sensitivity_rows,
        },
    }
    save_json(RESULTS_DIR / "selection_robustness_results.json", payload)
    save_csv(RESULTS_DIR / "selection_holdout_results.csv", holdout_rows)
    save_csv(RESULTS_DIR / "selection_weight_sensitivity.csv", sensitivity_rows)
    return payload


def main() -> None:
    payload = evaluate()
    holdout = payload["holdout"]
    sensitivity = payload["weight_sensitivity"]
    print(
        f"Holdout accuracy: {holdout['accuracy']:.3f} "
        f"({holdout['correct']}/{holdout['total']})"
    )
    print(
        "Principal selections under weight perturbation: "
        + ", ".join(sensitivity["principal_unique_selections"])
    )
    print(
        "Weighted selections under weight perturbation: "
        + ", ".join(sensitivity["weighted_unique_selections"])
    )


if __name__ == "__main__":
    main()
