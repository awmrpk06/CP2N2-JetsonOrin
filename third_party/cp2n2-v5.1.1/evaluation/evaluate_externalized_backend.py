"""Evaluate a simple externalized backend path via the remote HTTP service."""

from __future__ import annotations

from statistics import mean, pstdev
from time import perf_counter

from evaluation.common import PROJECT_ROOT, RESULTS_DIR, save_csv, save_json

from adapters.remote_edge_adapter import RemoteEdgeAdapter
from demos.common import make_remote_edge_monitoring_task
from remote.service_controller import start_remote_edge_service

ITERATIONS = 15


def evaluate() -> dict:
    service = start_remote_edge_service(PROJECT_ROOT)
    try:
        adapter = RemoteEdgeAdapter(service.base_url)
        task = make_remote_edge_monitoring_task(task_id="externalized-edge")

        roundtrip_times: list[float] = []
        backend_latencies: list[float] = []
        for _ in range(ITERATIONS):
            start = perf_counter()
            preparation = adapter.prepare(task)
            if not preparation.prepared:
                raise RuntimeError(f"Remote prepare failed: {preparation.details}")
            invocation = adapter.invoke(task)
            telemetry = adapter.collect_telemetry()
            roundtrip_times.append((perf_counter() - start) * 1000.0)
            backend_latencies.append(invocation.execution_latency_ms)
            adapter.reset()
            adapter.recalibrate()

        row = {
            "iterations": ITERATIONS,
            "backend_id": adapter.backend_id(),
            "endpoint_kind": adapter.describe().custom_metadata.get("endpoint_kind"),
            "mean_roundtrip_ms": round(mean(roundtrip_times), 6),
            "std_roundtrip_ms": round(pstdev(roundtrip_times), 6),
            "mean_backend_latency_ms": round(mean(backend_latencies), 6),
            "std_backend_latency_ms": round(pstdev(backend_latencies), 6),
            "transport_overhead_ms": round(mean(roundtrip_times) - mean(backend_latencies), 6),
        }
        payload = {"results": [row]}
        save_json(RESULTS_DIR / "externalized_backend_results.json", payload)
        save_csv(RESULTS_DIR / "externalized_backend_results.csv", [row])
        return payload
    finally:
        service.stop()


def main() -> None:
    payload = evaluate()
    row = payload["results"][0]
    print("Externalized backend evaluation complete.")
    print(
        f"{row['backend_id']}: mean_roundtrip={row['mean_roundtrip_ms']} ms, "
        f"transport_overhead={row['transport_overhead_ms']} ms"
    )


if __name__ == "__main__":
    main()
