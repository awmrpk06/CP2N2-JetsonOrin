"""Demo 2: invocation, telemetry, lifecycle, and recovery."""

from __future__ import annotations

from common import (
    build_default_orchestrator,
    make_chemical_task,
    print_header,
    print_run_summary,
)


def main() -> None:
    orchestrator = build_default_orchestrator()
    task = make_chemical_task(task_id="chemical-lifecycle-demo", input_level=1.8)

    print_header("Initial chemical backend telemetry")
    print(orchestrator.registry.get_adapter("chemical-backend").collect_telemetry())

    print_header("Repeated invocations until lifecycle recovery is triggered")
    for cycle in range(1, 13):
        run_result = orchestrator.execute_task(task)
        print(f"Cycle {cycle}")
        print_run_summary(run_result)
        print("-" * 80)

        if run_result.recovery_actions:
            print("Lifecycle recovery has been demonstrated. Stopping the loop.")
            break


if __name__ == "__main__":
    main()
