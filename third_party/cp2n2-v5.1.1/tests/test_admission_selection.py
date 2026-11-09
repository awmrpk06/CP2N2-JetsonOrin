from __future__ import annotations

import json

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from core.matcher import BackendMatcher
from core.task_model import SelectionPolicy
from demos.common import make_edge_task
from descriptors.capability_schema import Locality, SubstrateDescriptor


def _edge_descriptor(
    backend_id: str,
    *,
    latency_ms: float,
    locality: Locality,
) -> SubstrateDescriptor:
    descriptor = EdgeAdapter(backend_id=backend_id).describe()
    return descriptor.model_copy(
        update={
            "timing": descriptor.timing.model_copy(
                update={"typical_latency_ms": latency_ms}
            ),
            "policy": descriptor.policy.model_copy(update={"locality": locality.value}),
            "telemetry": descriptor.telemetry.model_copy(
                update={"supports_age_of_information": True}
            ),
        }
    )


def _two_candidate_fixture():
    fast_remote = _edge_descriptor(
        "fast-remote",
        latency_ms=2.0,
        locality=Locality.CLOUD,
    )
    safe_local = _edge_descriptor(
        "safe-local",
        latency_ms=10.0,
        locality=Locality.EDGE,
    )
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
    task = make_edge_task(task_id="selection-profiles")
    task.max_twin_age_ms = 1_000.0
    return task, [fast_remote, safe_local], runtime


def test_admission_and_feasibility_are_separate_from_ranking() -> None:
    descriptor = _edge_descriptor(
        "too-slow",
        latency_ms=30.0,
        locality=Locality.EDGE,
    )
    task = make_edge_task(task_id="hard-latency")
    report = BackendMatcher().rank_backends(
        task,
        [descriptor],
        runtime_state={"too-slow": {"health_status": "ready", "reservable": True}},
    )

    candidate = report.candidates[0]
    assert candidate.admission.passed
    assert not candidate.feasibility.passed
    assert any(
        violation.startswith("latency_budget:")
        for violation in candidate.feasibility.violations
    )
    assert not candidate.accepted
    assert candidate.ranking.rank is None
    assert candidate.ranking.rank_key == []
    assert candidate.score == 0.0


def test_every_admission_exclusion_names_its_constraint() -> None:
    task = make_edge_task(task_id="wrong-modality")
    chemical = ChemicalAdapter().describe()
    candidate = BackendMatcher().rank_backends(task, [chemical]).candidates[0]

    assert not candidate.admission.passed
    assert candidate.admission.violations
    assert all(":" in violation for violation in candidate.admission.violations)
    assert candidate.feasibility.violations == [
        "Feasibility was not evaluated because admission failed."
    ]


def test_documented_profiles_make_reproducible_tradeoffs() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    matcher = BackendMatcher()

    expected = {
        SelectionPolicy.LEXICOGRAPHIC: "safe-local",
        SelectionPolicy.LATENCY_FIRST: "fast-remote",
        SelectionPolicy.SAFETY_FRESHNESS_FIRST: "safe-local",
        SelectionPolicy.LOCALITY_COST_FIRST: "safe-local",
    }
    for policy, expected_backend in expected.items():
        report = matcher.rank_backends(
            task,
            descriptors,
            runtime_state=runtime,
            policy=policy,
        )
        assert report.best_candidate().backend_id == expected_backend
        assert report.best_candidate().ranking.policy == policy
        assert report.best_candidate().ranking.rank == 1
        assert report.best_candidate().ranking.rank_key


def test_locality_cost_profile_uses_cost_after_locality() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    task.preferred_locality = None
    report = BackendMatcher().rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
        policy=SelectionPolicy.LOCALITY_COST_FIRST,
    )
    assert report.best_candidate().backend_id == "fast-remote"


def test_weighted_heuristic_is_explicit_but_cannot_override_hard_constraints() -> None:
    descriptor = _edge_descriptor(
        "slow-but-overweighted",
        latency_ms=100.0,
        locality=Locality.EDGE,
    )
    task = make_edge_task(task_id="weighted-hard-boundary")
    matcher = BackendMatcher(
        weighted_overrides={
            "base_accepted": 1_000_000.0,
            "latency_within_budget": 1_000_000.0,
        }
    )
    report = matcher.rank_backends(
        task,
        [descriptor],
        runtime_state={
            descriptor.backend_id: {
                "health_status": "ready",
                "reservable": True,
            }
        },
        policy=SelectionPolicy.WEIGHTED_COMPARISON,
    )

    assert matcher.weighted_heuristic["base_accepted"] == 1_000_000.0
    assert report.best_candidate() is None
    assert not report.candidates[0].feasibility.passed


def test_baselines_are_deterministic_and_use_only_admissible_resources() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    static = BackendMatcher(static_priority=["safe-local", "fast-remote"])

    static_report = static.rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
        policy=SelectionPolicy.STATIC_PRIORITY,
    )
    constraint_report = static.rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
        policy=SelectionPolicy.CONSTRAINT_BASED,
    )
    random_a = static.rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
        policy=SelectionPolicy.RANDOM_ADMISSIBLE,
    )
    random_b = static.rank_backends(
        task,
        list(reversed(descriptors)),
        runtime_state=runtime,
        policy=SelectionPolicy.RANDOM_ADMISSIBLE,
    )

    assert static_report.best_candidate().backend_id == "safe-local"
    assert constraint_report.best_candidate().backend_id == "fast-remote"
    assert random_a.best_candidate().backend_id == random_b.best_candidate().backend_id
    assert all(candidate.accepted for candidate in random_a.accepted_candidates())


def test_cost_ceiling_is_a_feasibility_constraint_and_unknown_is_not_optimistic() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    task.max_estimated_cost = 0.5
    task.cost_currency = "EUR"
    del runtime["safe-local"]["estimated_cost"]

    report = BackendMatcher().rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
        policy=SelectionPolicy.LOCALITY_COST_FIRST,
    )
    assert report.best_candidate().backend_id == "fast-remote"
    safe_local = next(
        candidate for candidate in report.candidates
        if candidate.backend_id == "safe-local"
    )
    assert any(
        violation.startswith("cost_budget:")
        for violation in safe_local.feasibility.violations
    )


def test_declared_but_missing_runtime_telemetry_is_infeasible() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    task.required_telemetry_fields = ["drift_score"]
    del runtime["safe-local"]["drift_score"]

    report = BackendMatcher().rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
    )
    safe_local = next(
        candidate for candidate in report.candidates
        if candidate.backend_id == "safe-local"
    )
    assert safe_local.admission.passed
    assert not safe_local.feasibility.passed
    assert any(
        violation.startswith("required_telemetry_available:")
        for violation in safe_local.feasibility.violations
    )


def test_selection_report_is_strict_json_and_contains_weight_breakdown() -> None:
    task, descriptors, runtime = _two_candidate_fixture()
    report = BackendMatcher().rank_backends(
        task,
        descriptors,
        runtime_state=runtime,
    )
    payload = json.loads(report.model_dump_json())

    assert payload["policy"] == "lexicographic"
    assert payload["candidates"][0]["admission"]["passed"]
    assert payload["candidates"][0]["feasibility"]["passed"]
    assert payload["candidates"][0]["ranking"]["weighted_components"]
    assert payload["candidates"][0]["ranking"]["rank_key"]
