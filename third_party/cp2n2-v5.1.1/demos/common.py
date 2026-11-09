"""Shared demo helpers for the CP²N² prototype."""

from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_project_root() -> Path:
    """Add the project root to ``sys.path`` and return it."""
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.remote_edge_adapter import RemoteEdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from core.orchestrator import CP2N2Orchestrator
from core.task_model import OutputPreference, TaskKind, TaskRequest
from descriptors.capability_schema import Locality, SignalModality


def build_default_orchestrator() -> CP2N2Orchestrator:
    """Create an orchestrator with the three default demo backends."""
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(ChemicalAdapter())
    orchestrator.register_adapter(WetwareAdapter())
    orchestrator.register_adapter(EdgeAdapter())
    return orchestrator


def build_extended_orchestrator(remote_base_url: str | None = None) -> CP2N2Orchestrator:
    """Create an orchestrator optionally including an externalized remote edge backend."""
    orchestrator = build_default_orchestrator()
    if remote_base_url is not None:
        orchestrator.register_adapter(RemoteEdgeAdapter(base_url=remote_base_url))
    return orchestrator


def build_live_target_orchestrator(
    remote_base_url: str | None = None,
    *,
    include_cortical_labs: bool = False,
    include_biopattern_gate_e3: bool = False,
) -> CP2N2Orchestrator:
    """Create an orchestrator with optional live-target adapters."""
    orchestrator = build_extended_orchestrator(remote_base_url=remote_base_url)
    if include_cortical_labs:
        from adapters.cortical_labs_adapter import CorticalLabsAdapter

        orchestrator.register_adapter(CorticalLabsAdapter())
    if include_biopattern_gate_e3:
        from adapters.biopattern_gate_e3_adapter import BioPatternGateE3Adapter

        orchestrator.register_adapter(BioPatternGateE3Adapter())
    return orchestrator


def build_biopattern_gate_e3_orchestrator(
    *,
    telemetry_age_ms: float = 0.0,
    fail_after_trials: int | None = None,
) -> CP2N2Orchestrator:
    """Create the dedicated access-independent BioPattern Gate E3 target."""
    from adapters.biopattern_gate_e3_adapter import BioPatternGateE3Adapter

    orchestrator = build_default_orchestrator()
    orchestrator.register_adapter(
        BioPatternGateE3Adapter(
            telemetry_age_ms=telemetry_age_ms,
            fail_after_trials=fail_after_trials,
        )
    )
    return orchestrator


def make_edge_task(
    task_id: str = "task-edge",
    *,
    preferred_locality: Locality | None = Locality.EDGE,
    direct_backend_id: str | None = None,
    min_confidence: float = 0.2,
) -> TaskRequest:
    """Create a representative low-latency edge classification task."""
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.CLASSIFICATION,
        summary="Low-latency edge classification task.",
        required_input_modalities=[SignalModality.DIGITAL_VECTOR],
        preferred_output=OutputPreference.SCORE,
        latency_budget_ms=20.0,
        min_confidence=min_confidence,
        preferred_locality=preferred_locality,
        direct_backend_id=direct_backend_id,
        metadata={"input_vector": [0.1, 0.3, 0.5, 0.7]},
    )


def make_wetware_task(
    task_id: str = "task-wetware",
    *,
    human_supervision_available: bool = True,
    direct_backend_id: str | None = None,
) -> TaskRequest:
    """Create a representative closed-loop temporal inference task."""
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.TEMPORAL_INFERENCE,
        summary="Closed-loop stimulation and observation task.",
        required_input_modalities=[SignalModality.SPIKES],
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=120.0,
        continuous_monitoring_required=True,
        min_confidence=0.2,
        human_supervision_available=human_supervision_available,
        direct_backend_id=direct_backend_id,
        required_telemetry_fields=["health_status", "drift_score"],
        metadata={
            "stimulation_strength": 0.65,
            "observation_window_ms": 140.0,
        },
    )


def make_chemical_task(
    task_id: str = "task-chemical",
    input_level: float = 1.4,
    *,
    max_twin_age_ms: float | None = None,
    required_telemetry_fields: list[str] | None = None,
    direct_backend_id: str | None = None,
) -> TaskRequest:
    """Create a representative concentration-driven sensing task."""
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.SENSING,
        summary="Concentration-driven sensing and steady-state inference task.",
        required_input_modalities=[SignalModality.CONCENTRATION],
        preferred_output=OutputPreference.STATE_ESTIMATE,
        latency_budget_ms=15000.0,
        min_confidence=0.2,
        max_twin_age_ms=max_twin_age_ms,
        required_telemetry_fields=required_telemetry_fields or [],
        direct_backend_id=direct_backend_id,
        metadata={"input_level": input_level},
    )


def make_remote_edge_monitoring_task(task_id: str = "task-remote-edge") -> TaskRequest:
    """Create a task that prefers a remote edge/fog-style backend with telemetry."""
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.MONITORING,
        summary="Telemetry-aware vector monitoring task for a remote backend.",
        required_input_modalities=[SignalModality.DIGITAL_VECTOR],
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=30.0,
        min_confidence=0.25,
        preferred_locality=Locality.FOG,
        required_telemetry_fields=["health_status", "drift_score"],
        continuous_monitoring_required=True,
        metadata={"input_vector": [0.2, 0.2, 0.4, 0.9]},
    )


def make_cortical_task(
    task_id: str = "task-cortical",
    *,
    direct_backend_id: str | None = "cortical-labs-backend",
    allow_fallback: bool = False,
) -> TaskRequest:
    """Create a stimulation/recording task matching the regenerated CL adapter."""
    return TaskRequest(
        task_id=task_id,
        task_kind=TaskKind.CONTROL,
        summary="Closed-loop wetware stimulation task for the Cortical Labs backend.",
        required_input_modalities=[SignalModality.SPIKES],
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=500.0,
        min_confidence=0.0,
        continuous_monitoring_required=True,
        preferred_locality=Locality.LAB,
        direct_backend_id=direct_backend_id,
        required_telemetry_fields=["readiness_state", "health_status", "backend_latency_ms"],
        human_supervision_available=True,
        allow_fallback=allow_fallback,
        metadata={
            "stimulation_pattern": {
                "channels": [1],
                "amplitude": 0.4,
            },
            "observation_window_ms": 100,
            "pre_delay_ms": 20,
        },
    )


def print_header(title: str) -> None:
    """Print a demo section header."""
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def print_match_report(report) -> None:
    """Pretty-print a matcher report."""
    print(f"Task: {report.task_id}")
    for candidate in report.candidates:
        for line in candidate.explanation_lines():
            print(line)


def print_run_summary(run_result) -> None:
    """Pretty-print a task execution result."""
    print(f"Success: {run_result.success}")
    print(f"Selected backend: {run_result.decision.selected_backend_id}")
    print(f"Used fallback: {run_result.decision.used_fallback}")
    if run_result.invocation is not None:
        print(f"Latency (ms): {run_result.invocation.execution_latency_ms:.3f}")
        print(f"Confidence: {run_result.invocation.confidence}")
        print(f"Output payload: {run_result.invocation.output_payload}")
    print(f"Telemetry before: {run_result.telemetry_before}")
    print(f"Telemetry after: {run_result.telemetry_after}")
    if run_result.validation_failures:
        print("Validation failures:")
        for failure in run_result.validation_failures:
            print(f"  - {failure}")
    if run_result.recovery_actions:
        print("Recovery actions:")
        for action in run_result.recovery_actions:
            print(f"  - {action}")
    if run_result.failure_reason:
        print(f"Failure reason: {run_result.failure_reason}")
    if run_result.decision.notes:
        print("Decision notes:")
        for note in run_result.decision.notes:
            print(f"  - {note}")
