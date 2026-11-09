"""Versioned substrate coverage declarations for core evaluations."""

from __future__ import annotations


NON_CL_BACKEND_IDS = frozenset(
    {
        "chemical-backend",
        "wetware-backend",
        "edge-backend",
        "remote-edge-backend",
    }
)

CORE_EVALUATION_BACKEND_MATRIX: dict[str, frozenset[str]] = {
    "evaluate_overhead": frozenset(
        {"chemical-backend", "wetware-backend", "edge-backend"}
    ),
    "evaluate_portability": frozenset(
        {
            "chemical-backend",
            "wetware-backend",
            "edge-backend",
            "cortical-labs-backend",
        }
    ),
    "evaluate_matching": frozenset(
        {"chemical-backend", "wetware-backend", "edge-backend"}
    ),
    "evaluate_matching_baselines": frozenset(
        {"chemical-backend", "wetware-backend", "edge-backend"}
    ),
    "evaluate_selection_robustness": frozenset(
        {"chemical-backend", "wetware-backend", "edge-backend"}
    ),
    "evaluate_failure_campaign": frozenset(
        {"chemical-backend", "wetware-backend", "edge-backend"}
    ),
    "evaluate_externalized_backend": frozenset({"remote-edge-backend"}),
    "evaluate_distributed_testbed": frozenset({"remote-edge-backend"}),
}


def validate_core_evaluation_coverage() -> bool:
    """Fail if any core evaluation loses all non-CL substrate coverage."""

    missing = [
        name
        for name, backend_ids in CORE_EVALUATION_BACKEND_MATRIX.items()
        if not backend_ids.intersection(NON_CL_BACKEND_IDS)
    ]
    if missing:
        raise ValueError(
            "Core evaluations without a non-CL backend: "
            + ", ".join(sorted(missing))
        )
    return True
