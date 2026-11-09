"""Demo 3: fallback routing and drift-triggered recalibration."""

from __future__ import annotations

from common import make_edge_task, print_header, print_run_summary

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from core.orchestrator import CP2N2Orchestrator


class FailingPrimaryEdgeAdapter(EdgeAdapter):
    """Edge adapter that fails during invocation to demonstrate fallback."""

    def invoke(self, task):
        raise RuntimeError("Injected primary backend failure for fallback demonstration.")


def demo_recalibration() -> None:
    orchestrator = CP2N2Orchestrator()
    edge_adapter = EdgeAdapter(backend_id="edge-recalibration")
    edge_adapter._twin._drift_score = 0.79  # Demo-only state injection.
    orchestrator.register_adapter(edge_adapter)

    task = make_edge_task(task_id="edge-recalibration-demo")

    print_header("Drift-triggered recalibration")
    run_result = orchestrator.execute_task(task)
    print_run_summary(run_result)


def demo_fallback() -> None:
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(FailingPrimaryEdgeAdapter(backend_id="edge-a-primary"))
    orchestrator.register_adapter(EdgeAdapter(backend_id="edge-b-backup"))
    orchestrator.register_adapter(ChemicalAdapter())
    orchestrator.register_adapter(WetwareAdapter())

    task = make_edge_task(task_id="edge-fallback-demo")

    print_header("Fallback to a secondary compatible backend")
    run_result = orchestrator.execute_task(task)
    print_run_summary(run_result)


def main() -> None:
    demo_recalibration()
    demo_fallback()


if __name__ == "__main__":
    main()
