"""Automated A6 load and fault campaigns for the distributed testbed."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evaluation.common import PROJECT_ROOT, RESULTS_DIR
from evaluation.regenerate_rq2_figures import regenerate
from testbed.config import (
    CampaignConfiguration,
    load_campaign_configuration,
    load_deployment_configuration,
    load_network_profile,
)
from testbed.context import PARENT_SPAN_HEADER, TRACE_ID_HEADER
from testbed.deployment import start_local_distributed_testbed
from testbed.observability import JsonlSpanRecorder


DEFAULT_CAMPAIGN = PROJECT_ROOT / "deployments" / "campaigns" / "rq2-v1.json"
A6_SOURCE_FILES = (
    "core/execution.py",
    "evaluation/backend_matrix.py",
    "evaluation/evaluate_distributed_testbed.py",
    "evaluation/regenerate_rq2_figures.py",
    "evaluation/run_all_evaluations.py",
    "remote/control_plane_service.py",
    "remote/edge_service.py",
    "remote/gateway_service.py",
    "runtimes/remote_edge_runtime.py",
    "testbed/__init__.py",
    "testbed/config.py",
    "testbed/context.py",
    "testbed/deployment.py",
    "testbed/faults.py",
    "testbed/observability.py",
)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _task_payload(
    *,
    campaign_id: str,
    profile_id: str,
    client_count: int,
    repetition: int,
    client_index: int,
    trace_id: str,
) -> dict:
    identifier = (
        f"{campaign_id}-{profile_id}-{client_count}-"
        f"{repetition}-{client_index}"
    )
    return {
        "task": {
            "task_id": identifier,
            "client_id": f"client-{client_index}",
            "correlation_id": trace_id,
            "idempotency_key": identifier,
            "task_kind": "monitoring",
            "summary": "A6 distributed contention and fault campaign.",
            "required_input_modalities": ["digital_vector"],
            "preferred_output": "telemetry_aware_result",
            "latency_budget_ms": 500.0,
            "min_confidence": 0.0,
            "continuous_monitoring_required": True,
            "direct_backend_id": "remote-edge-backend",
            "allow_fallback": False,
            "max_twin_age_ms": 1000.0,
            "required_telemetry_fields": [
                "health_status",
                "drift_score",
                "age_of_information_ms",
            ],
            "lease_ttl_ms": 5000.0,
            "preparation_timeout_ms": 3000.0,
            "invocation_timeout_ms": 3000.0,
            "validation_timeout_ms": 3000.0,
            "abort_timeout_ms": 3000.0,
            "cooldown_timeout_ms": 3000.0,
            "metadata": {"input_vector": [0.1, 0.3, 0.5, 0.7]},
        }
    }


def _submit(
    *,
    gateway_url: str,
    timeout_s: float,
    recorder: JsonlSpanRecorder,
    campaign_id: str,
    profile_id: str,
    client_count: int,
    repetition: int,
    client_index: int,
) -> dict:
    trace_id = hashlib.sha256(
        (
            f"{campaign_id}:{profile_id}:{client_count}:"
            f"{repetition}:{client_index}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    payload = _task_payload(
        campaign_id=campaign_id,
        profile_id=profile_id,
        client_count=client_count,
        repetition=repetition,
        client_index=client_index,
        trace_id=trace_id,
    )
    encoded = json.dumps(payload).encode("utf-8")
    started = perf_counter()
    status_code = 0
    response_payload: dict = {}
    transport_error: str | None = None
    attributes = {
        "client_count": client_count,
        "client_index": client_index,
        "repetition": repetition,
    }
    with recorder.span(
        trace_id=trace_id,
        parent_span_id=None,
        operation="submit_task",
        profile_id=profile_id,
        attributes=attributes,
    ) as span_id:
        request = Request(
            gateway_url + "/execute",
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/json",
                TRACE_ID_HEADER: trace_id,
                PARENT_SPAN_HEADER: span_id,
            },
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:
                status_code = response.status
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            status_code = exc.code
            response_payload = json.loads(exc.read().decode("utf-8"))
        except (URLError, TimeoutError) as exc:
            transport_error = type(exc).__name__
        attributes["status_code"] = status_code
        attributes["transport_error"] = transport_error

    latency_ms = (perf_counter() - started) * 1000.0
    control_error = response_payload.get("error")
    error_code = (
        control_error.get("code")
        if isinstance(control_error, dict)
        else response_payload.get("error")
    )
    success = bool(response_payload.get("success", False))
    return {
        "campaign_id": campaign_id,
        "profile_id": profile_id,
        "client_count": client_count,
        "repetition": repetition,
        "client_index": client_index,
        "trace_id": trace_id,
        "http_status": status_code,
        "success": success,
        "error_code": error_code,
        "transport_error": transport_error,
        "latency_ms": round(latency_ms, 6),
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def summarize(rows: list[dict]) -> list[dict]:
    keys = sorted(
        {(str(row["profile_id"]), int(row["client_count"])) for row in rows}
    )
    summaries: list[dict] = []
    for profile_id, client_count in keys:
        selected = [
            row
            for row in rows
            if row["profile_id"] == profile_id
            and row["client_count"] == client_count
        ]
        latencies = [float(row["latency_ms"]) for row in selected]
        successes = sum(bool(row["success"]) for row in selected)
        repetitions = sorted({int(row["repetition"]) for row in selected})
        elapsed_s = (
            sum(
                max(
                    float(row["batch_elapsed_ms"])
                    for row in selected
                    if int(row["repetition"]) == repetition
                )
                for repetition in repetitions
            )
            / 1000.0
        )
        summaries.append(
            {
                "profile_id": profile_id,
                "client_count": client_count,
                "requests": len(selected),
                "successes": successes,
                "success_rate": round(successes / len(selected), 6),
                "mean_latency_ms": round(mean(latencies), 6),
                "latency_jitter_ms": round(
                    pstdev(latencies) if len(latencies) > 1 else 0.0, 6
                ),
                "p50_latency_ms": round(_percentile(latencies, 0.50), 6),
                "p95_latency_ms": round(_percentile(latencies, 0.95), 6),
                "p99_latency_ms": round(_percentile(latencies, 0.99), 6),
                "transport_errors": sum(
                    row["transport_error"] is not None for row in selected
                ),
                "resource_busy": sum(
                    str(row["error_code"]).endswith("RESOURCE_BUSY")
                    for row in selected
                ),
                "throughput_requests_s": round(
                    len(selected) / max(elapsed_s, 0.000001), 6
                ),
            }
        )
    return summaries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty() -> bool | None:
    try:
        return bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_ROOT,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def _archive_manifest(output_dir: Path, campaign: CampaignConfiguration) -> Path:
    files = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "schema_version": "1.0",
        "campaign_id": campaign.campaign_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "python": sys.version,
        "platform": platform.platform(),
        "source_files": {
            relative_path: _sha256(PROJECT_ROOT / relative_path)
            for relative_path in A6_SOURCE_FILES
        },
        "files": {
            str(path.relative_to(output_dir)).replace("\\", "/"): _sha256(path)
            for path in files
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def evaluate(
    campaign_path: str | Path = DEFAULT_CAMPAIGN,
    *,
    output_dir: str | Path | None = None,
    client_counts: list[int] | None = None,
    repetitions: int | None = None,
    profile_limit: int | None = None,
) -> dict:
    campaign_path = Path(campaign_path).resolve()
    campaign = load_campaign_configuration(campaign_path)
    deployment_path = (campaign_path.parent / campaign.deployment).resolve()
    load_deployment_configuration(deployment_path)
    output_dir = Path(output_dir or (RESULTS_DIR / "distributed-testbed"))
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(
            f"campaign output directory must be fresh and empty: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    config_archive = output_dir / "configuration"
    config_archive.mkdir(parents=True, exist_ok=True)
    shutil.copy2(campaign_path, config_archive / campaign_path.name)
    shutil.copy2(deployment_path, config_archive / deployment_path.name)

    selected_counts = client_counts or campaign.client_counts
    selected_repetitions = repetitions or campaign.repetitions
    profile_names = campaign.profiles[:profile_limit] if profile_limit else campaign.profiles
    all_rows: list[dict] = []

    for profile_name in profile_names:
        profile_path = (campaign_path.parent / profile_name).resolve()
        profile = load_network_profile(profile_path)
        shutil.copy2(profile_path, config_archive / profile_path.name)
        profile_output = output_dir / "services" / profile.profile_id
        testbed = start_local_distributed_testbed(
            project_root=PROJECT_ROOT,
            profile_path=profile_path,
            output_dir=profile_output,
            timeout_s=campaign.request_timeout_s,
        )
        client_recorder = JsonlSpanRecorder(
            profile_output / "traces" / "agent-client.jsonl",
            "agent-client",
        )
        try:
            for client_count in selected_counts:
                for repetition in range(selected_repetitions):
                    batch_started = perf_counter()
                    with ThreadPoolExecutor(max_workers=client_count) as executor:
                        futures = [
                            executor.submit(
                                _submit,
                                gateway_url=testbed.gateway_url,
                                timeout_s=campaign.request_timeout_s,
                                recorder=client_recorder,
                                campaign_id=campaign.campaign_id,
                                profile_id=profile.profile_id,
                                client_count=client_count,
                                repetition=repetition,
                                client_index=client_index,
                            )
                            for client_index in range(client_count)
                        ]
                        batch_rows = [future.result() for future in futures]
                    batch_elapsed_ms = (perf_counter() - batch_started) * 1000.0
                    for row in batch_rows:
                        row["batch_elapsed_ms"] = round(batch_elapsed_ms, 6)
                    all_rows.extend(batch_rows)
        finally:
            testbed.stop()

    summary_rows = summarize(all_rows)
    raw_jsonl = output_dir / "raw-requests.jsonl"
    raw_csv = output_dir / "raw-requests.csv"
    summary_csv = output_dir / "summary.csv"
    _write_jsonl(raw_jsonl, all_rows)
    _write_csv(raw_csv, all_rows)
    _write_csv(summary_csv, summary_rows)
    figures = regenerate(summary_csv, output_dir / "figures")
    manifest_path = _archive_manifest(output_dir, campaign)
    return {
        "output_dir": str(output_dir),
        "requests": len(all_rows),
        "summary_rows": len(summary_rows),
        "figures": [str(path) for path in figures],
        "manifest": str(manifest_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run one profile with 1 and 2 clients for local verification.",
    )
    args = parser.parse_args()
    result = evaluate(
        args.campaign,
        output_dir=args.output_dir,
        client_counts=[1, 2] if args.quick else None,
        repetitions=1 if args.quick else None,
        profile_limit=1 if args.quick else None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
