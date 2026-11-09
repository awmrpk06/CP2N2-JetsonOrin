"""Demo 1: discovery and explainable backend matching."""

from __future__ import annotations

from common import (
    build_default_orchestrator,
    make_chemical_task,
    make_edge_task,
    make_wetware_task,
    print_header,
    print_match_report,
)


def main() -> None:
    orchestrator = build_default_orchestrator()

    print_header("Discovery")
    descriptors = orchestrator.discover_backends()
    for descriptor in descriptors:
        print(
            f"{descriptor['backend_id']}: "
            f"{descriptor['display_name']} | "
            f"locality={descriptor['policy']['locality']} | "
            f"tasks={descriptor['capability']['supported_task_types']}"
        )

    tasks = [
        make_edge_task(),
        make_wetware_task(),
        make_chemical_task(),
    ]

    for task in tasks:
        print_header(f"Matching for {task.task_id}")
        report = orchestrator.plan_task(task)
        print_match_report(report)
        best = report.best_candidate()
        if best is not None:
            print(f"Best candidate: {best.backend_id} (score={best.score:.2f})")
        else:
            print("No acceptable backend candidate found.")


if __name__ == "__main__":
    main()
