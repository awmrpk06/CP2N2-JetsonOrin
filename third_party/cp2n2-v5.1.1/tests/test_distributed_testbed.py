from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.execution import run_with_timeout
from evaluation.evaluate_distributed_testbed import evaluate, summarize
from testbed.config import (
    CampaignConfiguration,
    LinkProfile,
    NetworkProfile,
    load_campaign_configuration,
    load_deployment_configuration,
    load_network_profile,
)
from testbed.context import bind_trace, current_trace_id, reset_trace
from testbed.faults import DeterministicFaultEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_PATH = PROJECT_ROOT / "deployments" / "campaigns" / "rq2-v1.json"


def test_versioned_a6_configuration_covers_all_required_conditions() -> None:
    campaign = load_campaign_configuration(CAMPAIGN_PATH)
    deployment = load_deployment_configuration(
        CAMPAIGN_PATH.parent / campaign.deployment
    )
    profiles = [
        load_network_profile(CAMPAIGN_PATH.parent / profile)
        for profile in campaign.profiles
    ]

    assert campaign.client_counts == [1, 2, 4, 8, 16, 32]
    assert {service.service_id for service in deployment.services} == {
        "agent-client",
        "gateway",
        "control-plane",
        "adapter-service",
    }
    assert any(
        link.latency_ms > 0 and link.jitter_ms > 0
        for profile in profiles
        for link in (
            profile.agent_gateway,
            profile.gateway_control,
            profile.control_adapter,
        )
    )
    assert any(
        link.loss_rate > 0
        for profile in profiles
        for link in (
            profile.agent_gateway,
            profile.gateway_control,
            profile.control_adapter,
        )
    )
    assert any(
        link.partition_every_n_requests is not None
        for profile in profiles
        for link in (
            profile.agent_gateway,
            profile.gateway_control,
            profile.control_adapter,
        )
    )
    assert any(profile.telemetry_staleness_ms > 0 for profile in profiles)


def test_campaign_rejects_client_counts_outside_a6_scope() -> None:
    with pytest.raises(ValidationError):
        CampaignConfiguration(
            campaign_id="invalid",
            deployment="deployment.json",
            profiles=["baseline.json"],
            client_counts=[1, 33],
        )


def test_fault_decisions_are_seeded_per_trace() -> None:
    profile = NetworkProfile(
        profile_id="deterministic",
        description="test",
        seed=99,
        gateway_control=LinkProfile(
            latency_ms=5.0,
            jitter_ms=2.0,
            loss_rate=0.5,
        ),
    )
    first = DeterministicFaultEngine(profile, "gateway_control")
    second = DeterministicFaultEngine(profile, "gateway_control")
    assert first.decide("trace-a", "POST /execute") == second.decide(
        "trace-a", "POST /execute"
    )


def test_timeout_worker_preserves_trace_context() -> None:
    tokens = bind_trace("trace-context", "parent")
    try:
        outcome = run_with_timeout(current_trace_id.get, 1000.0)
    finally:
        reset_trace(tokens)
    assert outcome.value == "trace-context"


def test_summary_reports_contention_and_latency_distribution() -> None:
    rows = [
        {
            "profile_id": "baseline",
            "client_count": 2,
            "repetition": 0,
            "success": True,
            "latency_ms": 10.0,
            "batch_elapsed_ms": 12.0,
            "transport_error": None,
            "error_code": None,
        },
        {
            "profile_id": "baseline",
            "client_count": 2,
            "repetition": 0,
            "success": False,
            "latency_ms": 12.0,
            "batch_elapsed_ms": 12.0,
            "transport_error": None,
            "error_code": "RESOURCE_BUSY",
        },
    ]
    result = summarize(rows)[0]
    assert result["success_rate"] == 0.5
    assert result["resource_busy"] == 1
    assert result["p95_latency_ms"] == 12.0
    assert result["latency_jitter_ms"] == 1.0


def test_campaign_refuses_to_append_to_existing_archive(tmp_path: Path) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "evidence.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh and empty"):
        evaluate(
            CAMPAIGN_PATH,
            output_dir=output_dir,
            client_counts=[1],
            repetitions=1,
            profile_limit=1,
        )
    assert (output_dir / "evidence.txt").read_text(encoding="utf-8") == "preserve"


def test_real_four_process_smoke_run_archives_correlated_evidence(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "a6-smoke"
    result = evaluate(
        CAMPAIGN_PATH,
        output_dir=output_dir,
        client_counts=[1, 2],
        repetitions=1,
        profile_limit=1,
    )
    assert result["requests"] == 3
    assert (output_dir / "raw-requests.jsonl").is_file()
    assert (output_dir / "summary.csv").is_file()
    assert (output_dir / "figures" / "rq2-p95-latency.png").is_file()
    assert (output_dir / "figures" / "rq2-success-rate.png").is_file()

    first = json.loads(
        (output_dir / "raw-requests.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    trace_id = first["trace_id"]
    trace_dir = output_dir / "services" / "baseline" / "traces"
    for name in (
        "agent-client.jsonl",
        "gateway.jsonl",
        "control-plane.jsonl",
        "adapter-service.jsonl",
    ):
        assert trace_id in (trace_dir / name).read_text(encoding="utf-8")

    gateway_records = [
        json.loads(line)
        for line in (trace_dir / "gateway.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(record["status"] in {"ok", "error"} for record in gateway_records)

    manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_files"]["testbed/config.py"]
    assert manifest["source_files"]["evaluation/evaluate_distributed_testbed.py"]
    for relative_path, expected_hash in manifest["files"].items():
        artifact = output_dir / relative_path
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected_hash
