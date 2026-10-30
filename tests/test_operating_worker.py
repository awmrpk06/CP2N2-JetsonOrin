from __future__ import annotations

import csv
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import upstream_sustained as worker

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "_temp/cp2n2-v5.1.1"
LEGACY_CSV_FIELDS = {
    "mode", "release_ns", "completion_deadline_ns", "success", "selected_backend", "error_code", "failure_reason",
    "error_phase", "maintenance_ok", "reset_ok", "recalibrate_ok", "reset_error", "recalibrate_error",
    "idempotent_replay", "prepare_ms", "backend_invoke_ms", "telemetry_ms", "direct_path_ms", "orchestrated_ms",
    "started_ns", "finished_ns", "invocation_ms", "reset_finished_ns", "cycle_finished_ns", "reset_ms",
    "recalibrate_ms", "maintenance_ms", "cycle_ms", "dispatch_lateness_ms", "release_to_completion_ms",
    "deadline_missed", "slot", "skipped_before", "previous_row_logging_ms",
}


class FakeClock:
    def __init__(self):
        self.now_ns = 0

    def clock(self):
        return self.now_ns

    def wait(self, seconds):
        self.now_ns += round(seconds * 1e9)


@pytest.fixture(autouse=True)
def pinned_revision(monkeypatch):
    monkeypatch.setattr(worker, "_upstream_revision", lambda upstream: worker.PINNED_COMMIT)


@pytest.fixture
def pinned_upstream():
    probe = subprocess.run(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], check=False, capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != worker.PINNED_COMMIT:
        pytest.skip("Pinned upstream checkout is not available")
    return UPSTREAM


def fake_control_plane(monkeypatch, on_invoke=None):
    calls = {"invoke": 0}

    def invoke(task):
        calls["invoke"] += 1
        if on_invoke is not None:
            on_invoke(calls["invoke"])

    adapter = SimpleNamespace(
        prepare=lambda task: SimpleNamespace(prepared=True, details=None), invoke=invoke,
        collect_telemetry=lambda: None, reset=lambda: True, recalibrate=lambda: True, backend_id=lambda: "edge-backend",
    )
    task = SimpleNamespace(model_dump_json=lambda indent: json.dumps({"task_id": "fake"}))
    monkeypatch.setattr(worker, "build_control_plane", lambda upstream: (SimpleNamespace(), adapter, task))
    return calls


def fake_diagnostics(monkeypatch, fail_before_request=None):
    calls = []

    class Session:
        def __init__(self, kind, output_dir, orchestrator, duration_s):
            calls.append(("init", kind, output_dir, orchestrator, duration_s))
            self.requests = 0

        def start(self):
            calls.append(("start",))

        def before_request(self):
            self.requests += 1
            if self.requests == fail_before_request:
                raise RuntimeError("profiler broke")
            calls.append(("before",))

        def after_request(self):
            calls.append(("after",))

        def close(self, completed_requests):
            calls.append(("close", completed_requests))

    def forbidden_sampler(*args, **kwargs):
        raise AssertionError("Retained-state counters must stay off during an intrusive session")

    module = SimpleNamespace(DiagnosticSession=Session, RetainedStateSampler=forbidden_sampler)
    monkeypatch.setitem(sys.modules, worker.DIAGNOSTICS_MODULE, module)
    return calls


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_stop_flag_ends_admission_at_cycle_boundary_without_tail_wait():
    clock = FakeClock()
    stop = threading.Event()
    slots = worker.scheduled_slots(1, 10, clock.clock, clock.wait, stop=stop)
    assert next(slots)[0] == 0
    stop.set()
    assert list(slots) == []
    assert clock.now_ns == 0


def test_stop_during_release_wait_admits_nothing_further():
    clock = FakeClock()
    stop = threading.Event()

    def wait(seconds):
        clock.wait(seconds)
        stop.set()

    slots = worker.scheduled_slots(1, 10, clock.clock, wait, stop=stop)
    assert next(slots)[0] == 0
    assert list(slots) == []
    assert clock.now_ns == 100_000_000


def test_signal_wakes_idle_wait_and_second_signal_hard_exits():
    exits = []
    stop = worker.GracefulStop(sleep=lambda seconds: stop.handle(signal.SIGTERM, None), hard_exit=exits.append)
    assert stop.wait(30) is True
    assert stop.reason() == "SIGTERM"
    assert stop.wait(30) is True
    assert exits == []
    stop.handle(signal.SIGTERM, None)
    assert exits == [128 + signal.SIGTERM]


def test_real_signal_interrupts_sleep_promptly():
    stop = worker.GracefulStop()
    previous = signal.getsignal(signal.SIGUSR1)
    stop.install((signal.SIGUSR1,))
    timer = threading.Timer(0.1, os.kill, (os.getpid(), signal.SIGUSR1))
    try:
        started = time.monotonic()
        timer.start()
        assert stop.wait(20) is True
        assert time.monotonic() - started < 5
    finally:
        timer.cancel()
        signal.signal(signal.SIGUSR1, previous)
    assert stop.reason() == "SIGUSR1"


def test_repeated_failures_log_bounded_tracebacks_in_constant_memory(caplog):
    log = worker.FailureLog(full_limit=2)
    with caplog.at_level(logging.ERROR, logger=worker.logger.name):
        for _ in range(1000):
            try:
                raise RuntimeError("again")
            except RuntimeError:
                log.report("workload", "Workload execution failed")
    records = [record for record in caplog.records if record.name == worker.logger.name]
    assert len(records) == 2 + 3
    assert records[0].exc_info is not None
    assert records[-1].exc_info is None and "1000 occurrences" in records[-1].getMessage()
    assert log.counts == {"workload": 1000}


def test_unreadable_orchestration_result_is_retained_as_failure():
    result = SimpleNamespace(success=True, decision=None, error=None, failure_reason=None, idempotent_replay=False)
    orchestrator = SimpleNamespace(execute_task=lambda task: result)
    adapter = SimpleNamespace(reset=lambda: True, recalibrate=lambda: True)
    row = worker.execute_cycle("orchestrated", orchestrator, adapter, None, 0, 10**9, failure_log=worker.FailureLog())
    assert row["success"] is False
    assert row["error_phase"] == "result"
    assert row["error_code"] == "AttributeError"
    assert row["report_error"].startswith("result: AttributeError")


def test_backend_identity_failure_is_retained_without_crashing():
    def broken_identity():
        raise RuntimeError("identity unavailable")

    adapter = SimpleNamespace(
        prepare=lambda task: SimpleNamespace(prepared=True, details=None), invoke=lambda task: None,
        collect_telemetry=lambda: None, reset=lambda: True, recalibrate=lambda: True, backend_id=broken_identity,
    )
    failure_log = worker.FailureLog()
    row = worker.execute_cycle("direct", None, adapter, None, 0, 10**9, failure_log=failure_log)
    assert row["success"] is True
    assert row["selected_backend"] is None
    assert row["report_error"] == "backend_id: RuntimeError: identity unavailable"
    assert failure_log.counts == {"backend_id": 1}


def test_options_reject_invalid_post_idle_and_profile(tmp_path):
    with pytest.raises(ValueError, match="Post-idle"):
        worker.validate_options(1, 10, "harness", None, 0, post_idle_s=-1)
    with pytest.raises(ValueError, match="intrusive profile"):
        worker.validate_options(1, 10, "harness", None, 0, intrusive_profile="everything")
    assert not (tmp_path / "out").exists()


def test_harness_run_records_post_idle_separately_and_keeps_legacy_fields(tmp_path):
    output = tmp_path / "out"
    summary = worker.run(Path("unused"), output, 0.2, 20, mode="harness", idle_s=0, post_idle_s=0.05)
    rows = read_rows(output / "requests.csv")
    assert summary["completed_duration"] is True
    assert summary["interruption"] is None and summary["fatal_error"] is None
    assert summary["completed_requests"] == len(rows) == summary["successful_requests"]
    assert summary["post_idle_completed"] is True
    assert summary["finished_ns"] >= summary["admission_deadline_ns"]
    assert summary["post_idle_started_ns"] >= summary["finished_ns"]
    assert summary["post_idle_finished_ns"] - summary["post_idle_started_ns"] >= 50_000_000
    assert LEGACY_CSV_FIELDS | {"report_error"} <= set(rows[0])
    assert json.loads((output / "summary.json").read_text()) == json.loads(json.dumps(summary))
    assert not (output / "summary.json.partial").exists()


def test_direct_run_supports_post_idle(tmp_path, monkeypatch):
    calls = fake_control_plane(monkeypatch)
    summary = worker.run(Path("unused"), tmp_path / "out", 0.1, 20, mode="direct", idle_s=0, post_idle_s=0.02)
    assert summary["completed_duration"] is True
    assert summary["post_idle_completed"] is True
    assert summary["unexpected_backends"] == 0
    assert calls["invoke"] == summary["completed_requests"] > 0


def test_stop_during_pre_idle_writes_partial_summary(tmp_path):
    stop = worker.GracefulStop(sleep=lambda seconds: stop.handle(signal.SIGTERM, None), hard_exit=pytest.fail)
    output = tmp_path / "out"
    summary = worker.run(Path("unused"), output, 1, 10, mode="harness", idle_s=30, post_idle_s=5, stop=stop)
    assert summary["interruption"] == "SIGTERM"
    assert summary["interruption_phase"] == "pre_idle"
    assert summary["completed_duration"] is False
    assert summary["completed_requests"] == 0 and summary["skipped_releases"] == 0
    assert summary["unreleased_slots"] == summary["planned_releases"] == 10
    assert summary["started_ns"] is None and summary["post_idle_started_ns"] is None
    assert json.loads((output / "summary.json").read_text())["interruption"] == "SIGTERM"
    assert not (output / "requests.csv").exists()


def test_stop_during_request_finishes_cycle_and_skips_post_idle(tmp_path, monkeypatch):
    stop = worker.GracefulStop(hard_exit=pytest.fail)
    fake_control_plane(monkeypatch, on_invoke=lambda count: count == 3 and stop.request(signal.SIGTERM))
    output = tmp_path / "out"
    summary = worker.run(Path("unused"), output, 2, 20, mode="direct", idle_s=0, post_idle_s=5, stop=stop)
    assert summary["completed_requests"] == summary["successful_requests"] == 3
    assert len(read_rows(output / "requests.csv")) == 3
    assert summary["interruption"] == "SIGTERM" and summary["interruption_phase"] == "active"
    assert summary["completed_duration"] is False
    assert summary["skipped_releases"] == 0
    assert summary["unreleased_slots"] == summary["planned_releases"] - summary["arrived_releases"]
    assert summary["post_idle_started_ns"] is None and summary["post_idle_completed"] is False


def test_setup_failure_records_fatal_error_and_propagates(tmp_path, monkeypatch):
    def broken(upstream):
        raise RuntimeError("control plane unavailable")

    monkeypatch.setattr(worker, "build_control_plane", broken)
    output = tmp_path / "out"
    with pytest.raises(RuntimeError, match="control plane unavailable"):
        worker.run(Path("unused"), output, 1, 10, mode="orchestrated", idle_s=0)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["fatal_error"] == {"type": "RuntimeError", "message": "control plane unavailable", "phase": "setup"}
    assert summary["completed_duration"] is False
    assert summary["interruption"] is None


def test_intrusive_session_wraps_each_request_and_disables_counters(tmp_path, monkeypatch):
    calls = fake_diagnostics(monkeypatch)
    output = tmp_path / "out"
    summary = worker.run(Path("unused"), output, 0.2, 20, mode="harness", idle_s=0, diagnostic_counts=True, intrusive_profile="calls")
    completed = summary["completed_requests"]
    assert calls[0] == ("init", "calls", output / "diagnostics", None, 0.2)
    assert calls[1] == ("start",)
    assert calls[2:-1] == [("before",), ("after",)] * completed
    assert calls[-1] == ("close", completed)
    assert summary["diagnostic_counts"] is True and summary["diagnostic_counts_active"] is False
    assert summary["intrusive_profile"] == "calls"
    assert not (output / "retained-state.jsonl").exists()


def test_session_failure_during_run_is_fatal_but_closes_and_summarizes(tmp_path, monkeypatch):
    calls = fake_diagnostics(monkeypatch, fail_before_request=2)
    output = tmp_path / "out"
    with pytest.raises(RuntimeError, match="profiler broke"):
        worker.run(Path("unused"), output, 1, 20, mode="harness", idle_s=0, intrusive_profile="allocation")
    summary = json.loads((output / "summary.json").read_text())
    assert summary["fatal_error"]["phase"] == "active"
    assert summary["completed_requests"] == 1
    assert calls[-1] == ("close", 1)
    assert len(read_rows(output / "requests.csv")) == 1


def test_harness_counts_use_the_real_sampler(tmp_path):
    output = tmp_path / "out"
    summary = worker.run(Path("unused"), output, 0.1, 20, mode="harness", idle_s=0, diagnostic_counts=True)
    samples = [json.loads(line) for line in (output / "retained-state.jsonl").read_text().splitlines()]
    assert summary["diagnostic_counts_active"] is True
    assert samples[0]["completed_requests"] == 0
    assert samples[-1]["completed_requests"] == summary["completed_requests"]
    assert samples[-1]["control_plane_present"] is False


def test_diagnostics_load_by_file_location_under_private_name(monkeypatch):
    monkeypatch.delitem(sys.modules, worker.DIAGNOSTICS_MODULE, raising=False)
    module = worker.load_growth_diagnostics()
    assert module.__name__ == worker.DIAGNOSTICS_MODULE
    assert Path(module.__file__).resolve() == ROOT / "evaluation/growth_diagnostics.py"
    assert worker.load_growth_diagnostics() is module


def test_legacy_control_plane_modules_are_refused_before_upstream_import(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    monkeypatch.setitem(sys.modules, "core.legacy_probe", SimpleNamespace(__file__=str(tmp_path / "legacy/core/probe.py")))
    monkeypatch.setitem(sys.modules, "demos.upstream_probe", SimpleNamespace(__file__=str(upstream / "demos/probe.py")))
    assert "core.legacy_probe" in worker.foreign_control_plane_modules(upstream)
    assert "demos.upstream_probe" not in worker.foreign_control_plane_modules(upstream)
    path_before = list(sys.path)
    with pytest.raises(ImportError, match="outside the pinned upstream"):
        worker.build_control_plane(upstream)
    assert sys.path == path_before


def test_module_invocation_loads_diagnostics_without_control_plane(tmp_path, pinned_upstream):
    output = tmp_path / "out"
    command = [
        sys.executable, "-m", "evaluation.upstream_sustained", "--upstream", str(pinned_upstream), "--output-dir", str(output),
        "--duration-s", "0.3", "--rate-hz", "10", "--mode", "harness", "--idle-s", "0", "--post-idle-s", "0.1", "--diagnostic-counts",
    ]
    completed = subprocess.run(command, check=False, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert summary["completed_duration"] is True and summary["post_idle_completed"] is True
    assert (output / "retained-state.jsonl").exists()
    assert worker.DIAGNOSTICS_MODULE in summary["loaded_modules"]
    assert "growth_diagnostics" not in summary["loaded_modules"]
    assert not [name for name in summary["loaded_modules"] if name.partition(".")[0] in worker.CONTROL_PLANE_PACKAGES]


def test_script_sigterm_during_idle_exits_with_partial_summary(tmp_path, pinned_upstream):
    output = tmp_path / "out"
    command = [
        sys.executable, str(ROOT / "evaluation/upstream_sustained.py"), "--upstream", str(pinned_upstream), "--output-dir", str(output),
        "--duration-s", "5", "--rate-hz", "10", "--mode", "harness", "--idle-s", "30",
    ]
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        give_up = time.monotonic() + 10
        while not (output / "task.json").exists():
            assert process.poll() is None, process.stderr.read()
            assert time.monotonic() < give_up, "worker never reached its idle phase"
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 128 + signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stderr.close()
    summary = json.loads((output / "summary.json").read_text())
    assert summary["interruption"] == "SIGTERM" and summary["interruption_phase"] == "pre_idle"
    assert summary["completed_duration"] is False and summary["completed_requests"] == 0
