"""Frozen, sequential driver and offline analyzer for the finite Jetson follow-up protocol."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

import psutil


def find_live_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "evaluation").is_dir() and (candidate / "_temp").is_dir():
            return candidate
    raise RuntimeError("Cannot locate the repository root; pass and run this tool from a repository checkout")


LIVE_ROOT = find_live_root()
REF_COMMIT = "5f4d05291a0e37d7f88b7a6874b8a7f57befd695"
UPSTREAM_COMMIT = "36fbae002b0a9e066416ea9cafbb966a33fe53bb"
DEFAULT_REPO = LIVE_ROOT / "_temp/overnight-followup-ref-5f4d"
DEFAULT_UPSTREAM = LIVE_ROOT / "_temp/cp2n2-v5.1.1"
DEFAULT_PYTHON = LIVE_ROOT / "_temp/cp2n2-python312/bin/python"
PROTOCOL_TRIAL_IDS = (
    "P01", "P02", "P03", "P04", "H01-B", "H01-I", "H02-I", "H02-B",
    "H03-B", "H03-I", "M01-F", "M01-S", "M02-S", "M02-F", "M03-F", "M03-S",
)
SCHEDULE = [
    ("P01-A2", "pilot", "", "pinned", 10, 60, 250),
    ("P02", "pilot", "", "pinned", 10, 60, 2000),
    ("P03", "pilot", "", "pinned", 50, 60, 250),
    ("P04", "pilot", "", "indexed", 50, 60, 250),
    ("H01-B", "primary", "H01", "pinned", 50, 300, 250),
    ("H01-I", "primary", "H01", "indexed", 50, 300, 250),
    ("H02-I", "primary", "H02", "indexed", 50, 300, 250),
    ("H02-B", "primary", "H02", "pinned", 50, 300, 250),
    ("H03-B", "primary", "H03", "pinned", 50, 300, 250),
    ("H03-I", "primary", "H03", "indexed", 50, 300, 250),
    ("M01-F", "primary", "M01", "pinned", 10, 1800, 250),
    ("M01-S", "primary", "M01", "pinned", 10, 1800, 2000),
    ("M02-S", "primary", "M02", "pinned", 10, 1800, 2000),
    ("M02-F", "primary", "M02", "pinned", 10, 1800, 250),
    ("M03-F", "primary", "M03", "pinned", 10, 1800, 250),
    ("M03-S", "primary", "M03", "pinned", 10, 1800, 2000),
]
SCHEDULE_FIELDS = ["order", "trial_id", "role", "block", "pair_condition", "store", "rate_hz", "duration_s", "interval_ms", "deadline_ms", "planned_releases", "config", "config_sha256", "output"]
UTC = timezone.utc


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def append_event(path: Path, event: str, **fields) -> None:
    entry = {"event": event, "utc": utc_now(), "epoch_s": time.time(), "monotonic_ns": time.monotonic_ns(), **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def import_runner(repo: Path):
    sys.path.insert(0, str(repo))
    from evaluation import operating_limits as ol
    from evaluation.reproduce_original import (
        PINNED_COMMIT,
        reproduction_environment,
        source_hashes,
    )
    return ol, PINNED_COMMIT, reproduction_environment, source_hashes


def read_file(path: Path) -> str | None:
    try:
        return path.read_text().strip().strip("\0")
    except OSError:
        return None


def command_result(args: list[str]) -> dict:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=15)
        clean = lambda text: re.sub(r"\x1b\[[0-9;]*m", "", text).strip()
        return {"command": args, "returncode": result.returncode, "stdout": clean(result.stdout), "stderr": clean(result.stderr)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": args, "unavailable": f"{type(exc).__name__}: {exc}"}


def live_environment(python: Path, ambient_estimate_c: float) -> dict:
    cpu_percent = psutil.cpu_percent(interval=1.0)
    uname = platform.uname()
    model = read_file(Path("/proc/device-tree/model"))
    meminfo = read_file(Path("/proc/meminfo")) or ""
    fan_files = (
        Path("/sys/devices/platform/pwm-fan/hwmon/hwmon0/pwm1"),
        *sorted(Path("/sys/devices/platform").glob("**/*tachometer/hwmon/hwmon*/rpm")),
        *sorted(Path("/sys/devices/platform/pwm-fan/hwmon").glob("hwmon*/fan1_input")),
    )
    fan = {str(path): read_file(path) for path in fan_files if path.exists()}
    service = command_result(["systemctl", "show", "nvfancontrol", "-p", "ActiveState", "-p", "SubState", "-p", "ExecStart"])
    processes = []
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            info = process.info
            memory = info.get("memory_info")
            processes.append({"name": info.get("name") or "unknown", "rss_mib": memory.rss / 2**20 if memory else None})
        except psutil.Error:
            continue
    processes.sort(key=lambda item: item["rss_mib"] or 0, reverse=True)
    packages = subprocess.run([str(python), "-m", "pip", "freeze"], capture_output=True, text=True, check=True).stdout.splitlines()
    dpkg = command_result(["dpkg-query", "-W", "-f=${Package} ${Version}\\n", "nvidia-jetpack", "nvidia-jetpack-runtime", "nvidia-l4t-core"])
    return {
        "captured_utc": utc_now(), "board_model": model, "architecture": platform.machine(),
        "kernel": {"system": uname.system, "release": uname.release, "version": uname.version},
        "l4t_release_file": read_file(Path("/etc/nv_tegra_release")),
        "jetson_release_v": command_result(["jetson_release", "-v"]),
        "nominal_ram_gb": 8, "mem_total_kb": next((line.split(":", 1)[1].strip() for line in meminfo.splitlines() if line.startswith("MemTotal:")), None),
        "system_memory_total_bytes": psutil.virtual_memory().total, "available_memory_bytes_at_preflight": psutil.virtual_memory().available,
        "python_executable": str(python), "python_version": platform.python_version(), "python_packages": packages,
        "nvidia_packages": dpkg,
        "power_mode": command_result(["nvpmodel", "-q"]),
        "cpu_online": read_file(Path("/sys/devices/system/cpu/online")),
        "cpu_policies": [{"path": str(policy), **{name: read_file(policy / name) for name in ("scaling_governor", "scaling_min_freq", "scaling_max_freq", "scaling_cur_freq")}} for policy in sorted(Path("/sys/devices/system/cpu").glob("cpufreq/policy*"))],
        "fan": {"nvfancontrol_service": service, "sysfs": fan,
                "physical_setup": "Stock developer-kit fan and heatsink, open air (confirmed by user)."},
        "background_load": {"one_second_system_cpu_percent": cpu_percent, "load_average_1_5_15": list(psutil.getloadavg()),
                            "process_count": len(psutil.pids()), "largest_processes_by_rss": processes[:15],
                            "note": "Names and RSS only; no command lines or credentials. Existing desktop/update services were left untouched."},
        "ambient_temperature": {"measured_c": None, "room_estimate_c": ambient_estimate_c, "source": "User-provided approximate room estimate; not continuously measured."},
    }


def row_record(item: tuple, order: int) -> dict:
    trial_id, role, block, store, rate, duration, interval = item
    pair_condition = ("baseline" if store == "pinned" else "indexed") if trial_id.startswith("H") else ("fast" if interval == 250 else "slow") if trial_id.startswith("M") else "pilot"
    return {
        "order": order, "trial_id": trial_id, "role": role, "block": block, "store": store,
        "rate_hz": rate, "duration_s": duration, "interval_ms": interval,
        "deadline_ms": 1000 / rate, "planned_releases": duration * rate,
        "pair_condition": pair_condition,
    }


def config_for(row: dict, upstream_commit: str) -> dict:
    return {
        "schema_version": 2, "upstream_commit": upstream_commit,
        "cooling": "Stock developer-kit fan and heatsink, open air",
        "ambient_temperature_c": None, "desktop_comparison": False,
        "interval_ms": row["interval_ms"], "platform_idle_s": 3, "cooldown_s": 60,
        "phases": {row["trial_id"]: {
            "implemented": True, "kind": "sustained", "duration_s": row["duration_s"], "idle_s": 3,
            "post_idle_s": 0, "modes": ["orchestrated"], "rates_hz": [row["rate_hz"]],
            "repetitions": 1, "diagnostic_counts": True, "lifecycle_variants": [row["store"]],
            "spike_instrumentation": False, "intrusive_profile": "none", "gc_policy": "default",
        }},
    }


def prepare(args) -> Path:
    repo, upstream, python = args.repo.resolve(), args.upstream.resolve(), Path(os.path.abspath(args.python))
    if git(repo, "rev-parse", "HEAD") != REF_COMMIT or git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Reference worktree is not clean at the requested commit")
    if git(upstream, "rev-parse", "HEAD") != UPSTREAM_COMMIT or git(upstream, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Pinned upstream is not clean at CP2N2 v5.1.1")
    ol, pinned_commit, _, source_hashes = import_runner(repo)
    if pinned_commit != UPSTREAM_COMMIT:
        raise RuntimeError("Runner's pinned commit differs from protocol")
    if not python.is_file():
        raise RuntimeError(f"Pinned Python executable missing: {python}")
    versions = {name: __import__("importlib.metadata", fromlist=["version"]).version(name) for name in ("cl-sdk", "mcp", "psutil", "pytest", "matplotlib", "numpy")}
    if versions["cl-sdk"] != "1.0.0" or versions["mcp"] != "1.27.0":
        raise RuntimeError(f"Pinned Python dependencies differ from reference: {versions}")
    if not (shutil.which("tegrastats") and psutil.virtual_memory().available >= 1024**3 and shutil.disk_usage(LIVE_ROOT).free >= 4 * 1024**3):
        raise RuntimeError("Missing tegrastats or required memory/disk reserve")
    environment = live_environment(python, args.ambient_estimate_c)
    expected = {
        "board_model": "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super",
        "architecture": "aarch64", "cpu_online": "0-5", "nominal_ram_gb": 8,
        "python_version": "3.12.15",
    }
    mismatches = {key: {"expected": value, "actual": environment.get(key)} for key, value in expected.items() if environment.get(key) != value}
    power_text = environment["power_mode"].get("stdout", "")
    if "25W" not in power_text or "\n1" not in power_text:
        mismatches["power_mode"] = {"expected": "25W mode ID 1", "actual": power_text}
    cpu_policies = environment["cpu_policies"]
    frequency_policy = all(item.get("scaling_governor") == "schedutil" and item.get("scaling_min_freq") == "729600" and item.get("scaling_max_freq") == "1344000" for item in cpu_policies)
    if not frequency_policy or len(cpu_policies) != 2:
        mismatches["cpu_frequency_policy"] = {"expected": "2 schedutil policies at 729600-1344000 kHz", "actual": cpu_policies}
    if "5.15.185-tegra" not in environment["kernel"].get("release", ""):
        mismatches["kernel"] = {"expected": "5.15.185-tegra", "actual": environment["kernel"].get("release")}
    jetson_release_text = environment["jetson_release_v"].get("stdout", "")
    if "6.2.2" not in jetson_release_text or "L4T 36.5.0" not in jetson_release_text:
        mismatches["jetson_release"] = {"expected": "JetPack 6.2.2 [L4T 36.5.0]", "actual": environment["jetson_release_v"]}
    if "ActiveState=active" not in environment["fan"]["nvfancontrol_service"].get("stdout", ""):
        mismatches["fan_controller"] = {"expected": "nvfancontrol active", "actual": environment["fan"]["nvfancontrol_service"]}
    if mismatches:
        raise RuntimeError(f"Material reference-environment mismatch: {mismatches}")
    live_status_before_bundle = git(LIVE_ROOT, "status", "--porcelain").splitlines()
    preparation_output_prefix = "?? evaluation/results/overnight-followup/"
    if args.bundle:
        bundle = args.bundle.resolve()
        bundle.mkdir(parents=True, exist_ok=False)
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        parent = LIVE_ROOT / "evaluation/results/overnight-followup"
        parent.mkdir(parents=True, exist_ok=True)
        bundle = parent / f"campaign-{stamp}"
        bundle.mkdir(exist_ok=False)
    for name in ("configs", "validation", "runs", "scheduler-logs", "tools", "prior-attempts"):
        (bundle / name).mkdir()
    rows = [row_record(item, index + 1) for index, item in enumerate(SCHEDULE)]
    for row in rows:
        config_path = bundle / "configs" / f"{row['trial_id']}.json"
        config = config_for(row, pinned_commit)
        specs = ol.campaign_specs(config)
        if list(specs) != [row["trial_id"]] or len(specs[row["trial_id"]]) != 1:
            raise RuntimeError(f"Configuration does not produce exactly one trial: {row['trial_id']}")
        json_write(config_path, config)
        row["config"] = str(config_path.relative_to(bundle))
        row["config_sha256"] = sha256(config_path)
        row["output"] = f"runs/{row['trial_id']}"
        command = [str(python), "-m", "evaluation.operating_limits", "validate", "--config", str(config_path), "--phase", "all", "--list-trials"]
        completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise RuntimeError(f"Runner validation failed for {row['trial_id']}: {completed.stderr[-2000:]}")
        validation = json.loads(completed.stdout)
        if validation.get("trials", {}).get(row["trial_id"], [{}])[0].get("duration_s") != row["duration_s"]:
            raise RuntimeError(f"Validated duration mismatch: {row['trial_id']}")
        json_write(bundle / "validation" / f"{row['trial_id']}.json", validation)
    prior_bundle = LIVE_ROOT / "evaluation/results/overnight-followup/campaign-20261107T122214Z"
    prior_attempts = []
    if (prior_bundle / "runs/P01").is_dir():
        prior_root = bundle / "prior-attempts/P01-A1"
        prior_root.mkdir()
        shutil.copytree(prior_bundle / "runs/P01", prior_root / "runs/P01")
        shutil.copytree(prior_bundle / "scheduler-logs/P01", prior_root / "scheduler-logs/P01")
        shutil.copy2(prior_bundle / "configs/P01.json", prior_root / "P01.json")
        prior_row = row_record(("P01", "pilot", "", "pinned", 10, 60, 250), 1)
        prior_row.update(config="prior-attempts/P01-A1/P01.json", output="prior-attempts/P01-A1/runs/P01")
        prior_check = verify_trial(bundle, repo, prior_row)
        if not all(prior_check["exact_setting_checks"].values()) or prior_check["infrastructure_failures"]:
            raise RuntimeError(f"Prior P01 archive failed corrected verification: {prior_check}")
        json_write(prior_root / "verification.json", prior_check)
        shutil.copy2(prior_bundle / "scheduler-ledger.jsonl", prior_root / "scheduler-ledger.jsonl")
        prior_driver_log = LIVE_ROOT / "_temp/overnight-followup/campaign-20261107T122214Z.log"
        if prior_driver_log.is_file():
            shutil.copy2(prior_driver_log, prior_root / "scheduler-driver.log")
        prior_attempts.append({"trial_id": "P01-A1", "original_protocol_id": "P01", "source_bundle": str(prior_bundle), "status": prior_check["status"], "wrapper_rejection": "incorrect pinned-store identity assertion; the runtime evidence and archive are valid under the corrected verifier", "reverified": True})
    suite_source = LIVE_ROOT / "evaluation/results/overnight-followup/campaign-20261107T120944Z/variant-suite-check.json"
    if not suite_source.is_file():
        raise RuntimeError("The completed baseline/indexed upstream suite report is missing")
    suite_report = json.loads(suite_source.read_text())
    expected_cases = suite_report["cases"][:3]
    if not suite_report.get("upstream_unmodified_after") or len(expected_cases) != 3 or any(case["returncode"] != 0 or "146 passed" not in (case["summary"] or "") for case in expected_cases):
        raise RuntimeError(f"Pinned, indexed or bounded/6000 suite gate did not pass: {suite_report}")
    if suite_report["cases"][3]["returncode"] != 1 or "2 failed" not in (suite_report["cases"][3]["summary"] or ""):
        raise RuntimeError("The bounded/1 positive control did not exhibit its expected history-dependent failures")
    shutil.copy2(suite_source, bundle / "variant-suite-check.json")
    with (bundle / "schedule.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEDULE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    source_fingerprint = ol.source_fingerprint()
    json_write(bundle / "source-fingerprint.json", {
        "reference_commit": REF_COMMIT, "upstream_commit": UPSTREAM_COMMIT,
        "evaluation_source_sha256": source_fingerprint,
        "upstream_tracked_source_sha256": source_hashes(upstream),
        "scheduler_sha256": sha256(Path(__file__).resolve()),
        "configuration_sha256": {row["trial_id"]: row["config_sha256"] for row in rows},
    })
    json_write(bundle / "source-change-log.json", {
        "reference_commit_used": REF_COMMIT, "live_checkout_commit_at_prepare": git(LIVE_ROOT, "rev-parse", "HEAD"),
        "commits_since_reference": git(LIVE_ROOT, "log", "--format=%H %s", f"{REF_COMMIT}..HEAD").splitlines(),
        "tracked_source_diff_since_reference": subprocess.run(["git", "-C", str(LIVE_ROOT), "diff", "--name-status", f"{REF_COMMIT}..HEAD"], capture_output=True, text=True, check=True).stdout.splitlines(),
        "preexisting_dirty_paths_not_used_or_modified": [item for item in live_status_before_bundle if not item.startswith(preparation_output_prefix)],
        "earlier_setup_attempt_outputs": [item for item in live_status_before_bundle if item.startswith(preparation_output_prefix)],
        "current_bundle": str(bundle.relative_to(LIVE_ROOT)),
        "measured_evaluation_source_changes": [],
        "note": "Runs use the detached clean reference worktree. The live checkout's later commits only document JetPack and add jetson_release provenance; dirty manuscript drafts are excluded.",
    })
    json_write(bundle / "preflight.json", environment)
    json_write(bundle / "preflight-attempts.json", [
        {"attempt": "prepare-1", "utc": "2026-11-07T12:07:33Z", "status": "failed_before_measurement", "reason": "Python venv launcher path was resolved through its symlink; validation subprocess could not import psutil.", "resolution": "Preserve the venv launcher path."},
        {"attempt": "prepare-2", "utc": "2026-11-07T12:08:56Z", "status": "failed_before_measurement", "reason": "Schedule CSV schema omitted pair_condition.", "resolution": "Add pair_condition to the schedule CSV fields."},
        {"attempt": "prepare-3", "utc": None, "status": "pilot_completed_but_wrapper_gate_failed", "trial_id": "P01-A1", "bundle": "evaluation/results/overnight-followup/campaign-20261107T122214Z", "reason": "Pinned-store identity validator incorrectly required class metadata; the raw run was complete and has been reverified. It is retained and repeated as P01-A2."},
        {"attempt": "prepare-4", "utc": None, "status": "prepared_then_superseded_before_measurement", "bundle": "evaluation/results/overnight-followup/campaign-20261107T120944Z", "reason": "Its source-change log included its own untracked bundle and its embedded helper predates the final README/metrics fixes."},
        {"attempt": "variant-suite-1", "utc": None, "status": "interrupted_without_report", "reason": "The first suite-check process received KeyboardInterrupt before writing a report; no upstream changes and no experiments started."},
        {"attempt": "variant-suite-2", "utc": "2026-11-07T12:14:17Z", "status": "completed", "report": "variant-suite-check.json", "outcome": "pinned/indexed/bounded-6000 each 146 passed; bounded-1 positive control 2 expected failures."},
        {"attempt": "reference-evaluation-tests", "utc": None, "status": "passed", "outcome": "pip check clean; 206 passed, 2 environment-conditional skipped."},
    ])
    shutil.copy2(Path(__file__).resolve(), bundle / "tools" / "overnight.py")
    json_write(bundle / "manifest.json", {
        "schema_version": 1, "state": "prepared", "prepared_utc": utc_now(),
        "reference_repo": str(repo), "reference_commit": REF_COMMIT,
        "live_checkout": str(LIVE_ROOT), "live_checkout_commit": git(LIVE_ROOT, "rev-parse", "HEAD"),
        "upstream_repo": str(upstream), "upstream_commit": UPSTREAM_COMMIT, "python": str(python), "python_packages": versions,
        "protocol_trial_ids": list(PROTOCOL_TRIAL_IDS), "order": [row["trial_id"] for row in rows], "rows": rows,
        "prior_attempts": prior_attempts, "pilot_repeat": {"attempt_id": "P01-A2", "repeats_protocol_id": "P01", "reason": "P01-A1 was sealed but rejected by a helper verification defect; repeat after correction before continuing."},
        "pairing": {"high_rate_order": ["B-I", "I-B", "B-I"], "monitoring_order": ["F-S", "S-F", "F-S"], "balanced": False},
        "cooldown_s": 60, "ambient_temperature_measured": False, "ambient_temperature_estimate_c": args.ambient_estimate_c,
        "no_manuscript_or_external_publication": True,
    })
    write_initial_readme(bundle, environment)
    print(bundle)
    return bundle


def write_initial_readme(bundle: Path, environment: dict) -> None:
    (bundle / "README.md").write_text(
        "# Overnight CP2N2 follow-up\n\n"
        "Questions: (1) effect of the existing resource-monitoring stack at 250 ms versus 2,000 ms; "
        "(2) full-history index effect at an offered rate of 50 requests/s. Comparisons use the same "
        "Jetson, simulated edge workload, pinned CP2N2 v5.1.1 and sequential scheduler. The treatment "
        "is the combined monitoring configuration; it is not an unmonitored control. The indexed arm "
        "retains full lifecycle history. No retention cap or GC policy change is used.\n\n"
        f"Prepared: {utc_now()}. Reference evaluation commit: `{REF_COMMIT}`. Upstream: `{UPSTREAM_COMMIT}`.\n\n"
        "Environment is recorded in `preflight.json`; the room temperature is only an approximate "
        "user estimate, not a sensor measurement. Each single-trial config, its hash and runner validation "
        "are preserved under `configs/` and `validation/`. The source hashes are in `source-fingerprint.json`.\n\n"
        "Results pending measurement. The scheduler records every start, finish, cooldown and failure in "
        "`scheduler-ledger.jsonl`. It stops on infrastructure/data-quality failures, preserves performance "
        "flags, and never retries a started trial.\n", encoding="utf-8")


def write_final_readme(bundle: Path, rows: list[dict], pairs: list[dict], summary: dict) -> None:
    manifest = json.loads((bundle / "manifest.json").read_text())
    preflight = json.loads((bundle / "preflight.json").read_text())
    attempts = json.loads((bundle / "preflight-attempts.json").read_text())
    status_by_id = {row["schedule"]["trial_id"]: row["status"] for row in rows}
    schedule_lines = ["| # | ID | Role | Block | Store | Offered req/s | Active s | Sample ms | Planned | Status |", "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for row in manifest["rows"]:
        schedule_lines.append(f"| {row['order']} | {row['trial_id']} | {row['role']} | {row['block'] or '-'} | {row['store']} | {row['rate_hz']} | {row['duration_s']} | {row['interval_ms']} | {row['planned_releases']} | {status_by_id.get(row['trial_id'], 'not run')} |")
    fan_values = ", ".join(f"{Path(path).name}={value}" for path, value in preflight["fan"]["sysfs"].items()) or "not exposed by sysfs"
    cpu_policies = "; ".join(f"{Path(item['path']).name}: {item['scaling_governor']} {item['scaling_min_freq']}-{item['scaling_max_freq']} kHz" for item in preflight["cpu_policies"])
    failed_attempts = sum(item["status"] not in ("completed", "passed") for item in attempts)
    text = f"""# Overnight CP2N2 Follow-up

## Questions and Scope

1. Does changing the existing process-tree/tegrastats sampling interval from 250 ms to 2,000 ms affect latency during sustained 10 requests/s execution?
2. Does the evaluation-layer full-history index improve the pinned v5.1.1 implementation at an offered 50 requests/s?

This compares the combined monitoring configuration, including sampling, serialization, logging and interval-periodic resource guards. It is not a monitoring-disabled comparison. Each row uses a fresh process/backend/history, orchestrated mode, simulated edge backend, periodic skip-expired-slots admission, default GC, full history, diagnostic counts, no intrusive profiler, no spike instrumentation, 3 s ready idle, no post-idle, 3 s platform baseline before/after, and a release-relative completion deadline equal to one period including maintenance (20 ms at 50/s, 100 ms at 10/s). Each adjacent trial is separated by at least 60 seconds.

## Environment

- Board: {preflight['board_model']}; 8 GB nominal RAM, `MemTotal` {preflight['mem_total_kb']}; architecture {preflight['architecture']}.
- Software: JetPack 6.2.2 / L4T 36.5.0 (`nvidia-l4t-core 36.5.0-20260115194252`); kernel {preflight['kernel']['release']}; measurement Python {preflight['python_version']}.
- Power/frequency: {preflight['power_mode'].get('stdout')}; online CPUs {preflight['cpu_online']}; {cpu_policies}. The configured 25 W mode is not a power measurement.
- Cooling/fan: {preflight['fan']['physical_setup']} `nvfancontrol` is {preflight['fan']['nvfancontrol_service'].get('stdout','').split('ActiveState=')[-1].splitlines()[0]}; preflight fan readings: {fan_values}.
- Background: one-second system CPU {preflight['background_load']['one_second_system_cpu_percent']}%; load averages {preflight['background_load']['load_average_1_5_15']}; {preflight['background_load']['process_count']} processes. Existing desktop/update services remained running and were not altered.
- Ambient: approximately {preflight['ambient_temperature']['room_estimate_c']} C by user estimate; not sensor-measured or continuous.
- Full Python package versions and raw board details are in `preflight.json`; trial-time temperatures and monitoring coverage are in each sealed resource archive.

## Schedule and Outcomes

The original 16 protocol identifiers are preserved in `manifest.json`. P01-A1 is a prior completed 60-second run retained under `prior-attempts/`; it passed all measurement checks but the first scheduler rejected it due to a faulty pinned-store class assertion. It is included as a prior attempt, not silently counted as a new scheduled row. P01-A2 repeats that pilot under a new ID after the assertion correction, before P02. The remaining rows retain the requested IDs and order.

The paired order alternates but is intentionally not perfectly balanced: H blocks B-I, I-B, B-I; M blocks F-S, S-F, F-S. Here B=pinned, I=indexed, F=250 ms, S=2,000 ms. The two comparisons are analyzed independently.

{chr(10).join(schedule_lines)}

{sum(row['schedule']['role']=='pilot' for row in rows)} scheduled pilots and {sum(row['schedule']['role']=='primary' for row in rows)} primary trials completed, plus {len(manifest.get('prior_attempts', []))} preserved prior pilot attempt(s); {summary['data_quality']['infrastructure_failures']} infrastructure failures and {summary['data_quality']['performance_flagged_trials']} trials with measured performance flags. All paired differences and condition summaries are in `results-summary.md`, `summary.json`, `per-run-results.csv` and `paired-differences.csv`.

## Attempts and Source Changes

There were {len(attempts)} recorded setup/suite attempts before the final run; {failed_attempts} ended before a valid report. They are listed in `preflight-attempts.json`; no scientific trial was started by those attempts. The successful variant report is `variant-suite-check.json`: pinned, indexed and bounded/6000 each passed 146 tests; bounded/1 was the deliberate positive control and failed two history-dependent tests.

Measurements used a detached clean worktree at `{REF_COMMIT}` and unchanged CP2N2 v5.1.1 `{UPSTREAM_COMMIT}`. The live checkout was at `{manifest['live_checkout_commit']}`; its two later commits only document JetPack and capture `jetson_release` output. No measured evaluation-source changes were made. See `source-change-log.json`, `source-fingerprint.json`, per-trial provenance and `manifest.json` for hashes. Existing manuscript changes were not used or modified. Nothing was pushed or uploaded.

## Reproduction and Analysis

The frozen single-row configs are under `configs/`; `schedule.csv` records the exact order. From the repository root, validate/run an individual row with the pinned Python:

```bash
PY312=/home/uit/Projects/phys-mcp/_temp/cp2n2-python312/bin/python
REPO=/home/uit/Projects/phys-mcp/_temp/overnight-followup-ref-5f4d
UPSTREAM=/home/uit/Projects/phys-mcp/_temp/cp2n2-v5.1.1
BUNDLE={bundle}
ID=P01
"$PY312" -m evaluation.operating_limits validate --config "$BUNDLE/configs/$ID.json" --phase all --list-trials
"$PY312" -m evaluation.operating_limits run --config "$BUNDLE/configs/$ID.json" --phase all --upstream "$UPSTREAM" --output-dir "$BUNDLE/runs/$ID"
```

The frozen scheduler is `tools/overnight.py`; full analysis is performed only after the measurement schedule ends:

```bash
"$PY312" "$BUNDLE/tools/overnight.py" analyze --bundle "$BUNDLE" --repo "$REPO"
```

The schedule ledger is `scheduler-ledger.jsonl`. Each run preserves the runner's campaign/phase/trial folder names and sealed raw CSV, resource telemetry, summary, provenance, logs and reproduction manifest. Never overwrite or resume a started scientific trial.

## Limitations

Three independent pairs per comparison are exploratory, noisy run-level replication; requests are not independent replicates. No statistical equivalence or absence-of-interference conclusion follows from small differences. The high-rate result is limited to this simulated workload and duration; the index retains all history and does not bound memory. The monitoring arm measures the whole observer configuration. Two-second sampling can miss short-lived peaks, and sampled CPU/RSS/temperature do not isolate control-plane power or actual resource maxima. The configured 25 W profile is not measured consumption. A matched request count does not control elapsed time or thermal history. Cycles above 100 ms and the prior 30-second local-block excess criterion are descriptive only; no new run used GC-event instrumentation, so individual new spikes are not attributed to GC.
"""
    (bundle / "README.md").write_text(text, encoding="utf-8")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_trial(bundle: Path, repo: Path, row: dict) -> dict:
    ol, _, _, _ = import_runner(repo)
    run_root = bundle / row["output"]
    phase = row["trial_id"]
    config = json.loads((bundle / row["config"]).read_text())
    phase_dir = run_root / phase
    specs = ol.trial_specs(config, phase)
    if len(specs) != 1:
        raise RuntimeError(f"Expected a single frozen spec for {phase}")
    trial_id = specs[0]["trial_id"]
    trial_dir = phase_dir / trial_id
    result, summary, classification = ol.assess_trial(trial_dir, specs[0], config)
    if classification["infrastructure_failures"]:
        raise RuntimeError(f"Archive verification found infrastructure defects for {row['trial_id']}: {classification['infrastructure_failures']}")
    if summary is None or summary.get("fatal_error") or summary.get("completed_duration") is not True:
        raise RuntimeError(f"Incomplete worker summary for {row['trial_id']}")
    exact = {
        "mode": summary.get("mode") == "orchestrated", "duration": summary.get("duration_s") == row["duration_s"],
        "rate": summary.get("rate_hz") == row["rate_hz"], "interval": json.loads((trial_dir / "profiling.json").read_text()).get("interval_ms") == row["interval_ms"],
        "relative_deadline": summary.get("relative_deadline_ms") == row["deadline_ms"],
        "gc": summary.get("gc_policy") == "default", "diagnostic_counts": summary.get("diagnostic_counts") is True,
        "spike_instrumentation": summary.get("spike_instrumentation") is False,
        "intrusive_profile": summary.get("intrusive_profile") == "none", "upstream": result.get("upstream_sources_unchanged") is True,
        "profile": result.get("profile_complete") is True,
        "planned_releases": summary.get("planned_releases") == row["planned_releases"],
        "lifecycle_variant": summary.get("lifecycle_variant") == row["store"],
    }
    install = summary.get("lifecycle_variant_install") or {}
    exact["store_identity"] = (
        install.get("variant") == "pinned" and install.get("semantics") == "unmodified v5.1.1 LifecycleStore"
        if row["store"] == "pinned" else install.get("variant") == "indexed" and install.get("installed_class") == "IndexedLifecycleStore"
    )
    if not all(exact.values()):
        raise RuntimeError(f"Recorded settings do not match frozen row {row['trial_id']}: {exact}")
    resource = json.loads((trial_dir / "resource-summary.json").read_text())
    steady = resource["windows"].get("steady_workload", {})
    coverage = (steady.get("power") or {}).get("coverage_fraction")
    if coverage is None or coverage < 0.8:
        raise RuntimeError(f"Insufficient interval-aware steady workload coverage for {row['trial_id']}: {coverage}")
    rows = ol.summarize_requests(trial_dir)
    retained = rows.get("retained_last") or {}
    counts = retained.get("collections", {})
    history = counts.get("lifecycle.history")
    first_history = ((rows.get("retained_first") or {}).get("collections") or {}).get("lifecycle.history")
    history_check = {
        "first": first_history, "last": history, "completed_successful": summary.get("successful_requests"),
        "expected_growth_if_all_six_transitions_per_success": 6 * (summary.get("successful_requests") or 0),
        "observed_growth": None if history is None or first_history is None else history - first_history,
        "interpretation": "advisory; failed or interrupted lifecycle paths can change the transition count",
    }
    if summary.get("successful_requests") == summary.get("completed_requests") and history_check["observed_growth"] != history_check["expected_growth_if_all_six_transitions_per_success"]:
        raise RuntimeError(f"All-success lifecycle growth differs from the observed six transitions per request for {row['trial_id']}: {history_check}")
    sample_periods = {}
    for file_name in ("tegrastats.jsonl", "process_tree.jsonl"):
        samples = jsonl(trial_dir / file_name)
        lower, upper = summary["started_ns"], summary["finished_ns"]
        times = [entry["monotonic_ns"] for entry in samples if lower <= entry["monotonic_ns"] <= upper]
        intervals = [(b - a) / 1e6 for a, b in pairwise(times)]
        cadence = statistics.median(intervals) if intervals else None
        sample_periods[file_name] = {"samples_during_active": len(times), "median_spacing_ms": cadence,
                                     "target_ms": row["interval_ms"], "cadence_valid": cadence is not None and 0.5 * row["interval_ms"] <= cadence <= 2.5 * row["interval_ms"]}
        if not sample_periods[file_name]["cadence_valid"]:
            raise RuntimeError(f"Monitoring cadence outside pilot acceptance for {row['trial_id']} ({file_name}): {sample_periods[file_name]}")
    return {
        "trial_id": row["trial_id"], "status": "performance_failure" if classification["performance_failures"] else "success",
        "performance_failures": classification["performance_failures"], "infrastructure_failures": [],
        "exact_setting_checks": exact, "history_check": history_check, "sampler_cadence": sample_periods,
        "sensor_coverage_fraction": coverage, "completed": summary["completed_requests"],
        "successful": summary["successful_requests"], "skipped": summary["skipped_releases"],
        "deadline_misses": summary["deadline_misses"], "summary": summary,
    }


def completed_events(bundle: Path) -> dict[str, dict]:
    path = bundle / "scheduler-ledger.jsonl"
    events = jsonl(path) if path.exists() else []
    started = {event["trial_id"]: event for event in events if event["event"] == "trial_started"}
    finished = {event["trial_id"]: event for event in events if event["event"] == "trial_finished"}
    if set(started) - set(finished):
        raise RuntimeError(f"A scientific trial started without a finish event: {sorted(set(started) - set(finished))}; preserve and investigate, do not redo it")
    return finished


def run_campaign(bundle: Path, repo: Path, upstream: Path, python: Path) -> None:
    ol, _, reproduction_environment, _ = import_runner(repo)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if git(repo, "rev-parse", "HEAD") != REF_COMMIT or git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Reference source worktree changed")
    if ol.source_fingerprint() != json.loads((bundle / "source-fingerprint.json").read_text())["evaluation_source_sha256"]:
        raise RuntimeError("Frozen source fingerprint changed")
    if sha256(Path(__file__).resolve()) != json.loads((bundle / "source-fingerprint.json").read_text())["scheduler_sha256"]:
        raise RuntimeError("Frozen scheduler changed")
    lock_path = LIVE_ROOT / "_temp/operating-limits.lock"
    lock_path.parent.mkdir(exist_ok=True)
    lock_handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another operating-limits measurement/analysis holds the live checkout lock") from None
    ledger = bundle / "scheduler-ledger.jsonl"
    finished = completed_events(bundle)
    append_event(ledger, "scheduler_started_or_resumed", bundle=str(bundle), already_finished=sorted(finished))
    last_finish = max((event.get("monotonic_ns", 0) for event in finished.values()), default=0)
    env = reproduction_environment(bundle, "sustained")
    for index, row in enumerate(manifest["rows"]):
        trial_id = row["trial_id"]
        if trial_id in finished:
            checked = verify_trial(bundle, repo, row)
            append_event(ledger, "previously_finished_reverified", trial_id=trial_id, status=checked["status"])
            if checked["infrastructure_failures"]:
                raise RuntimeError(f"Finished trial {trial_id} has infrastructure failure")
            continue
        for prior_id in [item["trial_id"] for item in manifest["rows"][:index]]:
            if prior_id not in finished:
                raise RuntimeError(f"Schedule ordering violation: {prior_id} is not finished before {trial_id}")
        if last_finish:
            elapsed = (time.monotonic_ns() - last_finish) / 1e9
            remaining = max(0, 60 - elapsed)
            if remaining:
                append_event(ledger, "cooldown_started", before_trial=trial_id, required_s=60, wait_s=remaining)
                time.sleep(remaining)
                append_event(ledger, "cooldown_finished", before_trial=trial_id, actual_wait_s=(time.monotonic_ns() - last_finish) / 1e9)
        config = bundle / row["config"]
        if sha256(config) != row["config_sha256"]:
            raise RuntimeError(f"Frozen configuration changed: {trial_id}")
        output = bundle / row["output"]
        if output.exists():
            raise RuntimeError(f"Refusing to overwrite an existing trial folder: {output}")
        command = [str(python), "-m", "evaluation.operating_limits", "run", "--config", str(config), "--phase", "all", "--upstream", str(upstream), "--output-dir", str(output)]
        append_event(ledger, "trial_started", trial_id=trial_id, order=row["order"], command=command, config_sha256=row["config_sha256"], output=str(output))
        log = bundle / "scheduler-logs" / trial_id
        log.mkdir(exist_ok=False)
        started = time.monotonic_ns()
        with (log / "stdout.log").open("x", encoding="utf-8") as stdout, (log / "stderr.log").open("x", encoding="utf-8") as stderr:
            completed_process = subprocess.run(command, cwd=repo, env=env, stdout=stdout, stderr=stderr, check=False)
        stdout_text = (log / "stdout.log").read_text(encoding="utf-8")
        try:
            report = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            append_event(ledger, "trial_infrastructure_failure", trial_id=trial_id, reason="runner_output_not_json", returncode=completed_process.returncode)
            raise RuntimeError(f"Runner output is malformed for {trial_id}") from exc
        phase_report = report.get("phases", {}).get(trial_id, {})
        if completed_process.returncode != 0 or phase_report.get("complete") is not True:
            append_event(ledger, "trial_infrastructure_failure", trial_id=trial_id, returncode=completed_process.returncode, runner_report=report)
            raise RuntimeError(f"Runner stopped at {trial_id}: rc={completed_process.returncode}, report={report}")
        checked = verify_trial(bundle, repo, row)
        if row["role"] == "pilot":
            append_event(ledger, "pilot_gate_passed", trial_id=trial_id, gate=checked)
        finished_ns = time.monotonic_ns()
        append_event(ledger, "trial_finished", trial_id=trial_id, status=checked["status"], performance_failures=checked["performance_failures"],
                     runner_returncode=completed_process.returncode, elapsed_s=(finished_ns - started) / 1e9,
                     finish_monotonic_ns=finished_ns, release_accounting={key: checked["summary"].get(key) for key in ("planned_releases", "arrived_releases", "completed_requests", "successful_requests", "skipped_releases", "unreleased_slots", "deadline_misses")},
                     history_check=checked["history_check"], sampler_cadence=checked["sampler_cadence"], sensor_coverage_fraction=checked["sensor_coverage_fraction"])
        finished[trial_id] = {"trial_id": trial_id, "monotonic_ns": finished_ns, "status": checked["status"]}
        last_finish = finished_ns
    manifest["state"] = "measurements_complete"
    manifest["measurements_completed_utc"] = utc_now()
    json_write(bundle / "measurements-complete.json", {"complete": True, "trials": list(finished), "completed_utc": utc_now()})
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    append_event(ledger, "schedule_complete", trial_count=len(finished))
    print(json.dumps({"bundle": str(bundle), "finished": len(finished), "state": "measurements_complete"}, indent=2))


def nearest(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(q * len(ordered)) - 1]


def summary_stats(values: list[float | None]) -> dict:
    data = [value for value in values if value is not None and math.isfinite(value)]
    return {"n": len(data), "median": statistics.median(data) if data else None, "min": min(data) if data else None, "max": max(data) if data else None}


def timing_stats(rows: list[dict], field: str) -> dict:
    values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
    return {"n": len(values), "median_ms": statistics.median(values) if values else None,
            "p95_ms": nearest(values, 0.95), "p99_ms": nearest(values, 0.99), "max_ms": max(values) if values else None}


def analyze_one(bundle: Path, repo: Path, row: dict) -> dict:
    ol, _, _, _ = import_runner(repo)
    output = bundle / row["output"]
    ol.analyze_campaign(output)
    phase = output / row["trial_id"]
    analysis = json.loads((phase / "analysis.json").read_text())
    entry = next(iter(analysis["trials"].values()))
    if entry["infrastructure_failures"]:
        raise RuntimeError(f"Offline verification found archive defects for {row['trial_id']}: {entry['infrastructure_failures']}")
    trial_id = next(iter(analysis["trials"]))
    directory = phase / trial_id
    worker = json.loads((directory / "workload/summary.json").read_text())
    with (directory / "workload/requests.csv").open(newline="", encoding="utf-8") as handle:
        requests = list(csv.DictReader(handle))
    if len(requests) != worker["completed_requests"]:
        raise RuntimeError(f"CSV/summary request count mismatch: {row['trial_id']}")
    active_s = (worker["finished_ns"] - worker["started_ns"]) / 1e9
    missed = sum(r["deadline_missed"] == "True" for r in requests)
    failed = sum(r["success"] != "True" for r in requests)
    if missed != worker["deadline_misses"]:
        raise RuntimeError(f"Deadline rows do not reconcile for {row['trial_id']}")
    planned, arrived, completed, successful = (worker[key] for key in ("planned_releases", "arrived_releases", "completed_requests", "successful_requests"))
    if planned != row["planned_releases"] or arrived != completed + worker["skipped_releases"] or planned != arrived + worker["unreleased_slots"]:
        raise RuntimeError(f"Release accounting does not reconcile for {row['trial_id']}")
    if successful != sum(r["success"] == "True" for r in requests):
        raise RuntimeError(f"Success rows do not reconcile for {row['trial_id']}")
    retained_path = directory / "workload/retained-state.jsonl"
    retained = jsonl(retained_path)
    resources = json.loads((directory / "resource-summary.json").read_text())
    steady = resources["windows"]["steady_workload"]
    process_tree = jsonl(directory / "process_tree.jsonl")
    active_tree = [item for item in process_tree if worker["started_ns"] <= item["monotonic_ns"] <= worker["finished_ns"]]
    worker_cpu, observer_cpu, observer_rss = [], [], []
    for snapshot in active_tree:
        worker_cpu.append(sum(p["cpu_percent_one_core"] or 0 for p in snapshot["processes"] if p["role"] in ("root", "child")))
        observers = [p for p in snapshot["processes"] if p["role"] == "observer"]
        observer_cpu.append(sum(p["cpu_percent_one_core"] or 0 for p in observers))
        observer_rss.append(sum(p["rss_kib"] or 0 for p in observers) / 1024)
    tree_rss = [snapshot["tree_rss_sum_kib"] / 1024 for snapshot in active_tree]
    sensor = jsonl(directory / "tegrastats.jsonl")
    sensor_active = [item for item in sensor if worker["started_ns"] <= item["monotonic_ns"] <= worker["finished_ns"]]
    elapsed_minute_rows: dict[int, list[dict]] = {}
    for request in requests:
        minute = int((int(request["started_ns"]) - worker["started_ns"]) / 60e9)
        elapsed_minute_rows.setdefault(minute, []).append(request)
    one_minute = []
    for minute, chunk in sorted(elapsed_minute_rows.items()):
        one_minute.append({"trial_id": row["trial_id"], "minute": minute + 1, "requests": len(chunk),
                           "cycle_median_ms": statistics.median(float(x["cycle_ms"]) for x in chunk),
                           "cycle_p95_ms": nearest([float(x["cycle_ms"]) for x in chunk], 0.95),
                           "release_to_completion_median_ms": statistics.median(float(x["release_to_completion_ms"]) for x in chunk)})
    local_block = {}
    outliers = []
    for request in requests:
        block = int(request["slot"]) // max(1, round(30 * row["rate_hz"]))
        local_block.setdefault(block, []).append(float(request["cycle_ms"]))
    for index, request in enumerate(requests):
        cycle_ms = float(request["cycle_ms"])
        block = int(request["slot"]) // max(1, round(30 * row["rate_hz"]))
        local_excess = cycle_ms - statistics.median(local_block[block])
        above_100 = cycle_ms > 100
        above_local = local_excess > 10
        if above_100 or above_local:
            outliers.append({"trial_id": row["trial_id"], "request_index": index, "slot": request["slot"], "cycle_ms": cycle_ms,
                             "deadline_missed": request["deadline_missed"], "over_100ms": above_100,
                             "local_block_excess_ms": local_excess if above_local else None})
    metrics = {
        "cycle": timing_stats(requests, "cycle_ms"), "release_to_completion": timing_stats(requests, "release_to_completion_ms"),
        "invocation": timing_stats(requests, "invocation_ms"),
        "active_elapsed_s": active_s, "achieved_requests_per_s": completed / active_s if active_s else None,
        "successful_requests_per_s": successful / active_s if active_s else None,
        "planned_releases": planned, "arrived_releases": arrived, "completed_requests": completed,
        "successful_requests": successful, "failed_requests": failed, "skipped_releases": worker["skipped_releases"],
        "unreleased_slots": worker["unreleased_slots"], "deadline_misses": missed,
        "deadline_misses_per_executed": missed / completed if completed else None,
        "skipped_per_planned": worker["skipped_releases"] / planned if planned else None,
        "request_failures": dict(Counter(r.get("error_code") or r.get("error_phase") or "unknown" for r in requests if r["success"] != "True")),
        "maintenance_failures": worker["maintenance_failures"], "unexpected_backends": worker["unexpected_backends"],
        "idempotent_replays": worker["idempotent_replays"], "worker_cpu_percent_one_core": statistics.mean(worker_cpu) if worker_cpu else None,
        "observer_cpu_percent_one_core": statistics.mean(observer_cpu) if observer_cpu else None,
        "worker_rss_max_mib": max(tree_rss) if tree_rss else None, "observer_rss_max_mib": max(observer_rss) if observer_rss else None,
        "temperature_max_c": steady.get("max_temperature_c"),
        "sensor_samples": len(sensor_active), "process_tree_samples": len(active_tree),
        "sensor_coverage_fraction": (steady.get("power") or {}).get("coverage_fraction"),
        "history_first": ((retained[0].get("collections") or {}).get("lifecycle.history") if retained else None),
        "history_last": ((retained[-1].get("collections") or {}).get("lifecycle.history") if retained else None),
        "history_samples": len(retained), "retained_state_last": retained[-1] if retained else None,
        "first_minute_cycle_median_ms": one_minute[0]["cycle_median_ms"] if one_minute else None,
        "last_minute_cycle_median_ms": one_minute[-1]["cycle_median_ms"] if one_minute else None,
        "one_minute_trend": one_minute,
        "cycles_over_100ms": sum(float(r["cycle_ms"]) > 100 for r in requests),
        "prior_local_block_excess_count": sum(item["local_block_excess_ms"] is not None for item in outliers),
        "high_rate_prefix_available": row["rate_hz"] == 50 and completed >= 10000,
    }
    if row["rate_hz"] == 50:
        for block_index in range(10):
            start, end = block_index * 1000, (block_index + 1) * 1000
            if completed < end:
                break
            chunk = requests[start:end]
            endpoint = end
            retained_at = max((sample for sample in retained if sample.get("completed_requests", -1) <= endpoint), key=lambda x: x.get("completed_requests", -1), default=None)
            metrics.setdefault("high_rate_prefix", []).append({
                "trial_id": row["trial_id"], "block": block_index + 1, "completed_start": start + 1, "completed_end": end,
                "cycle_median_ms": statistics.median(float(x["cycle_ms"]) for x in chunk),
                "cycle_p95_ms": nearest([float(x["cycle_ms"]) for x in chunk], .95),
                "cycle_max_ms": max(float(x["cycle_ms"]) for x in chunk),
                "retained_sample_completed": retained_at.get("completed_requests") if retained_at else None,
                "history_entries": ((retained_at.get("collections") or {}).get("lifecycle.history") if retained_at else None),
            })
    status = entry["status"]
    return {"schedule": row, "status": status, "infrastructure_failures": entry["infrastructure_failures"],
            "performance_failures": entry["performance_failures"], "terminal": entry["terminal"],
            "metrics": metrics, "resource_summary": resources, "workload_summary": worker,
            "process_tree_path": "process_tree.jsonl", "outliers": outliers}


def paired(rows: list[dict]) -> list[dict]:
    by_id = {row["schedule"]["trial_id"]: row for row in rows}
    output = []
    for index in range(1, 4):
        block = f"H0{index}"
        baseline, treatment = by_id.get(f"{block}-B"), by_id.get(f"{block}-I")
        output.append(pair_row(block, "indexed_minus_pinned", baseline, treatment, "indexed", "pinned"))
    for index in range(1, 4):
        block = f"M0{index}"
        fast, slow = by_id.get(f"{block}-F"), by_id.get(f"{block}-S")
        output.append(pair_row(block, "2000ms_minus_250ms", fast, slow, "2000ms", "250ms"))
    return output


def pair_row(block: str, comparison: str, ref: dict | None, treatment: dict | None, ref_label: str, base_label: str) -> dict:
    metrics = ("achieved_requests_per_s", "cycle", "release_to_completion", "observer_cpu_percent_one_core", "worker_cpu_percent_one_core")
    deltas = {}
    for metric in metrics:
        if ref is None or treatment is None:
            deltas[metric] = None
        elif metric in ("cycle", "release_to_completion"):
            deltas[metric] = {key: (treatment["metrics"][metric][f"{key}_ms"] - ref["metrics"][metric][f"{key}_ms"])
                              for key in ("median", "p95")}
        else:
            deltas[metric] = treatment["metrics"][metric] - ref["metrics"][metric]
    return {"block": block, "comparison": comparison, "reference": ref["schedule"]["trial_id"] if ref else None,
            "treatment": treatment["schedule"]["trial_id"] if treatment else None, "difference": deltas}


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def analyze_bundle(bundle: Path, repo: Path) -> None:
    if not (bundle / "measurements-complete.json").is_file():
        raise RuntimeError("Refusing aggregate analysis before all 16 trials completed")
    manifest = json.loads((bundle / "manifest.json").read_text())
    rows = [analyze_one(bundle, repo, row) for row in manifest["rows"]]
    pairs = paired(rows)
    per_run = []
    minutes = []
    prefixes = []
    outliers = []
    for row in rows:
        schedule, metrics = row["schedule"], row["metrics"]
        record = {**schedule, "status": row["status"], "performance_failures": ";".join(row["performance_failures"]),
                  "active_elapsed_s": metrics["active_elapsed_s"], "achieved_requests_per_s": metrics["achieved_requests_per_s"],
                  "successful_requests_per_s": metrics["successful_requests_per_s"], "cycle_median_ms": metrics["cycle"]["median_ms"],
                  "cycle_p95_ms": metrics["cycle"]["p95_ms"], "cycle_p99_ms": metrics["cycle"]["p99_ms"], "cycle_max_ms": metrics["cycle"]["max_ms"],
                  "release_to_completion_median_ms": metrics["release_to_completion"]["median_ms"],
                  "release_to_completion_p95_ms": metrics["release_to_completion"]["p95_ms"], "release_to_completion_max_ms": metrics["release_to_completion"]["max_ms"],
                  "planned": metrics["planned_releases"], "arrived": metrics["arrived_releases"], "completed": metrics["completed_requests"],
                  "successful": metrics["successful_requests"], "failed": metrics["failed_requests"], "skipped": metrics["skipped_releases"],
                  "unreleased": metrics["unreleased_slots"], "deadline_misses": metrics["deadline_misses"],
                  "misses_per_executed": metrics["deadline_misses_per_executed"], "skips_per_planned": metrics["skipped_per_planned"],
                  "maintenance_failures": metrics["maintenance_failures"], "request_failures": json.dumps(metrics["request_failures"], sort_keys=True),
                  "history_first": metrics["history_first"], "history_last": metrics["history_last"],
                  "worker_cpu_percent_one_core": metrics["worker_cpu_percent_one_core"], "observer_cpu_percent_one_core": metrics["observer_cpu_percent_one_core"],
                  "worker_rss_max_mib": metrics["worker_rss_max_mib"], "observer_rss_max_mib": metrics["observer_rss_max_mib"], "max_temperature_c": metrics["temperature_max_c"],
                  "sensor_samples": metrics["sensor_samples"], "process_tree_samples": metrics["process_tree_samples"],
                  "sensor_coverage_fraction": metrics["sensor_coverage_fraction"], "cycles_over_100ms": metrics["cycles_over_100ms"],
                  "high_rate_prefix_available": metrics["high_rate_prefix_available"]}
        per_run.append(record)
        minutes.extend(metrics["one_minute_trend"])
        prefixes.extend(metrics.get("high_rate_prefix", []))
        outliers.extend(row["outliers"])
    write_csv(bundle / "per-run-results.csv", per_run, list(per_run[0]))
    write_csv(bundle / "paired-differences.csv", [{"block": p["block"], "comparison": p["comparison"], "reference": p["reference"], "treatment": p["treatment"],
                                                    "cycle_median_delta_ms": (p["difference"]["cycle"] or {}).get("median"),
                                                    "cycle_p95_delta_ms": (p["difference"]["cycle"] or {}).get("p95"),
                                                    "release_median_delta_ms": (p["difference"]["release_to_completion"] or {}).get("median"),
                                                    "release_p95_delta_ms": (p["difference"]["release_to_completion"] or {}).get("p95"),
                                                    "throughput_delta_rps": p["difference"]["achieved_requests_per_s"],
                                                    "observer_cpu_delta_percent_one_core": p["difference"]["observer_cpu_percent_one_core"],
                                                    "worker_cpu_delta_percent_one_core": p["difference"]["worker_cpu_percent_one_core"]} for p in pairs],
              ["block", "comparison", "reference", "treatment", "cycle_median_delta_ms", "cycle_p95_delta_ms", "release_median_delta_ms", "release_p95_delta_ms", "throughput_delta_rps", "observer_cpu_delta_percent_one_core", "worker_cpu_delta_percent_one_core"])
    write_csv(bundle / "one-minute-trends.csv", minutes, ["trial_id", "minute", "requests", "cycle_median_ms", "cycle_p95_ms", "release_to_completion_median_ms"])
    write_csv(bundle / "high-rate-prefix.csv", prefixes, ["trial_id", "block", "completed_start", "completed_end", "cycle_median_ms", "cycle_p95_ms", "cycle_max_ms", "retained_sample_completed", "history_entries"])
    write_csv(bundle / "outliers.csv", outliers, ["trial_id", "request_index", "slot", "cycle_ms", "deadline_missed", "over_100ms", "local_block_excess_ms"])
    high = [row for row in rows if row["schedule"]["trial_id"].startswith("H")]
    monitoring = [row for row in rows if row["schedule"]["trial_id"].startswith("M")]
    def condition_summary(items: list[dict]) -> dict:
        keys = ("achieved_requests_per_s", "successful_requests_per_s", "cycle", "release_to_completion", "deadline_misses", "skipped_releases", "completed_requests", "successful_requests", "worker_rss_max_mib", "observer_rss_max_mib", "worker_cpu_percent_one_core", "observer_cpu_percent_one_core", "temperature_max_c", "sensor_coverage_fraction")
        out = {"n_independent_runs": len(items), "per_run": [item["metrics"] for item in items]}
        for key in keys:
            if key in ("cycle", "release_to_completion"):
                for statistic in ("median_ms", "p95_ms", "p99_ms", "max_ms"):
                    out[f"{key}_{statistic}"] = summary_stats([item["metrics"][key][statistic] for item in items])
            else:
                out[key] = summary_stats([item["metrics"][key] for item in items])
        out["totals"] = {key: sum(item["metrics"][key] for item in items) for key in ("planned_releases", "arrived_releases", "completed_requests", "successful_requests", "failed_requests", "skipped_releases", "unreleased_slots", "deadline_misses", "maintenance_failures")}
        return out
    summary = {
        "protocol": {"reference_commit": REF_COMMIT, "upstream_commit": UPSTREAM_COMMIT, "monitoring_treatment": "combined resource-monitor configuration (process-tree sampler, tegrastats, logging, serialization and interval-periodic guards); not unmonitored performance", "deadline": "one release period, release-relative, includes maintenance", "rate_denominator": "completed requests divided by worker active elapsed time (finished_ns - started_ns)", "replication": "independent runs, not requests; n=3 per primary condition", "sampling_caveat": "2,000 ms sampling can miss short-lived peaks; resource peaks are observational only"},
        "campaign": manifest, "status_counts": dict(Counter(row["status"] for row in rows)),
        "conditions": {"high_rate": condition_summary(high), "monitoring_frequency": condition_summary(monitoring)},
        "paired_differences": pairs,
        "data_quality": {"verified_archives": len(rows), "infrastructure_failures": sum(bool(row["infrastructure_failures"]) for row in rows),
                         "performance_flagged_trials": sum(row["status"] == "performance_failure" for row in rows),
                         "release_accounting_checks_passed": True, "deadline_row_counts_reconciled": True,
                         "high_rate_prefix_runs_available": [row["schedule"]["trial_id"] for row in high if row["metrics"]["high_rate_prefix_available"]],
                         "high_rate_prefix_runs_unavailable": [row["schedule"]["trial_id"] for row in high if not row["metrics"]["high_rate_prefix_available"]],
                         "outlier_count_over_100ms": sum(row["metrics"]["cycles_over_100ms"] for row in rows),
                         "matched_30s_local_block_excess_rows": sum(row["metrics"]["prior_local_block_excess_count"] for row in rows)},
    }
    summary["conditions"]["high_rate"]["by_store"] = {
        store: condition_summary([item for item in high if item["schedule"]["store"] == store])
        for store in ("pinned", "indexed")
    }
    summary["conditions"]["monitoring_frequency"]["by_interval_ms"] = {
        str(interval): condition_summary([item for item in monitoring if item["schedule"]["interval_ms"] == interval])
        for interval in (250, 2000)
    }
    json_write(bundle / "summary.json", summary)
    write_markdown(bundle, rows, pairs, summary)
    write_manuscript_insertions(bundle, rows, pairs)
    write_final_readme(bundle, rows, pairs, summary)
    make_plots(bundle, rows, pairs, prefixes, minutes)
    manifest["state"] = "analysis_complete"
    manifest["analysis_completed_utc"] = utc_now()
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"bundle": str(bundle), "rows": len(rows), "pairs": len(pairs), "summary": str(bundle / "summary.json")}, indent=2))


def fmt(value, digits=2) -> str:
    return "-" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def write_markdown(bundle: Path, rows: list[dict], pairs: list[dict], summary: dict) -> None:
    high_pairs = [pair for pair in pairs if pair["block"].startswith("H")]
    mon_pairs = [pair for pair in pairs if pair["block"].startswith("M")]
    def condition_line(name: str, condition: dict) -> str:
        cycle, p95, throughput = condition["cycle_median_ms"], condition["cycle_p95_ms"], condition["achieved_requests_per_s"]
        return (f"{name} condition medians [range], n={condition['n_independent_runs']}: "
                f"cycle median {fmt(cycle['median'],3)} [{fmt(cycle['min'],3)}, {fmt(cycle['max'],3)}] ms; "
                f"cycle p95 {fmt(p95['median'],3)} [{fmt(p95['min'],3)}, {fmt(p95['max'],3)}] ms; "
                f"throughput {fmt(throughput['median'],3)} [{fmt(throughput['min'],3)}, {fmt(throughput['max'],3)}] req/s.")
    lines = ["# Overnight CP2N2 follow-up results", "", "All paired values are treatment minus reference: indexed minus pinned for H; 2,000 ms minus 250 ms for M. Each pair is an independent run block. No equivalence claim is made from three noisy pairs.", "",
             "## High-rate indexing: 50 requests/s", "",
             condition_line("Pinned", summary["conditions"]["high_rate"]["by_store"]["pinned"]), "",
             condition_line("Indexed", summary["conditions"]["high_rate"]["by_store"]["indexed"]), "",
             "| Pair | Order | Indexed - pinned throughput req/s | Cycle median ms | Cycle p95 ms | Pinned/indexed misses | Pinned/indexed skips |", "| --- | --- | ---: | ---: | ---: | --- | --- |"]
    by_id = {row["schedule"]["trial_id"]: row for row in rows}
    for pair in high_pairs:
        b, i = by_id.get(pair["reference"]), by_id.get(pair["treatment"])
        d = pair["difference"]
        order = "indexed first" if i and b and i["schedule"]["order"] < b["schedule"]["order"] else "pinned first"
        lines.append(f"| {pair['block']} | {order} | {fmt(d['achieved_requests_per_s'],3)} | {fmt(d['cycle']['median'],3)} | {fmt(d['cycle']['p95'],3)} | {b['metrics']['deadline_misses'] if b else '-'} / {i['metrics']['deadline_misses'] if i else '-'} | {b['metrics']['skipped_releases'] if b else '-'} / {i['metrics']['skipped_releases'] if i else '-'} |")
    lines += ["", "## Monitoring frequency: 10 requests/s", "",
              condition_line("250 ms", summary["conditions"]["monitoring_frequency"]["by_interval_ms"]["250"]), "",
              condition_line("2,000 ms", summary["conditions"]["monitoring_frequency"]["by_interval_ms"]["2000"]), "",
              "| Pair | Order | 2,000 - 250 throughput req/s | Cycle median ms | Cycle p95 ms | Observer CPU delta, % of one core | Fast/slow misses | Fast/slow skips |", "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |"]
    for pair in mon_pairs:
        f, s = by_id.get(pair["reference"]), by_id.get(pair["treatment"])
        d = pair["difference"]
        order = "slow first" if f and s and s["schedule"]["order"] < f["schedule"]["order"] else "fast first"
        lines.append(f"| {pair['block']} | {order} | {fmt(d['achieved_requests_per_s'],3)} | {fmt(d['cycle']['median'],3)} | {fmt(d['cycle']['p95'],3)} | {fmt(d['observer_cpu_percent_one_core'],3)} | {f['metrics']['deadline_misses'] if f else '-'} / {s['metrics']['deadline_misses'] if s else '-'} | {f['metrics']['skipped_releases'] if f else '-'} / {s['metrics']['skipped_releases'] if s else '-'} |")
    lines += ["", "See `per-run-results.csv` for medians, nearest-rank p95/p99/max, release-to-completion, exact accounting, failures, memory, CPU, temperature and coverage. `one-minute-trends.csv` and `high-rate-prefix.csv` provide the prescribed trend and matched-request analysis. `outliers.csv` describes >100 ms cycles and the previous 30-second-block excess criterion.", "", "Interpret monitoring as a combined change to process-tree/tegrastats sampling, logging, serialization and guard cadence, not as disabling the monitoring stack. At 2,000 ms, short peaks can be missed. Do not claim isolated software power or general capacity from these runs.", "", "This report uses the exact worktree/ref and package identities in `manifest.json` and `preflight.json`. Read `README.md` for reproduction, attempts, limitations, and source/configuration history."]
    (bundle / "results-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manuscript_insertions(bundle: Path, rows: list[dict], pairs: list[dict]) -> None:
    high = [p for p in pairs if p["block"].startswith("H")]
    monitor = [p for p in pairs if p["block"].startswith("M")]
    high_values = "; ".join(f"{p['block']}: throughput {fmt(p['difference']['achieved_requests_per_s'],3)} req/s, cycle median {fmt(p['difference']['cycle']['median'],3)} ms, p95 {fmt(p['difference']['cycle']['p95'],3)} ms" for p in high)
    monitor_values = "; ".join(f"{p['block']}: throughput {fmt(p['difference']['achieved_requests_per_s'],3)} req/s, cycle median {fmt(p['difference']['cycle']['median'],3)} ms, p95 {fmt(p['difference']['cycle']['p95'],3)} ms" for p in monitor)
    text = f"""# Proposed Manuscript Insertions

Draft text only; do not overwrite the manuscript. Values below are generated from the verified run bundle.

## Methods

We conducted three independent paired blocks at 50 offered requests/s, comparing the unchanged pinned v5.1.1 lifecycle store with the evaluation-layer full-history index, and three independent paired 30-minute blocks at 10 requests/s, comparing 250 ms and 2,000 ms sampling of the same process-tree and tegrastats monitoring stack. Every condition used a fresh worker and backend state, default Python GC, full lifecycle retention, the same periodic skip-expired-slots scheduler, request logging, and a release-relative completion deadline equal to one period including maintenance. Trials were sequential with at least 60 seconds between adjacent runs. Throughput is completed requests divided by worker active time; misses and skipped releases are reported with executed/planned denominators. Replication is at the run-pair level, not per request.

## Results

High-rate paired differences (indexed minus pinned): {high_values}.

Monitoring paired differences (2,000 ms minus 250 ms): {monitor_values}.

Full per-run values, ranges and counts are in `results-summary.md`, `per-run-results.csv` and `paired-differences.csv`. State whether the latency pattern is consistent across all three pairs; do not replace these observed differences with an equivalence or non-interference claim.

## Limitations

The monitoring treatment combines sampling frequency, serialization/logging and periodic resource-guard cadence; it is not a monitoring-disabled comparison. Two-second sampling can miss short CPU, memory and temperature peaks. The experiment uses one Jetson, a simulated edge backend, a limited duration and three independent pairs per question. Indexing retains full history, but findings are specific to this workload and do not establish general CP2N2 capacity. The 25 W mode is a configured platform mode, not measured power consumption; platform observations are not control-plane-only energy. Room temperature is an approximate user estimate, not a continuous measurement.

## Professor Comments 4 and 6

- **Comment 4 (monitoring overhead):** We added paired 250 ms versus 2,000 ms measurements with the same monitoring stack and unchanged guards. The result is a comparison of the combined monitoring configuration, not unmonitored execution. Insert the exact three paired differences above and qualify the conclusion by their range and run-level replication.
- **Comment 6 (high-rate indexed execution):** We added three paired 50 requests/s comparisons of pinned and indexed full-retention stores. Insert all three paired differences, exact misses/skips and the first-10,000-request analysis where available. Do not imply that indexing bounds retained-history memory or generalizes beyond this workload.
"""
    (bundle / "manuscript-insertions.md").write_text(text, encoding="utf-8")


def make_plots(bundle: Path, rows: list[dict], pairs: list[dict], prefixes: list[dict], minutes: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for row in rows:
        if row["schedule"]["trial_id"].startswith("H"):
            series = [item for item in prefixes if item["trial_id"] == row["schedule"]["trial_id"]]
            if series:
                label = f"{row['schedule']['trial_id']} ({row['schedule']['store']})"
                axes[0].plot([item["completed_end"] for item in series], [item["cycle_median_ms"] for item in series], marker="o", label=label)
                axes[1].plot([item["completed_end"] for item in series], [item["history_entries"] for item in series], marker="o", label=label)
    axes[0].set(title="First 10,000 completed requests", xlabel="Completed request", ylabel="Cycle median (ms)")
    axes[1].set(title="Retained history at sampled block endpoints", xlabel="Completed request", ylabel="History entries")
    for axis in axes:
        axis.grid(True, alpha=.25)
        axis.legend(fontsize=7)
    fig.tight_layout(); fig.savefig(bundle / "high-rate-prefix.png", dpi=160); plt.close(fig)
    fig, axis = plt.subplots(figsize=(11, 5))
    for row in rows:
        if row["schedule"]["trial_id"].startswith("M"):
            series = [item for item in minutes if item["trial_id"] == row["schedule"]["trial_id"]]
            axis.plot([item["minute"] for item in series], [item["cycle_median_ms"] for item in series], label=row["schedule"]["trial_id"])
    axis.set(title="One-minute complete-cycle medians", xlabel="Active minute", ylabel="Cycle median (ms)")
    axis.grid(True, alpha=.25); axis.legend(ncol=2, fontsize=8); fig.tight_layout(); fig.savefig(bundle / "monitoring-trends.png", dpi=160); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for axis, key, title, ylabel in ((axes[0], "H", "50/s: indexed - pinned", "Latency difference (ms)"), (axes[1], "M", "2,000 - 250 ms", "Latency difference (ms)")):
        selected = [pair for pair in pairs if pair["block"].startswith(key)]
        x = list(range(1, len(selected) + 1))
        for offset, metric, label in ((-.2, "cycle", "cycle median"), (0, "cycle_p95", "cycle p95")):
            vals = []
            for pair in selected:
                if metric == "cycle_p95":
                    vals.append(pair["difference"]["cycle"]["p95"])
                else:
                    vals.append(pair["difference"]["cycle"]["median"])
            axis.bar([i + offset for i in x], vals, width=.18, label=label)
        tput = [pair["difference"]["achieved_requests_per_s"] for pair in selected]
        throughput_axis = axis.twinx()
        throughput_axis.plot(x, tput, marker="s", color="black", label="throughput difference (req/s)")
        axis.axhline(0, color="gray", linewidth=.8); axis.set_xticks(x, [pair["block"] for pair in selected])
        axis.set(title=title, xlabel="Independent pair", ylabel=ylabel); throughput_axis.set_ylabel("Throughput difference (req/s)")
        axis.grid(True, axis="y", alpha=.25)
        handles1, labels1 = axis.get_legend_handles_labels()
        handles2, labels2 = throughput_axis.get_legend_handles_labels()
        axis.legend(handles1 + handles2, labels1 + labels2, fontsize=7)
    fig.tight_layout(); fig.savefig(bundle / "paired-results.png", dpi=160); plt.close(fig)


def self_test(bundle: Path, repo: Path) -> None:
    ol, _, _, _ = import_runner(repo)
    manifest = json.loads((bundle / "manifest.json").read_text())
    rows = manifest["rows"]
    ids = [row["trial_id"] for row in rows]
    assert ids == [item[0] for item in SCHEDULE]
    assert manifest["protocol_trial_ids"] == list(PROTOCOL_TRIAL_IDS)
    assert len({row["output"] for row in rows}) == 16
    assert [row["store"] for row in rows if row["trial_id"].startswith("H02")] == ["indexed", "pinned"]
    assert [row["interval_ms"] for row in rows if row["trial_id"].startswith("M02")] == [2000, 250]
    for row in rows:
        assert sha256(bundle / row["config"]) == row["config_sha256"]
        config = json.loads((bundle / row["config"]).read_text())
        spec = ol.campaign_specs(config)[row["trial_id"]][0]
        assert spec["diagnostic_counts"] and spec["spike_instrumentation"] is False
        assert spec["intrusive_profile"] == "none" and spec["gc_policy"] == "default"
        assert spec["mode"] == "orchestrated" and spec["lifecycle_variant"] == row["store"]
        assert spec["duration_s"] == row["duration_s"] and spec["rate_hz"] == row["rate_hz"]
    sample = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert nearest(sample, .95) == 5.0 and nearest(sample, .99) == 5.0
    dummy = []
    for row in rows:
        dummy.append({"schedule": row, "metrics": {"achieved_requests_per_s": float(row["order"]),
            "cycle": {"median_ms": float(row["order"]), "p95_ms": float(row["order"])},
            "release_to_completion": {"median_ms": float(row["order"]), "p95_ms": float(row["order"])},
            "observer_cpu_percent_one_core": float(row["order"]), "worker_cpu_percent_one_core": float(row["order"])}})
    pair_checks = paired(dummy)
    assert len(pair_checks) == 6 and pair_checks[0]["difference"]["achieved_requests_per_s"] == 1
    assert pair_checks[3]["difference"]["achieved_requests_per_s"] == 1
    if ol.source_fingerprint() != json.loads((bundle / "source-fingerprint.json").read_text())["evaluation_source_sha256"]:
        raise RuntimeError("Frozen source fingerprint mismatch")
    if sha256(bundle / "tools/overnight.py") != sha256(Path(__file__).resolve()):
        raise RuntimeError("Frozen helper copy mismatch")
    print(json.dumps({"self_test": "passed", "rows": len(rows), "reference_commit": REF_COMMIT}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "self-test", "run", "analyze"))
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--ambient-estimate-c", type=float, default=24.0)
    args = parser.parse_args()
    if args.command == "prepare":
        args.bundle = prepare(args)
    elif args.bundle is None:
        parser.error(f"--bundle is required for {args.command}")
    else:
        bundle = args.bundle.resolve()
        if args.command == "self-test":
            self_test(bundle, args.repo.resolve())
        elif args.command == "run":
            self_test(bundle, args.repo.resolve())
            run_campaign(bundle, args.repo.resolve(), args.upstream.resolve(), Path(os.path.abspath(args.python)))
        else:
            analyze_bundle(bundle, args.repo.resolve())


if __name__ == "__main__":
    main()
