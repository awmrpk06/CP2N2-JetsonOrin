"""Optional retained-state observations and opt-in intrusive diagnostics for the pinned release.

Counts read len() of the pinned control-plane collections under their existing locks. The
allocation and calls kinds are explicitly intrusive (tracemalloc, cProfile, gc callbacks): their
outputs are diagnostics only and never primary timing evidence. Only the standard library is
imported, and profiling modules are imported lazily, so harness runs load no upstream modules.
"""

from __future__ import annotations

import gc
import json
import math
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path

DIAGNOSTIC_KINDS = ("none", "counts", "allocation", "calls")
INTRUSIVE_KINDS = frozenset({"allocation", "calls"})
SCOPE = (
    "Diagnostics only. Allocation and calls kinds are intrusive (tracemalloc, cProfile, gc callbacks); "
    "timings from such runs are not primary evidence."
)


def _positive_seconds(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number of seconds")
    if round(value * 1e9) < 1:
        raise ValueError(f"{name} must be at least one nanosecond")
    return float(value)


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _write_json_exclusive(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def retained_counts(orchestrator) -> dict:
    if orchestrator is None:
        return {"control_plane_present": False, "collections": {}}
    registry = orchestrator.registry
    collections = {}
    stores = (
        ("lifecycle", registry.lifecycle_store, ("_states", "_history")),
        ("leases", registry.lease_store, ("_leases_by_resource", "_resource_by_lease")),
        ("idempotency", registry.idempotency_store, ("_entries",)),
    )
    for name, store, fields in stores:
        with store._lock:
            for field in fields:
                collections[f"{name}.{field.removeprefix('_')}"] = len(getattr(store, field))
    if hasattr(registry.lifecycle_store, "variant_counts"):
        variant = registry.lifecycle_store.variant_counts()
        collections.update({"lifecycle.indexed_correlations": variant["indexed_correlations"], "lifecycle.evicted": variant["evicted"]})
    return {
        "control_plane_present": True, "collections": collections,
        "audit_scope": "No MCP audit surface is called by this workload; other allocation sources require separate allocation profiling.",
        "method": "len() under the existing store locks; no history() query or state copying",
    }


class RetainedStateSampler:
    def __init__(self, orchestrator, output: Path, interval_s: float = 10, *, clock: Callable[[], int] = time.monotonic_ns):
        self.interval_ns = round(_positive_seconds("interval_s", interval_s) * 1e9)
        self.orchestrator = orchestrator
        self.clock = clock
        self.next_sample_ns = None
        self.output = output.open("x", encoding="utf-8")

    def sample(self, completed_requests: int, force: bool = False) -> None:
        started = self.clock()
        if not force and self.next_sample_ns is not None and started < self.next_sample_ns:
            return
        snapshot = retained_counts(self.orchestrator)
        snapshot.update(monotonic_ns=started, completed_requests=completed_requests)
        snapshot["collection_duration_ns"] = self.clock() - started
        self.output.write(json.dumps(snapshot) + "\n")
        self.output.flush()
        self.next_sample_ns = started + self.interval_ns

    def close(self, completed_requests: int) -> None:
        try:
            self.sample(completed_requests, force=True)
        finally:
            self.output.close()


def create_diagnostic_session(kind: str, output_dir: Path, orchestrator=None, duration_s: float = 300, **options) -> DiagnosticSession | None:
    """Return None for kind "none" so that no session, directory, or instrumentation exists."""
    if kind not in DIAGNOSTIC_KINDS:
        raise ValueError(f"Unknown diagnostic kind {kind!r}; expected one of {DIAGNOSTIC_KINDS}")
    if kind == "none":
        return None
    return DiagnosticSession(kind, output_dir, orchestrator, duration_s, **options)


class DiagnosticSession:
    """Opt-in diagnostics around a request loop: start(), before_request(), after_request(), close().

    counts: retained-state.jsonl sampled at start, every interval_s, and at close.
    allocation: tracemalloc raw snapshots (pickle, loadable with tracemalloc.Snapshot.load) and JSON
        top differences against the start snapshot, at start, allocation_checkpoint_s, and close.
    calls: cProfile only during an early and a late window of profile_window_s each.
    Intrusive kinds also stream gc callback events to gc-events.jsonl. All files are created
    exclusively in a new output_dir; diagnostics.json records settings, clock, and instrumentation cost.
    """

    def __init__(
        self, kind: str, output_dir: Path, orchestrator=None, duration_s: float = 300, *,
        interval_s: float = 10, allocation_checkpoint_s: float = 60, profile_window_s: float = 5,
        traceback_frames: int = 1, top_n: int = 25, gc_buffer_limit: int = 10_000,
        clock: Callable[[], int] = time.monotonic_ns,
    ):
        if kind not in DIAGNOSTIC_KINDS[1:]:
            raise ValueError(f"DiagnosticSession kind must be one of {DIAGNOSTIC_KINDS[1:]}; use create_diagnostic_session for 'none'")
        if not callable(clock):
            raise TypeError("clock must be a callable returning integer nanoseconds")
        self.kind = kind
        self.orchestrator = orchestrator
        self.duration_ns = round(_positive_seconds("duration_s", duration_s) * 1e9)
        self.interval_s = _positive_seconds("interval_s", interval_s)
        self.checkpoint_ns = round(_positive_seconds("allocation_checkpoint_s", allocation_checkpoint_s) * 1e9)
        self.window_ns = round(_positive_seconds("profile_window_s", profile_window_s) * 1e9)
        self.settings = {
            "duration_s": duration_s, "interval_s": interval_s, "allocation_checkpoint_s": allocation_checkpoint_s,
            "profile_window_s": profile_window_s,
            "traceback_frames": _positive_int("traceback_frames", traceback_frames),
            "top_n": _positive_int("top_n", top_n), "gc_buffer_limit": _positive_int("gc_buffer_limit", gc_buffer_limit),
        }
        self._checkpoint_label = f"{allocation_checkpoint_s:g}s"
        self._clock = clock
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self._state = "new"
        self._started_ns = None
        self._completed = 0
        self._instrumentation = {"start_ns": 0, "close_ns": 0, "hook_calls": 0, "hook_total_ns": 0, "hook_max_ns": 0}
        self._errors: list[str] = []
        self._metadata = None
        self._sampler = None
        self._tm_preexisting = None
        self._tm_started = False
        self._tm_limit = None
        self._tm_initial = None
        self._tm_initial_totals = None
        self._checkpoints: list[dict] = []
        self._checkpoint_done = False
        self._profiler = None
        self._window = None
        self._windows: list[dict] = []
        self._early_closed = False
        self._late_opened = False
        self._gc_callback = self._on_gc
        self._gc_registered = False
        self._gc_handle = None
        self._gc_pending: list[tuple] = []
        self._gc_start_ns = None
        self._gc_written = 0
        self._gc_dropped = 0
        self._gc_callback_errors = 0

    def __enter__(self) -> DiagnosticSession:  # noqa: PYI034 - typing.Self needs Python 3.11
        self.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close(self._completed)
        return False

    def start(self) -> None:
        if self._state != "new":
            raise RuntimeError("DiagnosticSession can only be started once")
        self._state = "starting"
        started = self._clock()
        self._started_ns = started
        try:
            if self.kind == "counts":
                self._sampler = RetainedStateSampler(self.orchestrator, self.output_dir / "retained-state.jsonl", self.interval_s, clock=self._clock)
                self._sampler.sample(0, force=True)
            else:
                self._gc_handle = (self.output_dir / "gc-events.jsonl").open("x", encoding="utf-8")
                gc.callbacks.append(self._gc_callback)
                self._gc_registered = True
                if self.kind == "allocation":
                    self._start_tracemalloc()
                    self._allocation_checkpoint("start")
                else:
                    self._open_profile_window("early")
        except BaseException:
            self._cleanup()
            self._state = "closed"
            raise
        self._state = "running"
        self._instrumentation["start_ns"] = self._clock() - started

    def before_request(self) -> None:
        self._hook(None)

    def after_request(self, completed_requests: int | None = None) -> None:
        self._completed = self._completed + 1 if completed_requests is None else completed_requests
        self._hook(self._completed)

    def close(self, completed_requests: int) -> dict:
        if self._state == "closed" and self._metadata is not None:
            return self._metadata
        if self._state == "starting":
            raise RuntimeError("DiagnosticSession.start() did not finish")
        started = self._clock()
        self._completed = completed_requests
        try:
            if self._state == "running":
                if self._sampler is not None:
                    self._attempt("counts", lambda: self._sampler.close(completed_requests))
                if self._profiler is not None:
                    self._attempt("calls", self._close_profile_window)
                if self.kind == "allocation":
                    self._attempt("allocation", lambda: self._allocation_checkpoint("end"))
                if self._gc_handle is not None:
                    self._attempt("gc", self._flush_gc_events)
        finally:
            self._cleanup()
            self._state = "closed"
        closed = self._clock()
        self._instrumentation["close_ns"] = closed - started
        self._metadata = self._build_metadata(closed)
        _write_json_exclusive(self.output_dir / "diagnostics.json", self._metadata)
        return self._metadata

    def _attempt(self, step: str, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - cleanup must continue; failures are reported in metadata
            self._errors.append(f"{step}: {type(exc).__name__}: {exc}")

    def _hook(self, completed: int | None) -> None:
        if self._state != "running":
            raise RuntimeError("DiagnosticSession is not running")
        started = self._clock()
        elapsed = started - self._started_ns
        if self._sampler is not None and completed is not None:
            self._sampler.sample(completed)
        if self.kind == "calls":
            self._advance_profile_windows(elapsed)
        if self.kind == "allocation" and not self._checkpoint_done and elapsed >= self.checkpoint_ns:
            self._checkpoint_done = True
            self._allocation_checkpoint(self._checkpoint_label)
        if self._gc_pending:
            self._flush_gc_events()
        cost = self._clock() - started
        self._instrumentation["hook_calls"] += 1
        self._instrumentation["hook_total_ns"] += cost
        self._instrumentation["hook_max_ns"] = max(self._instrumentation["hook_max_ns"], cost)

    def _start_tracemalloc(self) -> None:
        import tracemalloc

        self._tm_preexisting = tracemalloc.is_tracing()
        if not self._tm_preexisting:
            tracemalloc.start(self.settings["traceback_frames"])
            self._tm_started = True
        self._tm_limit = tracemalloc.get_traceback_limit()

    def _allocation_checkpoint(self, label: str) -> None:
        import pickle
        import tracemalloc

        started = self._clock()
        raw = tracemalloc.take_snapshot()
        raw_path = self.output_dir / f"allocation-{label}.tracemalloc"
        with raw_path.open("xb") as handle:
            pickle.dump(raw, handle, pickle.HIGHEST_PROTOCOL)
        # Exclude tracemalloc's and this session's own bookkeeping from the reported differences.
        snapshot = raw.filter_traces((tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, __file__)))
        del raw
        total_size = total_count = 0
        for trace in snapshot.traces:
            total_size += trace.size
            total_count += 1
        top = []
        if self._tm_initial is None:
            self._tm_initial = snapshot
            self._tm_initial_totals = (total_size, total_count)
        else:
            for diff in snapshot.compare_to(self._tm_initial, "lineno")[: self.settings["top_n"]]:
                frame = diff.traceback[0]
                top.append({
                    "file": frame.filename, "line": frame.lineno, "size_bytes": diff.size, "size_diff_bytes": diff.size_diff,
                    "count": diff.count, "count_diff": diff.count_diff,
                })
        del snapshot
        current, peak = tracemalloc.get_traced_memory()
        summary = {
            "label": label, "clock_ns": started, "elapsed_ns": started - self._started_ns, "raw_snapshot": raw_path.name,
            "traced_current_bytes": current, "traced_peak_bytes": peak,
            "tracemalloc_overhead_bytes": tracemalloc.get_tracemalloc_memory(),
            "filtered_total_size_bytes": total_size, "filtered_total_count": total_count,
            "total_size_diff_bytes": total_size - self._tm_initial_totals[0],
            "total_count_diff": total_count - self._tm_initial_totals[1],
        }
        _write_json_exclusive(self.output_dir / f"allocation-{label}.json", {
            **summary, "scope": SCOPE, "traceback_limit": self._tm_limit, "key_type": "lineno",
            "compared_to": "allocation-start.tracemalloc" if label != "start" else None, "top_differences": top,
        })
        summary["instrumentation_ns"] = self._clock() - started
        self._checkpoints.append(summary)

    def _advance_profile_windows(self, elapsed: int) -> None:
        if self._profiler is not None and elapsed - self._window["start_elapsed_ns"] >= self.window_ns:
            self._close_profile_window()
        late_start = max(self.duration_ns - self.window_ns, self.window_ns)
        if self._profiler is None and self._early_closed and not self._late_opened and elapsed >= late_start:
            self._open_profile_window("late")

    def _open_profile_window(self, label: str) -> None:
        import cProfile

        if sys.getprofile() is not None:
            raise RuntimeError("Another profiler is active; refusing to replace it")
        profiler = cProfile.Profile()
        opened = self._clock()
        profiler.enable()
        self._profiler = profiler
        self._late_opened = self._late_opened or label == "late"
        self._window = {"label": label, "start_clock_ns": opened, "start_elapsed_ns": opened - self._started_ns}

    def _close_profile_window(self) -> None:
        import marshal
        import pstats

        profiler, window = self._profiler, self._window
        profiler.disable()
        closed = self._clock()
        self._profiler = self._window = None
        self._early_closed = True
        window.update(end_clock_ns=closed, profiled_ns=closed - window["start_clock_ns"])
        self._windows.append(window)
        profiler.create_stats()
        with (self.output_dir / f"calls-{window['label']}.pstats").open("xb") as handle:
            marshal.dump(profiler.stats, handle)
        stats = pstats.Stats(profiler).sort_stats("cumulative")
        top = []
        for function in stats.fcn_list[: self.settings["top_n"]]:
            primitive, total, own_s, cumulative_s, _callers = stats.stats[function]
            top.append({
                "file": function[0], "line": function[1], "function": function[2], "primitive_calls": primitive,
                "total_calls": total, "own_time_s": own_s, "cumulative_time_s": cumulative_s,
            })
        _write_json_exclusive(self.output_dir / f"calls-{window['label']}.json", {
            **window, "scope": SCOPE, "pstats": f"calls-{window['label']}.pstats", "sort": "cumulative",
            "profiler_timer": "cProfile default timer", "threads": "calling thread only", "top_cumulative": top,
        })
        window["instrumentation_ns"] = self._clock() - closed

    def _on_gc(self, phase: str, info: dict) -> None:
        # Runs inside the collector: no I/O and no references kept to info or tracked objects.
        try:
            now = self._clock()
            if phase == "start":
                self._gc_start_ns = now
            elif len(self._gc_pending) < self.settings["gc_buffer_limit"]:
                duration = now - self._gc_start_ns if self._gc_start_ns is not None else None
                self._gc_pending.append((now, duration, info["generation"], info["collected"], info["uncollectable"]))
                self._gc_start_ns = None
            else:
                self._gc_dropped += 1
                self._gc_start_ns = None
        except Exception:  # noqa: BLE001 - a gc callback must never raise into the collector
            self._gc_callback_errors += 1

    def _flush_gc_events(self) -> None:
        pending, self._gc_pending = self._gc_pending, []
        for clock_ns, duration_ns, generation, collected, uncollectable in pending:
            self._gc_handle.write(json.dumps({
                "clock_ns": clock_ns, "elapsed_ns": clock_ns - self._started_ns, "duration_ns": duration_ns,
                "generation": generation, "collected": collected, "uncollectable": uncollectable,
            }) + "\n")
        self._gc_written += len(pending)
        self._gc_handle.flush()

    def _cleanup(self) -> None:
        if self._profiler is not None:
            self._attempt("cleanup.profiler", self._profiler.disable)
            self._profiler = self._window = None
        if self._gc_registered:
            for index, callback in enumerate(gc.callbacks):
                if callback is self._gc_callback:
                    del gc.callbacks[index]
                    break
            self._gc_registered = False
        self._gc_pending = []
        if self._tm_started:
            import tracemalloc

            if tracemalloc.is_tracing():
                tracemalloc.stop()
            self._tm_started = False
        self._tm_initial = None
        if self._sampler is not None:
            self._attempt("cleanup.counts", self._sampler.output.close)
            self._sampler = None
        if self._gc_handle is not None:
            self._attempt("cleanup.gc", self._gc_handle.close)
            self._gc_handle = None

    def _build_metadata(self, closed: int) -> dict:
        clock = self._clock
        instrumentation = dict(self._instrumentation)
        instrumentation["total_ns"] = instrumentation["start_ns"] + instrumentation["hook_total_ns"] + instrumentation["close_ns"]
        metadata = {
            "schema_version": 1, "kind": self.kind, "intrusive": self.kind in INTRUSIVE_KINDS, "scope": SCOPE,
            "clock": {
                "name": f"{getattr(clock, '__module__', None) or type(clock).__module__}.{getattr(clock, '__qualname__', type(clock).__qualname__)}",
                "is_default_monotonic_ns": clock is time.monotonic_ns, "unit": "ns",
            },
            "python": platform.python_version(), "settings": self.settings,
            "started_clock_ns": self._started_ns, "closed_clock_ns": closed,
            "elapsed_ns": closed - self._started_ns if self._started_ns is not None else None,
            "completed_requests": self._completed, "instrumentation": instrumentation,
            "errors": list(self._errors),
        }
        if self.kind == "counts":
            metadata["counts"] = {"control_plane_present": self.orchestrator is not None, "method": "len() under the existing store locks"}
        if self.kind == "allocation":
            metadata["allocation"] = {
                "tracemalloc_preexisting": self._tm_preexisting, "traceback_limit": self._tm_limit,
                "checkpoints": self._checkpoints,
            }
        if self.kind == "calls":
            late_start_s = max(self.duration_ns - self.window_ns, self.window_ns) / 1e9
            metadata["calls"] = {"windows": self._windows, "late_window_start_s": late_start_s, "late_window_reached": self._late_opened}
        if self.kind in INTRUSIVE_KINDS:
            metadata["gc"] = {
                "events_written": self._gc_written, "dropped": self._gc_dropped, "callback_errors": self._gc_callback_errors,
                "delivery": "buffered in the callback and written outside it by request hooks and close()",
            }
        metadata["files"] = sorted(path.name for path in self.output_dir.iterdir()) + ["diagnostics.json"]
        return metadata


SPIKE_COLUMNS = (
    "spike_window_ms", "gc_collections", "gc_max_generation", "gc_ms", "process_cpu_ms", "off_cpu_ms",
    "voluntary_switches", "involuntary_switches", "minor_faults", "major_faults",
)


class SpikeRecorder:
    """Low-cost per-request attribution data: gc callback timing, process CPU time and rusage deltas.

    Process-wide (not per-thread) values are used because the pinned orchestrator runs adapter phases in
    short-lived worker threads. Every collection is also streamed to gc-events.jsonl outside the callback.
    """

    def __init__(self, output: Path, *, buffer_limit: int = 100_000, clock: Callable[[], int] = time.monotonic_ns):
        import resource
        import threading

        self._resource, self._threading = resource, threading
        self._clock = clock
        self._buffer_limit = _positive_int("buffer_limit", buffer_limit)
        self._main_thread = threading.main_thread().ident
        self._handle = output.open("x", encoding="utf-8")
        self._registered = False
        self._gc_start_ns = None
        self._gc_total_ns = 0
        self._gc_count = 0
        self._max_generation = -1
        self._pending: list[tuple] = []
        self._written = self._dropped = self._callback_errors = 0
        self._overhead_ns = self._overhead_max_ns = self._marks = 0

    def start(self) -> None:
        gc.callbacks.append(self._on_gc)
        self._registered = True
        self._started_ns = self._clock()

    def _on_gc(self, phase: str, info: dict) -> None:
        # Runs inside the collector: no I/O and no references kept to info.
        try:
            now = self._clock()
            if phase == "start":
                self._gc_start_ns = now
                return
            started, self._gc_start_ns = self._gc_start_ns, None
            duration = now - started if started is not None else 0
            self._gc_total_ns += duration
            self._gc_count += 1
            self._max_generation = max(self._max_generation, info["generation"])
            if len(self._pending) < self._buffer_limit:
                self._pending.append((started, now, info["generation"], info["collected"], info["uncollectable"], self._threading.get_ident() == self._main_thread))
            else:
                self._dropped += 1
        except Exception:  # noqa: BLE001 - a gc callback must never raise into the collector
            self._callback_errors += 1

    def begin(self) -> tuple:
        usage = self._resource.getrusage(self._resource.RUSAGE_SELF)
        self._max_generation = -1
        return (self._clock(), time.process_time_ns(), self._gc_total_ns, self._gc_count, usage.ru_nvcsw, usage.ru_nivcsw, usage.ru_minflt, usage.ru_majflt)

    def end(self, mark: tuple) -> dict:
        process_ns, now = time.process_time_ns(), self._clock()
        usage = self._resource.getrusage(self._resource.RUSAGE_SELF)
        wall_ns, cpu_ns = now - mark[0], process_ns - mark[1]
        row = {
            "spike_window_ms": wall_ns / 1e6, "gc_collections": self._gc_count - mark[3],
            "gc_max_generation": self._max_generation if self._gc_count > mark[3] else None,
            "gc_ms": (self._gc_total_ns - mark[2]) / 1e6, "process_cpu_ms": cpu_ns / 1e6, "off_cpu_ms": max(0, wall_ns - cpu_ns) / 1e6,
            "voluntary_switches": usage.ru_nvcsw - mark[4], "involuntary_switches": usage.ru_nivcsw - mark[5],
            "minor_faults": usage.ru_minflt - mark[6], "major_faults": usage.ru_majflt - mark[7],
        }
        self._flush()
        spent = self._clock() - now
        self._overhead_ns += spent
        self._overhead_max_ns = max(self._overhead_max_ns, spent)
        self._marks += 1
        return row

    def _flush(self) -> None:
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        for started, finished, generation, collected, uncollectable, main_thread in pending:
            self._handle.write(json.dumps({
                "start_ns": started, "end_ns": finished, "duration_ns": None if started is None else finished - started,
                "generation": generation, "collected": collected, "uncollectable": uncollectable, "main_thread": main_thread,
            }) + "\n")
        self._written += len(pending)

    def close(self) -> dict:
        if self._registered:
            gc.callbacks.remove(self._on_gc)
            self._registered = False
        try:
            self._flush()
        finally:
            self._handle.close()
        return {
            "file": "gc-events.jsonl", "events_written": self._written, "dropped": self._dropped, "callback_errors": self._callback_errors,
            "collections": self._gc_count, "gc_total_ms": self._gc_total_ns / 1e6, "gc_threshold": list(gc.get_threshold()),
            "gc_stats": gc.get_stats(), "gc_frozen": gc.get_freeze_count(), "marks": self._marks,
            "row_overhead_total_ms": self._overhead_ns / 1e6, "row_overhead_max_ms": self._overhead_max_ns / 1e6,
            "columns": list(SPIKE_COLUMNS),
            "scope": "Process-wide CPU time and rusage over each request window (begin to end); off_cpu_ms = window minus process CPU. "
                     "Collections are timed by gc callbacks in whichever thread triggered them.",
        }
