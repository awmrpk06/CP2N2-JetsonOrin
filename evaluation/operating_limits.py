"""Validate, execute and summarize the operating-limits campaign against the pinned CP2N2 release.

Measured trials run strictly one at a time under a host-wide lock. Every phase has its own frozen
configuration, ledger and immutable trial folders. A scientific trial is never resumed or redone in
the same campaign; resuming only continues with trials that never started.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

import psutil

from evaluation import local_control_experiment as lce
from evaluation import spike_analysis
from evaluation.reproduce_original import (
    HARNESS_SOURCES,
    PINNED_COMMIT,
    ROOT,
    digest,
    git_fact,
    hardware_preflight,
    hardware_state,
    reproduction_environment,
    run_stage,
    source_hashes,
)
from evaluation.resource_monitor import stop_process_group
from evaluation.summarize_profiles import verify_profile
from evaluation.upstream_sustained import (
    GC_POLICIES,
    INTRUSIVE_PROFILES,
    LIFECYCLE_VARIANTS,
    MODES,
    validate_options,
)

SCHEMA_VERSIONS = (1, 2)
# Fixed order for --phase all: attribution and diagnostics come before the long unchanged baseline.
ALL_PHASES = ("pilot", "attribution", "allocation-profiling", "call-profiling", "long-baseline", "sweep", "local-control", "paired-power")
TOP_KEYS = frozenset({"schema_version", "upstream_commit", "cooling", "ambient_temperature_c", "desktop_comparison", "interval_ms", "platform_idle_s", "cooldown_s", "phases"})
SUSTAINED_KEYS = frozenset({"implemented", "duration_s", "idle_s", "modes", "rates_hz", "repetitions", "diagnostic_counts"})
PHASE_SCHEMAS = {  # kind: (required keys, optional keys); schema 1 phases are implicitly sustained without a kind
    "sustained": (SUSTAINED_KEYS | {"kind"}, frozenset({"intrusive_profile", "post_idle_s", "lifecycle_variants", "retention_limit", "spike_instrumentation", "gc_policy"})),
    "local-control": (frozenset({"implemented", "kind", "repetitions", "seed", "backend", "duration_s", "rate_hz", "timeout_margin_s"}), frozenset()),
    "paired-power": (frozenset({"implemented", "kind", "pairs", "modes", "rate_hz", "active_s", "pre_idle_s", "post_idle_s", "platform_baseline_s", "diagnostic_counts"}), frozenset()),
}
TIMINGS = ("prepare_ms", "backend_invoke_ms", "telemetry_ms", "direct_path_ms", "orchestrated_ms", "invocation_ms", "reset_ms", "recalibrate_ms", "cycle_ms", "release_to_completion_ms", "dispatch_lateness_ms")
PERFORMANCE_COUNTERS = ("skipped_releases", "deadline_misses", "maintenance_failures", "unexpected_backends", "idempotent_replays")
# The worker exits 2 after a completed run when any of these is present; deadline misses and skips do not change its exit status.
WORKER_DEFECT_COUNTERS = ("maintenance_failures", "unexpected_backends", "idempotent_replays")
MIN_COOLDOWN_S = 60
SUSTAINED_TIMEOUT_MARGIN_S = 120
STARTUP_ALLOWANCE_S = 10
LOCAL_OVERHEAD_S = 15
MIN_FREE_DISK_BYTES = 4 * 1024 ** 3
COVERAGE_MIN = 0.9
HEARTBEAT_S = 15
PREFLIGHT_TIMEOUT_S = 120
LOCK_PATH = ROOT / "_temp" / "operating-limits.lock"
SCRATCH_ROOT = ROOT / "_temp"
UTC = timezone.utc  # datetime.UTC needs Python 3.11; the root test venv is 3.10
LOCAL_RESOURCE_SCOPE = "not_measured: no external process or platform sampler runs during local-control trials; only the supervisor's self-reported peak RSS is retained"
VARIANT_DEFAULTS = {"lifecycle_variant": "pinned", "retention_limit": None, "spike_instrumentation": False, "gc_policy": "default"}


def variant_options(spec: dict) -> dict:
    """Lifecycle-variant and spike settings of a trial; specs of earlier campaigns omit them and mean the defaults."""
    return {key: spec.get(key, default) for key, default in VARIANT_DEFAULTS.items()}


def phase_order(config: dict) -> tuple:
    """--phase all runs a configuration's phases in declaration order (the operating-limits plan declares ALL_PHASES)."""
    _validate_top(config)
    return tuple(config["phases"])


def _number(value, name: str, *, allow_zero: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number")
    return value


def _integer(value, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _boolean(value, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _rate(value, name: str = "rates_hz"):
    if _number(value, name) > 1000:
        raise ValueError(f"{name} must be in (0, 1000] Hz")
    return value


def _validate_top(config) -> None:
    if not isinstance(config, dict) or set(config) != TOP_KEYS:
        raise ValueError(f"Campaign configuration must have exactly these keys: {sorted(TOP_KEYS)}")
    if type(config["schema_version"]) is not int or config["schema_version"] not in SCHEMA_VERSIONS or config["upstream_commit"] != PINNED_COMMIT:
        raise ValueError(f"Expected schema {SCHEMA_VERSIONS} and the pinned original implementation")
    if type(config["interval_ms"]) is not int or config["interval_ms"] <= 0:
        raise ValueError("interval_ms must be a positive integer")
    _number(config["platform_idle_s"], "platform_idle_s")
    if _number(config["cooldown_s"], "cooldown_s") < MIN_COOLDOWN_S:
        raise ValueError(f"cooldown_s must be at least {MIN_COOLDOWN_S} s")
    if not isinstance(config["cooling"], str) or not config["cooling"].strip():
        raise ValueError("cooling must be a nonempty description")
    ambient = config["ambient_temperature_c"]
    if ambient is not None and (isinstance(ambient, bool) or not isinstance(ambient, (int, float)) or not math.isfinite(ambient)):
        raise ValueError("ambient_temperature_c must be null (unmeasured) or a finite number")
    _boolean(config["desktop_comparison"], "desktop_comparison")
    if not isinstance(config["phases"], dict) or not config["phases"]:
        raise ValueError("phases must be a nonempty object")


def _phase_settings(config: dict, phase: str) -> tuple[dict, str]:
    phases = config["phases"]
    if phase not in phases:
        raise ValueError("Unknown campaign phase")
    settings = phases[phase]
    if not isinstance(settings, dict) or settings.get("implemented") is not True:
        raise ValueError("This phase is planned but not implemented yet")
    if config["schema_version"] == 1:
        kind, (required, optional) = "sustained", (SUSTAINED_KEYS, frozenset())
    else:
        kind = settings.get("kind")
        if not isinstance(kind, str) or kind not in PHASE_SCHEMAS:
            raise ValueError(f"Phase kind must be one of {sorted(PHASE_SCHEMAS)}")
        required, optional = PHASE_SCHEMAS[kind]
    if not required <= set(settings) <= required | optional:
        raise ValueError(f"Phase settings must have exactly these keys: {sorted(required)}, optionally {sorted(optional)}")
    return settings, kind


def _sustained_specs(config: dict, phase: str, settings: dict) -> list[dict]:
    duration = _number(settings["duration_s"], "duration_s")
    idle = _number(settings["idle_s"], "idle_s", allow_zero=True)
    post_idle = _number(settings.get("post_idle_s", 0), "post_idle_s", allow_zero=True)
    counts = _boolean(settings["diagnostic_counts"], "diagnostic_counts")
    intrusive = settings.get("intrusive_profile", "none")
    if not isinstance(intrusive, str) or intrusive not in INTRUSIVE_PROFILES:
        raise ValueError(f"intrusive_profile must be one of {INTRUSIVE_PROFILES}")
    if intrusive != "none" and counts:
        raise ValueError("The worker disables retained counters under intrusive profiles; set diagnostic_counts false")
    repetitions = _integer(settings["repetitions"], "repetitions")
    modes, rates = settings["modes"], settings["rates_hz"]
    declared_variants = "lifecycle_variants" in settings
    variants = settings.get("lifecycle_variants", ["pinned"])
    if not isinstance(variants, list) or not variants or len(set(variants)) != len(variants) or not all(isinstance(item, str) and item in LIFECYCLE_VARIANTS for item in variants):
        raise ValueError(f"lifecycle_variants must be a nonempty list of distinct values from {LIFECYCLE_VARIANTS}")
    retention = settings.get("retention_limit")
    if ("bounded" in variants) != (retention is not None):
        raise ValueError("retention_limit is required exactly when the bounded variant is declared")
    spikes = _boolean(settings.get("spike_instrumentation", False), "spike_instrumentation")
    gc_policy = settings.get("gc_policy", "default")
    if not isinstance(gc_policy, str) or gc_policy not in GC_POLICIES:
        raise ValueError(f"gc_policy must be one of {GC_POLICIES}")
    extended = declared_variants or "spike_instrumentation" in settings or "gc_policy" in settings
    if not isinstance(modes, list) or not all(isinstance(mode, str) and mode in MODES for mode in modes):
        raise ValueError(f"modes must be a list drawn from {sorted(MODES)}")
    if not isinstance(rates, list) or not rates:
        raise ValueError("rates_hz must be a nonempty list of rates in (0, 1000] Hz")
    for rate in rates:
        _rate(rate)
    combinations = [(mode, rate, variant) for mode in modes for rate in rates for variant in variants]
    if not combinations or len(set(modes)) != len(modes) or len(set(rates)) != len(rates):
        raise ValueError("Trial conditions must be nonempty and unique")
    trials = []
    for repetition in range(repetitions):
        rotated = combinations[repetition % len(combinations):] + combinations[:repetition % len(combinations)]
        for mode, rate, variant in rotated:
            limit = retention if variant == "bounded" else None
            validate_options(duration, rate, mode, None, idle, post_idle_s=post_idle, intrusive_profile=intrusive, lifecycle_variant=variant, retention_limit=limit, spike_instrumentation=spikes, gc_policy=gc_policy)
            label = (f"{mode}-{variant}" if declared_variants else mode) + ("" if gc_policy == "default" else f"-gc{gc_policy}")
            trial = {
                "kind": "sustained", "phase": phase, "trial_id": f"{phase}-r{repetition + 1:02d}-{label}-{rate:g}hz",
                "mode": mode, "rate_hz": rate, "duration_s": duration, "idle_s": idle, "post_idle_s": post_idle,
                "diagnostic_counts": counts, "intrusive_profile": intrusive, "baseline_s": config["platform_idle_s"],
                "repetition": repetition + 1,
            }
            if extended:
                trial.update(lifecycle_variant=variant, retention_limit=limit, spike_instrumentation=spikes)
                if "gc_policy" in settings:
                    trial["gc_policy"] = gc_policy
            trials.append(trial)
    return trials


def _local_specs(config: dict, phase: str, settings: dict) -> list[dict]:
    repetitions = _integer(settings["repetitions"], "repetitions")
    seed = _integer(settings["seed"], "seed", minimum=0)
    if settings["backend"] != "pinned":
        raise ValueError("Campaign local-control trials must use the pinned backend; the fake backend is for tests only")
    defaults = lce.TrialConfig(mode=lce.MODES[0], partition=False, fault=False)
    declared = (_number(settings["duration_s"], "duration_s"), _rate(settings["rate_hz"], "rate_hz"))
    if declared != (defaults.duration_s, defaults.rate_hz):
        raise ValueError(f"local-control duration_s/rate_hz must equal the module's frozen protocol ({defaults.duration_s:g} s, {defaults.rate_hz:g} Hz)")
    margin = _number(settings["timeout_margin_s"], "timeout_margin_s")
    trials = []
    for trial in lce.build_matrix(repetitions, seed=seed):
        trial.validate()
        trials.append({
            "kind": "local-control", "phase": phase, "trial_id": trial.trial_id, "mode": trial.mode,
            "partition": trial.partition, "fault": trial.fault, "scenario": trial.scenario, "repetition": trial.repetition,
            "duration_s": trial.duration_s, "rate_hz": trial.rate_hz, "backend": "pinned", "seed": seed,
            "timeout_s": trial.duration_s + margin,
        })
    return trials


def _power_specs(config: dict, phase: str, settings: dict) -> list[dict]:
    """Balanced AB/BA blocks; the long platform baseline precedes only the first trial of each pair."""
    pairs = _integer(settings["pairs"], "pairs")
    modes = settings["modes"]
    if not isinstance(modes, list) or len(modes) != 2 or len(set(modes)) != 2 or not all(isinstance(mode, str) and mode in MODES for mode in modes):
        raise ValueError(f"paired-power modes must be two distinct modes from {sorted(MODES)}")
    rate = _rate(settings["rate_hz"], "rate_hz")
    active = _number(settings["active_s"], "active_s")
    pre_idle = _number(settings["pre_idle_s"], "pre_idle_s", allow_zero=True)
    post_idle = _number(settings["post_idle_s"], "post_idle_s", allow_zero=True)
    platform_baseline = _number(settings["platform_baseline_s"], "platform_baseline_s")
    counts = _boolean(settings["diagnostic_counts"], "diagnostic_counts")
    trials = []
    for pair in range(1, pairs + 1):
        order = modes if pair % 2 else modes[::-1]
        for position, mode in enumerate(order, start=1):
            validate_options(active, rate, mode, None, pre_idle, post_idle_s=post_idle)
            trials.append({
                "kind": "sustained", "phase": phase, "trial_id": f"{phase}-p{pair:02d}-{position}-{mode}-{rate:g}hz",
                "mode": mode, "rate_hz": rate, "duration_s": active, "idle_s": pre_idle, "post_idle_s": post_idle,
                "diagnostic_counts": counts, "intrusive_profile": "none",
                "baseline_s": platform_baseline if position == 1 else config["platform_idle_s"],
                "repetition": pair, "pair": pair, "pair_order": "AB" if pair % 2 else "BA", "pair_position": position,
            })
    return trials


def trial_specs(config: dict, phase: str) -> list[dict]:
    """Validate the whole top level and one phase strictly, then return its ordered trial list."""
    _validate_top(config)
    settings, kind = _phase_settings(config, phase)
    trials = {"sustained": _sustained_specs, "local-control": _local_specs, "paired-power": _power_specs}[kind](config, phase, settings)
    if len({trial["trial_id"] for trial in trials}) != len(trials):
        raise ValueError("Trial identifiers must be unique")
    return trials


def campaign_specs(config: dict, phases=None) -> dict[str, list[dict]]:
    return {phase: trial_specs(config, phase) for phase in (phase_order(config) if phases is None else phases)}


def _flag(value, column: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"Invalid boolean in requests.csv: {column}={value!r}")
    return value == "True"


def _counter(worker: dict, name: str) -> int:
    value = worker.get(name)
    if type(value) is not int or value < 0:
        raise ValueError(f"Worker counter missing or invalid: {name}")
    return value


def summarize_requests(folder: Path) -> dict:
    worker = json.loads((folder / "workload/summary.json").read_text())
    with (folder / "workload/requests.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = set(reader.fieldnames or ())
    completed = _counter(worker, "completed_requests")
    successful = _counter(worker, "successful_requests")
    skipped = _counter(worker, "skipped_releases")
    if len(rows) != completed:
        raise ValueError("Request count differs from worker summary")
    if successful > completed:
        raise ValueError("Successful requests exceed completed requests")

    def raw_count(column: str, required: bool) -> int | None:
        if column not in columns:
            if required and rows:
                raise ValueError(f"requests.csv lacks required column: {column}")
            return None
        return sum(_flag(row[column], column) for row in rows)

    if (raw_count("deadline_missed", True) or 0) != _counter(worker, "deadline_misses"):
        raise ValueError("Deadline-miss count differs from raw rows")
    raw_successes = raw_count("success", False)
    if raw_successes is not None and raw_successes != successful:
        raise ValueError("Success count differs from raw rows")
    late = sum(row["dispatch_lateness_ms"] not in (None, "") and float(row["dispatch_lateness_ms"]) > 0 for row in rows) if "dispatch_lateness_ms" in columns else None
    if late is not None and "late_dispatches" in worker and late != _counter(worker, "late_dispatches"):
        raise ValueError("Late-dispatch count differs from raw rows")
    if "skipped_before" in columns and sum(int(row["skipped_before"]) for row in rows) > skipped:
        raise ValueError("Raw inter-row skips exceed the worker's skipped releases")
    if "missed_releases" in worker and worker["missed_releases"] != skipped:
        raise ValueError("missed_releases differs from skipped_releases")
    if "planned_releases" in worker or "arrived_releases" in worker:
        planned, arrived = _counter(worker, "planned_releases"), _counter(worker, "arrived_releases")
        if planned != arrived + _counter(worker, "unreleased_slots") or skipped != max(0, arrived - completed):
            raise ValueError("Release accounting does not reconcile")
    metrics = {}
    for name in TIMINGS:
        if name not in columns:
            metrics[name] = {"supported": False, "samples": 0, "missing": len(rows), "mean": None, "median": None, "p95": None, "max": None}
            continue
        values = sorted(float(row[name]) for row in rows if row[name] not in (None, ""))
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError(f"Invalid timing values: {name}")
        metrics[name] = {
            "supported": bool(values), "samples": len(values), "missing": len(rows) - len(values),
            "mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "p95": values[math.ceil(0.95 * len(values)) - 1] if len(values) >= 20 else None,
            "max": max(values) if values else None,
        }
    counters = folder / "workload/retained-state.jsonl"
    retained = [json.loads(line) for line in counters.read_text().splitlines()] if counters.exists() else []
    return {
        "mode": worker["mode"], "interruption": worker.get("interruption"),
        "lifecycle_variant": worker.get("lifecycle_variant", "pinned"), "retention_limit": worker.get("retention_limit"),
        "gc_policy": worker.get("gc_policy", "default"), "gc_policy_summary": worker.get("gc_policy_summary"),
        "lifecycle_variant_final_counts": worker.get("lifecycle_variant_final_counts"),
        "planned_releases": worker.get("planned_releases"), "arrived_releases": worker.get("arrived_releases"),
        "completed_requests": completed, "successful_requests": successful,
        "failed_requests": completed - successful, "success_fraction": successful / completed if completed else None,
        "skipped_releases": skipped, "deadline_misses": worker["deadline_misses"], "late_dispatches": worker.get("late_dispatches"),
        "maintenance_failures": worker.get("maintenance_failures"), "unexpected_backends": worker.get("unexpected_backends"),
        "unreleased_slots": worker.get("unreleased_slots"), "completed_duration": worker["completed_duration"],
        "unsuccessful": bool(worker.get("interruption")) or worker["completed_duration"] is not True or completed == 0 or successful < completed,
        "achieved_requests_per_s": worker["achieved_requests_per_s"], "metrics": metrics,
        "retained_first": retained[0] if retained else None, "retained_last": retained[-1] if retained else None,
        "quantile_method": "nearest rank; p95 withheld below 20 observations; empty or absent timings are excluded, never zero",
    }


def source_fingerprint() -> dict:
    """Hashes of every module whose code shapes a trial, its monitoring, its verification or its analysis."""
    paths = sorted({*HARNESS_SOURCES, ROOT / "evaluation/operating_limits.py", ROOT / "evaluation/summarize_profiles.py", ROOT / "evaluation/local_control_experiment.py", ROOT / "evaluation/spike_analysis.py"})
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


@contextmanager
def measurement_lock():
    """Host-wide exclusive lock so measured trials and offline analysis never overlap; the kernel releases it on exit."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another operating-limits process holds the measurement lock; measured runs and analysis must not overlap") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _say(message: str) -> None:
    print(f"[operating-limits {datetime.now(UTC).strftime('%H:%M:%S')}Z] {message}", file=sys.stderr, flush=True)


@contextmanager
def _heartbeat(label: str, expected_s: float):
    """Progress line every HEARTBEAT_S from a sleeping thread that does no measurement work."""
    done = threading.Event()
    started = time.monotonic_ns()

    def beat() -> None:
        while not done.wait(HEARTBEAT_S):
            _say(f"{label}: {(time.monotonic_ns() - started) / 1e9:.0f} s elapsed of ~{expected_s:.0f} s")

    thread = threading.Thread(target=beat, name="operating-limits-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=1)


def read_ledger_file(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def read_ledger(output: Path) -> list[dict]:
    return read_ledger_file(output / "ledger.jsonl")


def trial_history(events: list[dict]) -> dict[str, list[dict]]:
    history: dict[str, list[dict]] = {}
    for event in events:
        if event.get("trial_id") is not None:
            history.setdefault(event["trial_id"], []).append(event)
    return history


def _record(ledger, event: str, **fields) -> None:
    entry = {"event": event, "utc": datetime.now(UTC).isoformat(), "monotonic_ns": time.monotonic_ns(), **fields}
    ledger.write(json.dumps(entry, sort_keys=True) + "\n")
    ledger.flush()
    os.fsync(ledger.fileno())


def _command_settings(command: list) -> dict:
    def value(flag: str):
        if command.count(flag) != 1 or command.index(flag) + 1 >= len(command):
            return None
        return command[command.index(flag) + 1]

    try:
        return {
            "script": Path(command[1]).name, "mode": value("--mode"), "rate_hz": float(value("--rate-hz")),
            "duration_s": float(value("--duration-s")), "idle_s": float(value("--idle-s")),
            "deadline_ms": value("--deadline-ms"), "diagnostic_counts": "--diagnostic-counts" in command,
            # stage_command omits default-valued flags, so absent means the worker default.
            "intrusive_profile": value("--intrusive-profile") if "--intrusive-profile" in command else "none",
            "post_idle_s": float(value("--post-idle-s")) if "--post-idle-s" in command else 0.0,
            "lifecycle_variant": value("--lifecycle-variant") if "--lifecycle-variant" in command else "pinned",
            "retention_limit": int(value("--retention-limit")) if "--retention-limit" in command else None,
            "spike_instrumentation": "--spike-instrumentation" in command,
            "gc_policy": value("--gc-policy") if "--gc-policy" in command else "default",
        }
    except (IndexError, TypeError, ValueError):
        return {}


def _manifest(folder: Path) -> dict:
    if not (folder / "reproduction-manifest.json").exists():
        raise RuntimeError(f"Incomplete trial retained without manifest: {folder.name}")
    verify_profile(folder)
    return json.loads((folder / "reproduction-manifest.json").read_text())


def verify_trial(folder: Path, spec: dict, config: dict) -> tuple[dict, dict | None]:
    """Check archive integrity and that the recorded sustained trial is the intended one."""
    manifest = _manifest(folder)
    missing = {"result.json", "provenance.json", "profiling.json"} - set(manifest)
    if missing:
        raise ValueError(f"Manifest of {folder.name} lacks {sorted(missing)}")
    result = json.loads((folder / "result.json").read_text())
    intended = {
        "script": "upstream_sustained.py", "mode": spec["mode"], "rate_hz": float(spec["rate_hz"]),
        "duration_s": float(spec["duration_s"]), "idle_s": float(spec["idle_s"]),
        "deadline_ms": None, "diagnostic_counts": spec["diagnostic_counts"],
        "intrusive_profile": spec["intrusive_profile"], "post_idle_s": float(spec["post_idle_s"]),
        **variant_options(spec),
    }
    command = json.loads((folder / "provenance.json").read_text()).get("command")
    if result.get("stage") != "sustained" or not isinstance(command, list) or _command_settings(command) != intended:
        raise ValueError(f"Recorded command of {folder.name} does not match the intended trial")
    profiling = json.loads((folder / "profiling.json").read_text())
    if profiling.get("interval_ms") != config["interval_ms"] or profiling.get("baseline_s") != spec["baseline_s"]:
        raise ValueError(f"Recorded profiling settings of {folder.name} do not match the campaign")
    summary = None
    if "workload/summary.json" in manifest:
        summary = json.loads((folder / "workload/summary.json").read_text())
        recorded = tuple(summary.get(key, default) for key, default in (
            ("mode", None), ("rate_hz", None), ("duration_s", None), ("idle_s", None), ("diagnostic_counts", None),
            ("intrusive_profile", "none"), ("post_idle_s", 0), *VARIANT_DEFAULTS.items(),
        ))
        if recorded != tuple(spec.get(key, default) for key, default in (*((key, None) for key in ("mode", "rate_hz", "duration_s", "idle_s", "diagnostic_counts", "intrusive_profile", "post_idle_s")), *VARIANT_DEFAULTS.items())):
            raise ValueError(f"Recorded workload of {folder.name} does not match the intended trial")
    return result, summary


def classify_trial(result: dict, summary: dict | None) -> dict:
    """Separate infrastructure failures (campaign stops) from measured performance failures (retained, campaign continues).

    Exit status 2 after an intact, completed run whose requests, maintenance or backend identity failed is a
    measured result. Any other unexpected status, or 2 without such a defect, is infrastructure. Skipped
    releases and deadline misses are results and never stop criteria.
    """
    infrastructure = []
    if result.get("guard_reason"):
        infrastructure.append(f"guard:{result['guard_reason']}")
    if result.get("timed_out"):
        infrastructure.append("timed_out")
    if result.get("upstream_sources_unchanged") is not True:
        infrastructure.append("upstream_sources_changed")
    if result.get("profile_complete") is not True:
        infrastructure.append("profile_incomplete")
    if result.get("parent_error"):
        infrastructure.append(f"parent_error:{(result['parent_error'] or {}).get('type')}")
    returncode = result.get("returncode")
    performance = []
    if summary is None:
        infrastructure.append("workload_summary_missing")
        if returncode != 0:
            infrastructure.append(f"returncode:{returncode}")
        return {"infrastructure_failures": infrastructure, "performance_failures": performance}
    if summary.get("fatal_error"):
        fatal = summary["fatal_error"]
        infrastructure.append(f"worker_fatal_error:{fatal.get('type') if isinstance(fatal, dict) else fatal}")
    if summary.get("interruption"):
        infrastructure.append(f"worker_interrupted:{summary['interruption']}")
    if summary.get("completed_duration") is not True:
        infrastructure.append("duration_not_completed")
    counters = {}
    for name in ("completed_requests", "successful_requests", *PERFORMANCE_COUNTERS):
        if type(summary.get(name)) is not int or summary[name] < 0:
            infrastructure.append(f"summary_counter_invalid:{name}")
        else:
            counters[name] = summary[name]
    intact = not infrastructure
    completed, successful = counters.get("completed_requests"), counters.get("successful_requests")
    if completed == 0:
        performance.append("no_completed_requests")
    if completed is not None and successful is not None and completed > successful:
        performance.append(f"unsuccessful_requests:{completed - successful}")
    performance.extend(f"{name}:{counters[name]}" for name in PERFORMANCE_COUNTERS if counters.get(name))
    worker_defect = completed == 0 or (completed is not None and successful is not None and completed > successful) or any(counters.get(name) for name in WORKER_DEFECT_COUNTERS)
    if returncode == 0:
        if worker_defect:
            infrastructure.append("returncode_inconsistent:0")
    elif not (returncode == 2 and intact and worker_defect):
        infrastructure.append(f"returncode:{returncode}")
    return {"infrastructure_failures": infrastructure, "performance_failures": performance}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _diagnostics_paths(manifest: dict) -> list[str]:
    return sorted(name for name in manifest if name.startswith("workload/") and Path(name).name == "diagnostics.json")


def diagnostic_failures(folder: Path, spec: dict, summary: dict) -> list[str]:
    """Instrumentation that was requested but is missing, incomplete or reports errors invalidates the trial."""
    manifest = json.loads((folder / "reproduction-manifest.json").read_text())
    failures = []
    if variant_options(spec)["spike_instrumentation"]:
        recorder = summary.get("spike_recorder")
        if "workload/gc-events.jsonl" not in manifest or not isinstance(recorder, dict):
            failures.append("spike_instrumentation_missing")
        elif recorder.get("dropped") or recorder.get("callback_errors") or recorder.get("marks") != summary.get("completed_requests"):
            failures.append("spike_instrumentation_incomplete")
    if variant_options(spec)["lifecycle_variant"] != "pinned":
        installed = summary.get("lifecycle_variant_install") or {}
        if installed.get("variant") != spec["lifecycle_variant"] or installed.get("retention_limit") != spec["retention_limit"] or not installed.get("installed_class"):
            failures.append("lifecycle_variant_not_installed")
    if variant_options(spec)["gc_policy"] != "default" and (summary.get("gc_policy_summary") or {}).get("policy") != spec["gc_policy"]:
        failures.append("gc_policy_not_applied")
    if spec["intrusive_profile"] == "none":
        if spec["diagnostic_counts"]:
            if "workload/retained-state.jsonl" not in manifest:
                return [*failures, "retained_counts_missing"]
            try:
                samples = _jsonl(folder / "workload/retained-state.jsonl")
            except ValueError:
                return [*failures, "retained_counts_unreadable"]
            if len(samples) < 2 or samples[-1].get("completed_requests") != summary.get("completed_requests"):
                failures.append("retained_counts_incomplete")
        return failures
    paths = _diagnostics_paths(manifest)
    if len(paths) != 1:
        return [f"diagnostics_json_count:{len(paths)}"]
    metadata = json.loads((folder / paths[0]).read_text())
    if metadata.get("kind") != spec["intrusive_profile"] or metadata.get("intrusive") is not True:
        failures.append("diagnostics_kind_mismatch")
    errors = metadata.get("errors")
    if not isinstance(errors, list):
        failures.append("diagnostics_errors_unreadable")
    elif errors:
        failures.append(f"diagnostics_errors:{len(errors)}")
    if metadata.get("completed_requests") != summary.get("completed_requests"):
        failures.append("diagnostics_request_count_mismatch")
    if spec["intrusive_profile"] == "allocation":
        labels = {item.get("label") for item in (metadata.get("allocation") or {}).get("checkpoints", [])}
        if not {"start", "end"} <= labels:
            failures.append("allocation_checkpoints_incomplete")
    if spec["intrusive_profile"] == "calls" and not (metadata.get("calls") or {}).get("late_window_reached"):
        failures.append("calls_late_window_missing")
    return failures


def _local_config(spec: dict) -> lce.TrialConfig:
    return lce.TrialConfig(mode=spec["mode"], partition=spec["partition"], fault=spec["fault"], repetition=spec["repetition"], duration_s=spec["duration_s"], rate_hz=spec["rate_hz"])


def local_command(spec: dict, folder: Path, upstream: Path, python: str) -> list[str]:
    """Controller command; the campaign interpreter (pinned Python 3.12) also runs the supervisor."""
    return lce.trial_command(_local_config(spec), folder / "controller", backend=spec["backend"], upstream=upstream, supervisor_python=Path(python), python=python)


def _relocatable(command: list) -> list:
    """Compare commands independently of where the campaign folder now lives."""
    command = list(command)
    if command.count("--output-dir") == 1 and command.index("--output-dir") + 1 < len(command):
        index = command.index("--output-dir") + 1
        command[index] = "/".join(Path(command[index]).parts[-2:])
    return command


def _owned_processes(folder: Path) -> list[psutil.Process]:
    """Processes whose command line references this trial folder are experiment-owned by construction."""
    marker = str(folder) + os.sep
    owned = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            if process.pid != os.getpid() and any(marker in part for part in process.info["cmdline"] or ()):
                owned.append(process)
        except psutil.Error:
            continue
    return owned


def _stop_local_tree(process: subprocess.Popen, folder: Path) -> dict:
    """Stop the controller's group, then its supervisor and proxy, which run in their own sessions."""
    record = stop_process_group(process)
    leftovers = _owned_processes(folder)
    for child in leftovers:
        with contextlib.suppress(psutil.Error):
            child.terminate()
    _, alive = psutil.wait_procs(leftovers, timeout=5)
    for child in alive:
        with contextlib.suppress(psutil.Error):
            child.kill()
    _, alive = psutil.wait_procs(alive, timeout=5)
    record.update(owned_stopped=[child.pid for child in leftovers], owned_remaining=[child.pid for child in alive])
    return record


def _seal(folder: Path, result: dict) -> None:
    (folder / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {str(path.relative_to(folder)): digest(path) for path in sorted(folder.rglob("*")) if path.is_file()}
    (folder / "reproduction-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_local_trial(spec: dict, folder: Path, upstream: Path) -> dict:
    """Run one local-control controller as a supervised subprocess and seal its folder with a checksum manifest."""
    upstream, folder = upstream.resolve(), folder.resolve()
    python = sys.executable
    command = local_command(spec, folder, upstream, python)
    if git_fact(upstream, "rev-parse", "HEAD") != PINNED_COMMIT:
        raise ValueError("Upstream checkout does not match the paper's v5.1.1 commit")
    if git_fact(upstream, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Pinned upstream has modified tracked files")
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic_ns()
    state = {"phase": "provenance", "before": None, "returncode": None, "timed_out": False, "parent_error": None, "process_cleanup": None}
    pending = None
    try:
        state["before"] = source_hashes(upstream)
        provenance = {
            "stage": "local-control", "trial": spec, "command": command, "cwd": str(ROOT),
            "python": sys.version, "python_executable": python, "supervisor_python": python,
            "upstream": str(upstream), "upstream_commit": PINNED_COMMIT, "source_sha256": state["before"],
            "harness_sources": source_fingerprint(), "started_utc": datetime.now(UTC).isoformat(),
            "timeout_s": spec["timeout_s"], "resource_profile": LOCAL_RESOURCE_SCOPE,
            "environment_policy": "Dotenv disabled; secrets and preexisting provider settings removed.",
            "scope": "Evaluation-layer local supervisor and loopback proxy around pinned CP2N2; not an upstream feature.",
            "hardware_preflight": hardware_preflight(),
        }
        (folder / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        state["phase"] = "command"
        with ExitStack() as stack:
            stdout = stack.enter_context((folder / "stdout.log").open("x", encoding="utf-8"))
            stderr = stack.enter_context((folder / "stderr.log").open("x", encoding="utf-8"))
            process = None
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=reproduction_environment(folder, "local-control"), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    process.wait(timeout=spec["timeout_s"])
                except subprocess.TimeoutExpired:
                    state["timed_out"] = True
            finally:
                if process is not None:
                    state["process_cleanup"] = _stop_local_tree(process, folder)
                    state["returncode"] = process.returncode
        state["phase"] = "finished"
    except BaseException as exc:  # noqa: BLE001 - archived below, then re-raised unchanged
        pending = exc
        state["parent_error"] = {"type": type(exc).__name__, "message": str(exc)[:2000], "phase": state["phase"]}
    try:
        unchanged = state["before"] is not None and state["before"] == source_hashes(upstream)
        result = {
            "stage": "local-control", "returncode": state["returncode"], "timed_out": state["timed_out"],
            "elapsed_s": (time.monotonic_ns() - started) / 1e9, "timeout_s": spec["timeout_s"],
            "upstream_sources_unchanged": unchanged, "guard_reason": None, "profile": False, "profile_complete": None,
            "resource_profile": LOCAL_RESOURCE_SCOPE, "parent_error": state["parent_error"], "process_cleanup": state["process_cleanup"],
            "hardware_postflight": None if pending is not None and not isinstance(pending, Exception) else hardware_state(),
        }
        _seal(folder, result)
    except Exception:
        if pending is None:
            raise
        result = None
    if pending is not None:
        raise pending
    return result


def verify_local_trial(folder: Path, spec: dict) -> tuple[dict, dict | None]:
    """Check archive integrity and that the controller ran exactly the intended local-control trial."""
    manifest = _manifest(folder)
    missing = {"result.json", "provenance.json"} - set(manifest)
    if missing:
        raise ValueError(f"Manifest of {folder.name} lacks {sorted(missing)}")
    result = json.loads((folder / "result.json").read_text())
    provenance = json.loads((folder / "provenance.json").read_text())
    python = provenance.get("python_executable")
    if result.get("stage") != "local-control" or provenance.get("trial") != spec or not isinstance(python, str) or not isinstance(provenance.get("command"), list):
        raise ValueError(f"Recorded local-control trial {folder.name} does not match the intended trial")
    expected = local_command(spec, folder.resolve(), Path(str(provenance.get("upstream"))), python)
    if _relocatable(provenance["command"]) != _relocatable(expected):
        raise ValueError(f"Recorded command of {folder.name} does not match the intended trial")
    summary = None
    if "controller/config.json" in manifest:
        recorded = json.loads((folder / "controller/config.json").read_text())
        intended = (spec["trial_id"], spec["mode"], spec["partition"], spec["fault"], spec["duration_s"], spec["rate_hz"], spec["backend"], python)
        if tuple(recorded.get(key) for key in ("trial_id", "mode", "partition", "fault", "duration_s", "rate_hz", "backend", "supervisor_python")) != intended:
            raise ValueError(f"Recorded controller configuration of {folder.name} does not match the intended trial")
    if "controller/summary.json" in manifest:
        summary = json.loads((folder / "controller/summary.json").read_text())
    return result, summary


def classify_local_trial(result: dict, summary: dict | None, spec: dict) -> dict:
    """Failed predeclared assertions are measured outcomes; controller errors, timeouts and inconsistencies are infrastructure."""
    infrastructure, performance = [], []
    if result.get("timed_out"):
        infrastructure.append("timed_out")
    if result.get("parent_error"):
        infrastructure.append(f"parent_error:{(result['parent_error'] or {}).get('type')}")
    if result.get("upstream_sources_unchanged") is not True:
        infrastructure.append("upstream_sources_changed")
    cleanup = result.get("process_cleanup") or {}
    if cleanup.get("group_remaining") or cleanup.get("owned_remaining"):
        infrastructure.append("owned_processes_remaining")
    elif cleanup.get("owned_stopped"):
        infrastructure.append("owned_processes_left_by_controller")
    returncode = result.get("returncode")
    if summary is None:
        infrastructure.append("controller_summary_missing")
        if returncode != 0:
            infrastructure.append(f"returncode:{returncode}")
        return {"infrastructure_failures": infrastructure, "performance_failures": performance}
    # summary["backend"] is replaced by backend counters after analysis; the backend name is checked via controller/config.json.
    if tuple(summary.get(key) for key in ("trial_id", "mode", "scenario")) != (spec["trial_id"], spec["mode"], spec["scenario"]):
        infrastructure.append("summary_identity_mismatch")
    if summary.get("status") == "error" or summary.get("error"):
        infrastructure.append(f"controller_error:{(summary.get('error') or {}).get('type')}")
        if returncode != 2:
            infrastructure.append(f"returncode:{returncode}")
        return {"infrastructure_failures": infrastructure, "performance_failures": performance}
    assertions = summary.get("assertions")
    if not isinstance(assertions, list) or not assertions or not all(isinstance(item, dict) and isinstance(item.get("name"), str) and type(item.get("passed")) is bool for item in assertions):
        infrastructure.append("assertions_invalid")
        return {"infrastructure_failures": infrastructure, "performance_failures": performance}
    passed = all(item["passed"] for item in assertions)
    if summary.get("passed") is not passed or summary.get("status") != ("passed" if passed else "failed"):
        infrastructure.append("assertion_summary_inconsistent")
    if returncode != (0 if passed else 1):
        infrastructure.append(f"returncode:{returncode}")
    performance.extend(f"assertion:{item['name']}" for item in assertions if not item["passed"])
    return {"infrastructure_failures": infrastructure, "performance_failures": performance}


def assess_trial(folder: Path, spec: dict, config: dict) -> tuple[dict, dict | None, dict]:
    """Verify every check for one sealed trial, then classify it."""
    if spec["kind"] == "local-control":
        result, summary = verify_local_trial(folder, spec)
        return result, summary, classify_local_trial(result, summary, spec)
    result, summary = verify_trial(folder, spec, config)
    classification = classify_trial(result, summary)
    if summary is not None:
        classification["infrastructure_failures"].extend(diagnostic_failures(folder, spec, summary))
    return result, summary, classification


def _finished_before(spec: dict, history: list[dict], folder: Path) -> bool:
    kinds = [event["event"] for event in history]
    if not kinds:
        if folder.exists():
            raise RuntimeError(f"Trial folder without ledger record: {spec['trial_id']}")
        return False
    if kinds != ["started", "finished"] or history[0].get("trial") != spec:
        raise RuntimeError(f"Trial {spec['trial_id']} has ledger history {kinds}; it must not be resumed or redone in this campaign")
    return True


def _trial_timeout_s(spec: dict) -> float:
    if spec["kind"] == "local-control":
        return spec["timeout_s"]
    return spec["idle_s"] + spec["duration_s"] + spec["post_idle_s"] + SUSTAINED_TIMEOUT_MARGIN_S


def expected_trial_seconds(spec: dict) -> float:
    if spec["kind"] == "local-control":
        return spec["duration_s"] + LOCAL_OVERHEAD_S
    return 2 * spec["baseline_s"] + spec["idle_s"] + spec["duration_s"] + spec["post_idle_s"] + STARTUP_ALLOWANCE_S


def estimate_runtime(config: dict, plans: dict[str, list[dict]]) -> dict:
    specs = [spec for phase_specs in plans.values() for spec in phase_specs]
    wall = sum(expected_trial_seconds(spec) for spec in specs) + config["cooldown_s"] * max(0, len(specs) - 1)
    return {
        "trials": len(specs), "active_s": sum(spec["duration_s"] for spec in specs),
        "estimated_wall_s": wall, "estimated_wall_h": round(wall / 3600, 2),
        "assumptions": f"platform baselines before and after each sustained trial, {STARTUP_ALLOWANCE_S} s start/teardown per sustained trial, {LOCAL_OVERHEAD_S} s per local-control trial, {config['cooldown_s']} s cooldown between consecutive trials",
    }


def _host_preflight(specs: list[dict], output: Path) -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError("Run measurement phases with the isolated Python 3.12 interpreter")
    if any(spec["kind"] == "sustained" for spec in specs) and shutil.which("tegrastats") is None:
        raise RuntimeError("Resource profiling requires tegrastats")
    probe = output
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < MIN_FREE_DISK_BYTES:
        raise RuntimeError(f"Need at least {MIN_FREE_DISK_BYTES // 1024 ** 3} GiB free disk before a campaign; {free // 1024 ** 2} MiB available")


def intrusive_preflight(upstream: Path, profile: str) -> dict:
    """Run the real worker briefly in harness mode with one intrusive profile, in a scratch folder outside the campaign."""
    upstream = upstream.resolve()
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operating-limits-preflight-", dir=SCRATCH_ROOT) as scratch:
        workload = Path(scratch) / "workload"
        command = [sys.executable, str(ROOT / "evaluation/upstream_sustained.py"), "--upstream", str(upstream), "--output-dir", str(workload), "--duration-s", "0.5", "--rate-hz", "10", "--mode", "harness", "--idle-s", "0", "--intrusive-profile", profile]
        try:
            environment = {**reproduction_environment(Path(scratch), "sustained"), "TMPDIR": scratch}
            completed = subprocess.run(command, cwd=upstream, env=environment, capture_output=True, timeout=PREFLIGHT_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            return {"profile": profile, "returncode": None, "failures": ["timeout"], "diagnostics_path": None, "stderr_tail": ""}
        failures = [] if completed.returncode == 0 else [f"returncode:{completed.returncode}"]
        summary = json.loads((workload / "summary.json").read_text()) if (workload / "summary.json").exists() else None
        if summary is None:
            failures.append("summary_missing")
        elif summary.get("fatal_error"):
            fatal = summary["fatal_error"]
            failures.append(f"worker_fatal_error:{fatal.get('type')}:{str(fatal.get('message'))[:300]}" if isinstance(fatal, dict) else f"worker_fatal_error:{fatal}")
        elif summary.get("intrusive_profile") != profile:
            failures.append(f"summary_profile_mismatch:{summary.get('intrusive_profile')}")
        found = sorted(workload.rglob("diagnostics.json")) if workload.exists() else []
        if len(found) != 1:
            failures.append(f"diagnostics_json_count:{len(found)}")
        else:
            metadata = json.loads(found[0].read_text())
            if metadata.get("kind") != profile or metadata.get("errors"):
                failures.append(f"diagnostics_invalid:{metadata.get('kind')}:{metadata.get('errors')}")
        return {
            "profile": profile, "returncode": completed.returncode, "failures": failures,
            "diagnostics_path": str(found[0].relative_to(Path(scratch))) if len(found) == 1 else None,
            "stderr_tail": completed.stderr[-2000:].decode("utf-8", "replace"),
        }


def _execute_trial(spec: dict, folder: Path, upstream: Path, config: dict) -> dict:
    if spec["kind"] == "local-control":
        return run_local_trial(spec, folder, upstream)
    return run_stage(
        upstream, folder, "sustained", timeout_s=_trial_timeout_s(spec), profile=True, interval_ms=config["interval_ms"],
        baseline_s=spec["baseline_s"], duration_s=spec["duration_s"], rate_hz=spec["rate_hz"], mode=spec["mode"],
        idle_s=spec["idle_s"], diagnostic_counts=spec["diagnostic_counts"], intrusive_profile=spec["intrusive_profile"],
        post_idle_s=spec["post_idle_s"], **{key: spec[key] for key in VARIANT_DEFAULTS if key in spec},
    )


def _status_of(classification: dict) -> str:
    return "infrastructure_failure" if classification["infrastructure_failures"] else "performance_failure" if classification["performance_failures"] else "success"


def run_phase(config: dict, phase: str, output: Path, upstream: Path, *, resume: bool = False) -> list[dict]:
    trial_specs(config, phase)
    with measurement_lock():
        return _run_phase_locked(config, phase, output, upstream, resume=resume)


def _run_phase_locked(config: dict, phase: str, output: Path, upstream: Path, *, resume: bool, fingerprint: dict | None = None, cooldown_before_first: bool = False, preflighted: set | None = None) -> list[dict]:
    specs = trial_specs(config, phase)
    _host_preflight(specs, output)
    fingerprint = source_fingerprint() if fingerprint is None else fingerprint
    preflighted = set() if preflighted is None else preflighted
    frozen = {"config": config, "phase": phase, "source_sha256": fingerprint, "python": sys.executable, "upstream": str(upstream.resolve())}
    if resume:
        if not (output / "frozen.json").is_file():
            raise ValueError("Cannot resume without a frozen campaign configuration")
        if json.loads((output / "frozen.json").read_text()) != frozen:
            raise ValueError("Cannot resume with changed configuration or experiment code")
        unexpected = sorted({path.name for path in output.iterdir() if path.is_dir()} - {spec["trial_id"] for spec in specs})
        if unexpected:
            raise RuntimeError(f"Unexpected folders in campaign output: {unexpected}")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "frozen.json").write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    history = trial_history(read_ledger(output))
    records = []
    with (output / "ledger.jsonl").open("a", encoding="utf-8") as ledger:
        _record(ledger, "campaign_resumed" if resume else "campaign_started", phase=phase)
        current = None
        try:
            for spec in specs:
                folder = output / spec["trial_id"]
                if _finished_before(spec, history.get(spec["trial_id"], []), folder):
                    result, _, classification = assess_trial(folder, spec, config)
                    if classification["infrastructure_failures"]:
                        raise RuntimeError(f"Campaign was stopped at {spec['trial_id']} by {classification['infrastructure_failures']}; investigate before a separately documented new attempt")
                    records.append({"trial_id": spec["trial_id"], "result": result, **classification})
                    continue
                if source_fingerprint() != fingerprint:
                    raise RuntimeError("Experiment sources changed during the campaign; start a new, separately identified campaign")
                profile = spec.get("intrusive_profile", "none")
                if profile != "none" and profile not in preflighted:
                    check = intrusive_preflight(upstream, profile)
                    _record(ledger, "intrusive_preflight", **check)
                    if check["failures"]:
                        raise RuntimeError(f"Intrusive {profile} preflight failed before any {phase} trial: {check['failures']}")
                    preflighted.add(profile)
                if records or cooldown_before_first:
                    _say(f"{phase}: cooldown {config['cooldown_s']} s")
                    time.sleep(config["cooldown_s"])
                current = spec["trial_id"]
                _record(ledger, "started", trial_id=current, trial=spec)
                _say(f"{phase}: started {current} (~{expected_trial_seconds(spec):.0f} s)")
                with _heartbeat(f"{phase}/{current}", expected_trial_seconds(spec)):
                    result = _execute_trial(spec, folder, upstream, config)
                _record(ledger, "finished", trial_id=current, result=result)
                result, _, classification = assess_trial(folder, spec, config)
                records.append({"trial_id": current, "result": result, **classification})
                _say(f"{phase}: finished {current}: {_status_of(classification)} {classification['infrastructure_failures'] or classification['performance_failures'] or ''}")
                current = None
                if classification["infrastructure_failures"]:
                    _record(ledger, "campaign_stopped", stopping_trial_id=spec["trial_id"], reasons=classification["infrastructure_failures"])
                    break
        except BaseException as exc:
            _record(ledger, "exception" if isinstance(exc, Exception) else "interrupted", trial_id=current, error_type=type(exc).__name__, error=str(exc)[:2000])
            raise
    return records


def run_all(config: dict, output: Path, upstream: Path, *, resume: bool = False) -> dict[str, list[dict]]:
    """Run every phase in declaration order under one source fingerprint; stop at the first phase that is not fully accounted for."""
    order = phase_order(config)
    plans = campaign_specs(config)
    specs = [spec for phase_specs in plans.values() for spec in phase_specs]
    with measurement_lock():
        _host_preflight(specs, output)
        fingerprint = source_fingerprint()
        frozen = {"config": config, "phases": list(order), "source_sha256": fingerprint, "python": sys.executable, "upstream": str(upstream.resolve())}
        if resume:
            if not (output / "campaign.json").is_file():
                raise ValueError("Cannot resume without a frozen campaign configuration")
            if json.loads((output / "campaign.json").read_text()) != frozen:
                raise ValueError("Cannot resume with changed configuration or experiment code")
            unexpected = sorted({path.name for path in output.iterdir() if path.is_dir()} - set(order))
            if unexpected:
                raise RuntimeError(f"Unexpected folders in campaign output: {unexpected}")
        else:
            output.mkdir(parents=True, exist_ok=False)
            (output / "campaign.json").write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
        results: dict[str, list[dict]] = {}
        preflighted: set = set()
        with (output / "campaign-ledger.jsonl").open("a", encoding="utf-8") as ledger:
            _record(ledger, "campaign_resumed" if resume else "campaign_started", phases=list(order), estimate=estimate_runtime(config, plans))
            phase = None
            try:
                if not resume:
                    for profile in sorted({spec.get("intrusive_profile", "none") for spec in specs} - {"none"}):
                        check = intrusive_preflight(upstream, profile)
                        _record(ledger, "intrusive_preflight", **check)
                        if check["failures"]:
                            raise RuntimeError(f"Intrusive {profile} preflight failed before any measured trial: {check['failures']}")
                        preflighted.add(profile)
                for index, phase in enumerate(order):
                    folder = output / phase
                    _record(ledger, "phase_started", phase=phase)
                    _say(f"phase {index + 1}/{len(order)} {phase}: {len(plans[phase])} trials")
                    records = _run_phase_locked(config, phase, folder, upstream, resume=folder.exists(), fingerprint=fingerprint, cooldown_before_first=index > 0, preflighted=preflighted)
                    results[phase] = records
                    stopping = [row["trial_id"] for row in records if row["infrastructure_failures"]]
                    _record(ledger, "phase_finished", phase=phase, accounted=len(records), planned=len(plans[phase]), infrastructure_failures=stopping)
                    if stopping or len(records) != len(plans[phase]):
                        _record(ledger, "campaign_stopped", phase=phase, stopping_trial_ids=stopping)
                        break
            except BaseException as exc:
                _record(ledger, "exception" if isinstance(exc, Exception) else "interrupted", phase=phase, error_type=type(exc).__name__, error=str(exc)[:2000])
                raise
    return results


def resource_digest(folder: Path) -> dict | None:
    path = folder / "resource-summary.json"
    if not path.exists():
        return None
    windows = {}
    for name, window in json.loads(path.read_text())["windows"].items():
        power = window.get("power") or {}
        windows[name] = {
            "duration_s": window.get("duration_s"), "mean_power_w": power.get("platform_mean_power_w"),
            "energy_j": power.get("platform_energy_j_over_observed_interval"), "coverage_fraction": power.get("coverage_fraction"),
            "power_gaps": len(window.get("power_gaps_s") or []), "max_temperature_c": window.get("max_temperature_c"),
            "system_cpu_mean_percent": window.get("system_cpu_mean_percent"),
            "tree_rss_max_mib": window.get("observed_tree_rss_sum_max_mib"), "tree_pss_max_mib": window.get("observed_tree_pss_sum_max_mib"),
        }
    return windows


def power_estimate(resources: dict | None) -> dict:
    """Platform VDD_IN window means; only windows with adequate sensor coverage are used, and signs are kept."""
    if not resources:
        return {"steady_w": None, "idle_adjusted_w": None, "excluded_windows": [], "reason": "no_resource_summary"}
    usable, excluded = {}, []
    for name in ("platform_before", "ready_worker_idle", "steady_workload", "post_worker_idle", "platform_after"):
        window = resources.get(name)
        if window is None:
            continue
        if window["mean_power_w"] is not None and (window["coverage_fraction"] or 0) >= COVERAGE_MIN:
            usable[name] = window["mean_power_w"]
        else:
            excluded.append(name)
    steady = usable.get("steady_workload")
    references = [name for name in ("ready_worker_idle", "post_worker_idle") if name in usable]
    return {
        "platform_before_w": usable.get("platform_before"), "ready_idle_w": usable.get("ready_worker_idle"), "steady_w": steady,
        "post_idle_w": usable.get("post_worker_idle"), "platform_after_w": usable.get("platform_after"),
        "idle_adjusted_w": steady - mean(usable[name] for name in references) if steady is not None and references else None,
        "idle_reference": "+".join(references) or None, "excluded_windows": excluded, "minimum_coverage_fraction": COVERAGE_MIN,
    }


def diagnostic_digest(folder: Path) -> dict:
    """Paths and compact counters of retained-state and intrusive diagnostic artifacts listed in the sealed manifest."""
    manifest = json.loads((folder / "reproduction-manifest.json").read_text())
    result = {}
    if "workload/retained-state.jsonl" in manifest:
        try:
            samples = _jsonl(folder / "workload/retained-state.jsonl")
        except ValueError:
            samples = None
        result["retained_counts"] = {
            "path": "workload/retained-state.jsonl", "samples": None if samples is None else len(samples),
            "first": samples[0] if samples else None, "last": samples[-1] if samples else None,
            "max_collection_duration_ns": max((sample.get("collection_duration_ns") or 0 for sample in samples), default=None) if samples else None,
        }
    for path in _diagnostics_paths(manifest):
        metadata = json.loads((folder / path).read_text())
        base = Path(path).parent
        result["intrusive"] = {
            "role": "diagnostic only; timings from this trial are not primary evidence",
            "path": path, "kind": metadata.get("kind"), "errors": metadata.get("errors"),
            "files": [str(base / name) for name in metadata.get("files", []) if str(base / name) in manifest],
            "instrumentation": metadata.get("instrumentation"), "gc": metadata.get("gc"),
            "allocation_checkpoints": [
                {key: item.get(key) for key in ("label", "elapsed_ns", "raw_snapshot", "traced_current_bytes", "traced_peak_bytes", "tracemalloc_overhead_bytes", "total_size_diff_bytes", "total_count_diff")}
                for item in (metadata.get("allocation") or {}).get("checkpoints", [])
            ],
            "call_windows": [{key: item.get(key) for key in ("label", "start_elapsed_ns", "profiled_ns", "instrumentation_ns")} for item in (metadata.get("calls") or {}).get("windows", [])],
            "late_window_reached": (metadata.get("calls") or {}).get("late_window_reached"),
        }
    return result


def local_digest(summary: dict) -> dict:
    backend = summary.get("backend") if isinstance(summary.get("backend"), dict) else {}
    return {
        "status": summary.get("status"), "passed": summary.get("passed"),
        "failed_assertions": [item.get("name") for item in summary.get("assertions") or [] if isinstance(item, dict) and item.get("passed") is not True],
        "steps": summary.get("steps"), "timeline": summary.get("timeline"), "job_state": (summary.get("job") or {}).get("state"),
        "backend_counters": backend.get("counters"), "supervisor_peak_rss_kib": backend.get("supervisor_peak_rss_kib"),
        "proxy": summary.get("proxy"), "resource_profile": LOCAL_RESOURCE_SCOPE,
    }


def analyze_phase(output: Path) -> dict:
    """Verify every sealed trial of one phase before summarizing it; writes analysis.json in the phase folder."""
    frozen = json.loads((output / "frozen.json").read_text())
    config = frozen["config"]
    specs = trial_specs(config, frozen["phase"])
    unexpected = sorted({path.name for path in output.iterdir() if path.is_dir()} - {spec["trial_id"] for spec in specs})
    if unexpected:
        raise ValueError(f"Unexpected folders in campaign output: {unexpected}")
    history = trial_history(read_ledger(output))
    trials = {}
    for spec in specs:
        folder = output / spec["trial_id"]
        kinds = [event["event"] for event in history.get(spec["trial_id"], [])]
        entry = {"trial": spec, "ledger": kinds}
        if not folder.exists():
            entry["status"] = "aborted_without_output" if kinds else "not_run"
        elif not (folder / "reproduction-manifest.json").exists():
            entry.update(status="partial", retained_files=sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()))
        else:
            result, summary, classification = assess_trial(folder, spec, config)
            if kinds != ["started", "finished"]:
                classification["infrastructure_failures"].append(f"ledger_history:{','.join(kinds) or 'none'}")
            entry.update(terminal=result, **classification)
            if spec["kind"] == "local-control":
                entry["local"] = local_digest(summary) if summary is not None else None
            else:
                entry.update(primary_timing=spec["intrusive_profile"] == "none", resources=resource_digest(folder), diagnostics=diagnostic_digest(folder))
                try:
                    entry["workload"] = summarize_requests(folder) if summary is not None else None
                except (OSError, KeyError, ValueError) as exc:
                    entry.update(workload=None, workload_error=f"{type(exc).__name__}: {exc}")
                    classification["infrastructure_failures"].append("workload_unreconciled")
                if entry["workload"] and entry["workload"]["completed_requests"] and spec["intrusive_profile"] == "none":
                    entry["spikes"] = spike_analysis.analyze_workload(folder / "workload", spec["rate_hz"])
            entry["status"] = _status_of(classification)
        trials[spec["trial_id"]] = entry
    counts = dict(Counter(entry["status"] for entry in trials.values()))
    analysis = {"phase": frozen["phase"], "planned_trials": len(specs), "status_counts": counts, "trials": trials}
    (output / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return analysis


def _stats(values) -> dict:
    values = [value for value in values if value is not None]
    if not values:
        return {"n": 0, "median": None, "mean": None, "stdev": None, "min": None, "max": None}
    return {"n": len(values), "median": median(values), "mean": mean(values), "stdev": stdev(values) if len(values) > 1 else None, "min": min(values), "max": max(values)}


TRIAL_COUNTERS = ("planned_releases", "completed_requests", "successful_requests", "skipped_releases", "deadline_misses", "late_dispatches", "maintenance_failures", "unexpected_backends", "achieved_requests_per_s")


def _spike_row(spikes: dict | None) -> dict | None:
    if not spikes:
        return None
    gc = spikes.get("gc") or {}
    return {
        "instrumented": spikes["instrumented"], "spikes": spikes["spikes"], "causes": spikes["causes"], "off_cpu_subcauses": spikes.get("off_cpu_subcauses"), "explained_fraction": spikes["explained_fraction"],
        "spikes_with_full_collection": spikes["spikes_with_full_collection"], "full_collections": len(gc.get("full_collections") or []) if gc else None,
        "full_collection_max_ms": gc.get("full_collection_max_ms"), "invocation_slope_ms_per_1k": spikes["invocation_trend"]["slope_ms_per_1k_requests"],
        "invocation_last_window_median_ms": spikes["invocation_trend"]["last_window_median_ms"],
    }


def _trial_row(trial_id: str, entry: dict) -> dict:
    workload = entry.get("workload") or {}
    metrics = workload.get("metrics") or {}

    def stats(name: str) -> dict:
        return {field: (metrics.get(name) or {}).get(field) for field in ("median", "p95", "max")}

    return {
        "trial_id": trial_id, "status": entry["status"], "repetition": entry["trial"]["repetition"],
        "lifecycle_variant": workload.get("lifecycle_variant", entry["trial"].get("lifecycle_variant", "pinned")),
        "retention_limit": workload.get("retention_limit", entry["trial"].get("retention_limit")),
        "gc_policy": workload.get("gc_policy", entry["trial"].get("gc_policy", "default")),
        "gc_policy_summary": workload.get("gc_policy_summary"),
        "lifecycle_variant_final_counts": workload.get("lifecycle_variant_final_counts"),
        **{key: workload.get(key) for key in TRIAL_COUNTERS},
        "cycle_ms": stats("cycle_ms"), "invocation_ms": stats("invocation_ms"), "release_to_completion_ms": stats("release_to_completion_ms"),
        "retained_last": (workload.get("retained_last") or {}).get("collections"),
        "steady_tree_rss_max_mib": ((entry.get("resources") or {}).get("steady_workload") or {}).get("tree_rss_max_mib"),
        "power": power_estimate(entry.get("resources")), "spikes": _spike_row(entry.get("spikes")),
        "infrastructure_failures": entry.get("infrastructure_failures", []), "performance_failures": entry.get("performance_failures", []),
    }


def _across(rows: list[dict]) -> dict:
    measured = [row for row in rows if row["completed_requests"] is not None and not row["infrastructure_failures"]]
    return {
        "method": "statistics over per-trial values of trials without infrastructure failures; requests are not pooled as replicates",
        "trials_with_data": len(measured),
        "cycle_median_ms": _stats(row["cycle_ms"]["median"] for row in measured),
        "cycle_p95_ms": _stats(row["cycle_ms"]["p95"] for row in measured),
        "invocation_median_ms": _stats(row["invocation_ms"]["median"] for row in measured),
        "invocation_p95_ms": _stats(row["invocation_ms"]["p95"] for row in measured),
        "achieved_requests_per_s": _stats(row["achieved_requests_per_s"] for row in measured),
        "totals": {key: sum(row[key] or 0 for row in measured) for key in TRIAL_COUNTERS if key != "achieved_requests_per_s"},
        "steady_power_w": _stats(row["power"]["steady_w"] for row in measured),
        "idle_adjusted_power_w": _stats(row["power"]["idle_adjusted_w"] for row in measured),
        "invocation_slope_ms_per_1k": _stats((row["spikes"] or {}).get("invocation_slope_ms_per_1k") for row in measured),
        "spikes": _stats((row["spikes"] or {}).get("spikes") for row in measured),
        "explained_spike_fraction": _stats((row["spikes"] or {}).get("explained_fraction") for row in measured),
        "full_collection_max_ms": _stats((row["spikes"] or {}).get("full_collection_max_ms") for row in measured),
        "spikes_with_full_collection": _stats((row["spikes"] or {}).get("spikes_with_full_collection") for row in measured),
        "gc_policy": sorted({row["gc_policy"] for row in measured}),
        "gc_freeze_runs": _stats(((row["gc_policy_summary"] or {}).get("runs")) for row in measured),
        "gc_freeze_total_ms": _stats(((row["gc_policy_summary"] or {}).get("total_ms")) for row in measured),
        "gc_freeze_max_ms": _stats(((row["gc_policy_summary"] or {}).get("max_ms")) for row in measured),
        "gc_frozen_objects_end": _stats(((row["gc_policy_summary"] or {}).get("frozen_objects_at_end")) for row in measured),
        "retained_history_last": _stats(((row["retained_last"] or {}).get("lifecycle.history")) for row in measured),
        "steady_tree_rss_max_mib": _stats(row["steady_tree_rss_max_mib"] for row in measured),
    }


def _paired_power(trials: dict) -> dict:
    pairs: dict[int, dict[int, tuple]] = {}
    for trial_id, entry in trials.items():
        spec = entry["trial"]
        usable = entry["status"] in ("success", "performance_failure")
        pairs.setdefault(spec["pair"], {})[spec["pair_position"]] = (spec, trial_id, power_estimate(entry.get("resources")) if usable else None)
    first = pairs[min(pairs)]
    reference, other = first[1][0]["mode"], first[2][0]["mode"]
    rows = []
    for pair in sorted(pairs):
        by_mode = {spec["mode"]: (trial_id, power) for spec, trial_id, power in pairs[pair].values()}

        def value(mode: str, field: str, by_mode=by_mode):
            power = by_mode.get(mode, (None, None))[1]
            return None if power is None else power.get(field)

        def delta(field: str, value=value):
            first_value, second_value = value(reference, field), value(other, field)
            return None if first_value is None or second_value is None else second_value - first_value

        leading = pairs[pair].get(1)
        rows.append({
            "pair": pair, "order": leading[0]["pair_order"] if leading else None,
            "trials": {mode: trial_id for mode, (trial_id, _) in by_mode.items()},
            "platform_baseline_w": leading[2].get("platform_before_w") if leading and leading[2] else None,
            "steady_w": {mode: value(mode, "steady_w") for mode in by_mode}, "idle_adjusted_w": {mode: value(mode, "idle_adjusted_w") for mode in by_mode},
            "delta_steady_w": delta("steady_w"), "delta_idle_adjusted_w": delta("idle_adjusted_w"),
        })
    return {
        "difference": f"{other}_minus_{reference}", "pairs": rows,
        "delta_steady_w": _stats(row["delta_steady_w"] for row in rows),
        "delta_idle_adjusted_w": _stats(row["delta_idle_adjusted_w"] for row in rows),
        "excluded_pairs": [row["pair"] for row in rows if row["delta_steady_w"] is None],
        "note": f"Platform VDD_IN over windows with >= {COVERAGE_MIN:.0%} sensor coverage; across-pair spread, signs kept, not control-plane-only power",
    }


def _local_conditions(trials: dict) -> dict:
    groups: dict[str, list[dict]] = {}
    for trial_id, entry in trials.items():
        spec, local = entry["trial"], entry.get("local") or {}
        steps = local.get("steps") or {}
        groups.setdefault(f"{spec['mode']}/{spec['scenario']}", []).append({
            "trial_id": trial_id, "status": entry["status"], "failed_assertions": local.get("failed_assertions") or [],
            "steps_executed": steps.get("executed"), "steps_succeeded": steps.get("succeeded"), "steps_missed": steps.get("missed"),
            "fallback_steps": len(steps.get("fallback_steps") or []), "fault_consumed_phase": (local.get("timeline") or {}).get("fault_consumed_phase"),
            "infrastructure_failures": entry.get("infrastructure_failures", []),
        })
    return {key: {
        "trials": rows, "status_counts": dict(Counter(row["status"] for row in rows)),
        "failed_assertion_counts": dict(Counter(name for row in rows for name in row["failed_assertions"])),
        "steps_missed": _stats(row["steps_missed"] for row in rows), "resource_profile": LOCAL_RESOURCE_SCOPE,
    } for key, rows in groups.items()}


def results_summary(analyses: dict[str, dict], phases=ALL_PHASES) -> dict:
    """Results-first condition summary across phases; offline only, nothing is rendered into a manuscript."""
    phases = {}
    for phase, analysis in analyses.items():
        trials = analysis["trials"]
        entry = {"planned_trials": analysis["planned_trials"], "status_counts": analysis["status_counts"]}
        if any(item["trial"]["kind"] == "local-control" for item in trials.values()):
            entry.update(kind="local-control", conditions=_local_conditions(trials))
        else:
            groups: dict[str, list] = {}
            roles = {}
            for trial_id, item in trials.items():
                spec = item["trial"]
                key = f"{spec['mode']}@{spec['rate_hz']:g}Hz" + ("" if spec["intrusive_profile"] == "none" else f"/{spec['intrusive_profile']}") + ("" if "lifecycle_variant" not in spec else f"/{spec['lifecycle_variant']}") + ("" if spec.get("gc_policy", "default") == "default" else f"/gc-{spec['gc_policy']}")
                groups.setdefault(key, []).append(_trial_row(trial_id, item))
                roles[key] = "primary" if spec["intrusive_profile"] == "none" else "diagnostic_not_primary"
            entry.update(kind="sustained", conditions={key: {"timing_role": roles[key], "trials": rows, "across_trials": _across(rows)} for key, rows in groups.items()})
            if any("pair" in item["trial"] for item in trials.values()):
                entry["paired_power"] = _paired_power(trials)
            diagnostics = {trial_id: item["diagnostics"]["intrusive"] for trial_id, item in trials.items() if (item.get("diagnostics") or {}).get("intrusive")}
            if diagnostics:
                entry["intrusive_diagnostics"] = diagnostics
        phases[phase] = entry
    return {
        "schema_version": 1, "generated_utc": datetime.now(UTC).isoformat(), "phases": phases,
        "phases_without_output": [phase for phase in phases if phase not in analyses],
        "notes": [
            "Generated offline from verified trial archives; never run concurrently with measurements.",
            "Per-trial nearest-rank statistics; across-trial values summarize trials, not pooled requests.",
            "Deadlines are experimental release-relative deadlines of one interarrival period unless stated otherwise.",
            "Intrusive allocation/calls trials are diagnostics only; their timings are not primary evidence.",
            "Power is platform VDD_IN; idle-adjusted values subtract the same worker's ready/post idle windows.",
        ],
    }


def _fmt(value, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def results_markdown(summary: dict) -> str:
    lines = ["# Operating-limits results summary", "", *(f"- {note}" for note in summary["notes"]), ""]
    for phase, entry in summary["phases"].items():
        lines += [f"## {phase}", "", f"Planned trials: {entry['planned_trials']}. Status counts: {entry['status_counts']}.", ""]
        if entry["kind"] == "local-control":
            lines += ["| Condition | Trials | Status counts | Failed assertions | Missed steps (median) |", "| --- | ---: | --- | --- | ---: |"]
            for key, condition in entry["conditions"].items():
                lines.append(f"| {key} | {len(condition['trials'])} | {condition['status_counts']} | {condition['failed_assertion_counts'] or '-'} | {_fmt(condition['steps_missed']['median'])} |")
        else:
            lines += [
                "| Condition | Role | Trials with data | Cycle median ms | Cycle p95 ms | Achieved req/s | Deadline misses | Skipped releases | Steady W | Idle-adjusted W |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for key, condition in entry["conditions"].items():
                across = condition["across_trials"]
                lines.append(
                    f"| {key} | {condition['timing_role']} | {across['trials_with_data']} | {_fmt(across['cycle_median_ms']['median'], 3)} | {_fmt(across['cycle_p95_ms']['median'], 3)} "
                    f"| {_fmt(across['achieved_requests_per_s']['median'])} | {across['totals']['deadline_misses']} | {across['totals']['skipped_releases']} "
                    f"| {_fmt(across['steady_power_w']['median'], 3)} | {_fmt(across['idle_adjusted_power_w']['median'], 3)} |"
                )
            if "paired_power" in entry:
                paired = entry["paired_power"]
                lines += ["", f"Paired {paired['difference']}: steady W median {_fmt(paired['delta_steady_w']['median'], 3)} (n={paired['delta_steady_w']['n']}, stdev {_fmt(paired['delta_steady_w']['stdev'], 3)}); idle-adjusted W median {_fmt(paired['delta_idle_adjusted_w']['median'], 3)} (n={paired['delta_idle_adjusted_w']['n']}). Excluded pairs: {paired['excluded_pairs'] or 'none'}."]
            if any(row["spikes"] and row["spikes"]["instrumented"] for condition in entry["conditions"].values() for row in condition["trials"]):
                lines += [
                    "", "Spike attribution (per trial; threshold 10 ms over the 30-s block median, plus every deadline miss):", "",
                    "| Trial | Lifecycle | GC policy | Invocation slope ms/1k req | Last-window median ms | Spikes | Causes | Off-CPU sub-causes | Explained | Gen-2 collections | Max gen-2 ms | Freeze max ms | Frozen objects | History entries | RSS max MiB |",
                    "| --- | --- | --- | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
                for condition in entry["conditions"].values():
                    for row in condition["trials"]:
                        spikes = row["spikes"] or {}
                        lines.append(
                            f"| {row['trial_id']} | {row['lifecycle_variant']} | {row['gc_policy']} | {_fmt(spikes.get('invocation_slope_ms_per_1k'), 3)} | {_fmt(spikes.get('invocation_last_window_median_ms'), 2)} | {_fmt(spikes.get('spikes'))} "
                            f"| {spikes.get('causes') or '-'} | {spikes.get('off_cpu_subcauses') or '-'} | {_fmt(spikes.get('explained_fraction'), 3)} | {_fmt(spikes.get('full_collections'))} | {_fmt(spikes.get('full_collection_max_ms'), 1)} "
                            f"| {_fmt((row['gc_policy_summary'] or {}).get('max_ms'), 3)} | {_fmt((row['gc_policy_summary'] or {}).get('frozen_objects_at_end'))} "
                            f"| {_fmt((row['retained_last'] or {}).get('lifecycle.history'))} | {_fmt(row['steady_tree_rss_max_mib'], 1)} |"
                        )
        lines.append("")
    return "\n".join(lines)


def analyze_campaign(output: Path) -> dict:
    """Analyze a phase folder or an --phase all campaign root; refuses while any campaign holds the measurement lock."""
    with measurement_lock():
        if (output / "frozen.json").is_file():
            analysis = analyze_phase(output)
            analyses = {analysis["phase"]: analysis}
            phases = (analysis["phase"],)
        elif (output / "campaign.json").is_file():
            phases = tuple(json.loads((output / "campaign.json").read_text())["phases"])
            analyses = {phase: analyze_phase(output / phase) for phase in phases if (output / phase / "frozen.json").is_file()}
        else:
            raise ValueError("Not a phase folder (frozen.json) or campaign root (campaign.json)")
        summary = results_summary(analyses, phases)
        (output / "results-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        (output / "results-summary.md").write_text(results_markdown(summary), encoding="utf-8")
    return summary


def _compact(event: dict) -> dict:
    keys = ("event", "utc", "phase", "trial_id", "stopping_trial_id", "stopping_trial_ids", "reasons", "error_type", "error", "profile", "failures")
    return {key: (event[key][:300] if isinstance(event[key], str) else event[key]) for key in keys if key in event}


def phase_status(output: Path) -> dict:
    """Concise per-trial states from the ledger; no checksum verification (analyze performs every check)."""
    if not (output / "frozen.json").is_file():
        return {"state": "no_frozen_configuration" if output.exists() else "not_started"}
    frozen = json.loads((output / "frozen.json").read_text())
    specs = trial_specs(frozen["config"], frozen["phase"])
    events = read_ledger(output)
    history = trial_history(events)
    trials = {}
    for spec in specs:
        items = history.get(spec["trial_id"], [])
        kinds = [event["event"] for event in items]
        if not kinds:
            state = "not_run"
        elif kinds == ["started", "finished"]:
            result = items[1].get("result") or {}
            state = f"finished rc={result.get('returncode')}" + (f" guard={result['guard_reason']}" if result.get("guard_reason") else "") + (" timed_out" if result.get("timed_out") else "")
        elif kinds == ["started"]:
            state = "started_without_finish_record"
        else:
            state = "irregular:" + ",".join(kinds)
        if kinds and not (output / spec["trial_id"] / "reproduction-manifest.json").exists():
            state += " no_manifest"
        trials[spec["trial_id"]] = state
    return {
        "phase": frozen["phase"], "planned_trials": len(specs),
        "state_counts": dict(Counter(state.split(" ")[0] for state in trials.values())), "trials": trials,
        "stops": [_compact(event) for event in events if event["event"] in ("campaign_stopped", "exception", "interrupted")][-3:],
        "last_event": _compact(events[-1]) if events else None,
    }


def campaign_status(output: Path) -> dict:
    if (output / "frozen.json").is_file() or not (output / "campaign.json").is_file():
        return phase_status(output)
    events = read_ledger_file(output / "campaign-ledger.jsonl")
    phases = json.loads((output / "campaign.json").read_text())["phases"]
    return {"phases": {phase: phase_status(output / phase) for phase in phases}, "campaign_events": [_compact(event) for event in events[-5:]]}


def _run_report(records: list[dict], planned: int) -> dict:
    return {
        "planned": planned, "accounted": len(records),
        "complete": len(records) == planned and not any(row["infrastructure_failures"] for row in records),
        "infrastructure_failures": {row["trial_id"]: row["infrastructure_failures"] for row in records if row["infrastructure_failures"]},
        "performance_failures": {row["trial_id"]: row["performance_failures"] for row in records if row["performance_failures"]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["validate", "run", "analyze", "status"])
    parser.add_argument("--config", type=Path, default=ROOT / "docs/experiments/operating-limits/plan.json")
    parser.add_argument("--phase", default="pilot", help="validate/run: one phase of the configuration, or 'all' for its declared order")
    parser.add_argument("--output-dir", type=Path, help="run: new (or --resume) phase folder, or campaign root with --phase all; analyze/status: either kind")
    parser.add_argument("--upstream", type=Path, default=ROOT / "_temp/cp2n2-v5.1.1")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--list-trials", action="store_true", help="validate: also print every planned trial")
    args = parser.parse_args()
    config = json.loads(args.config.read_text()) if args.command in ("validate", "run") else None
    phases = phase_order(config) if config is not None and args.phase == "all" else (args.phase,)
    if args.command == "validate":
        plans = campaign_specs(config, phases)
        report = {
            "phases": {phase: {"trials": len(specs), "active_s": sum(spec["duration_s"] for spec in specs), "estimated_wall_s": estimate_runtime(config, {phase: specs})["estimated_wall_s"]} for phase, specs in plans.items()},
            "estimate": estimate_runtime(config, plans), "source_sha256": source_fingerprint(),
        }
        if args.list_trials:
            report["trials"] = plans
        print(json.dumps(report, indent=2))
        return
    if args.output_dir is None:
        parser.error("This command requires --output-dir")
    output = args.output_dir.resolve()
    if args.command == "run":
        plans = campaign_specs(config, phases)
        if args.phase == "all":
            results = run_all(config, output, args.upstream, resume=args.resume)
        else:
            results = {args.phase: run_phase(config, args.phase, output, args.upstream, resume=args.resume)}
        report = {phase: _run_report(records, len(plans[phase])) for phase, records in results.items()}
        print(json.dumps({"phases": report, "not_reached": [phase for phase in phases if phase not in results]}, indent=2))
        if len(results) != len(phases) or not all(item["complete"] for item in report.values()):
            raise SystemExit(2)
    elif args.command == "analyze":
        summary = analyze_campaign(output)
        brief = {phase: {"planned_trials": entry["planned_trials"], "status_counts": entry["status_counts"]} for phase, entry in summary["phases"].items()}
        print(json.dumps({"phases": brief, "summary": str(output / "results-summary.json"), "markdown": str(output / "results-summary.md")}, indent=2))
    else:
        print(json.dumps(campaign_status(output), indent=2))


if __name__ == "__main__":
    main()