from __future__ import annotations

import gc
import json
import math
import pstats
import sys
import tracemalloc
from threading import RLock
from types import SimpleNamespace

import pytest

from evaluation.growth_diagnostics import (
    DiagnosticSession,
    RetainedStateSampler,
    create_diagnostic_session,
)

SECOND = 1_000_000_000


class FakeClock:
    def __init__(self) -> None:
        self.now = 0

    def __call__(self) -> int:
        return self.now

    def at(self, seconds: float) -> None:
        self.now = round(seconds * SECOND)


def fake_orchestrator():
    def forbidden_history():
        raise AssertionError("History must not be queried for diagnostics")

    lifecycle = SimpleNamespace(_lock=RLock(), _states={"edge": 1}, _history=[1, 2, 3], history=forbidden_history)
    leases = SimpleNamespace(_lock=RLock(), _leases_by_resource={}, _resource_by_lease={})
    idempotency = SimpleNamespace(_lock=RLock(), _entries={"a": 1})
    return SimpleNamespace(registry=SimpleNamespace(lifecycle_store=lifecycle, lease_store=leases, idempotency_store=idempotency))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def no_tracemalloc():
    if tracemalloc.is_tracing():
        pytest.skip("tracemalloc already tracing in this interpreter")
    yield
    assert not tracemalloc.is_tracing()


def test_none_kind_creates_nothing(tmp_path):
    assert create_diagnostic_session("none", tmp_path / "diag") is None
    assert not (tmp_path / "diag").exists()
    with pytest.raises(ValueError):
        DiagnosticSession("none", tmp_path / "diag")
    with pytest.raises(ValueError):
        create_diagnostic_session("bogus", tmp_path / "diag")
    assert not (tmp_path / "diag").exists()


@pytest.mark.parametrize("option", ["interval_s", "duration_s", "allocation_checkpoint_s", "profile_window_s"])
@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, True])
def test_intervals_are_validated_before_any_file_is_created(tmp_path, option, value):
    with pytest.raises(ValueError):
        DiagnosticSession("counts", tmp_path / "diag", **{option: value})
    assert not (tmp_path / "diag").exists()


def test_sampler_validates_interval_and_uses_injected_clock(tmp_path):
    with pytest.raises(ValueError):
        RetainedStateSampler(None, tmp_path / "bad.jsonl", interval_s=0)
    assert not (tmp_path / "bad.jsonl").exists()
    clock = FakeClock()
    clock.now = -5 * SECOND
    sampler = RetainedStateSampler(None, tmp_path / "counts.jsonl", clock=clock)
    sampler.sample(0)
    clock.at(4)
    sampler.sample(1)
    sampler.close(2)
    rows = read_jsonl(tmp_path / "counts.jsonl")
    assert [row["monotonic_ns"] for row in rows] == [-5 * SECOND, 4 * SECOND]
    assert rows[0]["collection_duration_ns"] == 0


def test_counts_samples_initial_every_interval_and_final(tmp_path):
    clock = FakeClock()
    session = DiagnosticSession("counts", tmp_path / "diag", fake_orchestrator(), duration_s=30, clock=clock)
    session.start()
    for second in (5, 10, 15, 20, 22):
        clock.at(second)
        session.before_request()
        session.after_request()
    clock.at(23)
    metadata = session.close(5)
    rows = read_jsonl(tmp_path / "diag" / "retained-state.jsonl")
    assert [row["completed_requests"] for row in rows] == [0, 2, 4, 5]
    assert [row["monotonic_ns"] for row in rows] == [0, 10 * SECOND, 20 * SECOND, 23 * SECOND]
    assert rows[-1]["collections"]["lifecycle.history"] == 3
    assert rows[-1]["collections"]["idempotency.entries"] == 1
    assert metadata["intrusive"] is False
    assert metadata["clock"]["name"].endswith("FakeClock") and not metadata["clock"]["is_default_monotonic_ns"]
    assert metadata["instrumentation"]["hook_calls"] == 10
    assert metadata["elapsed_ns"] == 23 * SECOND
    assert sorted(metadata["files"]) == ["diagnostics.json", "retained-state.jsonl"]
    assert read_json(tmp_path / "diag" / "diagnostics.json") == metadata
    assert session.close(5) is metadata


def test_harness_counts_import_no_new_modules(tmp_path):
    before = set(sys.modules)
    with DiagnosticSession("counts", tmp_path / "diag", None, duration_s=1, clock=FakeClock()) as session:
        session.after_request()
    assert set(sys.modules) == before
    row = read_jsonl(tmp_path / "diag" / "retained-state.jsonl")[0]
    assert row["control_plane_present"] is False


def test_output_directory_is_exclusive(tmp_path):
    DiagnosticSession("counts", tmp_path / "diag", clock=FakeClock())
    with pytest.raises(FileExistsError):
        DiagnosticSession("counts", tmp_path / "diag", clock=FakeClock())


def test_hooks_require_running_session(tmp_path):
    session = DiagnosticSession("counts", tmp_path / "diag", clock=FakeClock())
    with pytest.raises(RuntimeError):
        session.before_request()
    session.start()
    with pytest.raises(RuntimeError):
        session.start()
    session.close(0)
    with pytest.raises(RuntimeError):
        session.after_request()


def test_allocation_checkpoints_differences_and_cleanup(tmp_path, no_tracemalloc):
    clock = FakeClock()
    callbacks_before = list(gc.callbacks)
    session = DiagnosticSession("allocation", tmp_path / "diag", duration_s=120, clock=clock)
    session.start()
    assert tracemalloc.is_tracing()
    retained = [bytearray(1024) for _ in range(64)]
    clock.at(30)
    session.after_request()
    assert not (tmp_path / "diag" / "allocation-60s.json").exists()
    gc.collect()
    clock.at(61)
    session.before_request()
    session.after_request()
    clock.at(120)
    metadata = session.close(2)
    assert not tracemalloc.is_tracing()
    assert gc.callbacks == callbacks_before
    out = tmp_path / "diag"
    for label in ("start", "60s", "end"):
        assert isinstance(tracemalloc.Snapshot.load(str(out / f"allocation-{label}.tracemalloc")), tracemalloc.Snapshot)
    report = read_json(out / "allocation-60s.json")
    assert report["compared_to"] == "allocation-start.tracemalloc"
    ours = [row for row in report["top_differences"] if row["file"] == __file__]
    assert ours and ours[0]["size_diff_bytes"] >= 64 * 1024 and ours[0]["count_diff"] >= 64
    assert {"file", "line", "size_bytes", "size_diff_bytes", "count", "count_diff"} <= set(ours[0])
    assert all(row["file"] != tracemalloc.__file__ for row in report["top_differences"])
    assert [checkpoint["label"] for checkpoint in metadata["allocation"]["checkpoints"]] == ["start", "60s", "end"]
    assert metadata["allocation"]["tracemalloc_preexisting"] is False
    assert metadata["intrusive"] is True and metadata["errors"] == []
    events = read_jsonl(out / "gc-events.jsonl")
    assert events and metadata["gc"]["events_written"] == len(events)
    assert {"generation", "collected", "uncollectable", "duration_ns"} <= set(events[0])
    del retained


def test_preexisting_tracemalloc_is_left_running(tmp_path, no_tracemalloc):
    tracemalloc.start()
    try:
        with DiagnosticSession("allocation", tmp_path / "diag", duration_s=1, clock=FakeClock()) as session:
            session.after_request()
        assert tracemalloc.is_tracing()
        metadata = read_json(tmp_path / "diag" / "diagnostics.json")
        assert metadata["allocation"]["tracemalloc_preexisting"] is True
        assert metadata["errors"] == []
    finally:
        tracemalloc.stop()


def work(depth: int) -> int:
    return 1 if depth == 0 else work(depth - 1) + 1


def test_calls_profiles_only_bounded_early_and_late_windows(tmp_path):
    clock = FakeClock()
    callbacks_before = list(gc.callbacks)
    session = DiagnosticSession("calls", tmp_path / "diag", duration_s=20, clock=clock)
    session.start()
    clock.at(1)
    session.before_request()
    work(3)
    session.after_request()
    clock.at(6)
    session.before_request()
    assert sys.getprofile() is None
    clock.at(10)
    session.after_request()
    assert not (tmp_path / "diag" / "calls-late.json").exists()
    clock.at(16)
    session.before_request()
    work(2)
    session.after_request()
    clock.at(18)
    metadata = session.close(3)
    assert sys.getprofile() is None
    assert gc.callbacks == callbacks_before
    out = tmp_path / "diag"
    for label in ("early", "late"):
        stats = pstats.Stats(str(out / f"calls-{label}.pstats"))
        assert any(function[2] == "work" for function in stats.stats)
        report = read_json(out / f"calls-{label}.json")
        assert any(row["function"] == "work" for row in report["top_cumulative"])
    windows = metadata["calls"]["windows"]
    assert [(w["label"], w["start_elapsed_ns"], w["profiled_ns"]) for w in windows] == [("early", 0, 6 * SECOND), ("late", 16 * SECOND, 2 * SECOND)]
    assert metadata["calls"]["late_window_reached"] is True
    assert not any(name.endswith(".tracemalloc") for name in metadata["files"])


def test_calls_late_window_is_bounded_when_run_overruns(tmp_path):
    clock = FakeClock()
    with DiagnosticSession("calls", tmp_path / "diag", duration_s=20, clock=clock) as session:
        clock.at(5)
        session.after_request()
        clock.at(15)
        session.after_request()
        clock.at(21)
        session.after_request()
        assert sys.getprofile() is None
        clock.at(40)
    windows = read_json(tmp_path / "diag" / "diagnostics.json")["calls"]["windows"]
    assert [w["profiled_ns"] for w in windows] == [5 * SECOND, 6 * SECOND]


def test_calls_refuses_to_replace_existing_profiler_and_cleans_up(tmp_path):
    callbacks_before = list(gc.callbacks)

    def other_profiler(frame, event, arg):
        return None

    sys.setprofile(other_profiler)
    try:
        session = DiagnosticSession("calls", tmp_path / "diag", clock=FakeClock())
        with pytest.raises(RuntimeError, match="Another profiler"):
            session.start()
        assert sys.getprofile() is other_profiler
    finally:
        sys.setprofile(None)
    assert gc.callbacks == callbacks_before


def test_cleanup_runs_when_request_body_raises(tmp_path, no_tracemalloc):
    callbacks_before = list(gc.callbacks)
    with pytest.raises(KeyError), DiagnosticSession("allocation", tmp_path / "diag", duration_s=1, clock=FakeClock()):
        raise KeyError("request failed")
    assert gc.callbacks == callbacks_before
    assert read_json(tmp_path / "diag" / "diagnostics.json")["errors"] == []
