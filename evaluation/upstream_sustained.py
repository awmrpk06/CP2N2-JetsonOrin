"""Duration-based extension using only the pinned upstream task and runtime."""

from __future__ import annotations

import argparse
import csv
import importlib
import importlib.util
import json
import logging
import math
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

MODES = {"harness", "direct", "orchestrated"}
INTRUSIVE_PROFILES = ("none", "allocation", "calls")
PINNED_COMMIT = "36fbae002b0a9e066416ea9cafbb966a33fe53bb"
# Top-level packages defined by both the pinned upstream and this repository's legacy control plane.
CONTROL_PLANE_PACKAGES = ("adapters", "core", "demos", "descriptors", "twins", "remote")
DIAGNOSTICS_MODULE = "_phys_mcp_growth_diagnostics"
logger = logging.getLogger(__name__)


class FailureLog:
    """Full tracebacks for the first failures of each kind, then decade milestones only, in constant memory."""

    def __init__(self, full_limit: int = 3):
        self.full_limit = full_limit
        self.counts: dict[str, int] = {}

    def report(self, kind: str, message: str) -> None:
        count = self.counts[kind] = self.counts.get(kind, 0) + 1
        if count <= self.full_limit:
            logger.exception("%s (occurrence %d)", message, count)
        elif str(count).rstrip("0") == "1":
            logger.error("%s: %d occurrences so far; further tracebacks suppressed", message, count)


DEFAULT_FAILURE_LOG = FailureLog()


class _Wake(BaseException):
    pass


class GracefulStop:
    """First signal stops at the next cycle boundary and wakes idle waits; a second signal exits immediately."""

    def __init__(self, sleep=time.sleep, clock=time.monotonic_ns, hard_exit=os._exit):
        self._sleep, self._clock, self._hard_exit = sleep, clock, hard_exit
        self.signum = None
        self.requested_ns = None
        self._waiting = False

    def is_set(self) -> bool:
        return self.requested_ns is not None

    def request(self, signum=None) -> None:
        if self.requested_ns is None:
            self.signum = signum
            self.requested_ns = self._clock()

    def reason(self) -> str | None:
        if not self.is_set():
            return None
        if self.signum is None:
            return "stop_requested"
        try:
            return signal.Signals(self.signum).name
        except ValueError:
            return f"signal_{self.signum}"

    def handle(self, signum, frame) -> None:
        if self.is_set():
            os.write(2, b"Second stop signal: exiting immediately without a summary\n")
            self._hard_exit(128 + signum)
            return
        self.request(signum)
        if self._waiting:
            raise _Wake

    def install(self, signals=(signal.SIGTERM, signal.SIGINT)) -> None:
        for signum in signals:
            signal.signal(signum, self.handle)

    def wait(self, seconds: float) -> bool:
        # _Wake is raised at most once (the second signal hard-exits), so the outer handler always catches it.
        try:
            try:
                self._waiting = True
                if not self.is_set():
                    self._sleep(seconds)
            finally:
                self._waiting = False
        except _Wake:
            pass
        self._waiting = False
        return self.is_set()


def execute_cycle(mode, orchestrator, adapter, task, release_ns, deadline_ns, clock=time.monotonic_ns, failure_log=None):
    if mode not in MODES:
        raise ValueError("Unknown workload mode")
    failure_log = DEFAULT_FAILURE_LOG if failure_log is None else failure_log
    record = {
        "mode": mode, "release_ns": release_ns, "completion_deadline_ns": deadline_ns,
        "success": False, "selected_backend": None, "error_code": None, "failure_reason": None,
        "error_phase": None, "maintenance_ok": True, "reset_ok": True, "recalibrate_ok": True,
        "reset_error": None, "recalibrate_error": None, "idempotent_replay": False,
        "prepare_ms": None, "backend_invoke_ms": None, "telemetry_ms": None,
        "direct_path_ms": None, "orchestrated_ms": None,
    }
    result = None
    phase = "execute"
    record["started_ns"] = clock()
    try:
        if mode == "orchestrated":
            result = orchestrator.execute_task(task)
        elif mode == "direct":
            phase = "prepare"
            phase_started = clock()
            preparation = adapter.prepare(task)
            record["prepare_ms"] = (clock() - phase_started) / 1e6
            if preparation.prepared:
                phase = "invoke"
                phase_started = clock()
                adapter.invoke(task)
                record["backend_invoke_ms"] = (clock() - phase_started) / 1e6
                phase = "telemetry"
                phase_started = clock()
                adapter.collect_telemetry()
                record["telemetry_ms"] = (clock() - phase_started) / 1e6
                record["success"] = True
            else:
                record.update(error_phase="prepare", failure_reason=preparation.details)
        else:
            record["success"] = True
    except Exception as exc:  # noqa: BLE001 - backend failures are measured data
        failure_log.report("workload", "Workload execution failed")
        record.update(error_phase=phase, error_code=type(exc).__name__, failure_reason=str(exc))
    record["finished_ns"] = clock()
    record["invocation_ms"] = (record["finished_ns"] - record["started_ns"]) / 1e6
    if mode == "direct":
        record["direct_path_ms"] = record["invocation_ms"]
    elif mode == "orchestrated":
        record["orchestrated_ms"] = record["invocation_ms"]
    reset_started = clock()
    if mode != "harness":
        try:
            record["reset_ok"] = bool(adapter.reset())
        except Exception as exc:  # noqa: BLE001
            failure_log.report("reset", "Workload reset failed")
            record.update(reset_ok=False, reset_error=f"{type(exc).__name__}: {exc}")
    record["reset_finished_ns"] = clock()
    if mode != "harness":
        try:
            record["recalibrate_ok"] = bool(adapter.recalibrate())
        except Exception as exc:  # noqa: BLE001
            failure_log.report("recalibrate", "Workload recalibration failed")
            record.update(recalibrate_ok=False, recalibrate_error=f"{type(exc).__name__}: {exc}")
    record["cycle_finished_ns"] = clock()
    record["reset_ms"] = (record["reset_finished_ns"] - reset_started) / 1e6
    record["recalibrate_ms"] = (record["cycle_finished_ns"] - record["reset_finished_ns"]) / 1e6
    record["maintenance_ms"] = (record["cycle_finished_ns"] - record["finished_ns"]) / 1e6
    record["cycle_ms"] = (record["cycle_finished_ns"] - record["started_ns"]) / 1e6
    record["dispatch_lateness_ms"] = (record["started_ns"] - release_ns) / 1e6
    record["release_to_completion_ms"] = (record["cycle_finished_ns"] - release_ns) / 1e6
    record["deadline_missed"] = record["cycle_finished_ns"] > deadline_ns
    record["maintenance_ok"] = record["reset_ok"] and record["recalibrate_ok"]
    record["report_error"] = None
    if mode == "orchestrated" and result is not None:
        try:
            outcome = {
                "success": result.success, "selected_backend": result.decision.selected_backend_id,
                "error_code": str(result.error.code) if result.error else None,
                "failure_reason": result.failure_reason, "idempotent_replay": result.idempotent_replay,
            }
        except Exception as exc:  # noqa: BLE001
            failure_log.report("result", "Unreadable orchestration result")
            record.update(
                success=False, error_phase="result", error_code=type(exc).__name__, failure_reason=str(exc),
                report_error=f"result: {type(exc).__name__}: {exc}",
            )
        else:
            record.update(outcome)
    elif mode == "direct":
        try:
            record["selected_backend"] = adapter.backend_id()
        except Exception as exc:  # noqa: BLE001
            failure_log.report("backend_id", "Backend identity query failed")
            record["report_error"] = f"backend_id: {type(exc).__name__}: {exc}"
    elif mode == "harness":
        record["selected_backend"] = "harness-noop"
    return record


def validate_options(duration_s, rate_hz, mode, deadline_ms, idle_s, *, post_idle_s=0, intrusive_profile="none"):
    if mode not in MODES:
        raise ValueError("Unknown workload mode")
    if not math.isfinite(duration_s) or duration_s < 1e-9 or not math.isfinite(rate_hz) or not 0 < rate_hz <= 1000:
        raise ValueError("Duration must be positive and rate must be in (0, 1000] Hz")
    if deadline_ms is not None and (not math.isfinite(deadline_ms) or deadline_ms <= 0):
        raise ValueError("Deadline must be finite and positive")
    if not math.isfinite(idle_s) or idle_s < 0:
        raise ValueError("Idle duration must be finite and nonnegative")
    if not math.isfinite(post_idle_s) or post_idle_s < 0:
        raise ValueError("Post-idle duration must be finite and nonnegative")
    if intrusive_profile not in INTRUSIVE_PROFILES:
        raise ValueError("Unknown intrusive profile")


def arrival_accounting(started_ns, finished_ns, deadline_ns, period_ns, completed):
    planned = math.ceil((deadline_ns - started_ns) / period_ns)
    arrived = min(planned, math.ceil(max(0, min(finished_ns, deadline_ns) - started_ns) / period_ns))
    return {"planned_releases": planned, "arrived_releases": arrived, "skipped_releases": max(0, arrived - completed), "unreleased_slots": planned - arrived}


def scheduled_slots(duration_s: float, rate_hz: float, clock=time.monotonic_ns, wait=time.sleep, stop=None):
    """Yield release slots; a set ``stop`` (anything with ``is_set()``) ends admission and skips the tail wait."""
    def halted():
        return stop is not None and stop.is_set()

    started = clock()
    period_ns = round(1e9 / rate_hz)
    planned = math.ceil(round(duration_s * 1e9) / period_ns)
    deadline = started + round(duration_s * 1e9)
    slot = 0
    while slot < planned:
        if halted():
            return
        now = clock()
        if now >= deadline:
            break
        slot = max(slot, (now - started) // period_ns)
        release = started + slot * period_ns
        if release >= deadline:
            break
        if release > now:
            wait((release - now) / 1e9)
        if halted():
            return
        now = clock()
        if now >= deadline:
            break
        slot = max(slot, (now - started) // period_ns)
        release = started + slot * period_ns
        yield slot, release, started, deadline, planned
        slot += 1
    remaining = (deadline - clock()) / 1e9
    if remaining > 0 and not halted():
        wait(remaining)


def _upstream_revision(upstream: Path) -> str:
    revision = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if revision != PINNED_COMMIT:
        raise ValueError("Unexpected upstream revision")
    return revision


def load_growth_diagnostics():
    """Load growth_diagnostics by file location under a private name, independent of sys.path and of script or -m use."""
    module = sys.modules.get(DIAGNOSTICS_MODULE)
    if module is None:
        spec = importlib.util.spec_from_file_location(DIAGNOSTICS_MODULE, Path(__file__).resolve().with_name("growth_diagnostics.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[DIAGNOSTICS_MODULE] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(DIAGNOSTICS_MODULE, None)
            raise
    return module


def foreign_control_plane_modules(root: Path) -> list[str]:
    foreign = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if name.partition(".")[0] in CONTROL_PLANE_PACKAGES and origin and not Path(origin).resolve().is_relative_to(root):
            foreign.append(name)
    return sorted(foreign)


def _require_upstream_origin(root: Path) -> None:
    foreign = foreign_control_plane_modules(root)
    if foreign:
        raise ImportError(f"Control-plane modules loaded from outside the pinned upstream: {', '.join(foreign[:5])}")


def build_control_plane(upstream: Path):
    root = upstream.resolve()
    _require_upstream_origin(root)
    sys.path.insert(0, str(root))
    common = importlib.import_module("demos.common")
    orchestrator = common.build_default_orchestrator()
    task = common.make_edge_task(task_id="embedded-sustained-original-edge")
    adapter = orchestrator.registry.get_adapter("edge-backend")
    _require_upstream_origin(root)
    return orchestrator, adapter, task


class _Tally:
    def __init__(self, expected_backend: str):
        self.expected_backend = expected_backend
        self.completed = self.successes = self.maintenance_failures = self.unexpected_backends = 0
        self.replays = self.deadline_misses = self.late_dispatches = 0
        self.total_latency = self.total_cycle = self.total_logging = 0.0
        self.last_logging_ms = None

    def add(self, record: dict, logging_ms: float) -> None:
        self.completed += 1
        self.successes += bool(record["success"])
        self.maintenance_failures += not record["maintenance_ok"]
        self.unexpected_backends += record["selected_backend"] != self.expected_backend
        self.replays += bool(record["idempotent_replay"])
        self.deadline_misses += bool(record["deadline_missed"])
        self.late_dispatches += record["dispatch_lateness_ms"] > 0
        self.total_latency += record["invocation_ms"]
        self.total_cycle += record["cycle_ms"]
        self.total_logging += logging_ms
        self.last_logging_ms = logging_ms


def _close_all(closers: list, error: BaseException | None) -> None:
    first = None
    for close in reversed(closers):
        try:
            close()
        except Exception as exc:
            if error is None and first is None:
                first = exc
            else:
                logger.exception("Diagnostic cleanup failed after an earlier error")
    if first is not None:
        raise first


def _run_active(state: dict, tally: _Tally, stop, failure_log: FailureLog, options: dict, workload: tuple, output_dir: Path, diagnostics) -> None:
    orchestrator, adapter, task = workload
    closers = []
    error = None
    try:
        sampler = session = None
        if options["diagnostic_counts_active"]:
            sampler = diagnostics.RetainedStateSampler(orchestrator, output_dir / "retained-state.jsonl")
            closers.append(lambda: sampler.close(tally.completed))
            sampler.sample(0)
        if options["intrusive_profile"] != "none":
            session = diagnostics.DiagnosticSession(options["intrusive_profile"], output_dir / "diagnostics", orchestrator, options["duration_s"])
            closers.append(lambda: session.close(tally.completed))
            session.start()
        with (output_dir / "requests.csv").open("x", newline="", encoding="utf-8") as handle:
            writer = None
            next_flush = time.monotonic() + 1
            previous_slot = -1
            state["started_ns"] = time.monotonic_ns()
            state["deadline_ns"] = state["started_ns"] + round(options["duration_s"] * 1e9)
            for slot, release_ns, started_ns, deadline_ns, planned in scheduled_slots(options["duration_s"], options["rate_hz"], wait=stop.wait, stop=stop):
                state.update(started_ns=started_ns, deadline_ns=deadline_ns, planned=planned)
                if session is not None:
                    session.before_request()
                record = execute_cycle(options["mode"], orchestrator, adapter, task, release_ns, release_ns + options["relative_deadline_ns"], failure_log=failure_log)
                if session is not None:
                    session.after_request()
                record.update(slot=slot, skipped_before=slot - previous_slot - 1, previous_row_logging_ms=tally.last_logging_ms)
                logging_started = time.monotonic_ns()
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(record))
                    writer.writeheader()
                writer.writerow(record)
                if time.monotonic() >= next_flush:
                    handle.flush()
                    next_flush = time.monotonic() + 1
                previous_slot = slot
                tally.add(record, (time.monotonic_ns() - logging_started) / 1e6)
                if sampler is not None:
                    sampler.sample(tally.completed)
    except BaseException as exc:
        error = exc
        raise
    finally:
        state["finished_ns"] = time.monotonic_ns()
        _close_all(closers, error)


def _summary(state: dict, tally: _Tally, stop, failure_log: FailureLog, options: dict) -> dict:
    started, finished, deadline = state["started_ns"], state["finished_ns"], state["deadline_ns"]
    planned = state["planned"]
    if started is None or finished is None:
        accounting = {"planned_releases": planned, "arrived_releases": 0, "skipped_releases": 0, "unreleased_slots": planned}
    else:
        # Releases after a stop request are not skipped; they were never admitted.
        end = finished if stop.requested_ns is None or not started <= stop.requested_ns < finished else stop.requested_ns
        accounting = arrival_accounting(started, end, deadline, options["period_ns"], tally.completed)
    completed = tally.completed
    elapsed_ns = finished - started if started is not None and finished is not None else 0
    return {
        "schema_version": 3, "mode": options["mode"], "interruption": state["interruption"],
        "interruption_phase": state["interruption_phase"], "stop_signal": stop.reason(), "stop_requested_ns": stop.requested_ns,
        "fatal_error": state["fatal_error"], "last_phase": state["phase"],
        "diagnostic_counts": options["diagnostic_counts"], "diagnostic_counts_active": options["diagnostic_counts_active"],
        "intrusive_profile": options["intrusive_profile"],
        "completed_duration": state["interruption"] is None and state["fatal_error"] is None and finished is not None and deadline is not None and finished >= deadline,
        "upstream_commit": state["revision"], "duration_s": options["duration_s"], "rate_hz": options["rate_hz"],
        "idle_s": options["idle_s"], "post_idle_s": options["post_idle_s"],
        "started_ns": started, "finished_ns": finished, "ready_ns": state["ready_ns"],
        "post_idle_started_ns": state["post_idle_started_ns"], "post_idle_finished_ns": state["post_idle_finished_ns"],
        "post_idle_completed": state["post_idle_completed"],
        "admission_deadline_ns": deadline, "planned_releases": planned,
        "completed_requests": completed, "successful_requests": tally.successes,
        "missed_releases": accounting["skipped_releases"], **accounting,
        "deadline_misses": tally.deadline_misses, "late_dispatches": tally.late_dispatches,
        "relative_deadline_ms": options["relative_deadline_ns"] / 1e6,
        "deadline_definition": "Experimental release-relative completion deadline, not the TaskRequest's admission budget",
        "maintenance_failures": tally.maintenance_failures,
        "unexpected_backends": tally.unexpected_backends, "idempotent_replays": tally.replays,
        "latency_mean_ms": tally.total_latency / completed if completed else None,
        "cycle_mean_ms": tally.total_cycle / completed if completed else None,
        "row_logging_mean_ms": tally.total_logging / completed if completed else None,
        "final_row_logging_ms": tally.last_logging_ms,
        "achieved_requests_per_s": completed / (elapsed_ns / 1e9) if elapsed_ns > 0 else None,
        "failure_log_counts": dict(failure_log.counts),
        "loaded_modules": sorted(sys.modules),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "scope": "Sustained extension, not the original 25-run overhead protocol. Same original task factory and explicit maintenance; no injected faults or hosted model.",
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(partial, path)


def run(upstream: Path, output_dir: Path, duration_s: float, rate_hz: float, *, mode: str = "orchestrated", deadline_ms: float | None = None, idle_s: float = 3, diagnostic_counts: bool = False, intrusive_profile: str = "none", post_idle_s: float = 0, stop: GracefulStop | None = None) -> dict:
    validate_options(duration_s, rate_hz, mode, deadline_ms, idle_s, post_idle_s=post_idle_s, intrusive_profile=intrusive_profile)
    stop = GracefulStop() if stop is None else stop
    period_ns = round(1e9 / rate_hz)
    options = {
        "mode": mode, "duration_s": duration_s, "rate_hz": rate_hz, "idle_s": idle_s, "post_idle_s": post_idle_s,
        "diagnostic_counts": diagnostic_counts, "diagnostic_counts_active": diagnostic_counts and intrusive_profile == "none",
        "intrusive_profile": intrusive_profile, "period_ns": period_ns,
        "relative_deadline_ns": round(deadline_ms * 1e6) if deadline_ms is not None else period_ns,
    }
    state = {
        "phase": "setup", "revision": None, "ready_ns": None, "started_ns": None, "finished_ns": None, "deadline_ns": None,
        "planned": math.ceil(round(duration_s * 1e9) / period_ns), "post_idle_started_ns": None, "post_idle_finished_ns": None,
        "post_idle_completed": False, "interruption": None, "interruption_phase": None, "fatal_error": None,
    }
    tally = _Tally("harness-noop" if mode == "harness" else "edge-backend")
    failure_log = FailureLog()
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        state["revision"] = _upstream_revision(upstream)
        # Loaded before the upstream path is inserted so no upstream module can shadow its imports.
        diagnostics = load_growth_diagnostics() if diagnostic_counts or intrusive_profile != "none" else None
        orchestrator = adapter = task = None
        if mode != "harness":
            orchestrator, adapter, task = build_control_plane(upstream)
        (output_dir / "task.json").write_text(task.model_dump_json(indent=2) if task is not None else json.dumps({"mode": "harness", "substrate_executed": False}), encoding="utf-8")
        state["phase"] = "pre_idle"
        state["ready_ns"] = time.monotonic_ns()
        if not stop.wait(idle_s):
            state["phase"] = "active"
            _run_active(state, tally, stop, failure_log, options, (orchestrator, adapter, task), output_dir, diagnostics)
        if post_idle_s > 0 and not stop.is_set():
            state["phase"] = "post_idle"
            state["post_idle_started_ns"] = time.monotonic_ns()
            state["post_idle_completed"] = not stop.wait(post_idle_s)
            state["post_idle_finished_ns"] = time.monotonic_ns()
    except (KeyboardInterrupt, SystemExit) as exc:
        state.update(interruption=type(exc).__name__, interruption_phase=state["phase"])
    except BaseException as exc:
        state["fatal_error"] = {"type": type(exc).__name__, "message": str(exc), "phase": state["phase"]}
        raise
    finally:
        if stop.is_set() and state["interruption"] is None:
            state.update(interruption=stop.reason(), interruption_phase=state["phase"])
        summary = _summary(state, tally, stop, failure_log, options)
        try:
            _write_json_atomic(output_dir / "summary.json", summary)
        except Exception:
            if state["fatal_error"] is None:
                raise
            logger.exception("Could not write the partial summary after a fatal error")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=300)
    parser.add_argument("--rate-hz", type=float, default=10)
    parser.add_argument("--mode", choices=sorted(MODES), default="orchestrated")
    parser.add_argument("--deadline-ms", type=float)
    parser.add_argument("--idle-s", type=float, default=3)
    parser.add_argument("--diagnostic-counts", action="store_true")
    parser.add_argument("--intrusive-profile", choices=INTRUSIVE_PROFILES, default="none")
    parser.add_argument("--post-idle-s", type=float, default=0)
    args = parser.parse_args()
    stop = GracefulStop()
    stop.install()
    summary = run(
        args.upstream, args.output_dir, args.duration_s, args.rate_hz, mode=args.mode, deadline_ms=args.deadline_ms,
        idle_s=args.idle_s, diagnostic_counts=args.diagnostic_counts, intrusive_profile=args.intrusive_profile,
        post_idle_s=args.post_idle_s, stop=stop,
    )
    print(json.dumps(summary, indent=2))
    if stop.signum is not None:
        raise SystemExit(128 + stop.signum)
    if not summary["completed_duration"] or not summary["completed_requests"] or summary["successful_requests"] != summary["completed_requests"] or summary["maintenance_failures"] or summary["unexpected_backends"] or summary["idempotent_replays"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()