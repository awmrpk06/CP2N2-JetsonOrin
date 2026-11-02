"""Lifecycle-history variants, spike instrumentation and spike attribution; no device experiments are executed."""

from __future__ import annotations

import copy
import csv
import gc
import json
import random
import subprocess
import textwrap
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import pytest

from evaluation import growth_diagnostics, lifecycle_variants, spike_analysis
from evaluation import operating_limits as ol
from evaluation import upstream_sustained as worker
from evaluation.reproduce_original import ROOT, stage_command

PLAN = json.loads((ROOT / "docs/experiments/correction-and-spikes/plan.json").read_text())
UPSTREAM = ROOT / "_temp/cp2n2-v5.1.1"
PINNED_PYTHON = ROOT / "_temp/cp2n2-python312/bin/python"


class Record(SimpleNamespace):
    def model_copy(self, deep=False):
        return Record(**vars(self))


class ToyStore:
    """Mirrors the pinned store's history contract: append under an RLock, scan-and-copy queries."""

    def __init__(self):
        self._lock = RLock()
        self._history = []

    def transition(self, resource_id, to_state, *, reason, correlation_id=None):
        with self._lock:
            if to_state == "same":
                return None
            self._history.append(Record(resource_id=resource_id, to_state=to_state, correlation_id=correlation_id))
            return to_state

    def history(self, *, resource_id=None, correlation_id=None):
        with self._lock:
            records = self._history
            if resource_id is not None:
                records = [item for item in records if item.resource_id == resource_id]
            if correlation_id is not None:
                records = [item for item in records if item.correlation_id == correlation_id]
            return [item.model_copy(deep=True) for item in records]


def drive(store, steps, seed=7):
    rng = random.Random(seed)
    for _ in range(steps):
        store.transition(rng.choice("ab"), rng.choice(["x", "y", "same"]), reason="r", correlation_id=rng.choice([None, "c1", "c2", "c3"]))


def queries(store):
    return [
        [vars(item) for item in store.history(resource_id=resource, correlation_id=correlation)]
        for resource in (None, "a", "b") for correlation in (None, "c1", "c2", "c3", "missing")
    ]


def test_indexed_variant_answers_every_query_like_the_pinned_store():
    pinned, indexed = ToyStore(), ToyStore()
    drive(pinned, 5)
    drive(indexed, 5)
    record = lifecycle_variants.install(indexed, "indexed")
    assert record["counts_at_install"]["history"] == len(pinned._history)
    drive(pinned, 400, seed=11)
    drive(indexed, 400, seed=11)
    assert queries(indexed) == queries(pinned)
    assert type(indexed).__name__ == "IndexedLifecycleStore" and isinstance(indexed, ToyStore)


def test_bounded_variant_keeps_only_the_newest_transitions_and_a_consistent_index():
    pinned, bounded = ToyStore(), ToyStore()
    lifecycle_variants.install(bounded, "bounded", 25)
    drive(pinned, 300)
    drive(bounded, 300)
    newest = pinned._history[-25:]
    assert [vars(item) for item in bounded.history()] == [vars(item) for item in newest]
    for correlation in ("c1", "c2", "c3"):
        assert [vars(item) for item in bounded.history(correlation_id=correlation)] == [vars(item) for item in newest if item.correlation_id == correlation]
    counts = lifecycle_variants.counts(bounded)
    assert counts["history"] == counts["indexed_records"] == 25 and counts["evicted"] == len(pinned._history) - 25


@pytest.mark.parametrize(("variant", "limit"), [("fast", None), ("bounded", None), ("bounded", 0), ("indexed", 10), ("pinned", 5), ("bounded", True)])
def test_invalid_variant_settings_are_rejected(variant, limit):
    with pytest.raises(ValueError):
        lifecycle_variants.validate(variant, limit)


def test_variant_is_installed_once_and_pinned_leaves_the_store_untouched():
    store = ToyStore()
    assert lifecycle_variants.install(store, "pinned")["variant"] == "pinned" and type(store) is ToyStore
    lifecycle_variants.install(store, "indexed")
    with pytest.raises(RuntimeError):
        lifecycle_variants.install(store, "bounded", 5)


def test_indexed_variant_matches_the_real_pinned_store():
    if not PINNED_PYTHON.exists() or not (UPSTREAM / "core/lifecycle.py").exists():
        pytest.skip("Pinned upstream checkout or interpreter is not available")
    script = textwrap.dedent(f"""
        import importlib.util, json, random, sys
        sys.path.insert(0, {str(UPSTREAM)!r})
        spec = importlib.util.spec_from_file_location("variants", {str(ROOT / "evaluation/lifecycle_variants.py")!r})
        variants = importlib.util.module_from_spec(spec); spec.loader.exec_module(variants)
        from core.lifecycle import ALLOWED_TRANSITIONS, LifecycleStore
        def run(store):
            rng = random.Random(3)
            for resource in "ab":
                store.initialize(resource)
            for _ in range(600):
                resource = rng.choice("ab")
                current = store.snapshot(resource).state
                targets = sorted(state.value for state in ALLOWED_TRANSITIONS[current])
                store.transition(resource, rng.choice(targets), reason="r", correlation_id=rng.choice([None, "c1", "c2", "c3"]))
            return [[item.model_dump(mode="json", exclude={{"occurred_at"}}) for item in store.history(resource_id=r, correlation_id=c)]
                    for r in (None, "a", "b") for c in (None, "c1", "c2", "c3", "zz")]
        indexed = LifecycleStore(); variants.install(indexed, "indexed")
        print(json.dumps(run(LifecycleStore()) == run(indexed)))
    """)
    completed = subprocess.run([str(PINNED_PYTHON), "-c", script], capture_output=True, text=True, check=False, cwd=UPSTREAM)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) is True


def test_spike_recorder_attributes_collections_to_the_request_window(tmp_path):
    recorder = growth_diagnostics.SpikeRecorder(tmp_path / "gc-events.jsonl")
    recorder.start()
    mark = recorder.begin()
    gc.collect()
    row = recorder.end(mark)
    quiet = recorder.end(recorder.begin())
    stats = recorder.close()
    assert set(row) == set(growth_diagnostics.SPIKE_COLUMNS)
    assert row["gc_collections"] >= 1 and row["gc_max_generation"] == 2 and row["gc_ms"] > 0
    assert quiet["gc_collections"] == 0 and quiet["gc_max_generation"] is None
    events = [json.loads(line) for line in (tmp_path / "gc-events.jsonl").read_text().splitlines()]
    assert stats["events_written"] == len(events) >= 1 and stats["marks"] == 2 and not stats["dropped"]
    assert recorder._on_gc not in gc.callbacks


def test_worker_rejects_variants_outside_orchestrated_mode_and_spikes_with_intrusive_profiles():
    with pytest.raises(ValueError):
        worker.validate_options(5, 10, "direct", None, 0, lifecycle_variant="indexed")
    with pytest.raises(ValueError):
        worker.validate_options(5, 10, "orchestrated", None, 0, intrusive_profile="calls", spike_instrumentation=True)
    worker.validate_options(5, 10, "orchestrated", None, 0, lifecycle_variant="bounded", retention_limit=60, spike_instrumentation=True)


def test_default_commands_are_unchanged_and_variant_flags_round_trip(tmp_path):
    legacy = stage_command("sustained", tmp_path, upstream=tmp_path, mode="orchestrated", diagnostic_counts=True)
    assert not {"--lifecycle-variant", "--retention-limit", "--spike-instrumentation"} & set(legacy)
    assert ol._command_settings(legacy)["lifecycle_variant"] == "pinned"
    command = stage_command("sustained", tmp_path, upstream=tmp_path, lifecycle_variant="bounded", retention_limit=6000, spike_instrumentation=True)
    settings = ol._command_settings(command)
    assert (settings["lifecycle_variant"], settings["retention_limit"], settings["spike_instrumentation"]) == ("bounded", 6000, True)
    with pytest.raises(ValueError):
        stage_command("overhead", tmp_path, spike_instrumentation=True)


def test_correction_plan_is_balanced_and_labels_variants():
    plans = ol.campaign_specs(PLAN)
    assert list(plans) == ["pilot", "correction"] == list(ol.phase_order(PLAN))
    correction = plans["correction"]
    assert [row["lifecycle_variant"] for row in correction] == ["pinned", "indexed", "bounded", "indexed", "bounded", "pinned"]
    assert all(row["spike_instrumentation"] and row["duration_s"] == 1200 and row["mode"] == "orchestrated" for row in correction)
    assert {row["retention_limit"] for row in correction if row["lifecycle_variant"] == "bounded"} == {6000}
    assert all(row["retention_limit"] is None for row in correction if row["lifecycle_variant"] != "bounded")
    assert ol.estimate_runtime(PLAN, plans)["estimated_wall_h"] < 3


@pytest.mark.parametrize("mutate", [
    lambda phase: phase.pop("retention_limit"),
    lambda phase: phase.update(lifecycle_variants=["pinned", "pinned"]),
    lambda phase: phase.update(lifecycle_variants=["indexed"]),
    lambda phase: phase.update(spike_instrumentation="yes"),
    lambda phase: phase.update(modes=["direct"]),
])
def test_invalid_variant_phase_is_rejected(mutate):
    config = copy.deepcopy(PLAN)
    mutate(config["phases"]["pilot"])
    with pytest.raises(ValueError):
        ol.trial_specs(config, "pilot")


def test_operating_limits_specs_are_unchanged_by_the_extension():
    plan = json.loads((ROOT / "docs/experiments/operating-limits/plan.json").read_text())
    for specs in ol.campaign_specs(plan).values():
        assert all("lifecycle_variant" not in spec and "spike_instrumentation" not in spec for spec in specs)
    assert ol.phase_order(plan) == ol.ALL_PHASES


def test_summarize_requests_preserves_variant_and_gc_freeze_counters(tmp_path):
    rows = synthetic_rows(count=2, instrumented=False)
    for row in rows:
        row.update(success=True, deadline_missed=False)
    workload = write_workload(tmp_path / "trial/workload", rows)
    (workload / "summary.json").write_text(json.dumps({
        "mode": "orchestrated", "completed_requests": 2, "successful_requests": 2,
        "skipped_releases": 0, "deadline_misses": 0, "completed_duration": True,
        "achieved_requests_per_s": 10.0, "lifecycle_variant": "indexed", "retention_limit": None,
        "gc_policy": "freeze", "lifecycle_variant_final_counts": {"history": 15, "evicted": 0},
        "gc_policy_summary": {"runs": 1, "total_ms": 0.8, "max_ms": 0.8, "frozen_objects_at_end": 500},
    }))
    summary = ol.summarize_requests(workload.parent)
    assert (summary["lifecycle_variant"], summary["gc_policy"]) == ("indexed", "freeze")
    assert summary["lifecycle_variant_final_counts"]["history"] == 15
    assert summary["gc_policy_summary"]["frozen_objects_at_end"] == 500


def _manifested(folder: Path, summary: dict, files: dict) -> None:
    (folder / "workload").mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text)
    (folder / "reproduction-manifest.json").write_text(json.dumps({**{name: "x" for name in files}, "workload/summary.json": "x"}))


@pytest.mark.parametrize(("summary", "files", "expected"), [
    ({"spike_recorder": {"dropped": 0, "callback_errors": 0, "marks": 3}, "lifecycle_variant_install": {"variant": "bounded", "retention_limit": 60, "installed_class": "BoundedLifecycleStore"}}, {"workload/gc-events.jsonl": ""}, []),
    ({"spike_recorder": None, "lifecycle_variant_install": {"variant": "bounded", "retention_limit": 60, "installed_class": "B"}}, {}, ["spike_instrumentation_missing"]),
    ({"spike_recorder": {"dropped": 1, "callback_errors": 0, "marks": 3}, "lifecycle_variant_install": {"variant": "bounded", "retention_limit": 60, "installed_class": "B"}}, {"workload/gc-events.jsonl": ""}, ["spike_instrumentation_incomplete"]),
    ({"spike_recorder": {"dropped": 0, "callback_errors": 0, "marks": 3}, "lifecycle_variant_install": {"variant": "pinned", "retention_limit": None}}, {"workload/gc-events.jsonl": ""}, ["lifecycle_variant_not_installed"]),
])
def test_missing_or_incomplete_instrumentation_is_an_infrastructure_failure(tmp_path, summary, files, expected):
    spec = {"intrusive_profile": "none", "diagnostic_counts": False, "lifecycle_variant": "bounded", "retention_limit": 60, "spike_instrumentation": True}
    _manifested(tmp_path, summary, files)
    assert ol.diagnostic_failures(tmp_path, spec, {"completed_requests": 3, **summary}) == expected


def synthetic_rows(count=900, spikes=None, instrumented=True):
    spikes = spikes or {}
    rows = []
    for index in range(count):
        cycle = 10.0 + index * 0.001
        gc_ms, off = 0.0, 2.0
        kind = spikes.get(index)
        if kind == "gc":
            cycle, gc_ms = cycle + 90, 90.0
        elif kind == "off_cpu":
            cycle, off = cycle + 40, off + 40
        started = index * 100_000_000
        row = {
            "slot": index, "cycle_ms": cycle, "invocation_ms": cycle - 0.05, "deadline_missed": cycle > 100, "dispatch_lateness_ms": 0.1,
            "skipped_before": 0, "started_ns": started, "cycle_finished_ns": started + int(cycle * 1e6),
        }
        if instrumented:
            voluntary = 90 if kind == "off_cpu" else 16
            row.update(gc_ms=gc_ms, off_cpu_ms=off, process_cpu_ms=cycle - off, gc_collections=int(gc_ms > 0), gc_max_generation=2 if gc_ms else "", involuntary_switches=0, voluntary_switches=voluntary, major_faults=0)
        rows.append(row)
    return rows


def write_workload(folder: Path, rows: list[dict], events=None) -> Path:
    folder.mkdir(parents=True)
    with (folder / "requests.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if events is not None:
        (folder / "gc-events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    return folder


def test_spike_attribution_names_gc_and_off_cpu_causes_and_locates_full_collections(tmp_path):
    rows = synthetic_rows(count=1300, spikes={100: "gc", 500: "off_cpu"})
    events = [
        {"start_ns": rows[100]["started_ns"] + 1_000_000, "end_ns": 0, "duration_ns": 90_000_000, "generation": 2, "collected": 0, "uncollectable": 0, "main_thread": False},
        {"start_ns": rows[300]["started_ns"] - 5, "end_ns": 0, "duration_ns": 1_000_000, "generation": 1, "collected": 0, "uncollectable": 0, "main_thread": True},
        {"start_ns": 5, "end_ns": 6, "duration_ns": 1, "generation": 0, "collected": 0, "uncollectable": 0, "main_thread": True},
    ]
    report = spike_analysis.analyze_workload(write_workload(tmp_path / "workload", rows, events), 10)
    assert report["instrumented"] and report["spikes"] == 2 and report["causes"] == {"gc": 1, "off_cpu": 1}
    assert report["off_cpu_subcauses"] == {"voluntary_wait": 1}
    assert report["explained_fraction"] == 1.0 and report["spikes_with_full_collection"] == 1
    assert report["largest"][0]["index"] == 100 and report["largest"][0]["deadline_missed"] is True
    full = report["gc"]["full_collections"]
    assert len(full) == 1 and full[0]["request_index"] == 100 and full[0]["duration_ms"] == 90
    assert report["gc"]["collections_by_generation"] == {"0": 1, "1": 1, "2": 1}
    assert report["invocation_trend"]["slope_ms_per_1k_requests"] == pytest.approx(1.0, rel=0.01)


def test_off_cpu_spike_under_platform_load_is_cpu_contention(tmp_path):
    rows = synthetic_rows(count=900, spikes={400: "off_cpu"})
    rows[400]["voluntary_switches"] = 16
    workload = write_workload(tmp_path / "trial/workload", rows)
    started = rows[400]["started_ns"]
    (tmp_path / "trial/tegrastats.jsonl").write_text(json.dumps({"monotonic_ns": started + 100_000_000, "cpu_mean_percent": 91.5}) + "\n" + json.dumps({"monotonic_ns": 5, "cpu_mean_percent": 3}) + "\n")
    report = spike_analysis.analyze_workload(workload, 10)
    assert report["off_cpu_subcauses"] == {"cpu_contention": 1}
    assert report["largest"][0]["platform_cpu_mean_percent"] == 91.5


def test_gc_freeze_policy_freezes_periodically_outside_the_cycle():
    policy = worker.GcFreezePolicy(every=3)
    try:
        for completed in range(1, 8):
            policy.after_request(completed)
        summary = policy.summary()
        assert policy.runs == 2 and summary["frozen_objects_at_end"] > 0 and summary["max_ms"] >= 0
    finally:
        gc.unfreeze()


def test_gc_policy_is_validated_and_round_trips_through_commands(tmp_path):
    with pytest.raises(ValueError):
        worker.validate_options(5, 10, "direct", None, 0, gc_policy="freeze")
    with pytest.raises(ValueError):
        worker.validate_options(5, 10, "orchestrated", None, 0, gc_policy="off")
    command = stage_command("sustained", tmp_path, upstream=tmp_path, lifecycle_variant="indexed", gc_policy="freeze")
    assert ol._command_settings(command)["gc_policy"] == "freeze"
    assert ol._command_settings(stage_command("sustained", tmp_path, upstream=tmp_path))["gc_policy"] == "default"


def test_uninstrumented_spikes_are_reported_as_unattributed(tmp_path):
    rows = synthetic_rows(spikes={100: "gc"}, instrumented=False)
    report = spike_analysis.analyze_workload(write_workload(tmp_path / "workload", rows), 10)
    assert not report["instrumented"] and report["causes"] == {"unattributed_no_instrumentation": 1}
    assert report["explained_fraction"] is None and report["gc"] is None


def test_carried_delays_are_attributed_to_an_over_period_predecessor():
    rows = [{"cycle_ms": 150.0, "dispatch_lateness_ms": 0.1, "skipped_before": 0}, {"cycle_ms": 10.0, "dispatch_lateness_ms": 50.0, "skipped_before": 1}, {"cycle_ms": 10.0, "dispatch_lateness_ms": 2.0, "skipped_before": 0}]
    assert spike_analysis.carried_delays(rows, 100) == {
        "dispatches_late_by_1ms_or_more": 2, "of_which_after_over_period_predecessor": 1,
        "skipped_releases_in_rows": 1, "of_which_after_over_period_predecessor_skips": 1,
    }


def test_block_medians_merge_a_short_tail():
    values = [1.0] * 300 + [5.0] * 300 + [9.0] * 100
    medians = spike_analysis.block_medians(values)
    assert len(medians) == len(values) and medians[0] == 1.0 and medians[-1] == 5.0
