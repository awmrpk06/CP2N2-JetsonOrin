"""External process-tree sampling for pinned upstream experiments."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from statistics import mean

import psutil


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_process_group(process: subprocess.Popen, grace_s: float = 3.0, poll_s: float = 0.02) -> dict:
    """Stop an owned session leader and anything left in its group; a vanished group is the normal end state."""
    record = {"root_exited_before_stop": process.poll() is not None, "signals": [], "permission_denied": False}
    for signum in (signal.SIGTERM, signal.SIGKILL):
        if not _group_exists(process.pid):
            break
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            break
        except PermissionError:
            record["permission_denied"] = True
            break
        record["signals"].append(signum.name)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline and (process.poll() is None or _group_exists(process.pid)):
            time.sleep(poll_s)
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    record.update(returncode=process.returncode, root_reaped=process.returncode is not None, group_remaining=_group_exists(process.pid))
    return record


class ProcessTreeSampler:
    """Track discovered children by PID and creation time, including reparenting."""

    def __init__(self, root_pid: int, observer_pids: tuple[int, ...] = ()) -> None:
        self.root_pid = root_pid
        self.observer_pids = observer_pids
        try:
            self.root = psutil.Process(root_pid)
            self.root.create_time()
        except psutil.Error:
            self.root = None
        self.known: dict[tuple[int, float], psutil.Process] = {}
        self.previous: dict[tuple[int, float], tuple[int, float]] = {}
        self.last_memory_ns = 0

    def sample(self) -> dict:
        sampled_ns = time.monotonic_ns()
        discovery_errors = []
        try:
            root = self.root
            if root is None or not root.is_running():
                raise psutil.NoSuchProcess(self.root_pid)
            processes = [root, *root.children(recursive=True)]
        except psutil.Error as exc:
            processes = []
            discovery_errors.append(type(exc).__name__)
        for process in processes:
            try:
                self.known[(process.pid, process.create_time())] = process
            except psutil.Error as exc:
                discovery_errors.append(type(exc).__name__)
        candidates = [(identity, process, "root" if identity[0] == self.root_pid else "child") for identity, process in self.known.items()]
        for pid in self.observer_pids:
            try:
                process = psutil.Process(pid)
                candidates.append(((pid, process.create_time()), process, "observer"))
            except psutil.Error as exc:
                discovery_errors.append(type(exc).__name__)
        collect_pss = sampled_ns - self.last_memory_ns >= 1_000_000_000
        records = []
        errors = []
        for identity, process, role in candidates:
            try:
                if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                with process.oneshot():
                    cpu = process.cpu_times()
                    cpu_seconds = cpu.user + cpu.system
                    memory = process.memory_info()
                    record = {
                        "pid": process.pid, "created_s": identity[1], "ppid": process.ppid(),
                        "role": role, "name": process.name(), "threads": process.num_threads(),
                        "rss_kib": memory.rss / 1024, "vms_kib": memory.vms / 1024,
                        "cpu_time_s": cpu_seconds,
                        "pss_kib": None, "uss_kib": None, "cpu_percent_one_core": None,
                    }
                read_ns = time.monotonic_ns()
                record["monotonic_ns"] = read_ns
                previous = self.previous.get(identity)
                if previous is not None and read_ns > previous[0] and cpu_seconds >= previous[1]:
                    record["cpu_percent_one_core"] = 100 * (cpu_seconds - previous[1]) / ((read_ns - previous[0]) / 1e9)
                self.previous[identity] = (read_ns, cpu_seconds)
                if collect_pss:
                    try:
                        full_memory = process.memory_full_info()
                        record["pss_kib"] = getattr(full_memory, "pss", None)
                        record["uss_kib"] = getattr(full_memory, "uss", None)
                        for key in ("pss_kib", "uss_kib"):
                            if record[key] is not None:
                                record[key] /= 1024
                    except psutil.Error as exc:
                        record["memory_detail_error"] = type(exc).__name__
                records.append(record)
            except psutil.Error as exc:
                errors.append({"pid": identity[0], "error": type(exc).__name__})
        if collect_pss:
            self.last_memory_ns = sampled_ns
        live_ids = {(record["pid"], record["created_s"]) for record in records}
        self.known = {identity: process for identity, process in self.known.items() if identity in live_ids}
        self.previous = {identity: sample for identity, sample in self.previous.items() if identity in live_ids}
        workload = [record for record in records if record["role"] != "observer"]
        all_pss = bool(workload) and all(record["pss_kib"] is not None for record in workload)
        return {
            "monotonic_ns": sampled_ns, "finished_ns": time.monotonic_ns(),
            "root_pid": self.root_pid, "processes": records, "errors": errors,
            "discovery_errors": discovery_errors,
            "workload_process_count": len(workload),
            "tree_rss_sum_kib": sum(record["rss_kib"] for record in workload),
            "tree_pss_sum_kib": sum(record["pss_kib"] for record in workload) if all_pss else None,
            "cpu_normalization": "100 percent = one logical CPU; child CPU is not added to parent counters",
        }


def thermal_guard_limit() -> tuple[float, list[dict]]:
    trips = []
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        for kind_path in zone.glob("trip_point_*_type"):
            try:
                kind = kind_path.read_text().strip()
                temperature = float(kind_path.with_name(kind_path.name.replace("_type", "_temp")).read_text()) / 1000
                if kind in {"passive", "critical"} and temperature > 0:
                    trips.append({"zone": zone.name, "kind": kind, "temperature_c": temperature})
            except (OSError, ValueError):
                continue
    return min([80.0, *[trip["temperature_c"] - 5 for trip in trips]]), trips


def memory_guard_reason(available_bytes: int, tree_rss_sum_kib: float, *, reserve_bytes: int = 1024 ** 3, limit_kib: int = 1024 ** 2) -> str | None:
    if available_bytes < reserve_bytes:
        return "system_memory_reserve"
    if tree_rss_sum_kib > limit_kib:
        return "workload_resident_memory_limit"
    return None


def valid_window(bounds) -> bool:
    return isinstance(bounds, (list, tuple)) and len(bounds) == 2 and all(type(value) is int for value in bounds) and bounds[0] < bounds[1]


def read_samples(path: Path) -> tuple[list[dict], int]:
    """Read timestamped JSON lines, counting (not failing on) lines truncated by an interrupted writer."""
    rows, rejected = [], 0
    if not path.exists():
        return rows, rejected
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if isinstance(row, dict) and type(row.get("monotonic_ns")) is int:
                rows.append(row)
            else:
                rejected += 1
    return rows, rejected


def summarize_profile(folder: Path, windows: dict, interval_ms: int) -> dict:
    from evaluation.evaluate_tegra_resources import power_integral

    power_samples, power_rejected = read_samples(folder / "tegrastats.jsonl")
    snapshots, tree_rejected = read_samples(folder / "process_tree.jsonl")
    result = {}
    ignored = sorted(name for name, bounds in windows.items() if not valid_window(bounds))
    for name, bounds in windows.items():
        if name in ignored:
            continue
        started, finished = bounds
        selected = [sample for sample in power_samples if started <= sample["monotonic_ns"] <= finished]
        tree = [sample for sample in snapshots if started <= sample["monotonic_ns"] <= finished]
        safe_power = []
        gaps = []
        for index, sample in enumerate(power_samples):
            if index:
                first, last = power_samples[index - 1], sample
                gap_s = (last["monotonic_ns"] - first["monotonic_ns"]) / 1e9
                if gap_s > 3 * interval_ms / 1000 and first["monotonic_ns"] < finished and last["monotonic_ns"] > started:
                    gaps.append(gap_s)
                    safe_power.append({"monotonic_ns": (first["monotonic_ns"] + last["monotonic_ns"]) // 2})
            safe_power.append(dict(sample))

        def observed(field, rows=selected):
            return [sample[field] for sample in rows if sample.get(field) is not None]

        per_process = {}
        for snapshot in tree:
            for process in snapshot["processes"]:
                identity = f"{process['pid']}:{process['created_s']}"
                per_process.setdefault(identity, []).append(process)
        process_summary = []
        for identity, records in per_process.items():
            cpu = [record["cpu_percent_one_core"] for record in records if record["cpu_percent_one_core"] is not None]
            pss = [record["pss_kib"] for record in records if record["pss_kib"] is not None]
            process_summary.append({
                "identity": identity, "name": records[0]["name"], "role": records[0]["role"], "samples": len(records),
                "rss_max_mib": max(record["rss_kib"] for record in records) / 1024,
                "pss_max_mib": max(pss) / 1024 if pss else None,
                "cpu_mean_percent_one_core": mean(cpu) if cpu else None,
            })
        result[name] = {
            "duration_s": (finished - started) / 1e9,
            "sensor_samples": len(selected), "process_tree_samples": len(tree),
            "power": power_integral(safe_power, started, finished), "power_gaps_s": gaps,
            "max_temperature_c": max(observed("max_temperature_c"), default=None),
            "system_ram_max_mb": max(observed("ram_used_mb"), default=None),
            "system_swap_max_mb": max(observed("swap_used_mb"), default=None),
            "system_cpu_mean_percent": mean(observed("cpu_mean_percent")) if observed("cpu_mean_percent") else None,
            "gpu_max_percent": max(observed("gr3d_percent"), default=None),
            "observed_tree_rss_sum_max_mib": max((sample["tree_rss_sum_kib"] / 1024 for sample in tree), default=None),
            "observed_tree_pss_sum_max_mib": max((sample["tree_pss_sum_kib"] / 1024 for sample in tree if sample["tree_pss_sum_kib"] is not None), default=None),
            "processes": process_summary,
        }
    return {
        "windows": result,
        "ignored_windows": ignored,
        "rejected_lines": {"tegrastats": power_rejected, "process_tree": tree_rejected},
        "limitations": [
            "Whole-command interval includes imports, warmup, execution, output and plotting; it is not pure control-plane cost.",
            "Sampled process trees can miss children that start and exit between samples; summed RSS double-counts shared pages.",
            "CPU percentages use one-core normalization and first samples are undefined; PSS/USS are sampled at most once per second.",
            "Temperature is not heat flow; platform power and background load are not attributable solely to this workload.",
        ],
    }