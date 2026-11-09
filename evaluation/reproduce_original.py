"""Run the original paper's pinned release without modifying its evaluators."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import psutil

from evaluation.resource_monitor import stop_process_group
from evaluation.upstream_sustained import INTRUSIVE_PROFILES, validate_options

PINNED_COMMIT = "36fbae002b0a9e066416ea9cafbb966a33fe53bb"
ROOT = Path(__file__).resolve().parents[1]
# Every parent-monitor and worker module whose code shapes a run or its measurements.
HARNESS_SOURCES = tuple(ROOT / "evaluation" / name for name in (
    "__init__.py", "reproduce_original.py", "resource_monitor.py", "evaluate_tegra_resources.py",
    "upstream_sustained.py", "growth_diagnostics.py", "lifecycle_variants.py",
))
OUTPUT_LIMIT_BYTES = 64 * 1024
WORKLOAD_SUMMARY_LIMIT_BYTES = 16 * 1024 ** 2
DISK_RESERVE_BYTES = 512 * 1024 ** 2
CPUFREQ_FIELDS = ("scaling_governor", "scaling_driver", "scaling_min_freq", "scaling_max_freq", "scaling_cur_freq", "cpuinfo_min_freq", "cpuinfo_max_freq")
logger = logging.getLogger(__name__)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def git_fact(upstream: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(upstream), *arguments], capture_output=True, text=True, check=True).stdout.strip()


def source_hashes(upstream: Path) -> dict:
    paths = subprocess.run(["git", "-C", str(upstream), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    return {name.decode(): digest(upstream / name.decode()) for name in paths if name and (upstream / name.decode()).is_file()}


def stage_command(stage: str, output_dir: Path, *, upstream: Path | None = None, duration_s: float = 300, rate_hz: float = 10, mode: str = "orchestrated", deadline_ms: float | None = None, idle_s: float = 3, diagnostic_counts: bool = False, intrusive_profile: str = "none", post_idle_s: float = 0, lifecycle_variant: str = "pinned", retention_limit: int | None = None, spike_instrumentation: bool = False, gc_policy: str = "default") -> list[str]:
    commands = {
        "tests": ["-m", "pytest", "-q"],
        "core": ["-m", "evaluation.run_all_evaluations"],
        "overhead": ["-m", "evaluation.evaluate_overhead"],
        "http": ["-m", "evaluation.evaluate_externalized_backend"],
        "distributed": ["-m", "evaluation.evaluate_distributed_testbed"],
        "e3-control": ["-m", "evaluation.agent_to_pnn_campaign", "--planner", "reference", "--execute-e3", "--output-dir", str(output_dir / "control-trace")],
        "e3-provider": ["-m", "cl.app.run", "cl-apps/cp2n2-biopattern-gate", "cl-apps/cp2n2-biopattern-gate/default.json", str(output_dir / "provider-trace")],
        "sustained": [str(ROOT / "evaluation/upstream_sustained.py"), "--upstream", str(upstream or ROOT / "_temp/cp2n2-v5.1.1"), "--output-dir", str(output_dir / "workload"), "--duration-s", str(duration_s), "--rate-hz", str(rate_hz)],
    }
    if stage not in commands:
        raise ValueError("Only non-hosted, software-only reproduction stages are permitted")
    if stage != "sustained" and (intrusive_profile != "none" or post_idle_s != 0 or lifecycle_variant != "pinned" or retention_limit is not None or spike_instrumentation or gc_policy != "default"):
        raise ValueError("Intrusive profiles, post-idle windows, lifecycle variants and spike instrumentation apply only to the sustained stage")
    if stage == "sustained":
        commands[stage].extend(["--mode", mode, "--idle-s", str(idle_s)])
        if deadline_ms is not None:
            commands[stage].extend(["--deadline-ms", str(deadline_ms)])
        if diagnostic_counts:
            commands[stage].append("--diagnostic-counts")
        # Defaults add no flags, so earlier sustained commands remain byte-identical.
        if intrusive_profile != "none":
            commands[stage].extend(["--intrusive-profile", intrusive_profile])
        if post_idle_s != 0:
            commands[stage].extend(["--post-idle-s", str(post_idle_s)])
        if lifecycle_variant != "pinned":
            commands[stage].extend(["--lifecycle-variant", lifecycle_variant])
        if retention_limit is not None:
            commands[stage].extend(["--retention-limit", str(retention_limit)])
        if spike_instrumentation:
            commands[stage].append("--spike-instrumentation")
        if gc_policy != "default":
            commands[stage].extend(["--gc-policy", gc_policy])
    return [sys.executable, *commands[stage]]


def reproduction_environment(output_dir: Path, stage: str) -> dict:
    environment = {
        name: value for name, value in os.environ.items()
        if not re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", name, re.IGNORECASE)
        and not name.startswith(("CL_", "CP2N2_", "PHYSMCP_"))
        and name not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(PYTHON_DOTENV_DISABLED="1", CP2N2_RESULTS_DIR=str(output_dir / "evaluations"), MPLBACKEND="Agg")
    if stage == "e3-provider":
        environment.update(PYTHONUTF8="1", CL_SDK_VISUALISATION="0", CL_SDK_ACCELERATED_TIME="1")
    return environment


def harness_sources() -> dict:
    return {str(path.relative_to(ROOT)): digest(path) for path in HARNESS_SOURCES}


def _read_fact(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return handle.read(OUTPUT_LIMIT_BYTES).decode("utf-8", "replace").strip().strip("\0")
    except OSError:
        return None


def _query(command: list[str]) -> dict:
    try:
        fact = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"unavailable": type(exc).__name__}
    return {
        "returncode": fact.returncode, "output": fact.stdout[:OUTPUT_LIMIT_BYTES].decode("utf-8", "replace").strip(),
        "output_truncated": len(fact.stdout) > OUTPUT_LIMIT_BYTES,
    }


def hardware_state() -> dict:
    """Read-only snapshot of mutable platform state; nothing is configured."""
    cpu = Path("/sys/devices/system/cpu")
    return {
        "monotonic_ns": time.monotonic_ns(),
        "cpu_online": _read_fact(cpu / "online"), "cpu_present": _read_fact(cpu / "present"),
        "cpufreq": {policy.parent.name: {name: _read_fact(policy / name) for name in CPUFREQ_FIELDS} for policy in sorted(cpu.glob("cpu[0-9]*/cpufreq"))},
        "power_mode": _query(["nvpmodel", "-q"]),
    }


def hardware_preflight() -> dict:
    meminfo = _read_fact(Path("/proc/meminfo")) or ""
    uname = platform.uname()
    return {
        **hardware_state(),
        "kernel": {"system": uname.system, "release": uname.release, "version": uname.version, "machine": uname.machine},
        "mem_total": next((line.split(":", 1)[1].strip() for line in meminfo.splitlines() if line.startswith("MemTotal:")), None),
        "monitor_python": sys.executable,
    }


def load_workload_summary(path: Path) -> dict:
    if path.stat().st_size > WORKLOAD_SUMMARY_LIMIT_BYTES:
        raise ValueError("Workload summary exceeds the size limit")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise TypeError("Workload summary is not an object")
    return summary


def workload_windows(summary: dict, command: list[int]) -> tuple[dict, dict]:
    """Worker phase windows with valid integer bounds inside the command window; others are reported, not used."""
    bounds = {
        "steady_workload": ("started_ns", "finished_ns"),
        "ready_worker_idle": ("ready_ns", "started_ns"),
        "post_worker_idle": ("post_idle_started_ns", "post_idle_finished_ns"),
    }
    windows, ignored = {}, {}
    for name, keys in bounds.items():
        lower, upper = (summary.get(key) for key in keys)
        if name == "post_worker_idle" and lower is None and upper is None:
            continue
        if type(lower) is not int or type(upper) is not int:
            ignored[name] = "missing_bounds"
        elif lower >= upper:
            ignored[name] = "inverted_bounds"
        elif not (command[0] <= lower and upper <= command[1]):
            ignored[name] = "outside_command"
        else:
            windows[name] = [lower, upper]
    return windows, ignored


def profile_complete(profile_summary: dict | None) -> bool:
    """Coverage of the whole command by both monitors; deliberately independent of the worker's exit status."""
    command = (profile_summary or {}).get("windows", {}).get("command")
    return bool(
        command
        and command["sensor_samples"] >= 2
        and command["process_tree_samples"] >= 2
        and command["max_temperature_c"] is not None
        and command["power"]["coverage_fraction"] >= 0.8
    )


def run_stage(upstream: Path, output_dir: Path, stage: str, timeout_s: float = 1800, *, profile: bool = False, interval_ms: int = 250, baseline_s: float = 3, duration_s: float = 300, rate_hz: float = 10, mode: str = "orchestrated", deadline_ms: float | None = None, idle_s: float = 3, diagnostic_counts: bool = False, intrusive_profile: str = "none", post_idle_s: float = 0, lifecycle_variant: str = "pinned", retention_limit: int | None = None, spike_instrumentation: bool = False, gc_policy: str = "default") -> dict:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    if type(interval_ms) is not int or interval_ms <= 0 or not math.isfinite(baseline_s) or baseline_s < 0:
        raise ValueError("Invalid profiling interval or baseline duration")
    if profile and shutil.which("tegrastats") is None:
        raise RuntimeError("Resource profiling requires tegrastats")
    if stage == "sustained" and (not profile or not math.isfinite(duration_s) or duration_s <= 0 or not math.isfinite(rate_hz) or not 0 < rate_hz <= 1000):
        raise ValueError("Sustained trials require profiling and valid duration/rate settings")
    variant = {"lifecycle_variant": lifecycle_variant, "retention_limit": retention_limit, "spike_instrumentation": spike_instrumentation, "gc_policy": gc_policy}
    if stage == "sustained":
        validate_options(duration_s, rate_hz, mode, deadline_ms, idle_s, post_idle_s=post_idle_s, intrusive_profile=intrusive_profile, **variant)
    upstream = upstream.resolve()
    output_dir = output_dir.resolve()
    command = stage_command(stage, output_dir, upstream=upstream, duration_s=duration_s, rate_hz=rate_hz, mode=mode, deadline_ms=deadline_ms, idle_s=idle_s, diagnostic_counts=diagnostic_counts, intrusive_profile=intrusive_profile, post_idle_s=post_idle_s, **variant)
    if git_fact(upstream, "rev-parse", "HEAD") != PINNED_COMMIT:
        raise ValueError("Upstream checkout does not match the paper's v5.1.1 commit")
    if git_fact(upstream, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Pinned upstream has modified tracked files")
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    state = {"phase": "provenance", "before": None, "returncode": None, "timed_out": False, "guard_reason": None, "parent_error": None, "process_cleanup": None}
    windows: dict[str, list[int]] = {}
    pending = None
    try:
        state["before"] = source_hashes(upstream)
        _write_provenance(output_dir, stage, command, upstream, timeout_s, profile, interval_ms, state["before"], intrusive_profile, post_idle_s)
        _execute(command, upstream, output_dir, stage, timeout_s, profile, interval_ms, baseline_s, state, windows)
    except BaseException as exc:  # noqa: BLE001 - archived below, then re-raised unchanged
        pending = exc
        state["parent_error"] = {"type": type(exc).__name__, "message": str(exc)[:2000], "phase": state["phase"]}
        if state["guard_reason"] is None:
            state["guard_reason"] = "parent_exception" if isinstance(exc, Exception) else "parent_interrupted"
    result = None
    try:
        result = _archive(upstream, output_dir, stage, profile, interval_ms, started, state, windows, interrupted=pending is not None and not isinstance(pending, Exception))
    except Exception:
        if pending is None:
            raise
        logger.exception("Could not archive the partial result after a parent failure")
    if pending is not None:
        raise pending
    return result


def _write_provenance(output_dir: Path, stage: str, command: list[str], upstream: Path, timeout_s: float, profile: bool, interval_ms: int, before: dict, intrusive_profile: str, post_idle_s: float) -> None:
    preflight = hardware_preflight()
    metadata = {
        "stage": stage, "upstream_url": "https://github.com/fischesn/cp2n2",
        "upstream_commit": PINNED_COMMIT, "command": command, "cwd": str(upstream),
        "started_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "platform": platform.platform(), "timeout_s": timeout_s,
        "packages": sorted([{"name": item.metadata.get("Name", "unknown"), "version": item.version} for item in importlib.metadata.distributions()], key=lambda item: item["name"].lower()),
        "source_sha256": before,
        "harness_sources": harness_sources(),
        "environment_policy": "Dotenv disabled; secrets and preexisting provider settings removed. No thread, seed, count or warmup overrides.",
        "scope": "Reference planner is an oracle, not the original hosted Minimax experiment. E3 is software-only.",
        "profile": profile, "interval_ms": interval_ms if profile else None,
        "intrusive_profile": intrusive_profile, "post_idle_s": post_idle_s,
        "cooling": "Developer-kit fan and heatsink, open air (user confirmed)",
        "ambient_temperature_c": None,
        "system_memory_total_bytes": psutil.virtual_memory().total,
        "cpu_count_logical": psutil.cpu_count(),
        "hardware": {
            str(path): path.read_text().strip().strip("\0")
            for path in (Path("/proc/device-tree/model"), Path("/etc/nv_tegra_release"))
            if path.exists()
        },
        "cpu_governors": {str(path): path.read_text().strip() for path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor")},
        "hardware_preflight": preflight,
        "power_mode": preflight["power_mode"],
        "jetpack_packages": _query(["dpkg-query", "-W", "-f=${Package} ${Version}\n", "nvidia-jetpack", "nvidia-l4t-core"]),
    }
    (output_dir / "provenance.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _execute(command: list[str], upstream: Path, output_dir: Path, stage: str, timeout_s: float, profile: bool, interval_ms: int, baseline_s: float, state: dict, windows: dict) -> None:
    with ExitStack() as stack:
        stdout = stack.enter_context((output_dir / "stdout.log").open("x", encoding="utf-8"))
        stderr = stack.enter_context((output_dir / "stderr.log").open("x", encoding="utf-8"))
        sampler = tree_output = limit = None
        if profile:
            from evaluation.evaluate_tegra_resources import TegraStatsSampler
            from evaluation.resource_monitor import (
                memory_guard_reason,
                thermal_guard_limit,
            )

            state["phase"] = "profiling_setup"
            limit, trips = thermal_guard_limit()
            settings = {"interval_ms": interval_ms, "baseline_s": baseline_s, "temperature_stop_c": limit, "thermal_trip_points": trips, "min_free_disk_mib": DISK_RESERVE_BYTES // 1024 ** 2, "observer_sources": harness_sources()}
            settings.update(min_system_available_mib=1024, max_workload_rss_sum_mib=1024)
            (output_dir / "profiling.json").write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            sampler = stack.enter_context(TegraStatsSampler(interval_ms, output_dir / "tegrastats.jsonl"))
            tree_output = stack.enter_context((output_dir / "process_tree.jsonl").open("x", encoding="utf-8"))
            state["phase"] = "platform_before"
            idle_started = time.monotonic_ns()
            time.sleep(baseline_s)
            windows["platform_before"] = [idle_started, time.monotonic_ns()]
            state["phase"] = "preflight_guard"
            if shutil.disk_usage(output_dir).free < DISK_RESERVE_BYTES:
                state["guard_reason"] = "disk_reserve"
                raise RuntimeError("Insufficient free disk for profiled run")
            reason = memory_guard_reason(psutil.virtual_memory().available, 0)
            if reason:
                state["guard_reason"] = reason
                raise RuntimeError("Insufficient system memory reserve for profiled run")
        state["phase"] = "command"
        process = None
        command_started = time.monotonic_ns()
        try:
            process = subprocess.Popen(command, cwd=upstream, env=reproduction_environment(output_dir, stage), stdout=stdout, stderr=stderr, start_new_session=True)
            if profile:
                _monitor(process, sampler, tree_output, output_dir, command_started, timeout_s, interval_ms, limit, state)
            else:
                try:
                    process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    state["timed_out"] = True
        finally:
            # Runs on normal exit too, so descendants left in the owned group are never orphaned.
            if process is not None:
                state["process_cleanup"] = stop_process_group(process)
                state["returncode"] = process.returncode
            windows["command"] = [command_started, time.monotonic_ns()]
        if profile:
            state["phase"] = "platform_after"
            idle_started = time.monotonic_ns()
            time.sleep(baseline_s)
            windows["platform_after"] = [idle_started, time.monotonic_ns()]
        state["phase"] = "finished"


def _monitor(process: subprocess.Popen, sampler, tree_output, output_dir: Path, command_started: int, timeout_s: float, interval_ms: int, limit: float, state: dict) -> None:
    from evaluation.resource_monitor import ProcessTreeSampler, memory_guard_reason

    if sampler.pid is None:
        raise RuntimeError("Platform sampler did not start")
    tree_sampler = ProcessTreeSampler(process.pid, (os.getpid(), sampler.pid))
    while process.poll() is None:
        snapshot = tree_sampler.sample()
        snapshot["system_available_bytes"] = psutil.virtual_memory().available
        tree_output.write(json.dumps(snapshot) + "\n")
        now = time.monotonic_ns()
        sample = sampler.latest_sample
        reason = None
        if (now - command_started) / 1e9 > timeout_s:
            state["timed_out"] = True
            reason = "timeout"
        elif shutil.disk_usage(output_dir).free < DISK_RESERVE_BYTES:
            reason = "disk_reserve"
        elif memory := memory_guard_reason(snapshot["system_available_bytes"], snapshot["tree_rss_sum_kib"]):
            reason = memory
        elif sample and sample.get("max_temperature_c", -math.inf) >= limit:
            reason = "temperature_limit"
        elif now - command_started > 2_000_000_000 and (sample is None or sample.get("max_temperature_c") is None or now - sample["monotonic_ns"] > max(2_000_000_000, interval_ms * 4_000_000)):
            reason = "temperature_telemetry_unavailable"
        if reason:
            state["guard_reason"] = reason
            return
        time.sleep(interval_ms / 1000)


def _archive(upstream: Path, output_dir: Path, stage: str, profile: bool, interval_ms: int, started: float, state: dict, windows: dict, *, interrupted: bool) -> dict:
    ignored, summary_error, profile_summary, profile_error = {}, None, None, None
    if profile:
        from evaluation.resource_monitor import summarize_profile

        workload_path = output_dir / "workload" / "summary.json"
        if workload_path.exists() and "command" in windows:
            try:
                extra, ignored = workload_windows(load_workload_summary(workload_path), windows["command"])
                windows.update(extra)
            except (OSError, TypeError, ValueError) as exc:
                summary_error = f"{type(exc).__name__}: {exc}"[:2000]
        if windows:
            try:
                profile_summary = summarize_profile(output_dir, windows, interval_ms)
                (output_dir / "resource-summary.json").write_text(json.dumps(profile_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 - recorded; the archive must still be sealed
                profile_error = f"{type(exc).__name__}: {exc}"[:2000]
    result = {
        "stage": stage, "returncode": state["returncode"], "timed_out": state["timed_out"],
        "elapsed_s": time.monotonic() - started,
        "upstream_sources_unchanged": state["before"] is not None and state["before"] == source_hashes(upstream),
        "guard_reason": state["guard_reason"], "profile": profile,
        "windows_ns": windows, "ignored_windows": ignored,
        "profile_complete": profile_complete(profile_summary) if profile else None,
        "profile_error": profile_error, "workload_summary_error": summary_error,
        "parent_error": state["parent_error"], "process_cleanup": state["process_cleanup"],
        "hardware_postflight": None if interrupted else hardware_state(),
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {str(path.relative_to(output_dir)): digest(path) for path in sorted(output_dir.rglob("*")) if path.is_file()}
    (output_dir / "reproduction-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def summarize_reproduction(root: Path) -> dict:
    stages = {}
    verified_files = 0
    for folder in sorted(root.iterdir()):
        manifest_path = folder / "reproduction-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text())
        for relative, expected in manifest.items():
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder.resolve()) or digest(path) != expected:
                raise ValueError(f"Reproduction archive mismatch: {folder.name}/{relative}")
        verified_files += len(manifest)
        stages[folder.name] = json.loads((folder / "result.json").read_text())

    def load(relative):
        return json.loads((root / relative).read_text(encoding="utf-8"))

    reported = {"chemical": 0.361, "wetware": 0.194, "edge": 0.189}
    aggregate = {row["backend"]: row for row in load("core/evaluations/overhead_results.json")["results"]}
    standalone = {row["backend"]: row for row in load("overhead-standalone/evaluations/overhead_results.json")["results"]}
    comparisons = [{
        "backend": name, "published_added_ms": value,
        "jetson_aggregate_added_ms": aggregate[name]["added_overhead_ms"],
        "jetson_standalone_added_ms": standalone[name]["added_overhead_ms"],
        "iterations_per_path": aggregate[name]["iterations"],
    } for name, value in reported.items()]
    selectors = {}
    for case in load("core/evaluations/matching_baselines_results.json")["cases"]:
        counts = selectors.setdefault(case["selector"], {"correct": 0, "total": 0})
        counts["correct"] += int(case["correct"])
        counts["total"] += 1
    provider = load("e3-provider/provider-trace/result.json")
    e3_control = load("e3-control/control-trace/summary.json")
    report = {
        "upstream_commit": PINNED_COMMIT, "verified_files": verified_files, "stages": stages,
        "reproduction_complete": False,
        "comparison_source": "https://arxiv.org/html/2605.04256v2#S6.SS4",
        "overhead_comparison": comparisons,
        "http": load("core/evaluations/externalized_backend_results.json")["results"][0],
        "selectors": selectors,
        "matching_accuracy": load("core/evaluations/matching_results.json")["accuracy"],
        "fault_expectation_rate": load("core/evaluations/failure_campaign_results.json")["success_rate"],
        "holdout": load("core/evaluations/selection_robustness_results.json")["holdout"],
        "provider": {key: provider[key] for key in ("runtime_kind", "evidence_ceiling", "trial_count", "scored_trial_count", "sham_trial_count", "recording_artifact", "biological_claim")},
        "e3_control": {key: value for key, value in e3_control.items() if not isinstance(value, (list, dict))},
        "gaps": {
            "distributed": "Both unchanged full-default attempts aborted with connection-reset/disconnect exceptions; no complete 945-request matrix or final upstream manifest.",
            "hosted_model": "Not run: original AI-Lab credential is unavailable. Reference planner is not a substitute for the 160-call Minimax campaign.",
            "environment": "Python 3.12.15 and CL SDK 1.0.0 needed for cl.sim; original authors' exact interpreter/dependency/hardware configuration not established.",
            "timing": "Released code yields materially higher overhead here than the publication. Hardware-only attribution is unsupported; author reconciliation required.",
        },
    }
    (root / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Original-paper protocol reproduction on Jetson", "",
        "Status: partial reproduction, with failures retained. Not a replacement for the original evidence or a completed confirmation.", "",
        f"Pinned unmodified upstream: `{PINNED_COMMIT}` (CP2N2 v5.1.1).", "",
        "## Original versus observed local overhead", "",
        "All numbers are mean added wall-clock milliseconds, using the original 25 iterations per path. The standalone experiment was run after the aggregate campaign failed; both observations are retained, not pooled or selectively substituted.", "",
        "| Backend | Original paper | Jetson aggregate run | Jetson standalone run |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in comparisons:
        lines.append(f"| {row['backend']} | {row['published_added_ms']:.3f} | {row['jetson_aggregate_added_ms']:.3f} | {row['jetson_standalone_added_ms']:.3f} |")
    lines.extend(["", "These observations do not reproduce the publication's sub-millisecond values. They are not a hardware-only slowdown estimate: the paper's exact measured-code lineage, interpreter and dependency environment need to be reconciled with the author.", "", "## Executed stages", ""])
    for name, result in stages.items():
        lines.append(f"- {name}: exit {result['returncode']}; unchanged upstream source: {result['upstream_sources_unchanged']}; logs and checksum manifest retained.")
    lines.extend(["", "## Conformance", ""])
    for name, counts in selectors.items():
        lines.append(f"- {name}: {counts['correct']}/{counts['total']} expected selections.")
    lines.extend([
        f"- Matching accuracy: {report['matching_accuracy']}; fault expectation rate: {report['fault_expectation_rate']}.",
        f"- CL SDK Simulator: {provider['trial_count']} trials, {provider['recording_artifact']['stim_count']} stimulations, {provider['recording_artifact']['spike_count']} spikes; online/offline match: {provider['recording_artifact']['online_offline_match']}.",
        "- The initial Python 3.10 test run failed on missing cl.sim; the isolated Python 3.12 environment passed all 146 upstream tests.",
        "", "## Gaps", "",
    ])
    lines.extend(f"- {name}: {description}" for name, description in report["gaps"].items())
    lines.extend(["", f"Verified {verified_files} reproduction archive files. No original evaluator, task, seed, iteration count, client matrix, timeout profile, or recovery policy was patched.", ""])
    (root / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["tests", "core", "overhead", "http", "distributed", "e3-control", "e3-provider", "sustained", "summarize"])
    parser.add_argument("--upstream", type=Path, default=ROOT / "_temp" / "cp2n2-v5.1.1")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=1800)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--interval-ms", type=int, default=250)
    parser.add_argument("--baseline-s", type=float, default=3)
    parser.add_argument("--duration-s", type=float, default=300)
    parser.add_argument("--rate-hz", type=float, default=10)
    parser.add_argument("--mode", choices=["harness", "direct", "orchestrated"], default="orchestrated")
    parser.add_argument("--deadline-ms", type=float)
    parser.add_argument("--idle-s", type=float, default=3)
    parser.add_argument("--diagnostic-counts", action="store_true")
    parser.add_argument("--intrusive-profile", choices=INTRUSIVE_PROFILES, default="none")
    parser.add_argument("--post-idle-s", type=float, default=0)
    args = parser.parse_args()
    if args.stage == "summarize":
        report = summarize_reproduction(args.output_dir)
        print(json.dumps({"reproduction_complete": report["reproduction_complete"], "verified_files": report["verified_files"], "overhead_comparison": report["overhead_comparison"], "gaps": report["gaps"]}, indent=2))
        return
    result = run_stage(args.upstream, args.output_dir, args.stage, args.timeout_s, profile=args.profile, interval_ms=args.interval_ms, baseline_s=args.baseline_s, duration_s=args.duration_s, rate_hz=args.rate_hz, mode=args.mode, deadline_ms=args.deadline_ms, idle_s=args.idle_s, diagnostic_counts=args.diagnostic_counts, intrusive_profile=args.intrusive_profile, post_idle_s=args.post_idle_s)
    print(json.dumps(result, indent=2))
    if result["returncode"] != 0 or not result["upstream_sources_unchanged"] or (args.profile and not result["profile_complete"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()