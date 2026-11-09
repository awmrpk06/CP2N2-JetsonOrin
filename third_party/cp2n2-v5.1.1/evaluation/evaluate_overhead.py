"""Evaluate wall-clock control-plane overhead."""

from __future__ import annotations

from statistics import mean, pstdev
from time import perf_counter

from evaluation.common import RESULTS_DIR, save_csv, save_json
from evaluation.plots import save_grouped_bar_chart

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from demos.common import build_default_orchestrator, make_chemical_task, make_edge_task, make_wetware_task


ITERATIONS = 25


def _measure_direct(adapter, task, iterations: int) -> list[float]:
    durations_ms: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        preparation = adapter.prepare(task)
        if not preparation.prepared:
            adapter.reset()
            adapter.recalibrate()
            preparation = adapter.prepare(task)
        if not preparation.prepared:
            raise RuntimeError(f"Direct execution could not prepare backend {adapter.backend_id()}.")
        adapter.invoke(task)
        adapter.collect_telemetry()
        durations_ms.append((perf_counter() - start) * 1000.0)
        adapter.reset()
        adapter.recalibrate()
    return durations_ms


def _measure_orchestrated(orchestrator, task, backend_id: str, iterations: int) -> list[float]:
    durations_ms: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        run_result = orchestrator.execute_task(task)
        durations_ms.append((perf_counter() - start) * 1000.0)
        if not run_result.success:
            raise RuntimeError(f"Orchestrated execution failed for backend {backend_id}: {run_result.failure_reason}")
        adapter = orchestrator.registry.get_adapter(backend_id)
        adapter.reset()
        adapter.recalibrate()
    return durations_ms


def evaluate() -> dict:
    cases = [
        {
            "name": "chemical",
            "task": make_chemical_task(task_id="eval-chemical", input_level=1.4),
            "direct_adapter": ChemicalAdapter(),
            "orchestrator": build_default_orchestrator(),
            "backend_id": "chemical-backend",
        },
        {
            "name": "wetware",
            "task": make_wetware_task(task_id="eval-wetware"),
            "direct_adapter": WetwareAdapter(),
            "orchestrator": build_default_orchestrator(),
            "backend_id": "wetware-backend",
        },
        {
            "name": "edge",
            "task": make_edge_task(task_id="eval-edge"),
            "direct_adapter": EdgeAdapter(),
            "orchestrator": build_default_orchestrator(),
            "backend_id": "edge-backend",
        },
    ]

    rows: list[dict] = []
    for case in cases:
        direct_times = _measure_direct(case["direct_adapter"], case["task"], ITERATIONS)
        orchestrated_times = _measure_orchestrated(case["orchestrator"], case["task"], case["backend_id"], ITERATIONS)

        direct_mean = mean(direct_times)
        orchestrated_mean = mean(orchestrated_times)
        added_overhead = orchestrated_mean - direct_mean
        relative_factor = orchestrated_mean / direct_mean if direct_mean > 0 else 0.0

        rows.append(
            {
                "backend": case["name"],
                "iterations": ITERATIONS,
                "direct_mean_ms": round(direct_mean, 6),
                "direct_std_ms": round(pstdev(direct_times), 6),
                "orchestrated_mean_ms": round(orchestrated_mean, 6),
                "orchestrated_std_ms": round(pstdev(orchestrated_times), 6),
                "added_overhead_ms": round(added_overhead, 6),
                "relative_factor": round(relative_factor, 6),
            }
        )

    payload = {"iterations": ITERATIONS, "results": rows}
    save_json(RESULTS_DIR / "overhead_results.json", payload)
    save_csv(RESULTS_DIR / "overhead_results.csv", rows)

    labels = [row["backend"] for row in rows]
    direct_values = [row["direct_mean_ms"] for row in rows]
    orchestrated_values = [row["orchestrated_mean_ms"] for row in rows]
    save_grouped_bar_chart(
        labels=labels,
        series_a=direct_values,
        series_b=orchestrated_values,
        series_a_label="Direct path",
        series_b_label="Orchestrated path",
        title="Wall-clock overhead of CP²N² control-plane execution",
        ylabel="Mean wall-clock time (ms)",
        output_path=RESULTS_DIR / "overhead_bar_chart.png",
    )

    return payload


def main() -> None:
    payload = evaluate()
    print("Overhead evaluation complete.")
    for row in payload["results"]:
        print(
            f"{row['backend']}: direct={row['direct_mean_ms']} ms, "
            f"orchestrated={row['orchestrated_mean_ms']} ms, "
            f"added={row['added_overhead_ms']} ms"
        )


if __name__ == "__main__":
    main()
