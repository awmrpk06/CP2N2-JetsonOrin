from __future__ import annotations

import csv
import json
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.embedded_worker import TrialConfig, execute_once

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["direct", "orchestrated"])
def test_worker_runs_in_fresh_process_without_evaluation_imports(tmp_path, mode):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mode": mode, "profile": "edge-only", "iterations": 3, "warmup": 2}))
    output_dir = tmp_path / "trial"
    result = subprocess.run(
        [sys.executable, "-m", "evaluation.embedded_worker", "--config", str(config_path), "--output-dir", str(output_dir)],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads((output_dir / "worker_summary.json").read_text())
    assert summary["completed_requests"] == summary["successful_requests"] == 3
    assert summary["process_peak_rss_kib"] > 0
    assert not any(name.startswith(("matplotlib", "demos", "evaluation.common")) for name in summary["loaded_modules"])
    with (output_dir / "requests.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert all(row["selected_backend"] == "edge-backend" for row in rows)
    assert [json.loads(line)["event"] for line in result.stdout.splitlines()] == [
        "ready", "warmup_start", "measurement_start", "measurement_end",
    ]
    original = (output_dir / "requests.csv").read_bytes()
    retry = subprocess.run(
        [sys.executable, "-m", "evaluation.embedded_worker", "--config", str(config_path), "--output-dir", str(output_dir)],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert retry.returncode != 0
    assert (output_dir / "requests.csv").read_bytes() == original


def test_invocation_and_maintenance_have_separate_timers():
    adapter = SimpleNamespace(
        prepare=lambda task: SimpleNamespace(prepared=True),
        invoke=lambda task: SimpleNamespace(execution_latency_ms=999, confidence=0.9),
        collect_telemetry=dict,
        backend_id=lambda: "edge-backend",
        reset=lambda: True,
        recalibrate=lambda: True,
    )
    times = iter([0, 2_000_000, 5_000_000])
    record = execute_once(TrialConfig(mode="direct"), None, adapter, None, clock=lambda: next(times))
    assert record["invocation_ms"] == 2
    assert record["maintenance_ms"] == 3
    assert record["execution_and_maintenance_ms"] == 5
    assert record["backend_reported_latency_proxy_ms"] == 999


@pytest.mark.parametrize("values", [
    {"iterations": 0}, {"iterations": True}, {"rate_hz": 0}, {"rate_hz": float("nan")},
    {"warmup": -1}, {"profile": "unknown"}, {"backend": "http"},
])
def test_invalid_worker_config(values):
    with pytest.raises(ValueError):
        TrialConfig(**values)


def test_power_integrates_only_observed_measurement_interval():
    from evaluation.evaluate_tegra_resources import power_integral

    result = power_integral([
        {"monotonic_ns": 0, "vdd_in_mw": 1000},
        {"monotonic_ns": 2_000_000_000, "vdd_in_mw": 3000},
    ], 500_000_000, 1_500_000_000)
    assert result["platform_energy_j_over_observed_interval"] == 2
    assert result["coverage_fraction"] == 1
    assert power_integral([], 0, 1)["platform_mean_power_w"] is None
    assert power_integral([{"monotonic_ns": 0}, {"monotonic_ns": 1}], 0, 1)["observed_interval_s"] == 0


def test_process_sample_units_and_missing_process(tmp_path):
    import os

    from evaluation.evaluate_tegra_resources import sample_process

    process_dir = tmp_path / "123"
    process_dir.mkdir()
    (process_dir / "status").write_text("Name:\tworker\nVmRSS:\t1024 kB\nVmHWM:\t2048 kB\n")
    fields = ["0"] * 30
    fields[11] = str(os.sysconf("SC_CLK_TCK"))
    fields[12] = str(2 * os.sysconf("SC_CLK_TCK"))
    (process_dir / "stat").write_text("123 (worker with spaces) " + " ".join(fields))
    sample = sample_process(123, "worker", tmp_path)
    assert sample["rss_kib"] == 1024
    assert sample["peak_rss_kib"] == 2048
    assert sample["cpu_time_s"] == 3
    assert sample_process(456, "worker", tmp_path) is None


def test_campaign_provenance_preservation_and_verification(tmp_path, monkeypatch):
    from evaluation import evaluate_tegra_resources as evaluator

    original = tmp_path / "original.csv"
    original.write_text("original")
    monkeypatch.setattr(evaluator, "legacy_manifest", lambda: {"original.csv": evaluator.sha256_file(original)})
    monkeypatch.setattr(evaluator, "provenance", lambda: {"test": True})
    campaign = {"schema_version": 1, "label": "test", "trials": [{"iterations": 3, "warmup": 0, "idle_s": 0.05}]}
    output = tmp_path / "campaign"
    result = evaluator.run_campaign(campaign, output, sensors=False, interval_ms=20)
    assert result["all_trials_passed"]
    assert evaluator.verify_campaign(output)["trial_count"] == 1
    summary = result["trials"][0]["summary"]
    assert summary["latency_p99_ms"] is None
    assert summary["power"]["platform_mean_power_w"] is None
    samples = [json.loads(line) for line in (output / "trial-000" / "process_samples.jsonl").read_text().splitlines()]
    assert {row["role"] for row in samples} == {"worker", "controller"}
    with pytest.raises(FileExistsError):
        evaluator.run_campaign(campaign, output, sensors=False)
    (output / "trial-000" / "requests.csv").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        evaluator.verify_campaign(output)


def test_invalid_campaign_does_not_create_output(tmp_path):
    from evaluation.evaluate_tegra_resources import run_campaign

    with pytest.raises(ValueError):
        run_campaign({"schema_version": 1, "trials": [{"iterations": -1}]}, tmp_path / "bad", sensors=False)
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("backend", ["chemical", "wetware", "http"])
def test_additional_backends_direct_and_orchestrated(tmp_path, monkeypatch, backend):
    from evaluation import evaluate_tegra_resources as evaluator

    monkeypatch.setattr(evaluator, "legacy_manifest", dict)
    monkeypatch.setattr(evaluator, "provenance", dict)
    campaign = {"schema_version": 1, "trials": [
        {"backend": backend, "mode": mode, "iterations": 3, "warmup": 1}
        for mode in ("direct", "orchestrated")
    ]}
    output = tmp_path / backend
    result = evaluator.run_campaign(campaign, output, sensors=False, interval_ms=20)
    assert result["all_trials_passed"], result
    assert evaluator.verify_campaign(output)["trial_count"] == 2
    if backend == "http":
        samples = [json.loads(line) for line in (output / "trial-000" / "process_samples.jsonl").read_text().splitlines()]
        assert "service" in {row["role"] for row in samples}


def test_scheduler_skips_missed_arrivals_without_catchup_burst():
    from evaluation.embedded_worker import scheduled_release

    assert scheduled_release(100, 350, 100) == (300, 2)
    assert scheduled_release(100, 90, 100) == (100, 0)


def test_native_recovery_remains_inside_orchestrator_timing():
    result = SimpleNamespace(
        success=True, invocation=None, telemetry_after={}, failure_reason=None,
        decision=SimpleNamespace(selected_backend_id="edge-backend", used_fallback=False),
        validation_failures=[], recovery_actions=["native recalibration"],
    )
    orchestrator = SimpleNamespace(execute_task=lambda task: result)
    adapter = SimpleNamespace(backend_id=lambda: "edge-backend")
    times = iter([0, 7_000_000, 7_000_000])
    record = execute_once(TrialConfig(maintenance="native"), orchestrator, adapter, None, clock=lambda: next(times))
    assert record["invocation_ms"] == 7
    assert record["maintenance_ms"] == 0
    assert json.loads(record["recovery_actions"]) == ["native recalibration"]


def test_invocation_failure_is_retained_and_maintenance_still_runs():
    calls = []

    def fail(task):
        raise RuntimeError("injected failure")

    adapter = SimpleNamespace(
        prepare=lambda task: SimpleNamespace(prepared=True), invoke=fail,
        backend_id=lambda: "edge-backend",
        reset=lambda: calls.append("reset") or True,
        recalibrate=lambda: calls.append("recalibrate") or False,
    )
    record = execute_once(TrialConfig(mode="direct"), None, adapter, None)
    assert not record["success"]
    assert record["error"] == "RuntimeError: injected failure"
    assert record["maintenance_error"] is not None
    assert calls == ["reset", "recalibrate"]


def test_timeout_keeps_failure_archive_and_reaps_worker(tmp_path, monkeypatch):
    from evaluation import evaluate_tegra_resources as evaluator

    monkeypatch.setattr(evaluator, "legacy_manifest", dict)
    monkeypatch.setattr(evaluator, "provenance", dict)
    campaign = {"schema_version": 1, "trials": [{"idle_s": 60, "iterations": 1}]}
    output = tmp_path / "timeout"
    result = evaluator.run_campaign(campaign, output, sensors=False, interval_ms=20, timeout_s=0.2)
    assert not result["all_trials_passed"]
    assert result["trials"][0]["timed_out"]
    samples = [json.loads(line) for line in (output / "trial-000" / "process_samples.jsonl").read_text().splitlines()]
    workers = {row["pid"] for row in samples if row["role"] == "worker"}
    assert workers
    assert all(not Path(f"/proc/{pid}").exists() for pid in workers)
    assert evaluator.verify_campaign(output)["trial_count"] == 1


def test_setup_error_is_archived(tmp_path, monkeypatch):
    from evaluation import evaluate_tegra_resources as evaluator

    monkeypatch.setattr(evaluator, "legacy_manifest", dict)
    monkeypatch.setattr(evaluator, "provenance", dict)

    def fail(*args, **kwargs):
        raise RuntimeError("injected setup failure")

    monkeypatch.setattr(evaluator, "run_isolated_trial", fail)
    output = tmp_path / "setup-error"
    result = evaluator.run_campaign({"schema_version": 1, "trials": [{}]}, output, sensors=False)
    assert not result["all_trials_passed"]
    assert "injected setup failure" in result["trials"][0]["error"]
    assert evaluator.verify_campaign(output)["trial_count"] == 1


def test_legacy_inventory_never_reads_environment_or_new_campaigns(tmp_path):
    from evaluation.evaluate_tegra_resources import legacy_manifest

    results = tmp_path / "evaluation" / "results"
    results.mkdir(parents=True)
    (results / "june.json").write_text("{}")
    (tmp_path / "recording.h5").write_bytes(b"fixture")
    (tmp_path / ".env").write_text("DO_NOT_ARCHIVE=secret")
    (results / "embedded").mkdir()
    (results / "embedded" / "new.json").write_text("{}")
    assert set(legacy_manifest(tmp_path)) == {"evaluation/results/june.json", "recording.h5"}


def test_missing_tegrastats_fields_are_not_invented():
    from evaluation.evaluate_tegra_resources import parse_tegrastats_line

    row = parse_tegrastats_line("RAM 100/1024MB CPU [10%@729,off] GR3D_FREQ 0%")
    assert row["ram_used_mb"] == 100
    assert row["cpu_mean_percent"] == 10
    assert row["gr3d_percent"] == 0
    assert "vdd_in_mw" not in row


def test_provenance_does_not_capture_arbitrary_environment(monkeypatch):
    from evaluation import evaluate_tegra_resources as evaluator

    monkeypatch.setenv("PRIVATE_API_TOKEN", "never-capture-this")
    monkeypatch.setattr(evaluator, "source_manifest", dict)
    monkeypatch.setattr(evaluator, "command_fact", lambda command: {"value": None})
    assert "never-capture-this" not in json.dumps(evaluator.provenance())


class FakeClock:
    def __init__(self):
        self.now_ns = 0

    def clock(self):
        return self.now_ns

    def wait(self, seconds):
        self.now_ns += round(seconds * 1e9)


def test_duration_schedule_stops_admission_and_waits_to_deadline():
    from evaluation.embedded_worker import measurement_schedule

    clock = FakeClock()
    config = TrialConfig(iterations=None, duration_s=1, rate_hz=10)
    slots = list(measurement_schedule(config, 0, clock.clock, clock.wait))
    assert len(slots) == 10
    assert slots[-1] == (9, 900_000_000, 0)
    assert clock.now_ns == 1_000_000_000


def test_duration_schedule_does_not_catch_up_after_slow_request():
    from evaluation.embedded_worker import measurement_schedule

    clock = FakeClock()
    config = TrialConfig(iterations=None, duration_s=1, rate_hz=10)
    schedule = measurement_schedule(config, 0, clock.clock, clock.wait)
    assert next(schedule) == (0, 0, 0)
    clock.wait(0.35)
    assert next(schedule) == (1, 300_000_000, 2)
    clock.wait(0.7)
    assert list(schedule) == []


def test_duration_schedule_does_not_admit_after_sleep_overshoot():
    from evaluation.embedded_worker import measurement_schedule

    clock = FakeClock()
    config = TrialConfig(iterations=None, duration_s=1, rate_hz=10)
    schedule = measurement_schedule(config, 0, clock.clock, lambda seconds: clock.wait(seconds + 2))
    assert next(schedule) == (0, 0, 0)
    assert list(schedule) == []


@pytest.mark.parametrize("values", [
    {"duration_s": 1}, {"iterations": None}, {"iterations": None, "duration_s": 0},
    {"iterations": None, "duration_s": float("inf")},
])
def test_invalid_duration_configuration(values):
    with pytest.raises(ValueError):
        TrialConfig(**values)


def test_duration_worker_and_idle_phases(tmp_path, monkeypatch):
    from evaluation import evaluate_tegra_resources as evaluator

    monkeypatch.setattr(evaluator, "legacy_manifest", dict)
    monkeypatch.setattr(evaluator, "provenance", dict)
    config = {"iterations": None, "duration_s": 0.15, "rate_hz": 100, "warmup": 0, "baseline_s": 0.05}
    output = tmp_path / "duration"
    result = evaluator.run_campaign({"schema_version": 1, "trials": [config]}, output, sensors=False, interval_ms=20)
    assert result["all_trials_passed"], result
    worker = json.loads((output / "trial-000" / "worker_summary.json").read_text())
    assert worker["measurement_elapsed_s"] >= 0.15
    assert worker["completed_requests"] + worker["skipped_releases"] == worker["planned_releases"] == 15
    assert set(worker["phases_ns"]) == {"ready_idle", "idle_before", "measurement", "idle_after"}
    events = [json.loads(line) for line in (output / "trial-000" / "events.jsonl").read_text().splitlines()]
    ended = next(event for event in events if event["event"] == "measurement_end")
    assert ended["monotonic_ns"] == worker["measurement_finished_ns"]
    assert ended["monotonic_ns"] <= worker["phases_ns"]["idle_after"][0]
    assert evaluator.verify_campaign(output)["trial_count"] == 1
    assert not result["trials"][0]["summary"]["baseline_adjusted_power"]["usable"]


def test_phase_cpu_and_memory_use_only_the_requested_window():
    from evaluation.evaluate_tegra_resources import phase_resources

    samples = [
        {"role": "worker", "monotonic_ns": 0, "rss_kib": 9999, "cpu_time_s": 0},
        {"role": "worker", "monotonic_ns": 1_000_000_000, "rss_kib": 100, "cpu_time_s": 0.2},
        {"role": "worker", "monotonic_ns": 2_000_000_000, "rss_kib": 200, "cpu_time_s": 0.7},
    ]
    phase = phase_resources(samples, 1_000_000_000, 2_000_000_000)["worker"]
    assert phase["rss_mean_kib"] == 150
    assert phase["cpu_percent_one_core"] == pytest.approx(50)


def test_baseline_power_keeps_negative_values_and_requires_coverage():
    from evaluation.evaluate_tegra_resources import baseline_adjusted_power

    phases = {name: {"power": {"coverage_fraction": 1, "platform_mean_power_w": value}}
              for name, value in (("idle_before", 5), ("measurement", 4), ("idle_after", 7))}
    assert baseline_adjusted_power(phases)["active_minus_bracketing_idle_w"] == -2
    phases["measurement"]["power"]["coverage_fraction"] = 0.5
    assert baseline_adjusted_power(phases)["active_minus_bracketing_idle_w"] is None


def test_sampling_off_captures_no_periodic_resource_data(tmp_path):
    from evaluation.evaluate_tegra_resources import run_isolated_trial

    output = tmp_path / "sampling-off"
    result = run_isolated_trial(TrialConfig(iterations=3), output, interval_ms=250, sensors=False, process_sampling=False, timeout_s=60)
    assert result["returncode"] == 0
    assert (output / "process_samples.jsonl").read_text() == ""
    assert not (output / "tegrastats.jsonl").exists()


def test_process_sampling_respects_requested_interval(tmp_path):
    from evaluation.evaluate_tegra_resources import run_isolated_trial

    output = tmp_path / "sampling-on"
    result = run_isolated_trial(TrialConfig(iterations=2, idle_s=0.5), output, interval_ms=100, sensors=False, timeout_s=60)
    assert result["returncode"] == 0
    samples = [json.loads(line) for line in (output / "process_samples.jsonl").read_text().splitlines()]
    timestamps = [row["monotonic_ns"] for row in samples if row["role"] == "worker"]
    assert len(timestamps) >= 2
    assert all(last - first >= 90_000_000 for first, last in pairwise(timestamps))


def test_calibration_rotates_all_orders_with_matching_seeds():
    from evaluation.evaluate_tegra_resources import make_calibration_campaign

    trials = make_calibration_campaign()["trials"]
    assert len(trials) == 18
    assert len({tuple(row["condition"] for row in trials[index:index + 3]) for index in range(0, 18, 3)}) == 6
    for index in range(0, 18, 3):
        assert len({row["seed"] for row in trials[index:index + 3]}) == 1
        assert len({row["pair_id"] for row in trials[index:index + 3]}) == 1


def test_calibration_estimates_use_blocks_and_keep_failures():
    from evaluation.evaluate_tegra_resources import (
        calibration_report,
        make_calibration_campaign,
    )

    campaign = make_calibration_campaign(repetitions=2)
    results = [{"returncode": 0, "summary": {"latency_mean_ms": {"off": 1, "250ms": 1.1, "1000ms": 0.9}[row["condition"]]}} for row in campaign["trials"]]
    report = calibration_report(campaign, results)
    assert report["comparisons"]["250ms"]["mean_latency_difference_percent"]["mean"] == pytest.approx(10)
    assert report["comparisons"]["1000ms"]["mean_latency_difference_percent"]["mean"] == pytest.approx(-10)
    results[0]["returncode"] = 2
    report = calibration_report(campaign, results)
    assert report["comparisons"]["250ms"]["mean_latency_difference_percent"]["paired_blocks"] == 1
    assert report["comparisons"]["250ms"]["excluded_pairs"]


def test_malformed_calibration_fails_before_creating_archive(tmp_path):
    from evaluation.evaluate_tegra_resources import (
        make_calibration_campaign,
        run_campaign,
    )

    campaign = make_calibration_campaign(repetitions=2)
    campaign["trials"][1]["seed"] = 999
    with pytest.raises(ValueError, match="matched"):
        run_campaign(campaign, tmp_path / "invalid", sensors=False)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("schema_version,expected_usable", [(2, True), (3, False)])
def test_disabled_baseline_correction_preserves_old_archive_semantics(tmp_path, schema_version, expected_usable):
    from evaluation.evaluate_tegra_resources import summarize_trial

    worker = {
        "schema_version": schema_version, "config": {"iterations": 1, "baseline_s": 0},
        "completed_requests": 1, "successful_requests": 1, "skipped_releases": 0,
        "measurement_started_ns": 0, "measurement_finished_ns": 1_000_000_000,
        "process_peak_rss_kib": 100,
        "phases_ns": {name: [0, 1_000_000_000] for name in ("idle_before", "measurement", "idle_after")},
    }
    (tmp_path / "worker_summary.json").write_text(json.dumps(worker))
    (tmp_path / "requests.csv").write_text("success,skipped_releases_before,invocation_ms\nTrue,0,1\n")
    (tmp_path / "tegrastats.jsonl").write_text("\n".join(json.dumps({"monotonic_ns": timestamp, "vdd_in_mw": 1000}) for timestamp in (0, 1_000_000_000)))
    result = summarize_trial(tmp_path)
    assert result["baseline_adjusted_power"]["usable"] is expected_usable
    if not expected_usable:
        assert result["baseline_adjusted_power"]["active_minus_bracketing_idle_w"] is None


def test_workshop_campaign_has_balanced_pairs_and_fixed_http_seed():
    from evaluation.workshop_paper import paper_campaign

    campaign = paper_campaign()
    assert len(campaign["trials"]) == 60
    for index in range(0, 60, 2):
        first, second = campaign["trials"][index:index + 2]
        assert first["pair_id"] == second["pair_id"]
        assert {first["mode"], second["mode"]} == {"direct", "orchestrated"}
        assert {key: value for key, value in first.items() if key not in {"mode", "condition"}} == {
            key: value for key, value in second.items() if key not in {"mode", "condition"}
        }
        if first["backend"] == "http":
            assert first["seed"] == 19


def test_manuscript_refuses_unknown_placeholders_and_escapes_pdf_markup():
    from evaluation.workshop_paper import inline_markup, render_manuscript

    assert render_manuscript("Value {{value}}", {"value": "1.234"}) == "Value 1.234"
    with pytest.raises(ValueError, match="Unknown"):
        render_manuscript("{{invented_result}}", {})
    assert inline_markup([{"type": "text", "raw": "A < B & C"}]) == "A &lt; B &amp; C"


def test_publication_path_policy_excludes_secrets_and_local_artifacts():
    from scripts.audit_publication import blocked_path

    for name in (".env", ".env.local", ".ssh/id_ed25519", "_temp/probe.py", ".venv/bin/python", "recording.h5", "private.pem"):
        assert blocked_path(name)
    for name in (".env.example", "docs/embedded-feasibility/paper.pdf", "evaluation/results/embedded/study/requests.csv"):
        assert not blocked_path(name)


def test_only_manifest_verified_simulator_recordings_are_publishable():
    import hashlib

    from scripts.audit_publication import approved_simulator_recording

    name = "evaluation/results/original-paper-20261024/e3-provider/provider-trace/example.h5"
    payload = b"\x89HDF\r\n\x1a\nfixture"
    evidence = {
        "runtime_kind": "sdk_simulator", "evidence_ceiling": "E3", "biological_claim": False,
        "recording_artifact": {"filename": "example.h5", "online_offline_match": True},
    }
    manifest = {"provider-trace/example.h5": hashlib.sha256(payload).hexdigest()}
    assert approved_simulator_recording(name, payload, evidence, manifest)
    assert not approved_simulator_recording("private/example.h5", payload, evidence, manifest)
    assert not approved_simulator_recording(name, payload + b"tampered", evidence, manifest)
    evidence["biological_claim"] = True
    assert not approved_simulator_recording(name, payload, evidence, manifest)


def test_original_reproduction_keeps_upstream_defaults_and_blocks_secrets(tmp_path, monkeypatch):
    from evaluation.reproduce_original import reproduction_environment, stage_command

    monkeypatch.setenv("GEMINI_API_KEY", "never-pass-this")
    monkeypatch.setenv("CL_SDK_REPLAY_PATH", "/private/replay")
    environment = reproduction_environment(tmp_path, "core")
    assert "GEMINI_API_KEY" not in environment
    assert "CL_SDK_REPLAY_PATH" not in environment
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert stage_command("core", tmp_path)[1:] == ["-m", "evaluation.run_all_evaluations"]
    assert stage_command("overhead", tmp_path)[1:] == ["-m", "evaluation.evaluate_overhead"]
    assert stage_command("distributed", tmp_path)[1:] == ["-m", "evaluation.evaluate_distributed_testbed"]
    with pytest.raises(ValueError):
        stage_command("hosted-model", tmp_path)


def test_process_tree_sampler_tracks_child_and_separates_observer():
    import os
    import time

    import psutil

    from evaluation.resource_monitor import ProcessTreeSampler

    process = subprocess.Popen([sys.executable, "-c", "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(child.pid,flush=True); child.wait()"], stdout=subprocess.PIPE, text=True)
    child_pid = int(process.stdout.readline())
    try:
        sampler = ProcessTreeSampler(process.pid, (os.getpid(),))
        first = sampler.sample()
        roles = {row["pid"]: row["role"] for row in first["processes"]}
        assert roles[process.pid] == "root"
        assert roles[child_pid] == "child"
        assert roles[os.getpid()] == "observer"
        assert first["workload_process_count"] == 2
        assert all(row["cpu_percent_one_core"] is None for row in first["processes"])
        time.sleep(0.02)
        second = sampler.sample()
        assert all(row["cpu_percent_one_core"] is not None for row in second["processes"])
        assert second["tree_rss_sum_kib"] == sum(row["rss_kib"] for row in second["processes"] if row["role"] != "observer")
    finally:
        psutil.Process(child_pid).terminate()
        process.wait(timeout=10)


def test_process_tree_sampler_reports_missing_root():
    from evaluation.resource_monitor import ProcessTreeSampler

    snapshot = ProcessTreeSampler(2_000_000_000).sample()
    assert snapshot["workload_process_count"] == 0
    assert snapshot["tree_pss_sum_kib"] is None
    assert snapshot["discovery_errors"]


def test_resource_profile_detects_power_gaps_and_separates_phases(tmp_path):
    from evaluation.resource_monitor import summarize_profile

    (tmp_path / "process_tree.jsonl").write_text("")
    rows = [{"monotonic_ns": value, "vdd_in_mw": 2000, "max_temperature_c": 50, "gr3d_percent": 0} for value in (0, 250_000_000, 2_000_000_000, 2_250_000_000)]
    (tmp_path / "tegrastats.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    summary = summarize_profile(tmp_path, {"command": [0, 2_250_000_000]}, 250)["windows"]["command"]
    assert summary["power_gaps_s"] == [1.75]
    assert summary["power"]["coverage_fraction"] == pytest.approx(0.5 / 2.25)
    assert summary["power"]["platform_energy_j_over_observed_interval"] == 1.0
    assert summary["max_temperature_c"] == 50
    assert summary["observed_tree_pss_sum_max_mib"] is None


def test_tree_sampler_does_not_reattach_to_reused_root_pid(monkeypatch):
    import psutil

    from evaluation.resource_monitor import ProcessTreeSampler

    dead_root = SimpleNamespace(pid=42, create_time=lambda: 100.0, is_running=lambda: False)
    monkeypatch.setattr(psutil, "Process", lambda pid: dead_root)
    sampler = ProcessTreeSampler(42)

    def unexpected_lookup(pid):
        raise AssertionError("Root PID must not be looked up again after its process exits")

    monkeypatch.setattr(psutil, "Process", unexpected_lookup)
    assert sampler.sample()["workload_process_count"] == 0


def test_upstream_sustained_schedule_is_bounded_without_catchup():
    from evaluation.upstream_sustained import scheduled_slots

    clock = FakeClock()
    iterator = scheduled_slots(1, 10, clock.clock, clock.wait)
    assert next(iterator)[0] == 0
    clock.wait(0.35)
    assert next(iterator)[0] == 3
    clock.wait(0.8)
    assert list(iterator) == []


def test_sustained_stage_does_not_modify_original_entry_points(tmp_path):
    from evaluation.reproduce_original import stage_command

    assert stage_command("overhead", tmp_path, duration_s=999)[1:] == ["-m", "evaluation.evaluate_overhead"]
    command = stage_command("sustained", tmp_path, duration_s=2, rate_hz=10)
    assert "upstream_sustained.py" in command[1]
    assert command[command.index("--duration-s") + 1] == "2"
    assert command[command.index("--rate-hz") + 1] == "10"


def test_sustained_schedule_skips_releases_missed_during_sleep():
    from evaluation.upstream_sustained import scheduled_slots

    clock = FakeClock()
    iterator = scheduled_slots(1, 10, clock.clock, lambda seconds: clock.wait(seconds + 0.25))
    assert next(iterator)[0] == 0
    assert next(iterator)[0] == 3
    assert next(iterator)[0] == 6


def test_sustained_report_separates_first_and_last_windows(tmp_path):
    from evaluation.summarize_profiles import sustained_windows

    workload = tmp_path / "workload"
    workload.mkdir()
    (workload / "summary.json").write_text(json.dumps({"started_ns": 0, "finished_ns": 300_000_000_000}))
    rows = [{"processes": [{"role": "root", "monotonic_ns": timestamp, "rss_kib": rss, "pss_kib": rss / 2, "cpu_percent_one_core": cpu}]} for timestamp, rss, cpu in ((1, 1024, 10), (299_000_000_000, 2048, 20))]
    (tmp_path / "process_tree.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    (workload / "requests.csv").write_text("started_ns,invocation_ms\n1,5\n299000000000,9\n")
    windows = sustained_windows(tmp_path)
    assert windows["first"]["rss_mean_mib"] == 1
    assert windows["last"]["rss_mean_mib"] == 2
    assert windows["last"]["invocation_mean_ms"] == 9


def test_upstream_paper_keeps_published_and_both_jetson_runs_separate():
    from evaluation.upstream_paper import latency_rows

    published = {"chemical": 0.361, "wetware": 0.194, "edge": 0.189}
    aggregate = {"results": [{"backend": name, "iterations": 25, "added_overhead_ms": 6} for name in published]}
    standalone = {"results": [{"backend": name, "iterations": 25, "added_overhead_ms": 9} for name in published]}
    rows = latency_rows(published, aggregate, standalone)
    assert rows[0]["published_ms"] == 0.361
    assert rows[0]["aggregate_ms"] == 6
    assert rows[0]["standalone_ms"] == 9
    standalone["results"][0]["iterations"] = 2000
    with pytest.raises(ValueError, match="25 iterations"):
        latency_rows(published, aggregate, standalone)


def test_legacy_paper_preservation_is_non_destructive(tmp_path):
    from evaluation.upstream_paper import preserve_legacy

    (tmp_path / "paper-source.md").write_text("# phys-MCP Baseline")
    (tmp_path / "paper.pdf").write_bytes(b"original")
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures/chart.png").write_bytes(b"chart")
    preserve_legacy(tmp_path)
    (tmp_path / "paper.pdf").write_bytes(b"new draft")
    preserve_legacy(tmp_path)
    assert (tmp_path / "legacy-20261019/paper.pdf").read_bytes() == b"original"


def test_results_export_does_not_write_a_manuscript(tmp_path, monkeypatch):
    from evaluation import upstream_paper

    monkeypatch.setattr(upstream_paper, "collect_data", lambda *args: {"source_manifests": {"test": "hash"}})
    monkeypatch.setattr(upstream_paper, "write_figures", lambda *args: [])
    monkeypatch.setattr(upstream_paper, "tables_and_values", lambda data: ({"comparison_table": [["metric", "value"], ["cost", "1"]]}, {}))
    (tmp_path / "paper.md").write_text("leave this draft alone")
    upstream_paper.export_results(tmp_path, tmp_path, tmp_path)
    assert (tmp_path / "paper.md").read_text() == "leave this draft alone"
    assert not (tmp_path / "paper.pdf").exists()
    assert (tmp_path / "v511-results.json").exists()
    assert (tmp_path / "results-manifest.json").exists()


def test_operating_cycle_deadline_miss_does_not_mean_skipped_arrival():
    from evaluation.upstream_sustained import execute_cycle

    times = iter([0, 109_000_000, 109_000_000, 110_000_000, 111_000_000])
    row = execute_cycle("harness", None, None, None, 0, 100_000_000, clock=lambda: next(times))
    assert row["invocation_ms"] == 109
    assert row["reset_ms"] == row["recalibrate_ms"] == 1
    assert row["cycle_ms"] == 111
    assert row["deadline_missed"]
    assert row["dispatch_lateness_ms"] == 0


def test_operating_cycle_keeps_prepare_and_both_maintenance_failures():
    from evaluation.upstream_sustained import execute_cycle

    calls = []

    def failed_reset():
        calls.append("reset")
        raise RuntimeError("reset failed")

    adapter = SimpleNamespace(
        prepare=lambda task: SimpleNamespace(prepared=False, details="not ready"),
        reset=failed_reset, recalibrate=lambda: calls.append("recalibrate") or False,
        backend_id=lambda: "edge-backend",
    )
    row = execute_cycle("direct", None, adapter, None, 0, 100_000_000)
    assert not row["success"]
    assert row["error_phase"] == "prepare"
    assert row["backend_invoke_ms"] is None
    assert row["reset_error"] == "RuntimeError: reset failed"
    assert not row["maintenance_ok"]
    assert calls == ["reset", "recalibrate"]


def test_interrupted_run_does_not_count_future_slots_as_misses():
    from evaluation.upstream_sustained import arrival_accounting

    result = arrival_accounting(0, 550_000_000, 1_000_000_000, 100_000_000, 5)
    assert result == {"planned_releases": 10, "arrived_releases": 6, "skipped_releases": 1, "unreleased_slots": 4}


def test_retained_counts_never_query_or_copy_history():
    from threading import RLock

    from evaluation.growth_diagnostics import retained_counts

    def forbidden_history():
        raise AssertionError("History must not be queried for diagnostics")

    lifecycle = SimpleNamespace(_lock=RLock(), _states={"edge": 1}, _history=[1, 2, 3], history=forbidden_history)
    leases = SimpleNamespace(_lock=RLock(), _leases_by_resource={}, _resource_by_lease={})
    idempotency = SimpleNamespace(_lock=RLock(), _entries={})
    orchestrator = SimpleNamespace(registry=SimpleNamespace(lifecycle_store=lifecycle, lease_store=leases, idempotency_store=idempotency))
    snapshot = retained_counts(orchestrator)
    assert snapshot["collections"]["lifecycle.history"] == 3
    assert snapshot["collections"]["idempotency.entries"] == 0
    assert retained_counts(None)["control_plane_present"] is False


def test_operating_cycle_failure_is_not_labeled_as_harness():
    from evaluation.upstream_sustained import execute_cycle

    def fail(task):
        raise RuntimeError("injected")

    adapter = SimpleNamespace(reset=lambda: True, recalibrate=lambda: True)
    row = execute_cycle("orchestrated", SimpleNamespace(execute_task=fail), adapter, None, 0, 1)
    assert not row["success"]
    assert row["selected_backend"] is None
    assert row["error_code"] == "RuntimeError"


def test_memory_guards_report_limits_without_treating_growth_as_failure():
    from evaluation.resource_monitor import memory_guard_reason

    assert memory_guard_reason(2 * 1024 ** 3, 200 * 1024) is None
    assert memory_guard_reason(512 * 1024 ** 2, 200 * 1024) == "system_memory_reserve"
    assert memory_guard_reason(2 * 1024 ** 3, 2 * 1024 ** 2) == "workload_resident_memory_limit"


def test_operating_limits_matrix_is_bounded_and_unimplemented_phases_fail():
    from evaluation.operating_limits import trial_specs

    config = json.loads((ROOT / "docs/experiments/operating-limits/plan.json").read_text())
    assert len(trial_specs(config, "attribution")) == 9
    assert len(trial_specs(config, "long-baseline")) == 3
    assert len(trial_specs(config, "sweep")) == 12
    assert sum(row["duration_s"] for row in trial_specs(config, "long-baseline")) == 5400
    assert len(trial_specs(config, "local-control")) == 24
    config["phases"]["local-control"]["implemented"] = False
    with pytest.raises(ValueError, match="not implemented"):
        trial_specs(config, "local-control")


def test_operating_analysis_reports_cycle_quantiles_and_distinct_misses(tmp_path):
    from evaluation.operating_limits import summarize_requests

    workload = tmp_path / "workload"
    workload.mkdir()
    (workload / "summary.json").write_text(json.dumps({
        "mode": "harness", "completed_requests": 2, "successful_requests": 2,
        "skipped_releases": 0, "deadline_misses": 1, "late_dispatches": 0,
        "unreleased_slots": 0, "completed_duration": True, "achieved_requests_per_s": 10,
    }))
    (workload / "requests.csv").write_text("deadline_missed,invocation_ms,cycle_ms\nFalse,1,2\nTrue,109,111\n")
    result = summarize_requests(tmp_path)
    assert result["skipped_releases"] == 0
    assert result["deadline_misses"] == 1
    assert result["metrics"]["cycle_ms"]["median"] == 56.5
    assert result["metrics"]["cycle_ms"]["p95"] is None