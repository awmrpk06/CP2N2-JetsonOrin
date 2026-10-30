"""Loopback client-disconnection experiment for Jetson-local CP2N2 control.

Evaluation-layer extension, not an upstream CP2N2 feature: the pinned gateway only forwards
synchronous requests and has no disconnected autonomy. One module provides separate process roles:

  controller  runs one trial: spawns supervisor + proxy, acts as the remote client, writes summary.json
  supervisor  experiment-only local supervisor around the pinned CP2N2 orchestrator (``--backend fake``
              is a pure-Python test double that never imports CP2N2)
  proxy       localhost TCP proxy that cuts only the client-to-supervisor link; the supervisor-to-backend
              path is in-process and never touched, and no host network setting is changed
  matrix      prints the planned 2 modes x 4 scenarios x N repetitions trial list without running it

Owner tokens on loopback are an experiment convention, not authentication. Nothing here claims crash
recovery or durable state: the supervisor ledger is a per-trial JSONL file.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import random
import resource
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCHEMA_VERSION = 1
PINNED_REVISION = "36fbae002b0a9e066416ea9cafbb966a33fe53bb"
MODULE_PATH = Path(__file__).resolve()
ROOT = MODULE_PATH.parents[1]
DEFAULT_UPSTREAM = ROOT / "_temp" / "cp2n2-v5.1.1"
DEFAULT_PINNED_PYTHON = ROOT / "_temp" / "cp2n2-python312" / "bin" / "python"
MAX_STEPS = 900
MAX_PAGE = 100
PRIMARY_ID = "edge-primary"
FALLBACK_ID = "edge-fallback"
FAULT_MESSAGE = "local-control experiment: injected one-shot primary preparation failure"
MODES = ("local", "client")
SCENARIOS = {
    "connected-nofault": (False, False),
    "connected-fault": (False, True),
    "partition-nofault": (True, False),
    "partition-fault": (True, True),
}
TERMINAL_JOB_STATES = {"completed", "aborted", "failed"}
LINK_ERRORS = (OSError, http.client.HTTPException, ValueError)


@dataclass(frozen=True)
class TrialConfig:
    """One trial. Defaults are the frozen 90 s protocol; tests shorten durations and boundaries."""

    mode: str
    partition: bool
    fault: bool
    repetition: int = 1
    duration_s: float = 90.0
    rate_hz: float = 10.0
    partition_start_s: float = 30.0
    partition_end_s: float = 60.0
    fault_at_s: float = 45.0
    accept_deadline_s: float = 30.0
    poll_interval_s: float = 1.0
    start_lead_s: float = 0.3
    ready_timeout_s: float = 60.0
    finalize_timeout_s: float = 30.0
    min_local_partition_ratio: float = 0.9
    reconnect_boundary_misses: int = 2
    owner: str = "campaign-client"

    @property
    def scenario(self) -> str:
        return f"{'partition' if self.partition else 'connected'}-{'fault' if self.fault else 'nofault'}"

    @property
    def trial_id(self) -> str:
        return f"lc-r{self.repetition:02d}-{self.mode}-{self.scenario}"

    @property
    def steps(self) -> int:
        return round(self.duration_s * self.rate_hz)

    @property
    def period_ns(self) -> int:
        return round(1e9 / self.rate_hz)

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        values = (self.duration_s, self.rate_hz, self.partition_start_s, self.partition_end_s, self.fault_at_s, self.accept_deadline_s, self.poll_interval_s)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("timing values must be finite")
        if not 0 < self.rate_hz <= 1000 or self.duration_s <= 0:
            raise ValueError("rate must be in (0, 1000] Hz and duration positive")
        if not 1 <= self.steps <= MAX_STEPS:
            raise ValueError(f"duration x rate must give 1..{MAX_STEPS} steps")
        if not 0 < self.partition_start_s < self.partition_end_s < self.duration_s:
            raise ValueError("need 0 < partition_start < partition_end < duration")
        if not 0 < self.accept_deadline_s <= self.partition_start_s:
            raise ValueError("job acceptance deadline must precede the partition window")
        if not 0 <= self.fault_at_s < self.duration_s:
            raise ValueError("fault time must fall inside the trial")
        if self.poll_interval_s <= 0 or self.start_lead_s <= 0 or not 0 < self.min_local_partition_ratio <= 1:
            raise ValueError("poll interval, start lead and partition ratio must be positive")


def build_matrix(repetitions: int = 3, *, seed: int = 20261029, **overrides) -> list[TrialConfig]:
    """Planned matrix: modes x scenarios x repetitions, order shuffled per repetition block with a frozen seed."""
    trials = []
    for repetition in range(1, repetitions + 1):
        block = [TrialConfig(mode=mode, partition=partition, fault=fault, repetition=repetition, **overrides) for mode in MODES for partition, fault in SCENARIOS.values()]
        random.Random(f"{seed}:{repetition}").shuffle(block)
        trials.extend(block)
    return trials


def trial_command(config: TrialConfig, output_dir: Path, *, backend: str = "pinned", upstream: Path = DEFAULT_UPSTREAM, supervisor_python: Path | None = None, python: str = sys.executable) -> list[str]:
    """Exact controller command for one trial, for use by a sequential campaign runner."""
    command = [python, str(MODULE_PATH), "controller", "--mode", config.mode, "--partition" if config.partition else "--no-partition", "--fault" if config.fault else "--no-fault", "--output-dir", str(output_dir), "--backend", backend, "--upstream", str(upstream)]
    if supervisor_python is not None:
        command += ["--supervisor-python", str(supervisor_python)]
    defaults = TrialConfig(mode=config.mode, partition=config.partition, fault=config.fault)
    for name, value in asdict(config).items():
        if name not in {"mode", "partition", "fault"} and value != getattr(defaults, name):
            command += [f"--{name.replace('_', '-')}", str(value)]
    return command


def make_plan(config: TrialConfig, job_id: str, t0_ns: int) -> dict:
    """Bounded pre-authorized job plan; both modes accept the identical plan except for ``mode``."""
    return {
        "schema": "local-control-plan/1", "job_id": job_id, "owner": config.owner, "mode": config.mode,
        "steps": config.steps, "rate_hz": config.rate_hz, "t0_ns": t0_ns,
        "task_factory": "demos.common.make_edge_task", "selection_policy": "static_priority",
        "backend_priority": [PRIMARY_ID, FALLBACK_ID],
    }


def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def step_task_id(job_id: str, step: int) -> str:
    return f"{job_id}:step:{step:04d}"


def step_idempotency_key(job_id: str, step: int) -> str:
    """Native CP2N2 idempotency key; the store scopes it by ``client_id`` (the job owner)."""
    return f"{job_id}/step/{step:04d}"


class JsonlWriter:
    """Thread-safe, append-only, flushed JSONL stream; returns each record's byte offset."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._handle = path.open("xb")

    def write(self, record: dict) -> int:
        line = (json.dumps(record, sort_keys=True, default=str) + "\n").encode()
        with self._lock:
            offset = self._handle.tell()
            self._handle.write(line)
            self._handle.flush()
            return offset

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def _value(item):
    return None if item is None else str(getattr(item, "value", item))


# --------------------------------------------------------------------------- backends


class FakeBackend:
    """Deterministic test double with owner-scoped idempotency, static priority and a one-shot prepare fault."""

    kind = "fake"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idempotency: dict[tuple[str, str], dict] = {}
        self._invocations: dict[str, int] = {}
        self._backend_invocations = {PRIMARY_ID: 0, FALLBACK_ID: 0}
        self._armed = False
        self.fault = {"armed_ns": None, "consumed_ns": None, "consumed_task_id": None}

    def provenance(self) -> dict:
        return {"backend_kind": self.kind, "cp2n2_imported": False, "note": "pure-Python test double; no CP2N2 code"}

    def arm_fault(self, now_ns: int) -> None:
        with self._lock:
            self._armed = True
            self.fault["armed_ns"] = now_ns

    def execute(self, *, task_id: str, owner: str, key: str, correlation_id: str) -> dict:
        with self._lock:
            cached = self._idempotency.get((owner, key))
            if cached is not None:
                return {**cached, "idempotent_replay": True}
            selected, used_fallback, notes = PRIMARY_ID, False, [f"Selected primary candidate '{PRIMARY_ID}'."]
            if self._armed:
                self._armed = False
                self.fault.update(consumed_ns=time.monotonic_ns(), consumed_task_id=task_id)
                selected, used_fallback = FALLBACK_ID, True
                notes += [f"Preparation failed for '{PRIMARY_ID}': {FAULT_MESSAGE}", f"Trying fallback candidate '{FALLBACK_ID}'."]
            self._invocations[task_id] = self._invocations.get(task_id, 0) + 1
            self._backend_invocations[selected] += 1
            result = {"success": True, "selected_backend": selected, "used_fallback": used_fallback, "idempotent_replay": False, "error_code": None, "failure_reason": None, "correlation_id": correlation_id, "final_lifecycle": "ready", "lease_id": f"fake-lease-{task_id}", "notes": notes}
            self._idempotency[(owner, key)] = result
            return dict(result)

    def invocation_report(self) -> dict:
        with self._lock:
            return {"by_backend": dict(self._backend_invocations), "total": sum(self._backend_invocations.values()), "steps_with_multiple": sorted(task for task, count in self._invocations.items() if count > 1), "native_idempotency_entries": len(self._idempotency)}

    def resource_report(self) -> dict:
        return {backend: {"lifecycle": "ready", "version": None, "lease_id": None} for backend in (PRIMARY_ID, FALLBACK_ID)}


class PinnedBackend:
    """Pinned CP2N2 v5.1.1: native orchestrator, static-priority matcher, FaultInjectingAdapter, idempotency."""

    kind = "pinned"

    def __init__(self, upstream: Path) -> None:
        upstream = upstream.resolve()
        clashes = [name for name in ("core", "adapters", "demos", "descriptors", "twins", "runtimes") if name in sys.modules]
        if clashes:
            raise RuntimeError(f"Refusing to load pinned CP2N2 after modules were already imported: {clashes}")
        sys.path.insert(0, str(upstream))
        import core.orchestrator as orchestrator_module
        from adapters.edge_adapter import EdgeAdapter
        from adapters.fault_injecting_adapter import FaultInjectingAdapter, FaultProfile
        from core.matcher import BackendMatcher
        from core.task_model import TaskRequest
        from demos.common import make_edge_task

        module_file = Path(orchestrator_module.__file__).resolve()
        if upstream not in module_file.parents:
            raise RuntimeError(f"Imported orchestrator {module_file} is not from the pinned checkout {upstream}")
        self._upstream = upstream
        self._module_file = module_file
        self._task_request = TaskRequest
        self._make_edge_task = make_edge_task
        self._fault_profile = FaultProfile
        self._lock = threading.Lock()
        self._invocations: dict[str, int] = {}
        self._backend_invocations = {PRIMARY_ID: 0, FALLBACK_ID: 0}
        self.fault = {"armed_ns": None, "consumed_ns": None, "consumed_task_id": None}
        self.orchestrator = orchestrator_module.CP2N2Orchestrator(matcher=BackendMatcher(static_priority=[PRIMARY_ID, FALLBACK_ID]))
        primary_edge = EdgeAdapter(backend_id=PRIMARY_ID)
        fallback_edge = EdgeAdapter(backend_id=FALLBACK_ID)
        self._primary = FaultInjectingAdapter(primary_edge)
        self.orchestrator.register_adapter(self._primary)
        self.orchestrator.register_adapter(fallback_edge)
        # Instance-level observers only; upstream classes and declarations stay unmodified.
        self._observe_invocations(primary_edge, PRIMARY_ID)
        self._observe_invocations(fallback_edge, FALLBACK_ID)
        self._observe_fault_consumption()

    def _observe_invocations(self, adapter, backend_id: str) -> None:
        original = adapter.invoke

        def invoke(task):
            with self._lock:
                self._invocations[task.task_id] = self._invocations.get(task.task_id, 0) + 1
                self._backend_invocations[backend_id] += 1
            return original(task)

        adapter.invoke = invoke

    def _observe_fault_consumption(self) -> None:
        original = self._primary.prepare

        def prepare(task):
            result = original(task)
            if not result.prepared and result.details == FAULT_MESSAGE:
                with self._lock:
                    if self.fault["consumed_ns"] is None:
                        self.fault.update(consumed_ns=time.monotonic_ns(), consumed_task_id=task.task_id)
            return result

        self._primary.prepare = prepare

    def provenance(self) -> dict:
        return {"backend_kind": self.kind, "cp2n2_imported": True, "upstream": str(self._upstream), "orchestrator_module": str(self._module_file), "python": sys.version, "root_legacy_core_loaded": any(str(ROOT / "core") in str(getattr(module, "__file__", "") or "") for module in list(sys.modules.values()))}

    def arm_fault(self, now_ns: int) -> None:
        self._primary.configure(self._fault_profile(prepare_failure_message=FAULT_MESSAGE, one_shot=True))
        with self._lock:
            self.fault["armed_ns"] = now_ns

    def execute(self, *, task_id: str, owner: str, key: str, correlation_id: str) -> dict:
        base = self._make_edge_task(task_id=task_id).model_dump()
        task = self._task_request.model_validate({**base, "client_id": owner, "idempotency_key": key, "correlation_id": correlation_id, "selection_policy": "static_priority"})
        result = self.orchestrator.execute_task(task)
        return {
            "success": result.success, "selected_backend": result.decision.selected_backend_id,
            "used_fallback": result.decision.used_fallback, "idempotent_replay": result.idempotent_replay,
            "error_code": _value(result.error.code) if result.error else None, "failure_reason": result.failure_reason,
            "correlation_id": result.correlation_id,
            "final_lifecycle": _value(result.final_lifecycle.state) if result.final_lifecycle else None,
            "lease_id": result.lease.lease_id if result.lease else None, "notes": list(result.decision.notes),
        }

    def invocation_report(self) -> dict:
        store = self.orchestrator.registry.idempotency_store
        with self._lock:
            report = {"by_backend": dict(self._backend_invocations), "total": sum(self._backend_invocations.values()), "steps_with_multiple": sorted(task for task, count in self._invocations.items() if count > 1)}
        # Private size read for diagnostics only; the native store retains one entry per step key by design.
        report["native_idempotency_entries"] = len(store._entries) if hasattr(store, "_entries") else None
        return report

    def resource_report(self) -> dict:
        registry = self.orchestrator.registry
        report = {}
        for backend in (PRIMARY_ID, FALLBACK_ID):
            snapshot = registry.lifecycle_store.snapshot(backend)
            lease = registry.lease_store.current(backend)
            report[backend] = {"lifecycle": _value(snapshot.state), "version": snapshot.version, "lease_id": lease.lease_id if lease else None}
        return report


# --------------------------------------------------------------------------- supervisor


class Supervisor:
    """Experiment-only local supervisor: accepted-job dedup, local/client step scheduling, cursor ledger."""

    def __init__(self, backend, output_dir: Path, *, owner: str, token: str, clock=time.monotonic_ns, run_scheduler: bool = True) -> None:
        self.backend = backend
        self.owner = owner
        self._token = token
        self._clock = clock
        self._run_scheduler = run_scheduler
        self.events = JsonlWriter(output_dir / "events.jsonl")
        self.results_path = output_dir / "results.jsonl"
        self._results = JsonlWriter(self.results_path)
        self._state_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._plan: dict | None = None
        self._plan_hash: str | None = None
        self._job_state: str | None = None
        self._accepted_ns: int | None = None
        self._terminal_ns: int | None = None
        self._status: list[dict | str | None] = []
        self._offsets: list[int] = []
        self._trial: dict | None = None
        self.counters = {"duplicate_starts": 0, "rejected_starts": 0, "supervisor_dedup_hits": 0, "in_progress_conflicts": 0, "late_triggers": 0, "early_triggers": 0, "execution_errors": 0, "native_idempotent_replays": 0}

    # ---- harness (direct controller link, never proxied)
    def configure_trial(self, t0_ns: int, fault_at_s: float | None) -> tuple[int, dict]:
        with self._state_lock:
            if self._trial is not None:
                return 409, {"error": "trial_already_configured"}
            self._trial = {"t0_ns": t0_ns, "fault_at_s": fault_at_s}
        self.events.write({"event": "trial_configured", "t0_ns": t0_ns, "fault_at_s": fault_at_s, "now_ns": self._clock()})
        if fault_at_s is not None and self._run_scheduler:
            self._spawn(self._fault_timer, t0_ns + round(fault_at_s * 1e9))
        return 200, {"configured": True}

    def arm_fault_now(self) -> None:
        with self._exec_lock:
            now = self._clock()
            self.backend.arm_fault(now)
        self.events.write({"event": "fault_armed", "armed_ns": now, "kind": "one_shot_prepare_failure", "backend": PRIMARY_ID})

    def _fault_timer(self, arm_ns: int) -> None:
        if self._wait_until(arm_ns):
            self.arm_fault_now()

    # ---- client API (reachable only through the proxy in a trial)
    def _authorized(self, owner: str | None, token: str | None) -> bool:
        return owner == self.owner and token == self._token

    def start_job(self, owner, token, plan, claimed_hash) -> tuple[int, dict]:
        if not self._authorized(owner, token):
            self.counters["rejected_starts"] += 1
            return 403, {"error": "owner_not_authorized"}
        error = self._plan_error(plan, owner)
        if error is None and plan_hash(plan) != claimed_hash:
            error = "plan_hash_mismatch"
        if error is not None:
            self.counters["rejected_starts"] += 1
            return 400, {"error": error}
        with self._state_lock:
            if self._plan is not None:
                if plan["job_id"] == self._plan["job_id"] and claimed_hash == self._plan_hash:
                    self.counters["duplicate_starts"] += 1
                    return 200, {"duplicate": True, "job": self._job_view()}
                self.counters["rejected_starts"] += 1
                return 409, {"error": "another_job_or_plan_already_accepted", "accepted_job_id": self._plan["job_id"]}
            self._plan, self._plan_hash = dict(plan), claimed_hash
            self._status = [None] * plan["steps"]
            self._job_state, self._accepted_ns = "running", self._clock()
            view = self._job_view()
        self.events.write({"event": "job_accepted", "job_id": plan["job_id"], "plan_hash": claimed_hash, "mode": plan["mode"], "accepted_ns": self._accepted_ns})
        if self._run_scheduler:
            self._spawn(self._schedule)
        return 201, {"duplicate": False, "job": view}

    @staticmethod
    def _plan_error(plan, owner) -> str | None:
        if not isinstance(plan, dict) or plan.get("schema") != "local-control-plan/1":
            return "invalid_plan"
        if plan.get("owner") != owner or plan.get("mode") not in MODES:
            return "invalid_plan_owner_or_mode"
        steps, rate = plan.get("steps"), plan.get("rate_hz")
        if not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS or not isinstance(rate, (int, float)) or not 0 < rate <= 1000:
            return "plan_out_of_bounds"
        if not isinstance(plan.get("t0_ns"), int) or not isinstance(plan.get("job_id"), str) or not plan["job_id"]:
            return "invalid_plan_identity"
        return None

    def _job_view(self) -> dict:
        committed = len(self._offsets)
        executed = sum(1 for item in self._status if isinstance(item, dict) and item["status"] == "executed")
        return {"job_id": self._plan["job_id"], "owner": self._plan["owner"], "mode": self._plan["mode"], "plan_hash": self._plan_hash, "state": self._job_state, "steps": self._plan["steps"], "committed": committed, "executed": executed, "missed": sum(1 for item in self._status if isinstance(item, dict) and item["status"] == "missed")}

    def _check_job(self, owner, token, job_id) -> tuple[int, dict] | None:
        if not self._authorized(owner, token):
            return 403, {"error": "owner_not_authorized"}
        if self._plan is None or job_id != self._plan["job_id"]:
            return 404, {"error": "unknown_job"}
        return None

    def job_status(self, owner, token, job_id) -> tuple[int, dict]:
        with self._state_lock:
            return self._check_job(owner, token, job_id) or (200, {"job": self._job_view()})

    def release_ns(self, step: int) -> int:
        return self._plan["t0_ns"] + step * round(1e9 / self._plan["rate_hz"])

    def trigger_step(self, owner, token, job_id, step: int, received_ns: int) -> tuple[int, dict]:
        with self._state_lock:
            rejected = self._check_job(owner, token, job_id)
            if rejected is not None:
                return rejected
            if self._plan["mode"] != "client":
                return 409, {"error": "local_mode_job_steps_are_supervisor_scheduled"}
            if not 0 <= step < self._plan["steps"]:
                return 404, {"error": "unknown_step"}
            current = self._status[step]
            if isinstance(current, dict):
                self.counters["supervisor_dedup_hits"] += 1
                return 200, {"deduplicated": True, "record": current}
            if current == "in_progress":
                self.counters["in_progress_conflicts"] += 1
                return 409, {"error": "step_in_progress", "retryable": True}
            release = self.release_ns(step)
            period = round(1e9 / self._plan["rate_hz"])
            if received_ns < release:
                self.counters["early_triggers"] += 1
                return 425, {"error": "step_window_not_open"}
            if received_ns >= release + period:
                self.counters["late_triggers"] += 1
                return 410, {"error": "step_window_closed", "replay": False}
            self._status[step] = "in_progress"
        return 200, {"deduplicated": False, "record": self._execute(step, "client", received_ns)}

    def _execute(self, step: int, trigger: str, received_ns: int) -> dict:
        job_id, owner = self._plan["job_id"], self._plan["owner"]
        task_id = step_task_id(job_id, step)
        correlation_id = f"{task_id}:{trigger}:{received_ns}"
        with self._exec_lock:
            started = self._clock()
            try:
                outcome = self.backend.execute(task_id=task_id, owner=owner, key=step_idempotency_key(job_id, step), correlation_id=correlation_id)
            except Exception as exc:  # noqa: BLE001 - any backend error is a recorded failed outcome
                self.counters["execution_errors"] += 1
                self.events.write({"event": "execution_error", "step": step, "traceback": traceback.format_exc()})
                outcome = {"success": False, "selected_backend": None, "used_fallback": False, "idempotent_replay": False, "error_code": type(exc).__name__, "failure_reason": str(exc), "correlation_id": correlation_id}
            finished = self._clock()
        self.counters["native_idempotent_replays"] += int(bool(outcome.get("idempotent_replay")))
        record = {"job_id": job_id, "owner": owner, "step": step, "task_id": task_id, "idempotency_key": step_idempotency_key(job_id, step), "status": "executed", "trigger": trigger, "release_ns": self.release_ns(step), "received_ns": received_ns, "started_ns": started, "finished_ns": finished, **outcome}
        return self._commit(step, record)

    def _commit(self, step: int, record: dict) -> dict:
        with self._state_lock:
            record["seq"] = len(self._offsets)
            self._offsets.append(self._results.write(record))
            summary = {"seq": record["seq"], "step": step, "status": record["status"], "success": record.get("success"), "selected_backend": record.get("selected_backend"), "used_fallback": record.get("used_fallback", False)}
            self._status[step] = summary
        return summary

    def _commit_missed(self, step: int, reason: str) -> None:
        with self._state_lock:
            if self._status[step] is not None:
                return
            self._status[step] = "in_progress"
        self._commit(step, {"job_id": self._plan["job_id"], "owner": self._plan["owner"], "step": step, "task_id": step_task_id(self._plan["job_id"], step), "status": "missed", "reason": reason, "release_ns": self.release_ns(step), "success": None, "selected_backend": None, "used_fallback": False})

    def close_window(self, step: int) -> None:
        """Client mode: a step with no in-window trigger is recorded missed; it is never replayed."""
        self._commit_missed(step, "no_trigger_in_window")

    def run_local_step(self, step: int) -> None:
        now = self._clock()
        period = round(1e9 / self._plan["rate_hz"])
        if now >= self.release_ns(step) + period:
            self._commit_missed(step, "late_local_dispatch")
            return
        with self._state_lock:
            if self._status[step] is not None:
                return
            self._status[step] = "in_progress"
        self._execute(step, "local", now)

    def _schedule(self) -> None:
        state = "completed"
        try:
            period = round(1e9 / self._plan["rate_hz"])
            for step in range(self._plan["steps"]):
                if self._plan["mode"] == "local":
                    if not self._wait_until(self.release_ns(step)):
                        state = "aborted"
                        break
                    self.run_local_step(step)
                else:
                    if not self._wait_until(self.release_ns(step) + period):
                        state = "aborted"
                        break
                    self.close_window(step)
        except Exception:  # noqa: BLE001 - recorded as a failed job with traceback
            state = "failed"
            self.events.write({"event": "scheduler_error", "traceback": traceback.format_exc()})
        self.finish_job(state)

    def finish_job(self, state: str) -> None:
        with self._state_lock:
            self._job_state, self._terminal_ns = state, self._clock()
        self.events.write({"event": "job_terminal", "state": state, "terminal_ns": self._terminal_ns})

    def results(self, owner, token, job_id, cursor: int, limit: int) -> tuple[int, dict]:
        with self._state_lock:
            rejected = self._check_job(owner, token, job_id)
            if rejected is not None:
                return rejected
            committed = len(self._offsets)
            if cursor < 0 or not 1 <= limit <= MAX_PAGE:
                return 400, {"error": "invalid_cursor_or_limit", "max_limit": MAX_PAGE}
            if cursor > committed:
                return 409, {"error": "stale_or_future_cursor", "committed": committed}
            count = min(limit, committed - cursor)
            start = self._offsets[cursor] if count else None
            state = self._job_state
        items = []
        if count:
            with self.results_path.open("rb") as handle:
                handle.seek(start)
                items = [json.loads(handle.readline()) for _ in range(count)]
        next_cursor = cursor + count
        return 200, {"items": items, "cursor": cursor, "next_cursor": next_cursor, "committed": committed, "job_state": state, "complete": state in TERMINAL_JOB_STATES and next_cursor == committed}

    # ---- lifecycle
    def _spawn(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _wait_until(self, target_ns: int) -> bool:
        while not self._stop.is_set():
            remaining = target_ns - self._clock()
            if remaining <= 0:
                return True
            self._stop.wait(min(remaining / 1e9, 0.5))
        return False

    def finalize(self, timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._state_lock:
                if self._plan is None or self._job_state in TERMINAL_JOB_STATES:
                    break
            time.sleep(0.02)
        with self._state_lock:
            job = self._job_view() if self._plan is not None else None
            job_extra = {"accepted_ns": self._accepted_ns, "terminal_ns": self._terminal_ns}
        with self._exec_lock:
            fault = dict(self.backend.fault)
            invocations = self.backend.invocation_report()
            resources = self.backend.resource_report()
        return {"job": job, **job_extra, "trial": self._trial, "counters": dict(self.counters), "fault": fault, "invocations": invocations, "resources": resources, "provenance": self.backend.provenance(), "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "retained": {"ledger_offsets": len(self._offsets), "step_status_slots": len(self._status)}}

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self.events.close()
        self._results.close()


class _JsonHandler(BaseHTTPRequestHandler):
    """Minimal JSON request handler; subclasses set ``routes`` and ``on_request``."""

    server_version = "local-control-experiment/1"

    def log_message(self, *args):
        return

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, sort_keys=True, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        received_ns = time.monotonic_ns()
        try:
            status, payload = self.handle_route(method, urlsplit(self.path), received_ns)
        except Exception:  # noqa: BLE001 - logged with traceback and answered as HTTP 500
            self.server.on_error(traceback.format_exc())
            status, payload = 500, {"error": "internal_error"}
        try:
            self._send(status, payload)
        except OSError:
            self.server.on_error("response not delivered: " + traceback.format_exc(limit=1))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


class _ClientApiHandler(_JsonHandler):
    def handle_route(self, method, url, received_ns):
        supervisor: Supervisor = self.server.supervisor
        supervisor.events.write({"event": "client_request", "method": method, "path": url.path, "received_ns": received_ns})
        owner, token = self.headers.get("X-Owner"), self.headers.get("X-Owner-Token")
        parts = [part for part in url.path.split("/") if part]
        if method == "POST" and parts == ["v1", "jobs"]:
            body = self._body()
            return supervisor.start_job(owner, token, body.get("plan"), body.get("plan_hash"))
        if len(parts) >= 3 and parts[:2] == ["v1", "jobs"]:
            job_id = parts[2]
            if method == "GET" and len(parts) == 3:
                return supervisor.job_status(owner, token, job_id)
            if method == "GET" and parts[3:] == ["results"]:
                query = parse_qs(url.query)
                try:
                    cursor, limit = int(query.get("cursor", ["0"])[0]), int(query.get("limit", [str(MAX_PAGE)])[0])
                except ValueError:
                    return 400, {"error": "invalid_cursor_or_limit"}
                return supervisor.results(owner, token, job_id, cursor, limit)
            if method == "POST" and len(parts) == 5 and parts[3] == "steps" and parts[4].isdigit():
                return supervisor.trigger_step(owner, token, job_id, int(parts[4]), received_ns)
        return 404, {"error": "not_found"}


class _HarnessHandler(_JsonHandler):
    def handle_route(self, method, url, received_ns):
        supervisor: Supervisor = self.server.supervisor
        if method == "GET" and url.path == "/harness/health":
            return 200, {"ok": True}
        if method == "POST" and url.path == "/harness/trial":
            body = self._body()
            return supervisor.configure_trial(int(body["t0_ns"]), body.get("fault_at_s"))
        if method == "POST" and url.path == "/harness/finalize":
            report = supervisor.finalize(float(self._body().get("timeout_s", 30)))
            write_json_atomic(self.server.output_dir / "final.json", report)
            return 200, report
        if method == "POST" and url.path == "/harness/shutdown":
            self.server.shutdown_requested.set()
            return 200, {"shutting_down": True}
        return 404, {"error": "not_found"}


def _serve(handler, attributes: dict) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    for name, value in attributes.items():
        setattr(server, name, value)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    return server


def _install_sigterm(event: threading.Event) -> None:
    signal.signal(signal.SIGTERM, lambda signum, frame: event.set())


def supervisor_main(args) -> int:
    output_dir = Path(args.output_dir)
    shutdown = threading.Event()
    _install_sigterm(shutdown)
    backend = FakeBackend() if args.backend == "fake" else PinnedBackend(Path(args.upstream))
    supervisor = Supervisor(backend, output_dir, owner=args.owner, token=args.token)

    def on_error(text: str) -> None:
        supervisor.events.write({"event": "handler_error", "traceback": text})

    common = {"supervisor": supervisor, "output_dir": output_dir, "shutdown_requested": shutdown, "on_error": on_error}
    client_server = _serve(_ClientApiHandler, common)
    harness_server = _serve(_HarnessHandler, common)
    try:
        write_json_atomic(Path(args.ready_file), {"pid": os.getpid(), "client_port": client_server.server_address[1], "harness_port": harness_server.server_address[1], "provenance": backend.provenance()})
        shutdown.wait()
    finally:
        client_server.shutdown()
        harness_server.shutdown()
        client_server.server_close()
        harness_server.server_close()
        supervisor.stop()
    return 0


# --------------------------------------------------------------------------- proxy


class LinkProxy:
    """Loopback TCP relay whose client link can be cut and restored; it never touches host networking."""

    def __init__(self, target_port: int, events: JsonlWriter) -> None:
        self._target = ("127.0.0.1", target_port)
        self._events = events
        self._lock = threading.Lock()
        self._up = True
        self._pairs: set[tuple[socket.socket, socket.socket]] = set()
        self._stop = threading.Event()
        self.stats = {"accepted": 0, "refused_while_down": 0, "cut_connections": 0, "upstream_connect_errors": 0}
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                up = self._up
                self.stats["accepted" if up else "refused_while_down"] += 1
            if not up:
                _close_quietly(client)
                continue
            try:
                upstream = socket.create_connection(self._target, timeout=5)
            except OSError:
                self.stats["upstream_connect_errors"] += 1
                _close_quietly(client)
                continue
            pair = (client, upstream)
            with self._lock:
                if not self._up:
                    _close_quietly(client)
                    _close_quietly(upstream)
                    continue
                self._pairs.add(pair)
            for source, sink in (pair, pair[::-1]):
                threading.Thread(target=self._pump, args=(source, sink, pair), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket, pair) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            with self._lock:
                self._pairs.discard(pair)
            for sock in pair:
                _close_quietly(sock)

    def set_link(self, up: bool) -> dict:
        with self._lock:
            changed = self._up != up
            self._up = up
            cut = list(self._pairs) if not up else []
            self._pairs.difference_update(cut)
            self.stats["cut_connections"] += len(cut)
            changed_ns = time.monotonic_ns()
        for pair in cut:
            for sock in pair:
                _close_quietly(sock)
        record = {"event": "link_up" if up else "link_down", "changed": changed, "changed_ns": changed_ns, "cut_connections": len(cut)}
        self._events.write(record)
        return {"up": up, **record}

    def close(self) -> None:
        self._stop.set()
        self.set_link(False)
        _close_quietly(self._listener)
        self._thread.join(timeout=2)


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class _ProxyAdminHandler(_JsonHandler):
    def handle_route(self, method, url, received_ns):
        proxy: LinkProxy = self.server.proxy
        if method == "POST" and url.path == "/link":
            return 200, proxy.set_link(bool(self._body()["up"]))
        if method == "GET" and url.path == "/stats":
            return 200, dict(proxy.stats)
        if method == "POST" and url.path == "/shutdown":
            self.server.shutdown_requested.set()
            return 200, {"shutting_down": True, "stats": dict(proxy.stats)}
        return 404, {"error": "not_found"}


def proxy_main(args) -> int:
    output_dir = Path(args.output_dir)
    shutdown = threading.Event()
    _install_sigterm(shutdown)
    events = JsonlWriter(output_dir / "events.jsonl")
    proxy = LinkProxy(args.target_port, events)

    def on_error(text: str) -> None:
        events.write({"event": "handler_error", "traceback": text})

    admin = _serve(_ProxyAdminHandler, {"proxy": proxy, "shutdown_requested": shutdown, "on_error": on_error})
    try:
        write_json_atomic(Path(args.ready_file), {"pid": os.getpid(), "client_port": proxy.port, "admin_port": admin.server_address[1]})
        shutdown.wait()
    finally:
        admin.shutdown()
        admin.server_close()
        events.write({"event": "proxy_stopped", "stats": dict(proxy.stats)})
        proxy.close()
        events.close()
    return 0


# --------------------------------------------------------------------------- client + controller


def http_json(port: int, method: str, path: str, body: dict | None = None, *, timeout: float = 5.0, headers: dict | None = None) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        connection.close()


class TrialClient:
    """Remote-client stand-in. Talks to the supervisor only through the proxy and never replays missed steps."""

    def __init__(self, config: TrialConfig, port: int, *, token: str, job_id: str, output_dir: Path, clock=time.monotonic_ns) -> None:
        self.config = config
        self.port = port
        self.job_id = job_id
        self._headers = {"X-Owner": config.owner, "X-Owner-Token": token}
        self._clock = clock
        self.events = JsonlWriter(output_dir / "events.jsonl")
        self.ledger = JsonlWriter(output_dir / "reconciled.jsonl")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.known: list[dict | str | None] = [None] * config.steps
        self.authoritative: dict[int, tuple] = {}
        self.cursor = 0
        self._link_failed = False
        self.counts = {"start_attempts": 0, "triggers_sent": 0, "acks": 0, "trigger_link_errors": 0, "trigger_rejections": 0, "client_late_skips": 0, "polls": 0, "poll_link_errors": 0, "reconnects": 0, "ack_mismatches": 0, "unknown_acks_resolved": 0, "duplicates_in_feed": 0, "pages": 0}
        self.report: dict = {}

    def _request(self, method, path, body=None, timeout=2.0):
        return http_json(self.port, method, path, body, timeout=timeout, headers=self._headers)

    def run(self, t0_ns: int) -> dict:
        config = self.config
        plan = make_plan(config, self.job_id, t0_ns)
        digest = plan_hash(plan)
        self.report.update(job_id=self.job_id, plan_hash=digest, accepted=False)
        self._wait_until(t0_ns)
        accept_deadline = t0_ns + round(config.accept_deadline_s * 1e9)
        while self._clock() < accept_deadline and not self.report["accepted"]:
            self.counts["start_attempts"] += 1
            try:
                status, body = self._request("POST", "/v1/jobs", {"plan": plan, "plan_hash": digest}, timeout=1.0)
            except LINK_ERRORS as exc:
                self.events.write({"event": "start_link_error", "error": repr(exc), "ns": self._clock()})
                time.sleep(0.05)
                continue
            self.events.write({"event": "start_response", "status": status, "body": body, "ns": self._clock()})
            if status in (200, 201):
                self.report.update(accepted=True, accepted_offset_s=(self._clock() - t0_ns) / 1e9, accept_status=status)
            else:
                break
        if not self.report["accepted"]:
            return self._finish()
        self.report["duplicate_start_probe"] = self._duplicate_start_probe(plan, digest)
        poller = threading.Thread(target=self._poll_loop, daemon=True)
        poller.start()
        try:
            if config.mode == "client":
                self._trigger_loop(t0_ns)
            else:
                self._wait_until(t0_ns + config.steps * config.period_ns)
        finally:
            self._stop.set()
            poller.join(timeout=10)
        self._final_drain()
        self.report["stale_cursor_probe"] = self._stale_cursor_probe()
        self.report["post_terminal_start_probe"] = self._duplicate_start_probe(plan, digest)
        return self._finish()

    def _duplicate_start_probe(self, plan, digest) -> dict:
        try:
            status, body = self._request("POST", "/v1/jobs", {"plan": plan, "plan_hash": digest})
        except LINK_ERRORS as exc:
            return {"ok": False, "error": repr(exc)}
        job = body.get("job") or {}
        return {"ok": status == 200 and body.get("duplicate") is True and job.get("job_id") == self.job_id and job.get("plan_hash") == digest, "status": status, "state": job.get("state")}

    def _stale_cursor_probe(self) -> dict:
        try:
            status, body = self._request("GET", f"/v1/jobs/{self.job_id}/results?cursor={self.cursor + 1}&limit=10")
        except LINK_ERRORS as exc:
            return {"ok": False, "error": repr(exc)}
        return {"ok": status == 409, "status": status, "error": body.get("error")}

    def _trigger_loop(self, t0_ns: int) -> None:
        period = self.config.period_ns
        for step in range(self.config.steps):
            release = t0_ns + step * period
            self._wait_until(release)
            now = self._clock()
            if now >= release + period:
                self.counts["client_late_skips"] += 1
                self.events.write({"event": "client_late_skip", "step": step, "ns": now})
                continue
            self.counts["triggers_sent"] += 1
            try:
                status, body = self._request("POST", f"/v1/jobs/{self.job_id}/steps/{step}", timeout=max(0.005, (release + period - now) / 1e9))
            except LINK_ERRORS as exc:
                with self._lock:
                    self.known[step] = "unknown"
                self.counts["trigger_link_errors"] += 1
                self.events.write({"event": "trigger_link_error", "step": step, "error": repr(exc), "sent_ns": now, "ns": self._clock()})
                continue
            if status == 200:
                with self._lock:
                    self.known[step] = body["record"]
                self.counts["acks"] += 1
            else:
                self.counts["trigger_rejections"] += 1
                self.events.write({"event": "trigger_rejected", "step": step, "status": status, "body": body, "sent_ns": now})

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.config.poll_interval_s):
            self.poll_once()

    def poll_once(self) -> bool:
        self.counts["polls"] += 1
        try:
            status, body = self._request("GET", f"/v1/jobs/{self.job_id}")
            if status != 200:
                raise ValueError(f"status {status}: {body}")
            job_state = body["job"]["state"]
            while True:
                status, page = self._request("GET", f"/v1/jobs/{self.job_id}/results?cursor={self.cursor}&limit={MAX_PAGE}")
                if status != 200:
                    raise ValueError(f"results status {status}: {page}")
                self._ingest(page)
                if page["next_cursor"] >= page["committed"]:
                    job_state = page["job_state"]
                    break
        except LINK_ERRORS as exc:
            self.counts["poll_link_errors"] += 1
            if not self._link_failed:
                self.events.write({"event": "poll_link_lost", "error": repr(exc), "ns": self._clock()})
            self._link_failed = True
            return False
        if self._link_failed:
            self._link_failed = False
            self.counts["reconnects"] += 1
            self.events.write({"event": "reconnected_and_reconciled", "cursor": self.cursor, "job_state": job_state, "ns": self._clock()})
        self.report["last_job_state"] = job_state
        return True

    def _ingest(self, page: dict) -> None:
        self.counts["pages"] += 1
        with self._lock:
            for item in page["items"]:
                step = item["step"]
                if step in self.authoritative:
                    self.counts["duplicates_in_feed"] += 1
                known = self.known[step]
                if isinstance(known, dict) and (known["seq"], known["success"], known["selected_backend"]) != (item["seq"], item["success"], item["selected_backend"]):
                    self.counts["ack_mismatches"] += 1
                elif known == "unknown":
                    self.counts["unknown_acks_resolved"] += 1
                self.authoritative[step] = (item["seq"], item["status"], item["success"], item["selected_backend"])
                self.ledger.write(item)
            self.cursor = page["next_cursor"]

    def _final_drain(self) -> None:
        deadline = time.monotonic() + self.config.finalize_timeout_s
        while time.monotonic() < deadline:
            if self.poll_once() and self.report.get("last_job_state") in TERMINAL_JOB_STATES:
                return
            time.sleep(0.05)

    def _wait_until(self, target_ns: int) -> None:
        remaining = target_ns - self._clock()
        if remaining > 0:
            time.sleep(remaining / 1e9)

    def _finish(self) -> dict:
        self.events.close()
        self.ledger.close()
        self.report["counts"] = dict(self.counts)
        self.report["authoritative_records"] = len(self.authoritative)
        self.report["unknown_acks_unresolved"] = sum(1 for step, value in enumerate(self.known) if value == "unknown" and step not in self.authoritative)
        return self.report


def _spawn_role(role: str, python: str, role_dir: Path, extra: list[str]) -> subprocess.Popen:
    role_dir.mkdir(parents=True, exist_ok=False)
    command = [python, "-I", "-B", str(MODULE_PATH), role, "--output-dir", str(role_dir), "--ready-file", str(role_dir / "ready.json"), *extra]
    (role_dir / "command.json").write_text(json.dumps(command) + "\n", encoding="utf-8")
    with (role_dir / "stdout.log").open("wb") as stdout, (role_dir / "stderr.log").open("wb") as stderr:
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=role_dir, start_new_session=True)


def _wait_ready(process: subprocess.Popen, path: Path, timeout_s: float, role: str) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise RuntimeError(f"{role} exited with {process.returncode} before becoming ready")
        time.sleep(0.02)
    raise TimeoutError(f"{role} not ready within {timeout_s} s")


def _stop_process(process: subprocess.Popen, timeout_s: float = 5.0) -> dict:
    forced = None
    if process.poll() is None:
        forced = "SIGTERM"
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            forced = "SIGKILL"
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=timeout_s)
        except ProcessLookupError:
            process.wait(timeout=timeout_s)
    return {"pid": process.pid, "returncode": process.returncode, "forced": forced}


def upstream_revision(upstream: Path) -> str:
    return subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def _partition_driver(config: TrialConfig, admin_port: int, t0_ns: int, stop: threading.Event, record: dict) -> None:
    try:
        for key, offset_s, up in (("down", config.partition_start_s, False), ("up", config.partition_end_s, True)):
            target = t0_ns + round(offset_s * 1e9)
            while not stop.is_set() and time.monotonic_ns() < target:
                stop.wait(min((target - time.monotonic_ns()) / 1e9, 0.5))
            if stop.is_set():
                return
            status, body = http_json(admin_port, "POST", "/link", {"up": up})
            if status != 200:
                record["error"] = f"link {key} rejected: {body}"
                return
            record[f"{key}_ns"] = body["changed_ns"]
            record[f"{key}_cut_connections"] = body["cut_connections"]
    except (*LINK_ERRORS, KeyError):
        record["error"] = traceback.format_exc()


def run_trial(config: TrialConfig, output_dir: Path, *, backend: str = "pinned", upstream: Path = DEFAULT_UPSTREAM, supervisor_python: str | None = None) -> tuple[dict, int]:
    """Run one trial; returns (summary, exit_code) with 0 passed, 1 assertion failure, 2 error."""
    config.validate()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    if supervisor_python is None:
        supervisor_python = str(DEFAULT_PINNED_PYTHON) if backend == "pinned" else sys.executable
    token = hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    job_id = f"{config.trial_id}-job"
    write_json_atomic(output_dir / "config.json", {"schema_version": SCHEMA_VERSION, "trial_id": config.trial_id, "scenario": config.scenario, "backend": backend, "upstream": str(upstream), "supervisor_python": supervisor_python, **asdict(config)})
    summary: dict = {"schema_version": SCHEMA_VERSION, "experiment": "local-control-disconnection", "trial_id": config.trial_id, "mode": config.mode, "scenario": config.scenario, "backend": backend, "status": "error", "passed": False, "assertions": [], "processes": {}}
    children: dict[str, subprocess.Popen] = {}
    partition_stop = threading.Event()
    partition_record: dict = {}
    partition_thread = None
    try:
        if backend == "pinned":
            revision = upstream_revision(upstream)
            summary["upstream_revision"] = revision
            if revision != PINNED_REVISION:
                raise RuntimeError(f"Unexpected upstream revision {revision}")
        elif backend != "fake":
            raise ValueError("backend must be 'pinned' or 'fake'")
        supervisor_extra = ["--backend", backend, "--upstream", str(upstream), "--owner", config.owner, "--token", token]
        children["supervisor"] = _spawn_role("supervisor", supervisor_python, output_dir / "supervisor", supervisor_extra)
        supervisor_ready = _wait_ready(children["supervisor"], output_dir / "supervisor" / "ready.json", config.ready_timeout_s, "supervisor")
        summary["provenance"] = supervisor_ready["provenance"]
        children["proxy"] = _spawn_role("proxy", sys.executable, output_dir / "proxy", ["--target-port", str(supervisor_ready["client_port"])])
        proxy_ready = _wait_ready(children["proxy"], output_dir / "proxy" / "ready.json", config.ready_timeout_s, "proxy")
        harness = supervisor_ready["harness_port"]
        t0_ns = time.monotonic_ns() + round(config.start_lead_s * 1e9)
        status, body = http_json(harness, "POST", "/harness/trial", {"t0_ns": t0_ns, "fault_at_s": config.fault_at_s if config.fault else None})
        if status != 200:
            raise RuntimeError(f"trial configuration rejected: {body}")
        if config.partition:
            partition_thread = threading.Thread(target=_partition_driver, args=(config, proxy_ready["admin_port"], t0_ns, partition_stop, partition_record), daemon=True)
            partition_thread.start()
        (output_dir / "client").mkdir()
        client = TrialClient(config, proxy_ready["client_port"], token=token, job_id=job_id, output_dir=output_dir / "client")
        client_report = client.run(t0_ns)
        if partition_thread is not None:
            partition_thread.join(timeout=config.finalize_timeout_s)
        status, final = http_json(harness, "POST", "/harness/finalize", {"timeout_s": config.finalize_timeout_s}, timeout=config.finalize_timeout_s + 5)
        if status != 200:
            raise RuntimeError(f"supervisor finalize failed: {final}")
        http_json(harness, "POST", "/harness/shutdown", {})
        summary["processes"]["supervisor"] = _wait_exit(children["supervisor"])
        http_json(proxy_ready["admin_port"], "POST", "/shutdown", {})
        summary["processes"]["proxy"] = _wait_exit(children["proxy"])
        summary.update(analyse_trial(config, output_dir, t0_ns, client_report, final, partition_record, summary["processes"]))
        summary["status"] = "passed" if summary["passed"] else "failed"
    except BaseException as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        write_json_atomic(output_dir / "error.json", summary["error"])
        if not isinstance(exc, Exception):
            raise
    finally:
        partition_stop.set()
        if partition_thread is not None:
            partition_thread.join(timeout=5)
        for role, process in children.items():
            if role not in summary["processes"] or process.poll() is None:
                summary["processes"][role] = _stop_process(process)
        summary["files"] = sorted(str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file())
        write_json_atomic(output_dir / "summary.json", summary)
    return summary, {"passed": 0, "failed": 1}.get(summary["status"], 2)


def _wait_exit(process: subprocess.Popen, timeout_s: float = 10.0) -> dict:
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return _stop_process(process)
    return {"pid": process.pid, "returncode": process.returncode, "forced": None}


def analyse_trial(config: TrialConfig, output_dir: Path, t0_ns: int, client: dict, final: dict, link: dict, processes: dict) -> dict:
    """Derive per-phase counts from the on-disk ledger and check the predeclared expectations."""
    records = read_jsonl(output_dir / "supervisor" / "results.jsonl")
    supervisor_events = read_jsonl(output_dir / "supervisor" / "events.jsonl")
    client_ledger = read_jsonl(output_dir / "client" / "reconciled.jsonl")
    period_s = 1 / config.rate_hz

    def phase(step: int) -> str:
        release_s = step * period_s
        if release_s < config.partition_start_s:
            return "before"
        return "during" if release_s < config.partition_end_s else "after"

    per_step: dict[int, list[dict]] = {}
    for record in records:
        per_step.setdefault(record["step"], []).append(record)
    phases = {name: {"opportunities": 0, "executed": 0, "succeeded": 0, "missed": 0, "used_fallback": 0} for name in ("before", "during", "after")}
    for step in range(config.steps):
        bucket = phases[phase(step)]
        bucket["opportunities"] += 1
        for record in per_step.get(step, [])[:1]:
            executed = record["status"] == "executed"
            bucket["executed"] += int(executed)
            bucket["missed"] += int(not executed)
            bucket["succeeded"] += int(executed and bool(record["success"]))
            bucket["used_fallback"] += int(bool(record.get("used_fallback")))
    executed_records = [record for record in records if record["status"] == "executed"]
    fault = final["fault"]
    consumed_step = None
    if fault.get("consumed_task_id"):
        consumed_step = int(fault["consumed_task_id"].rsplit(":", 1)[1])
    down_ns, up_ns = link.get("down_ns"), link.get("up_ns")
    arrivals_in_partition = None
    if down_ns is not None and up_ns is not None:
        guard_ns = round(0.05 * 1e9)
        arrivals_in_partition = sum(1 for event in supervisor_events if event["event"] == "client_request" and down_ns + guard_ns <= event["received_ns"] < up_ns)
    client_counts = client.get("counts", {})
    supervisor_view = {record["step"]: (record["seq"], record["status"], record["success"], record["selected_backend"]) for record in records}
    client_view = {item["step"]: (item["seq"], item["status"], item["success"], item["selected_backend"]) for item in client_ledger}

    checks: list[tuple[str, bool, object]] = []

    def check(name: str, passed: bool, detail: object = None) -> None:
        checks.append((name, bool(passed), detail))

    check("job_accepted_before_deadline", client.get("accepted") and client.get("accepted_offset_s", math.inf) < config.accept_deadline_s, client.get("accepted_offset_s"))
    check("duplicate_start_acknowledged_without_new_job", client.get("duplicate_start_probe", {}).get("ok") and client.get("post_terminal_start_probe", {}).get("ok") and final["counters"]["rejected_starts"] == 0, [client.get("duplicate_start_probe"), client.get("post_terminal_start_probe")])
    check("job_terminal_completed", (final.get("job") or {}).get("state") == "completed", (final.get("job") or {}).get("state"))
    check("every_step_committed_exactly_once", sorted(per_step) == list(range(config.steps)) and all(len(items) == 1 for items in per_step.values()), {"committed": len(records), "steps": config.steps})
    check("no_duplicate_backend_execution", not final["invocations"]["steps_with_multiple"] and final["invocations"]["total"] == len(executed_records) and final["counters"]["native_idempotent_replays"] == 0, final["invocations"])
    check("leases_released_and_backends_ready", all(item["lease_id"] is None and item["lifecycle"] == "ready" for item in final["resources"].values()), final["resources"])
    check("client_reconciled_to_authoritative_ledger", client_view == supervisor_view and client_counts.get("ack_mismatches") == 0 and client_counts.get("duplicates_in_feed") == 0 and client.get("unknown_acks_unresolved") == 0, {key: client_counts.get(key) for key in ("ack_mismatches", "duplicates_in_feed", "unknown_acks_resolved", "reconnects")})
    check("stale_cursor_rejected", client.get("stale_cursor_probe", {}).get("ok"), client.get("stale_cursor_probe"))
    check("supervisor_and_proxy_exited_cleanly", all(item.get("returncode") == 0 and item.get("forced") is None for item in processes.values()), processes)
    if config.partition:
        check("partition_applied", down_ns is not None and up_ns is not None and "error" not in link, link)
        check("proxy_isolated_client_link", arrivals_in_partition == 0, arrivals_in_partition)
    during = phases["during"]
    if config.mode == "local":
        check("no_missed_local_steps", all(bucket["missed"] == 0 for bucket in phases.values()), phases)
        if config.partition:
            check("local_continues_during_outage", during["executed"] >= math.ceil(config.min_local_partition_ratio * during["opportunities"]), during)
    else:
        check("client_steps_before_window_not_missed", phases["before"]["missed"] == 0, phases["before"])
        if config.partition:
            guard_steps = math.ceil(0.05 / period_s) + 1
            core = [step for step in range(config.steps) if phase(step) == "during" and step * period_s >= config.partition_start_s + guard_steps * period_s]
            executed_core = sum(1 for step in core if per_step.get(step, [{}])[0].get("status") == "executed")
            check("remote_client_misses_outage_opportunities", executed_core == 0 and during["missed"] >= len(core), {"core_opportunities": len(core), "executed_core": executed_core, "during": during})
            check("reconnect_boundary_misses_bounded", phases["after"]["missed"] <= config.reconnect_boundary_misses, phases["after"])
        else:
            check("connected_client_no_missed_steps", all(bucket["missed"] == 0 for bucket in phases.values()), phases)
    fallback_steps = [record["step"] for record in executed_records if record.get("used_fallback")]
    if config.fault:
        armed_ok = fault.get("armed_ns") is not None and fault["armed_ns"] >= t0_ns + round(config.fault_at_s * 1e9)
        check("fault_armed_on_schedule", armed_ok, fault)
        consuming = per_step.get(consumed_step, [{}])[0] if consumed_step is not None else {}
        check("fault_consumed_with_native_fallback", consumed_step is not None and consuming.get("success") is True and consuming.get("selected_backend") == FALLBACK_ID and fallback_steps == [consumed_step], {"consumed_step": consumed_step, "fallback_steps": fallback_steps})
        if config.partition and fault.get("consumed_ns") is not None and down_ns is not None and up_ns is not None:
            if config.mode == "local":
                check("fault_consumed_during_outage_by_local_control", down_ns <= fault["consumed_ns"] < up_ns, fault["consumed_ns"])
            else:
                check("fault_not_consumed_until_reconnect", fault["consumed_ns"] >= up_ns, fault["consumed_ns"])
    else:
        check("no_fault_no_fallback", fault.get("armed_ns") is None and not fallback_steps, fallback_steps)

    def offset(ns):
        return None if ns is None else (ns - t0_ns) / 1e9

    assertions = [{"name": name, "passed": passed, "detail": detail} for name, passed, detail in checks]
    return {
        "passed": all(item["passed"] for item in assertions),
        "assertions": assertions,
        "timeline": {"t0_ns": t0_ns, "planned_partition_s": [config.partition_start_s, config.partition_end_s] if config.partition else None, "link_down_offset_s": offset(down_ns), "link_up_offset_s": offset(up_ns), "fault_planned_offset_s": config.fault_at_s if config.fault else None, "fault_armed_offset_s": offset(fault.get("armed_ns")), "fault_consumed_offset_s": offset(fault.get("consumed_ns")), "fault_consumed_step": consumed_step, "fault_consumed_phase": phase(consumed_step) if consumed_step is not None else None},
        "job": {**(final.get("job") or {}), "accepted_offset_s": client.get("accepted_offset_s"), "terminal_offset_s": offset(final.get("terminal_ns"))},
        "steps": {"planned": config.steps, "committed": len(records), "executed": len(executed_records), "succeeded": sum(1 for record in executed_records if record["success"]), "missed": len(records) - len(executed_records), "fallback_steps": fallback_steps, "by_phase": phases},
        "backend": {"invocations": final["invocations"], "resources": final["resources"], "counters": final["counters"], "supervisor_peak_rss_kib": final["peak_rss_kib"], "retained": final["retained"]},
        "client": client,
        "proxy": {"client_requests_reaching_supervisor_during_partition": arrivals_in_partition, "link": link},
    }


# --------------------------------------------------------------------------- CLI


def _add_trial_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = TrialConfig(mode="local", partition=False, fault=False)
    for name, value in asdict(defaults).items():
        if name in {"mode", "partition", "fault"}:
            continue
        parser.add_argument(f"--{name.replace('_', '-')}", type=type(value), default=value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    roles = parser.add_subparsers(dest="role", required=True)
    controller = roles.add_parser("controller", help="run one trial and write summary.json")
    controller.add_argument("--mode", choices=MODES, required=True)
    controller.add_argument("--partition", action=argparse.BooleanOptionalAction, required=True)
    controller.add_argument("--fault", action=argparse.BooleanOptionalAction, required=True)
    controller.add_argument("--output-dir", type=Path, required=True)
    controller.add_argument("--backend", choices=("pinned", "fake"), default="pinned")
    controller.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    controller.add_argument("--supervisor-python")
    _add_trial_arguments(controller)
    supervisor = roles.add_parser("supervisor")
    supervisor.add_argument("--output-dir", required=True)
    supervisor.add_argument("--ready-file", required=True)
    supervisor.add_argument("--backend", choices=("pinned", "fake"), required=True)
    supervisor.add_argument("--upstream", required=True)
    supervisor.add_argument("--owner", required=True)
    supervisor.add_argument("--token", required=True)
    proxy = roles.add_parser("proxy")
    proxy.add_argument("--output-dir", required=True)
    proxy.add_argument("--ready-file", required=True)
    proxy.add_argument("--target-port", type=int, required=True)
    matrix = roles.add_parser("matrix", help="print the planned trial list without running anything")
    matrix.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args(argv)
    if args.role == "supervisor":
        return supervisor_main(args)
    if args.role == "proxy":
        return proxy_main(args)
    if args.role == "matrix":
        print(json.dumps([{"trial_id": item.trial_id, **asdict(item)} for item in build_matrix(args.repetitions)], indent=2))
        return 0
    overrides = {name: getattr(args, name) for name in asdict(TrialConfig(mode="local", partition=False, fault=False)) if name not in {"mode", "partition", "fault"}}
    config = replace(TrialConfig(mode=args.mode, partition=args.partition, fault=args.fault), **overrides)
    summary, code = run_trial(config, args.output_dir, backend=args.backend, upstream=args.upstream, supervisor_python=args.supervisor_python)
    print(json.dumps({"summary_path": str(Path(args.output_dir) / "summary.json"), "trial_id": summary["trial_id"], "status": summary["status"], "passed": summary["passed"], "exit_code": code, "failed_assertions": [item["name"] for item in summary["assertions"] if not item["passed"]]}))
    return code


if __name__ == "__main__":
    sys.exit(main())
