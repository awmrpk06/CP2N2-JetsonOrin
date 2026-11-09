"""Evaluate CP²N² behavior under representative runtime faults."""

from __future__ import annotations

from evaluation.common import PROJECT_ROOT, RESULTS_DIR, save_csv, save_json

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.fault_injecting_adapter import FaultInjectingAdapter, FaultProfile
from core.orchestrator import OrchestrationRunResult
from descriptors.capability_schema import Locality
from demos.common import (
    build_default_orchestrator,
    build_extended_orchestrator,
    make_chemical_task,
    make_edge_task,
    make_remote_edge_monitoring_task,
    make_wetware_task,
)
from remote.service_controller import start_remote_edge_service


def _row_from_result(
    scenario: str,
    run_result: OrchestrationRunResult,
    expected_behavior: str,
    expectation_met: bool,
) -> dict:
    return {
        "scenario": scenario,
        "expected_behavior": expected_behavior,
        "success": run_result.success,
        "selected_backend": run_result.decision.selected_backend_id,
        "used_fallback": run_result.decision.used_fallback,
        "failure_reason": run_result.failure_reason,
        "validation_failures": " | ".join(run_result.validation_failures),
        "recovery_actions": " | ".join(run_result.recovery_actions),
        "decision_notes": " | ".join(run_result.decision.notes),
        "expectation_met": expectation_met,
    }


def evaluate() -> dict:
    service = start_remote_edge_service(PROJECT_ROOT)
    try:
        rows: list[dict] = []

        # Scenario 1: the preferred local edge backend is too drifted, so the remote
        # externalized backend should be selected instead.
        orchestrator = build_extended_orchestrator(remote_base_url=service.base_url)
        orchestrator.register_adapter(
            FaultInjectingAdapter(
                EdgeAdapter(),
                FaultProfile(override_telemetry={"drift_score": 0.97, "health_status": "degraded"}),
            ),
            overwrite=True,
        )
        task = make_remote_edge_monitoring_task(task_id="fault-drifted-edge")
        run_result = orchestrator.execute_task(task)
        rows.append(
            _row_from_result(
                scenario="drifted_primary_edge_avoided",
                run_result=run_result,
                expected_behavior="Select remote edge backend because local edge is runtime-degraded.",
                expectation_met=(run_result.success and run_result.decision.selected_backend_id == "remote-edge-backend"),
            )
        )

        # Scenario 2: the local edge backend fails during preparation; fallback should recover.
        orchestrator = build_extended_orchestrator(remote_base_url=service.base_url)
        orchestrator.register_adapter(
            FaultInjectingAdapter(
                EdgeAdapter(),
                FaultProfile(prepare_failure_message="Injected prepare failure for edge backend."),
            ),
            overwrite=True,
        )
        task = make_edge_task(task_id="fault-prepare-fallback", preferred_locality=None)
        run_result = orchestrator.execute_task(task)
        rows.append(
            _row_from_result(
                scenario="prepare_failure_with_fallback",
                run_result=run_result,
                expected_behavior="Fallback to remote edge backend after local prepare failure.",
                expectation_met=(run_result.success and run_result.decision.used_fallback),
            )
        )

        # Scenario 3: wetware access without human supervision must be rejected.
        orchestrator = build_default_orchestrator()
        task = make_wetware_task(task_id="fault-policy-reject", human_supervision_available=False)
        run_result = orchestrator.execute_task(task)
        rows.append(
            _row_from_result(
                scenario="policy_violation_rejected",
                run_result=run_result,
                expected_behavior="Reject wetware task because human supervision is unavailable.",
                expectation_met=(not run_result.success and run_result.decision.selected_backend_id is None),
            )
        )

        # Scenario 4: stale chemical twin state violates freshness bound and must be rejected.
        orchestrator = build_default_orchestrator()
        orchestrator.register_adapter(
            FaultInjectingAdapter(
                ChemicalAdapter(),
                FaultProfile(override_telemetry={"age_of_information_ms": 4000.0}),
            ),
            overwrite=True,
        )
        task = make_chemical_task(
            task_id="fault-stale-chemical",
            max_twin_age_ms=1000.0,
            required_telemetry_fields=["age_of_information_ms"],
        )
        run_result = orchestrator.execute_task(task)
        rows.append(
            _row_from_result(
                scenario="stale_twin_rejected",
                run_result=run_result,
                expected_behavior="Reject chemical backend because age_of_information exceeds task bound.",
                expectation_met=(not run_result.success and run_result.decision.selected_backend_id is None),
            )
        )

        # Scenario 5: local edge loses required telemetry after invocation; fallback should recover.
        orchestrator = build_extended_orchestrator(remote_base_url=service.base_url)
        orchestrator.register_adapter(
            FaultInjectingAdapter(
                EdgeAdapter(),
                FaultProfile(drop_telemetry_fields={"drift_score"}),
            ),
            overwrite=True,
        )
        task = make_edge_task(task_id="fault-telemetry-loss", preferred_locality=Locality.EDGE)
        task.required_telemetry_fields = ["drift_score"]
        task.continuous_monitoring_required = True
        run_result = orchestrator.execute_task(task)
        rows.append(
            _row_from_result(
                scenario="telemetry_loss_excludes_primary",
                run_result=run_result,
                expected_behavior=(
                    "Select the remote backend because feasibility excludes the "
                    "primary before ranking."
                ),
                expectation_met=(
                    run_result.success
                    and run_result.decision.selected_backend_id
                    == "remote-edge-backend"
                    and not run_result.decision.used_fallback
                ),
            )
        )

        payload = {
            "scenarios": rows,
            "success_rate": round(sum(1 for row in rows if row["expectation_met"]) / len(rows), 6),
        }
        save_json(RESULTS_DIR / "failure_campaign_results.json", payload)
        save_csv(RESULTS_DIR / "failure_campaign_results.csv", rows)
        return payload
    finally:
        service.stop()


def main() -> None:
    payload = evaluate()
    print("Failure campaign evaluation complete.")
    print(f"Expectation match rate: {payload['success_rate']:.3f}")
    for row in payload["scenarios"]:
        print(
            f"{row['scenario']}: selected={row['selected_backend']} "
            f"success={row['success']} expectation_met={row['expectation_met']}"
        )


if __name__ == "__main__":
    main()
