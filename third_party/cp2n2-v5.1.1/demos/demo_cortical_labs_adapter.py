"""Demonstrate the Cortical Labs adapter against the CL SDK Simulator or live CL setup.

This demo is intentionally direct:
- it registers the optional Cortical Labs adapter via the normal CP²N² orchestrator
- it submits a directed wetware task to that backend
- it prints descriptor, plan, execution result, and telemetry

Use this after verifying the standalone CL smoke tests.
"""

from __future__ import annotations

from demos.common import (
    build_live_target_orchestrator,
    print_header,
    print_match_report,
    print_run_summary,
)
from core.task_model import OutputPreference, TaskKind, TaskRequest
from descriptors.capability_schema import Locality, SignalModality


def make_cortical_runtime_task(task_id: str = "task-cortical-runtime") -> TaskRequest:
    """Create a task that matches the live CL adapter semantics.

    The task is directed to the Cortical Labs backend so that the demo exercises
    the real API-backed path instead of the synthetic wetware twin.
    """
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.CONTROL,
        summary="Closed-loop stimulation/recording task against the Cortical Labs path.",
        required_input_modalities=[SignalModality.SPIKES],
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=500.0,
        min_confidence=0.0,
        continuous_monitoring_required=True,
        preferred_locality=Locality.LAB,
        direct_backend_id="cortical-labs-backend",
        required_telemetry_fields=["readiness_state", "health_status", "backend_latency_ms"],
        human_supervision_available=True,
        allow_fallback=False,
        metadata={
            "stimulation_pattern": {
                "channels": [1],
                "amplitude": 0.4,
            },
            "observation_window_ms": 100,
            "pre_delay_ms": 20,
        },
    )


def main() -> None:
    orchestrator = build_live_target_orchestrator(include_cortical_labs=True)
    task = make_cortical_runtime_task()

    print_header("Cortical Labs runtime adapter: descriptor and directed task")

    # Show the published descriptor for the target backend.
    adapter = orchestrator.registry.get_adapter("cortical-labs-backend")
    print("Published descriptor:")
    print(adapter.describe().model_dump())
    print()

    report = orchestrator.plan_task(task)
    print_match_report(report)

    run_result = orchestrator.execute_task(task)
    print_run_summary(run_result)


if __name__ == "__main__":
    main()
