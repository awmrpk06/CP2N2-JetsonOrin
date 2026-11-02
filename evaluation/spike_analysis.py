"""Offline latency-spike attribution and latency-trend estimates for sustained-workload archives.

A spike is a request whose complete cycle exceeds the median of its 30-second block (300 requests at
10 Hz) by at least a threshold, or that missed its deadline. With spike instrumentation, each spike's
excess is split into garbage-collection time, off-CPU time (descheduled or waiting) and non-GC on-CPU
work, each relative to the same block's medians; the dominant component names the cause when it covers
at least half the excess. Without instrumentation, spikes are reported as unattributed.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median

BLOCK = 300
THRESHOLDS_MS = (5.0, 10.0, 20.0)
PRIMARY_THRESHOLD_MS = 10.0
DOMINANCE = 0.5
INSTRUMENTED = ("gc_ms", "process_cpu_ms", "off_cpu_ms")
CONTENTION_INVOLUNTARY = 10
CONTENTION_PLATFORM_CPU = 50.0
WAIT_VOLUNTARY_FACTOR = 2.0
TEGRA_MATCH_NS = 1_000_000_000


def _float(value) -> float | None:
    return None if value in (None, "") else float(value)


def load_rows(workload: Path) -> list[dict]:
    with (workload / "requests.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_gc_events(workload: Path) -> list[dict] | None:
    path = workload / "gc-events.jsonl"
    if not path.exists():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def block_medians(values: list[float], block: int = BLOCK) -> list[float]:
    """Median of each value's own block; a short final block is merged into the previous one."""
    bounds = list(range(0, len(values), block))
    if len(bounds) > 1 and len(values) - bounds[-1] < block // 2:
        bounds.pop()
    result = []
    for index, start in enumerate(bounds):
        end = bounds[index + 1] if index + 1 < len(bounds) else len(values)
        result.extend([median(values[start:end])] * (end - start))
    return result


def trend(values: list[float], per: int = 600) -> dict:
    """Least-squares slope of per-window medians against request index, in ms per 1,000 requests."""
    windows = [(start + per / 2, median(values[start:start + per])) for start in range(0, len(values) - per + 1, per)]
    if len(windows) < 2:
        return {"windows": len(windows), "slope_ms_per_1k_requests": None, "first_window_median_ms": windows[0][1] if windows else None, "last_window_median_ms": windows[-1][1] if windows else None}
    mean_x = sum(x for x, _ in windows) / len(windows)
    mean_y = sum(y for _, y in windows) / len(windows)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in windows) / sum((x - mean_x) ** 2 for x, _ in windows)
    return {"windows": len(windows), "window_requests": per, "slope_ms_per_1k_requests": slope * 1000, "first_window_median_ms": windows[0][1], "last_window_median_ms": windows[-1][1]}


def _classify(components: dict, excess: float) -> str:
    if excess <= 0:
        return "deadline_only"
    name, value = max(components.items(), key=lambda item: item[1])
    return name if value >= DOMINANCE * excess else "mixed"


def attribute(rows: list[dict], threshold_ms: float = PRIMARY_THRESHOLD_MS) -> dict:
    cycle = [float(row["cycle_ms"]) for row in rows]
    base_cycle = block_medians(cycle) if cycle else []
    instrumented = bool(rows) and all(column in rows[0] for column in INSTRUMENTED)
    if instrumented:
        gc_ms = [float(row["gc_ms"]) for row in rows]
        off = [float(row["off_cpu_ms"]) for row in rows]
        on = [float(row["process_cpu_ms"]) - value for row, value in zip(rows, gc_ms)]
        base_gc, base_off, base_on = block_medians(gc_ms), block_medians(off), block_medians(on)
    spikes = []
    for index, row in enumerate(rows):
        excess = cycle[index] - base_cycle[index]
        missed = row.get("deadline_missed") == "True"
        if excess < threshold_ms and not missed:
            continue
        spike = {
            "index": index, "slot": int(row["slot"]), "cycle_ms": cycle[index], "block_median_ms": base_cycle[index],
            "excess_ms": excess, "deadline_missed": missed, "dispatch_lateness_ms": _float(row.get("dispatch_lateness_ms")),
        }
        if instrumented:
            components = {"gc": gc_ms[index] - base_gc[index], "off_cpu": off[index] - base_off[index], "on_cpu_non_gc": on[index] - base_on[index]}
            spike.update(
                components_ms=components, cause=_classify(components, excess),
                gc_collections=int(row["gc_collections"]), gc_max_generation=None if row["gc_max_generation"] == "" else int(row["gc_max_generation"]),
                involuntary_switches=int(row["involuntary_switches"]), major_faults=int(row["major_faults"]),
            )
        else:
            spike["cause"] = "unattributed_no_instrumentation"
        spikes.append(spike)
    return {"instrumented": instrumented, "threshold_ms": threshold_ms, "spikes": spikes}


def carried_delays(rows: list[dict], period_ms: float) -> dict:
    """Late dispatches and skipped releases are attributed to an over-period predecessor cycle when there is one."""
    late = skipped = carried_late = carried_skips = 0
    for index, row in enumerate(rows):
        lateness, skips = float(row["dispatch_lateness_ms"]), int(row["skipped_before"])
        predecessor_over = index > 0 and float(rows[index - 1]["cycle_ms"]) > period_ms
        if lateness >= 1.0:
            late += 1
            carried_late += predecessor_over
        if skips:
            skipped += skips
            carried_skips += skips if predecessor_over else 0
    return {"dispatches_late_by_1ms_or_more": late, "of_which_after_over_period_predecessor": carried_late, "skipped_releases_in_rows": skipped, "of_which_after_over_period_predecessor_skips": carried_skips}


def gc_alignment(rows: list[dict], events: list[dict] | None) -> dict | None:
    """Locate every generation-1/2 collection in the request it interrupted, if any."""
    if events is None:
        return None
    starts = [int(row["started_ns"]) for row in rows]
    counts = Counter(event["generation"] for event in events)
    located = []
    for event in events:
        if event["generation"] < 1 or event["start_ns"] is None:
            continue
        position = bisect.bisect_right(starts, event["start_ns"]) - 1
        inside = position >= 0 and event["start_ns"] <= int(rows[position]["cycle_finished_ns"])
        located.append({
            "generation": event["generation"], "duration_ms": event["duration_ns"] / 1e6, "main_thread": event.get("main_thread"),
            "request_index": position if inside else None, "request_cycle_ms": float(rows[position]["cycle_ms"]) if inside else None,
        })
    full = [item for item in located if item["generation"] == 2]
    return {
        "collections_by_generation": {str(key): value for key, value in sorted(counts.items())},
        "total_gc_ms": sum((event["duration_ns"] or 0) for event in events) / 1e6,
        "full_collections": full,
        "full_collection_max_ms": max((item["duration_ms"] for item in full), default=None),
        "generation1_max_ms": max((item["duration_ms"] for item in located if item["generation"] == 1), default=None),
    }


def load_platform(trial_folder: Path) -> list[tuple[int, float]] | None:
    path = trial_folder / "tegrastats.jsonl"
    if not path.exists():
        return None
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        sample = json.loads(line) if line.strip() else {}
        if sample.get("monotonic_ns") is not None and sample.get("cpu_mean_percent") is not None:
            samples.append((sample["monotonic_ns"], float(sample["cpu_mean_percent"])))
    return sorted(samples)


def classify_off_cpu(spikes: list[dict], rows: list[dict], platform: list[tuple[int, float]] | None) -> None:
    """Split off-CPU spikes: preemption under platform load versus extra voluntary waits (thread hand-offs, wake-ups)."""
    voluntary = [float(row["voluntary_switches"]) for row in rows]
    base = block_medians(voluntary)
    times = [sample[0] for sample in platform or ()]
    for spike in spikes:
        if spike.get("cause") != "off_cpu":
            continue
        row = rows[spike["index"]]
        started = int(row["started_ns"])
        cpu = None
        if times:
            position = bisect.bisect_left(times, started)
            near = min((index for index in (position - 1, position) if 0 <= index < len(times)), key=lambda index: abs(times[index] - started))
            cpu = platform[near][1] if abs(times[near] - started) <= TEGRA_MATCH_NS else None
        involuntary = int(row["involuntary_switches"])
        if involuntary >= CONTENTION_INVOLUNTARY or (cpu is not None and cpu >= CONTENTION_PLATFORM_CPU):
            subcause = "cpu_contention"
        elif voluntary[spike["index"]] >= WAIT_VOLUNTARY_FACTOR * max(base[spike["index"]], 1):
            subcause = "voluntary_wait"
        else:
            subcause = "unclassified"
        spike.update(off_cpu_subcause=subcause, platform_cpu_mean_percent=cpu, voluntary_switches=int(row["voluntary_switches"]), voluntary_block_median=base[spike["index"]])


def analyze_workload(workload: Path, rate_hz: float) -> dict:
    rows = load_rows(workload)
    events = load_gc_events(workload)
    primary = attribute(rows)
    if primary["instrumented"]:
        classify_off_cpu(primary["spikes"], rows, load_platform(workload.parent))
    by_threshold = {f"{threshold:g}ms": len(attribute(rows, threshold)["spikes"]) for threshold in THRESHOLDS_MS}
    spikes = primary["spikes"]
    causes = Counter(spike["cause"] for spike in spikes)
    explained = sum(count for cause, count in causes.items() if cause in ("gc", "off_cpu", "on_cpu_non_gc"))
    gc_major = [spike for spike in spikes if spike.get("gc_max_generation") == 2]
    return {
        "requests": len(rows), "instrumented": primary["instrumented"], "block_requests": BLOCK, "primary_threshold_ms": PRIMARY_THRESHOLD_MS,
        "spike_counts_by_threshold": by_threshold, "spikes": len(spikes), "deadline_missed_spikes": sum(spike["deadline_missed"] for spike in spikes),
        "causes": dict(causes), "off_cpu_subcauses": dict(Counter(spike["off_cpu_subcause"] for spike in spikes if "off_cpu_subcause" in spike)),
        "explained_fraction": explained / len(spikes) if spikes and primary["instrumented"] else None,
        "spikes_with_full_collection": len(gc_major),
        "largest": sorted(spikes, key=lambda spike: -spike["excess_ms"])[:10],
        "invocation_trend": trend([float(row["invocation_ms"]) for row in rows]),
        "cycle_trend": trend([float(row["cycle_ms"]) for row in rows]),
        "carried_delays": carried_delays(rows, 1000 / rate_hz),
        "gc": gc_alignment(rows, events),
        "method": __doc__.strip().splitlines()[0],
    }


def prior_alignment(folders: list[Path], top: int = 8) -> dict:
    """Uninstrumented runs: do the largest cycles fall at the same completed-request counts in every repetition?"""
    runs = {}
    for folder in folders:
        rows = load_rows(folder / "workload")
        order = sorted(range(len(rows)), key=lambda index: -float(rows[index]["cycle_ms"]))[:top]
        runs[folder.name] = [{"index": index, "slot": int(rows[index]["slot"]), "cycle_ms": float(rows[index]["cycle_ms"]), "dispatch_lateness_ms": float(rows[index]["dispatch_lateness_ms"])} for index in sorted(order)]
    return {"runs": runs, "note": "index = completed-request position (0-based); slot = scheduled release slot"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trial_folders", type=Path, nargs="+", help="sealed sustained trial folders (each containing workload/)")
    parser.add_argument("--rate-hz", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.rate_hz) or args.rate_hz <= 0:
        parser.error("--rate-hz must be positive")
    report = {
        "trials": {folder.name: analyze_workload(folder / "workload", args.rate_hz) for folder in args.trial_folders},
        "alignment": prior_alignment(args.trial_folders),
    }
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
