"""Parent-monitor guards: option wiring, worker windows, cleanup and partial archives (fakes, no sensors)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest

import evaluation.evaluate_tegra_resources as tegra
import evaluation.reproduce_original as ro
import evaluation.resource_monitor as rm

SPAWNER = r"""
import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
pathlib.Path(sys.argv[1] + ".tmp").write_text(str(child.pid))
pathlib.Path(sys.argv[1] + ".tmp").rename(sys.argv[1])
time.sleep(float(sys.argv[2]))
"""

WORKER = r"""
import json, pathlib, sys, time
out = pathlib.Path(sys.argv[1])
out.mkdir()
ready = time.monotonic_ns(); time.sleep(0.05)
started = time.monotonic_ns(); time.sleep(0.15); finished = time.monotonic_ns()
post = time.monotonic_ns(); time.sleep(0.1); post_end = time.monotonic_ns()
summary = {"schema_version": 3, "ready_ns": ready, "started_ns": started, "finished_ns": finished,
           "post_idle_started_ns": post, "post_idle_finished_ns": post_end}
(out / "summary.json").write_text(json.dumps(summary))
sys.exit(3)
"""


class FakeTegraStats:
    def __init__(self, interval_ms, path):
        self.path, self.pid, self.handle = path, os.getpid(), None

    def _write(self):
        sample = {"monotonic_ns": time.monotonic_ns(), "vdd_in_mw": 5000, "max_temperature_c": 40.0}
        self.handle.write(json.dumps(sample) + "\n")
        self.handle.flush()
        return sample

    def __enter__(self):
        self.handle = self.path.open("x", encoding="utf-8")
        self._write()
        return self

    def __exit__(self, *exc_info):
        self._write()
        self.handle.close()

    @property
    def latest_sample(self):
        return self._write()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    monkeypatch.setattr(ro, "git_fact", lambda upstream, *args: ro.PINNED_COMMIT if args[0] == "rev-parse" else "")
    monkeypatch.setattr(ro, "source_hashes", lambda upstream: {"core/x.py": "0" * 64})
    monkeypatch.setattr(ro, "_query", lambda command: {"unavailable": "test"})
    monkeypatch.setattr(ro.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(tegra, "TegraStatsSampler", FakeTegraStats)
    monkeypatch.setattr(rm, "thermal_guard_limit", lambda: (80.0, []))
    monkeypatch.setattr(rm, "memory_guard_reason", lambda *args, **kwargs: None)
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def worker(script, *args):
        monkeypatch.setattr(ro, "stage_command", lambda *a, **k: [sys.executable, "-c", script, *map(str, args)])

    return upstream, worker


def _gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _sealed(folder):
    manifest = json.loads((folder / "reproduction-manifest.json").read_text())
    assert "result.json" in manifest and "provenance.json" in manifest
    return json.loads((folder / "result.json").read_text())


def test_stage_command_defaults_are_unchanged_and_options_are_appended(tmp_path):
    base = ro.stage_command("sustained", tmp_path, duration_s=2, rate_hz=10, diagnostic_counts=True)
    assert base[-5:] == ["--mode", "orchestrated", "--idle-s", "3", "--diagnostic-counts"]
    extended = ro.stage_command("sustained", tmp_path, duration_s=2, rate_hz=10, diagnostic_counts=True, intrusive_profile="calls", post_idle_s=60)
    assert extended == [*base, "--intrusive-profile", "calls", "--post-idle-s", "60"]
    assert ro.stage_command("core", tmp_path)[1:] == ["-m", "evaluation.run_all_evaluations"]
    with pytest.raises(ValueError, match="only to the sustained"):
        ro.stage_command("core", tmp_path, intrusive_profile="allocation")
    with pytest.raises(ValueError, match="only to the sustained"):
        ro.stage_command("overhead", tmp_path, post_idle_s=5)


@pytest.mark.parametrize("options", [{"intrusive_profile": "bogus"}, {"post_idle_s": -1.0}, {"post_idle_s": float("nan")}])
def test_invalid_worker_options_are_rejected_before_any_output(harness, tmp_path, options):
    upstream, _ = harness
    with pytest.raises(ValueError):
        ro.run_stage(upstream, tmp_path / "run", "sustained", profile=True, duration_s=1, **options)
    assert not (tmp_path / "run").exists()


def test_workload_windows_ignore_missing_inverted_and_outside_bounds():
    command = [100, 1000]
    pre_idle_interrupt = {"ready_ns": 200, "started_ns": None, "finished_ns": None, "post_idle_started_ns": None, "post_idle_finished_ns": None}
    assert ro.workload_windows(pre_idle_interrupt, command) == ({}, {"steady_workload": "missing_bounds", "ready_worker_idle": "missing_bounds"})
    complete = {"ready_ns": 150, "started_ns": 200, "finished_ns": 800, "post_idle_started_ns": 810, "post_idle_finished_ns": 950}
    assert ro.workload_windows(complete, command) == ({"steady_workload": [200, 800], "ready_worker_idle": [150, 200], "post_worker_idle": [810, 950]}, {})
    windows, ignored = ro.workload_windows({**complete, "post_idle_finished_ns": 805, "finished_ns": 2000, "ready_ns": True}, command)
    assert windows == {}
    assert ignored == {"steady_workload": "outside_command", "ready_worker_idle": "missing_bounds", "post_worker_idle": "inverted_bounds"}
    assert ro.workload_windows({**complete, "post_idle_finished_ns": None}, command)[1] == {"post_worker_idle": "missing_bounds"}


def test_profile_summary_skips_invalid_windows_and_truncated_lines(tmp_path):
    rows = [{"monotonic_ns": value, "vdd_in_mw": 1000, "max_temperature_c": 45} for value in (0, 250_000_000, 500_000_000)]
    (tmp_path / "tegrastats.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows) + '{"monotonic_ns": 7')
    summary = rm.summarize_profile(tmp_path, {"command": [0, 500_000_000], "none": [None, 5], "inverted": [9, 3]}, 250)
    assert set(summary["windows"]) == {"command"}
    assert summary["ignored_windows"] == ["inverted", "none"]
    assert summary["rejected_lines"] == {"tegrastats": 1, "process_tree": 0}
    assert summary["windows"]["command"]["power"]["coverage_fraction"] == 1
    assert ro.profile_complete(summary) is False
    assert ro.profile_complete(None) is False


def test_worker_failure_stays_distinct_from_complete_profile_with_post_idle(harness, tmp_path):
    upstream, worker = harness
    folder = tmp_path / "run"
    worker(WORKER, folder / "workload")
    result = ro.run_stage(upstream, folder, "tests", timeout_s=10, profile=True, interval_ms=100, baseline_s=0.05)
    assert result["returncode"] == 3
    assert result["profile_complete"] is True
    assert result["guard_reason"] is None and result["parent_error"] is None
    assert result["process_cleanup"]["root_exited_before_stop"] and not result["process_cleanup"]["group_remaining"]
    assert set(result["windows_ns"]) == {"platform_before", "command", "platform_after", "steady_workload", "ready_worker_idle", "post_worker_idle"}
    assert result["hardware_postflight"]["power_mode"] == {"unavailable": "test"}
    resources = json.loads((folder / "resource-summary.json").read_text())
    assert resources["windows"]["post_worker_idle"]["duration_s"] > 0
    expected_sources = {str(path.relative_to(ro.ROOT)) for path in ro.HARNESS_SOURCES}
    provenance = json.loads((folder / "provenance.json").read_text())
    profiling = json.loads((folder / "profiling.json").read_text())
    assert set(provenance["harness_sources"]) == set(profiling["observer_sources"]) == expected_sources
    assert {"kernel", "mem_total", "cpu_online", "cpufreq", "power_mode"} <= set(provenance["hardware_preflight"])
    assert profiling["baseline_s"] == 0.05
    assert _sealed(folder) == result


def test_monitor_exception_stops_owned_group_and_seals_partial_archive(harness, tmp_path, monkeypatch):
    upstream, worker = harness
    folder, pidfile = tmp_path / "run", tmp_path / "grandchild.pid"
    worker(SPAWNER, pidfile, 30)

    class BrokenTreeSampler(rm.ProcessTreeSampler):
        def sample(self):
            deadline = time.monotonic() + 1.5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise RuntimeError("tree sampler broke")

    monkeypatch.setattr(rm, "ProcessTreeSampler", BrokenTreeSampler)
    with pytest.raises(RuntimeError, match="tree sampler broke"):
        ro.run_stage(upstream, folder, "tests", timeout_s=10, profile=True, interval_ms=100, baseline_s=0)
    result = _sealed(folder)
    assert result["guard_reason"] == "parent_exception"
    assert result["parent_error"] == {"type": "RuntimeError", "message": "tree sampler broke", "phase": "command"}
    assert result["process_cleanup"]["signals"][0] == "SIGTERM" and not result["process_cleanup"]["group_remaining"]
    assert result["returncode"] == -signal.SIGTERM
    assert result["profile_complete"] is False
    assert _gone(int(pidfile.read_text()))


def test_sigint_terminates_worker_keeps_partial_metadata_and_reraises(harness, tmp_path, monkeypatch):
    upstream, worker = harness
    folder, pidfile = tmp_path / "run", tmp_path / "grandchild.pid"
    worker(SPAWNER, pidfile, 30)

    class InterruptedTreeSampler(rm.ProcessTreeSampler):
        def sample(self):
            snapshot = super().sample()
            if pidfile.exists():
                signal.raise_signal(signal.SIGINT)
            return snapshot

    monkeypatch.setattr(rm, "ProcessTreeSampler", InterruptedTreeSampler)
    with pytest.raises(KeyboardInterrupt):
        ro.run_stage(upstream, folder, "tests", timeout_s=10, profile=True, interval_ms=20, baseline_s=0)
    result = _sealed(folder)
    assert result["guard_reason"] == "parent_interrupted"
    assert result["parent_error"]["type"] == "KeyboardInterrupt"
    assert result["hardware_postflight"] is None
    assert not result["process_cleanup"]["group_remaining"]
    assert (folder / "process_tree.jsonl").read_text().strip()
    assert _gone(int(pidfile.read_text()))


def test_unprofiled_timeout_stops_whole_group(harness, tmp_path):
    upstream, worker = harness
    folder, pidfile = tmp_path / "run", tmp_path / "grandchild.pid"
    worker(SPAWNER, pidfile, 30)
    result = ro.run_stage(upstream, folder, "tests", timeout_s=1.0)
    assert result["timed_out"] is True and result["guard_reason"] is None
    assert result["process_cleanup"]["signals"][0] == "SIGTERM"
    assert _gone(int(pidfile.read_text()))
    assert _sealed(folder) == result


def test_normal_exit_terminates_descendants_left_in_owned_group(harness, tmp_path):
    upstream, worker = harness
    folder, pidfile = tmp_path / "run", tmp_path / "grandchild.pid"
    worker(SPAWNER, pidfile, 0)
    result = ro.run_stage(upstream, folder, "tests", timeout_s=10)
    assert result["returncode"] == 0 and result["timed_out"] is False
    cleanup = result["process_cleanup"]
    assert cleanup["root_exited_before_stop"] and cleanup["signals"] == ["SIGTERM"] and not cleanup["group_remaining"]
    assert _gone(int(pidfile.read_text()))


def test_preflight_guard_is_archived_with_reason(harness, tmp_path, monkeypatch):
    upstream, worker = harness
    folder = tmp_path / "run"
    worker("raise SystemExit(0)")
    monkeypatch.setattr(rm, "memory_guard_reason", lambda *args, **kwargs: "system_memory_reserve")
    with pytest.raises(RuntimeError, match="memory reserve"):
        ro.run_stage(upstream, folder, "tests", timeout_s=10, profile=True, interval_ms=100, baseline_s=0)
    result = _sealed(folder)
    assert result["guard_reason"] == "system_memory_reserve"
    assert result["returncode"] is None and result["process_cleanup"] is None
    assert result["parent_error"]["phase"] == "preflight_guard"
    assert set(result["windows_ns"]) == {"platform_before"}


def test_stop_process_group_tolerates_finished_process_and_lookup_race(monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    process.wait(timeout=10)
    assert rm.stop_process_group(process)["signals"] == []
    monkeypatch.setattr(rm, "_group_exists", lambda pgid: True)
    record = rm.stop_process_group(process)
    assert record["signals"] == [] and record["returncode"] == 0
