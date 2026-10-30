"""Mocked tests for the operating-limits campaign runner; no device experiments are executed."""

import copy
import csv
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import local_control_experiment as lce
from evaluation import operating_limits as ol
from evaluation.reproduce_original import ROOT, digest, stage_command

PLAN = json.loads((ROOT / "docs/experiments/operating-limits/plan.json").read_text())
COLUMNS = ["slot", "skipped_before", "success", "deadline_missed", "dispatch_lateness_ms", "invocation_ms", "cycle_ms", "prepare_ms"]
PILOT_IDS = ["pilot-r01-harness-10hz", "pilot-r01-direct-10hz", "pilot-r01-orchestrated-10hz"]


def good_rows(count=3):
    return [
        {"slot": index, "skipped_before": 0, "success": True, "deadline_missed": False, "dispatch_lateness_ms": 0.0, "invocation_ms": 1.0 + index, "cycle_ms": 2.0 + index, "prepare_ms": ""}
        for index in range(count)
    ]


def worker_summary(rows, *, mode="harness", rate_hz=10, duration_s=2, idle_s=0, diagnostic_counts=True, intrusive_profile="none", post_idle_s=0):
    completed = len(rows)
    return {
        "schema_version": 3, "mode": mode, "interruption": None, "fatal_error": None, "diagnostic_counts": diagnostic_counts,
        "intrusive_profile": intrusive_profile, "post_idle_s": post_idle_s, "idle_s": idle_s,
        "completed_duration": True, "duration_s": duration_s, "rate_hz": rate_hz,
        "planned_releases": completed, "arrived_releases": completed, "unreleased_slots": 0,
        "completed_requests": completed, "successful_requests": sum(row["success"] for row in rows),
        "skipped_releases": 0, "missed_releases": 0, "deadline_misses": sum(row["deadline_missed"] for row in rows),
        "late_dispatches": sum(row["dispatch_lateness_ms"] > 0 for row in rows),
        "maintenance_failures": 0, "unexpected_backends": 0, "idempotent_replays": 0, "achieved_requests_per_s": rate_hz,
    }


SETTINGS = ("mode", "rate_hz", "duration_s", "idle_s", "diagnostic_counts", "intrusive_profile", "post_idle_s")


def write_workload(folder, rows, **overrides):
    workload = folder / "workload"
    workload.mkdir(parents=True)
    with (workload / "requests.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    summary = worker_summary(rows, **{key: overrides.pop(key) for key in SETTINGS if key in overrides})
    summary.update(overrides)
    (workload / "summary.json").write_text(json.dumps(summary))
    return summary


def worker_exit_code(summary):
    """Mirror upstream_sustained.main's exit status."""
    defect = not summary["completed_duration"] or not summary["completed_requests"] or summary["successful_requests"] != summary["completed_requests"]
    return 2 if defect or summary["maintenance_failures"] or summary["unexpected_backends"] or summary["idempotent_replays"] else 0


def diagnostics_metadata(kind, completed):
    metadata = {"schema_version": 1, "kind": kind, "intrusive": True, "errors": [], "completed_requests": completed, "instrumentation": {"total_ns": 5}, "gc": {"events_written": 2, "dropped": 0, "callback_errors": 0}}
    if kind == "allocation":
        metadata["allocation"] = {"checkpoints": [{"label": "start", "traced_current_bytes": 10}, {"label": "60s"}, {"label": "end", "total_size_diff_bytes": 128}]}
        metadata["files"] = ["allocation-end.json", "allocation-start.json", "diagnostics.json"]
    else:
        metadata["calls"] = {"windows": [{"label": "early"}, {"label": "late"}], "late_window_reached": True}
        metadata["files"] = ["calls-early.json", "calls-late.json", "diagnostics.json"]
    return metadata


def resource_summary(steady_w, coverage=1.0):
    def window(power_w, window_coverage=1.0):
        return {"duration_s": 1.0, "power": {"platform_mean_power_w": power_w, "coverage_fraction": window_coverage, "platform_energy_j_over_observed_interval": power_w}, "power_gaps_s": [], "max_temperature_c": 41.0, "observed_tree_rss_sum_max_mib": 95.0}

    return {"windows": {"platform_before": window(4.5), "ready_worker_idle": window(4.6), "steady_workload": window(steady_w, coverage), "post_worker_idle": window(4.8), "platform_after": window(4.5)}}


def seal(folder):
    manifest_path = folder / "reproduction-manifest.json"
    manifest = {str(path.relative_to(folder)): digest(path) for path in sorted(folder.rglob("*")) if path.is_file() and path != manifest_path}
    manifest_path.write_text(json.dumps(manifest))


class FakeRunner:
    """Stands in for run_stage and writes a sealed archive shaped like a real sustained trial."""

    def __init__(self, behaviours=None, power=None):
        self.behaviours = behaviours or {}
        self.power = power or {"harness": 4.7, "direct": 5.0, "orchestrated": 5.5}
        self.calls = []

    def __call__(self, upstream, folder, stage, timeout_s, *, profile, interval_ms, baseline_s, duration_s, rate_hz, mode, idle_s, diagnostic_counts, intrusive_profile, post_idle_s):
        assert stage == "sustained" and profile and timeout_s > duration_s + idle_s + post_idle_s
        self.calls.append(folder.name)
        behaviour = self.behaviours.get(len(self.calls), {})
        folder.mkdir(parents=True)
        if "raise" in behaviour:
            (folder / "stdout.log").write_text("partial output\n")
            raise behaviour["raise"]
        command = stage_command("sustained", folder, upstream=upstream, duration_s=duration_s, rate_hz=rate_hz, mode=mode, idle_s=idle_s, diagnostic_counts=diagnostic_counts, intrusive_profile=intrusive_profile, post_idle_s=post_idle_s)
        (folder / "provenance.json").write_text(json.dumps({"command": command}))
        (folder / "profiling.json").write_text(json.dumps({"interval_ms": interval_ms, "baseline_s": baseline_s}))
        settings = {"mode": mode, "rate_hz": rate_hz, "duration_s": duration_s, "idle_s": idle_s, "diagnostic_counts": diagnostic_counts, "intrusive_profile": intrusive_profile, "post_idle_s": post_idle_s}
        summary = write_workload(folder, behaviour.get("rows", good_rows()), **settings, **behaviour.get("summary", {}))
        if diagnostic_counts and intrusive_profile == "none" and not behaviour.get("no_counts"):
            samples = [{"completed_requests": 0, "collections": {"lifecycle.history": 0}}, {"completed_requests": summary["completed_requests"], "collections": {"lifecycle.history": 9}}]
            (folder / "workload/retained-state.jsonl").write_text("".join(json.dumps(sample) + "\n" for sample in samples))
        if intrusive_profile != "none":
            (folder / "workload/diagnostics").mkdir()
            metadata = {**diagnostics_metadata(intrusive_profile, summary["completed_requests"]), **behaviour.get("diagnostics", {})}
            (folder / "workload/diagnostics/diagnostics.json").write_text(json.dumps(metadata))
            for name in metadata["files"]:
                if name != "diagnostics.json":
                    (folder / "workload/diagnostics" / name).write_text("{}")
        (folder / "resource-summary.json").write_text(json.dumps(resource_summary(self.power[mode])))
        result = {"stage": "sustained", "returncode": worker_exit_code(summary), "timed_out": False, "guard_reason": None, "upstream_sources_unchanged": True, "profile_complete": True, "parent_error": None, **behaviour.get("result", {})}
        (folder / "result.json").write_text(json.dumps(result))
        seal(folder)
        return result


class FakeLocalRunner:
    """Stands in for run_local_trial with the archive layout that verify_local_trial checks."""

    def __init__(self, behaviours=None):
        self.behaviours = behaviours or {}
        self.calls = []

    def __call__(self, spec, folder, upstream):
        self.calls.append(folder.name)
        behaviour = self.behaviours.get(len(self.calls), {})
        folder = folder.resolve()
        python = ol.sys.executable
        command = ol.local_command(spec, folder, upstream.resolve(), python)
        folder.mkdir(parents=True)
        (folder / "provenance.json").write_text(json.dumps({"trial": spec, "command": command, "python_executable": python, "upstream": str(upstream.resolve())}))
        controller = folder / "controller"
        controller.mkdir()
        (controller / "config.json").write_text(json.dumps({"trial_id": spec["trial_id"], "mode": spec["mode"], "partition": spec["partition"], "fault": spec["fault"], "duration_s": spec["duration_s"], "rate_hz": spec["rate_hz"], "backend": spec["backend"], "supervisor_python": python}))
        failed = behaviour.get("fail", [])
        assertions = [{"name": "job_terminal_completed", "passed": True}, *({"name": name, "passed": False} for name in failed)]
        passed = not failed
        summary = {"trial_id": spec["trial_id"], "mode": spec["mode"], "scenario": spec["scenario"], "backend": {"counters": {"native_idempotent_replays": 0}, "supervisor_peak_rss_kib": 50_000}, "status": "passed" if passed else "failed", "passed": passed, "assertions": assertions, "steps": {"planned": 900, "executed": 900 - len(failed), "succeeded": 900 - len(failed), "missed": len(failed), "fallback_steps": [450] if spec["fault"] else []}, "timeline": {"fault_consumed_phase": "during" if spec["fault"] else None}}
        if "error" in behaviour:
            summary.update(status="error", passed=False, assertions=[], backend=spec["backend"], error={"type": behaviour["error"], "message": "boom"})
        (controller / "summary.json").write_text(json.dumps(summary))
        returncode = behaviour.get("returncode", {"passed": 0, "failed": 1}.get(summary["status"], 2))
        result = {"stage": "local-control", "returncode": returncode, "timed_out": False, "upstream_sources_unchanged": True, "parent_error": None, "process_cleanup": {"group_remaining": False, "owned_stopped": [], "owned_remaining": []}}
        (folder / "result.json").write_text(json.dumps(result))
        seal(folder)
        return result


@pytest.fixture
def campaign(monkeypatch, tmp_path):
    sleeps, preflights = [], []
    monkeypatch.setattr(ol, "sys", SimpleNamespace(version_info=(3, 12, 0), version="3.12.0 (test)", executable=sys.executable, stderr=sys.stderr))
    monkeypatch.setattr(ol, "time", SimpleNamespace(sleep=sleeps.append, monotonic_ns=time.monotonic_ns))
    monkeypatch.setattr(ol, "shutil", SimpleNamespace(which=lambda name: f"/usr/bin/{name}", disk_usage=lambda path: SimpleNamespace(free=1 << 40)))
    monkeypatch.setattr(ol, "LOCK_PATH", tmp_path / "measurement.lock")
    monkeypatch.setattr(ol, "intrusive_preflight", lambda upstream, profile: preflights.append(profile) or {"profile": profile, "returncode": 0, "failures": []})
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    state = SimpleNamespace(config=copy.deepcopy(PLAN), output=tmp_path / "campaign", upstream=upstream, sleeps=sleeps, preflights=preflights)

    def run(runner, *, resume=False, config=None, phase="pilot", local=None):
        monkeypatch.setattr(ol, "run_stage", runner)
        monkeypatch.setattr(ol, "run_local_trial", local or FakeLocalRunner())
        return ol.run_phase(config or state.config, phase, state.output, upstream, resume=resume)

    def run_all(runner, local, *, resume=False, config=None):
        monkeypatch.setattr(ol, "run_stage", runner)
        monkeypatch.setattr(ol, "run_local_trial", local)
        return ol.run_all(config or state.config, state.output, upstream, resume=resume)

    def interrupt_next_sleep():
        def sleep(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(ol, "time", SimpleNamespace(sleep=sleep, monotonic_ns=time.monotonic_ns))

    def restore_sleep():
        monkeypatch.setattr(ol, "time", SimpleNamespace(sleep=sleeps.append, monotonic_ns=time.monotonic_ns))

    state.run, state.run_all, state.interrupt_next_sleep, state.restore_sleep = run, run_all, interrupt_next_sleep, restore_sleep
    return state


def events(output):
    return [event["event"] for event in ol.read_ledger(output)]


def interrupted_after_first_trial(campaign):
    runner = FakeRunner()
    campaign.interrupt_next_sleep()
    with pytest.raises(KeyboardInterrupt):
        campaign.run(runner)
    campaign.restore_sleep()
    return runner


def test_planned_matrix_is_stable():
    assert [row["trial_id"] for row in ol.trial_specs(PLAN, "pilot")] == PILOT_IDS
    attribution = ol.trial_specs(PLAN, "attribution")
    assert [row["mode"] for row in attribution[3:6]] == ["direct", "orchestrated", "harness"]
    sweep = ol.trial_specs(PLAN, "sweep")
    assert len({row["trial_id"] for row in sweep}) == len(sweep) == 12
    plans = ol.campaign_specs(PLAN)
    assert list(plans) == list(ol.ALL_PHASES)
    assert {phase: len(specs) for phase, specs in plans.items()} == {"pilot": 3, "attribution": 9, "allocation-profiling": 2, "call-profiling": 1, "long-baseline": 3, "sweep": 12, "local-control": 24, "paired-power": 10}
    assert [(row["mode"], row["intrusive_profile"], row["duration_s"]) for row in plans["allocation-profiling"] + plans["call-profiling"]] == [("direct", "allocation", 300), ("orchestrated", "allocation", 300), ("orchestrated", "calls", 300)]
    assert all(row["duration_s"] == 1800 and row["rate_hz"] == 10 for row in plans["long-baseline"])
    local = plans["local-control"]
    assert [row["trial_id"] for row in local] == [trial.trial_id for trial in lce.build_matrix(3, seed=20261029)]
    assert all(row["backend"] == "pinned" and row["timeout_s"] == 210 for row in local)
    power = plans["paired-power"]
    assert [row["mode"] for row in power[:4]] == ["direct", "orchestrated", "orchestrated", "direct"]
    assert [row["baseline_s"] for row in power[:2]] == [60, 3]
    assert all((row["duration_s"], row["idle_s"], row["post_idle_s"], row["rate_hz"]) == (120, 60, 60, 10) for row in power)
    assert 5.5 < ol.estimate_runtime(PLAN, plans)["estimated_wall_h"] < 7


def _set(key, value):
    return lambda config, phase: config.__setitem__(key, value)


def _phase(key, value):
    return lambda config, phase: phase.__setitem__(key, value)


@pytest.mark.parametrize("mutate", [
    _set("cooldown_s", float("nan")), _set("cooldown_s", 0), _set("cooldown_s", "60"),
    _set("interval_ms", 250.0), _set("interval_ms", True), _set("interval_ms", 0),
    _set("platform_idle_s", -1), _set("platform_idle_s", None),
    _phase("diagnostic_counts", "yes"), _phase("diagnostic_counts", 1),
    _phase("duration_s", float("inf")), _phase("duration_s", 0), _phase("idle_s", -1),
    _phase("rates_hz", [True]), _phase("rates_hz", [1001]), _phase("rates_hz", ["10"]), _phase("rates_hz", []), _phase("rates_hz", [10, 10.0]),
    _phase("modes", ["fast"]), _phase("modes", "harness"), _phase("modes", ["direct", "direct"]),
    _phase("repetitions", True), _phase("repetitions", 0), _phase("implemented", "yes"), _phase("diagnostics_count", True),
    lambda config, phase: phase.pop("idle_s"),
])
def test_invalid_phase_is_rejected_before_outputs(tmp_path, mutate):
    config = copy.deepcopy(PLAN)
    mutate(config, config["phases"]["pilot"])
    with pytest.raises(ValueError):
        ol.trial_specs(config, "pilot")
    with pytest.raises(ValueError):
        ol.run_phase(config, "pilot", tmp_path / "campaign", tmp_path)
    assert not (tmp_path / "campaign").exists()


def test_successful_campaign_is_ledgered_with_cooldowns(campaign):
    runner = FakeRunner()
    records = campaign.run(runner)
    assert [row["trial_id"] for row in records] == runner.calls == PILOT_IDS
    assert all(not row["infrastructure_failures"] and not row["performance_failures"] for row in records)
    assert events(campaign.output) == ["campaign_started"] + ["started", "finished"] * 3
    assert campaign.sleeps == [60, 60]
    assert json.loads((campaign.output / "frozen.json").read_text())["phase"] == "pilot"


def test_performance_failures_are_retained_separately_and_campaign_continues(campaign):
    rows = good_rows()
    rows[1].update(success=False, deadline_missed=True)
    records = campaign.run(FakeRunner({2: {"rows": rows}}))
    assert len(records) == 3
    assert records[1]["infrastructure_failures"] == []
    assert records[1]["performance_failures"] == ["unsuccessful_requests:1", "deadline_misses:1"]
    analysis = ol.analyze_phase(campaign.output)
    assert analysis["status_counts"] == {"success": 2, "performance_failure": 1}
    workload = analysis["trials"][PILOT_IDS[1]]["workload"]
    assert workload["failed_requests"] == 1 and workload["unsuccessful"] is True


def test_guard_abort_stops_campaign_and_blocks_resume(campaign):
    runner = FakeRunner({1: {"result": {"guard_reason": "temperature_limit", "returncode": -15}, "summary": {"completed_duration": False}}})
    records = campaign.run(runner)
    assert len(records) == 1 and "guard:temperature_limit" in records[0]["infrastructure_failures"]
    assert events(campaign.output)[-1] == "campaign_stopped"
    with pytest.raises(RuntimeError, match="stopped"):
        campaign.run(runner, resume=True)
    assert runner.calls == PILOT_IDS[:1]


def test_runner_exception_is_ledgered_and_partial_trial_is_kept_not_redone(campaign):
    runner = FakeRunner({2: {"raise": RuntimeError("tegrastats vanished")}})
    with pytest.raises(RuntimeError, match="tegrastats"):
        campaign.run(runner)
    last = ol.read_ledger(campaign.output)[-1]
    assert (last["event"], last["trial_id"], last["error_type"]) == ("exception", PILOT_IDS[1], "RuntimeError")
    assert (campaign.output / PILOT_IDS[1] / "stdout.log").exists()
    with pytest.raises(RuntimeError, match="must not be resumed"):
        campaign.run(runner, resume=True)
    assert runner.calls == PILOT_IDS[:2]
    trials = ol.analyze_phase(campaign.output)["trials"]
    assert trials[PILOT_IDS[0]]["status"] == "success"
    assert trials[PILOT_IDS[1]]["status"] == "partial" and trials[PILOT_IDS[1]]["retained_files"] == ["stdout.log"]
    assert trials[PILOT_IDS[2]]["status"] == "not_run"


def test_interrupt_inside_trial_is_recorded(campaign):
    with pytest.raises(KeyboardInterrupt):
        campaign.run(FakeRunner({1: {"raise": KeyboardInterrupt()}}))
    last = ol.read_ledger(campaign.output)[-1]
    assert (last["event"], last["trial_id"]) == ("interrupted", PILOT_IDS[0])


def test_interrupt_between_trials_resumes_only_remaining_trials(campaign):
    runner = interrupted_after_first_trial(campaign)
    last = ol.read_ledger(campaign.output)[-1]
    assert (last["event"], last["trial_id"]) == ("interrupted", None)
    records = campaign.run(runner, resume=True)
    assert [row["trial_id"] for row in records] == runner.calls == PILOT_IDS
    assert events(campaign.output).count("started") == 3


def test_resume_requires_identical_frozen_configuration_and_sources(campaign, monkeypatch):
    runner = interrupted_after_first_trial(campaign)
    changed = copy.deepcopy(campaign.config)
    changed["cooldown_s"] = 90
    with pytest.raises(ValueError, match="changed"):
        campaign.run(runner, resume=True, config=changed)
    monkeypatch.setattr(ol, "source_fingerprint", dict)
    with pytest.raises(ValueError, match="changed"):
        campaign.run(runner, resume=True)
    assert runner.calls == PILOT_IDS[:1]


def test_resume_without_frozen_configuration_fails(campaign):
    campaign.output.mkdir()
    with pytest.raises(ValueError, match="frozen"):
        campaign.run(FakeRunner(), resume=True)


def _tamper_bytes(folder):
    with (folder / "workload/requests.csv").open("a") as handle:
        handle.write("extra\n")


def _relabel_mode(folder):
    path = folder / "workload/summary.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "mode": "direct"}))
    seal(folder)


def _relabel_rate(folder):
    path = folder / "provenance.json"
    command = json.loads(path.read_text())["command"]
    command[command.index("--rate-hz") + 1] = "25"
    path.write_text(json.dumps({"command": command}))
    seal(folder)


@pytest.mark.parametrize("tamper, message", [(_tamper_bytes, "checksum"), (_relabel_mode, "does not match"), (_relabel_rate, "does not match")])
def test_resume_verifies_archive_and_recorded_trial(campaign, tamper, message):
    runner = interrupted_after_first_trial(campaign)
    tamper(campaign.output / PILOT_IDS[0])
    with pytest.raises(ValueError, match=message):
        campaign.run(runner, resume=True)
    assert runner.calls == PILOT_IDS[:1]


@pytest.mark.parametrize("name, message", [(PILOT_IDS[1], "without ledger record"), ("stray", "Unexpected folders")])
def test_resume_rejects_unaccounted_folders(campaign, name, message):
    runner = interrupted_after_first_trial(campaign)
    (campaign.output / name).mkdir()
    with pytest.raises(RuntimeError, match=message):
        campaign.run(runner, resume=True)


def test_classification_treats_missing_or_interrupted_workload_as_infrastructure():
    clean = {"stage": "sustained", "returncode": 0, "timed_out": False, "guard_reason": None, "upstream_sources_unchanged": True, "profile_complete": True}
    assert ol.classify_trial(clean, None)["infrastructure_failures"] == ["workload_summary_missing"]
    summary = {**worker_summary(good_rows()), "interruption": "SystemExit", "completed_duration": False}
    assert ol.classify_trial(clean, summary)["infrastructure_failures"] == ["worker_interrupted:SystemExit", "duration_not_completed"]
    del summary["deadline_misses"]
    assert "summary_counter_invalid:deadline_misses" in ol.classify_trial(clean, summary)["infrastructure_failures"]


def test_summary_excludes_empty_and_unsupported_timings_and_reports_failures(tmp_path):
    rows = good_rows()
    rows[2]["success"] = False
    rows[2]["invocation_ms"] = ""
    write_workload(tmp_path, rows)
    result = ol.summarize_requests(tmp_path)
    assert (result["failed_requests"], result["success_fraction"], result["unsuccessful"]) == (1, 2 / 3, True)
    assert result["metrics"]["invocation_ms"] == {"supported": True, "samples": 2, "missing": 1, "mean": 1.5, "median": 1.5, "p95": None, "max": 2.0}
    assert result["metrics"]["prepare_ms"]["supported"] is False and result["metrics"]["prepare_ms"]["mean"] is None
    assert result["metrics"]["backend_invoke_ms"]["supported"] is False and result["metrics"]["backend_invoke_ms"]["samples"] == 0


def test_summary_of_zero_requests_is_unsuccessful_not_zero_latency(tmp_path):
    write_workload(tmp_path, [], completed_duration=True)
    result = ol.summarize_requests(tmp_path)
    assert result["unsuccessful"] is True and result["success_fraction"] is None
    assert result["metrics"]["cycle_ms"]["mean"] is None


@pytest.mark.parametrize("overrides, match", [
    ({"successful_requests": 2}, "Success count"),
    ({"deadline_misses": 1}, "Deadline-miss"),
    ({"late_dispatches": 1}, "Late-dispatch"),
    ({"arrived_releases": 4, "planned_releases": 4}, "Release accounting"),
    ({"missed_releases": 2}, "missed_releases"),
    ({"skipped_releases": "0"}, "skipped_releases"),
])
def test_summary_counters_must_reconcile_with_raw_rows(tmp_path, overrides, match):
    write_workload(tmp_path, good_rows(), **overrides)
    with pytest.raises(ValueError, match=match):
        ol.summarize_requests(tmp_path)


def test_summary_rejects_ambiguous_boolean(tmp_path):
    write_workload(tmp_path, good_rows())
    path = tmp_path / "workload/requests.csv"
    path.write_text(path.read_text().replace("False", "no", 1))
    with pytest.raises(ValueError, match="Invalid boolean"):
        ol.summarize_requests(tmp_path)


def small_config():
    config = copy.deepcopy(PLAN)
    phases = config["phases"]
    phases["attribution"]["repetitions"] = 1
    phases["long-baseline"]["repetitions"] = 1
    phases["sweep"].update(rates_hz=[1, 50], repetitions=1)
    phases["local-control"]["repetitions"] = 1
    phases["paired-power"]["pairs"] = 2
    return config


def _phase_of(name, key, value):
    return lambda config: config["phases"][name].__setitem__(key, value)


@pytest.mark.parametrize("phase, mutate", [
    ("pilot", lambda config: config.__setitem__("extra", 1)),
    ("pilot", lambda config: config.__setitem__("schema_version", 3)),
    ("pilot", lambda config: config.__setitem__("schema_version", 1)),
    ("pilot", lambda config: config.__setitem__("cooldown_s", 30)),
    ("pilot", lambda config: config.__setitem__("desktop_comparison", "no")),
    ("pilot", lambda config: config.__setitem__("ambient_temperature_c", "warm")),
    ("pilot", _phase_of("pilot", "kind", "burst")),
    ("pilot", lambda config: config["phases"]["pilot"].pop("kind")),
    ("allocation-profiling", _phase_of("allocation-profiling", "intrusive_profile", "everything")),
    ("allocation-profiling", _phase_of("allocation-profiling", "diagnostic_counts", True)),
    ("attribution", _phase_of("attribution", "post_idle_s", -1)),
    ("local-control", _phase_of("local-control", "backend", "fake")),
    ("local-control", _phase_of("local-control", "duration_s", 60)),
    ("local-control", _phase_of("local-control", "seed", True)),
    ("local-control", _phase_of("local-control", "timeout_margin_s", 0)),
    ("local-control", _phase_of("local-control", "rates_hz", [10])),
    ("paired-power", _phase_of("paired-power", "modes", ["direct", "direct"])),
    ("paired-power", _phase_of("paired-power", "pairs", 0)),
    ("paired-power", _phase_of("paired-power", "platform_baseline_s", 0)),
    ("paired-power", _phase_of("paired-power", "diagnostic_counts", "false")),
    ("paired-power", _phase_of("paired-power", "rate_hz", 2000)),
])
def test_strict_configuration_is_rejected_before_any_output(campaign, phase, mutate):
    config = copy.deepcopy(PLAN)
    mutate(config)
    with pytest.raises(ValueError):
        ol.trial_specs(config, phase)
    with pytest.raises(ValueError):
        campaign.run_all(FakeRunner(), FakeLocalRunner(), config=config)
    assert not campaign.output.exists()


def test_schema_one_configuration_remains_readable():
    legacy = copy.deepcopy(PLAN)
    legacy["schema_version"] = 1
    legacy["phases"] = {"pilot": {key: value for key, value in PLAN["phases"]["pilot"].items() if key != "kind"}}
    assert [row["trial_id"] for row in ol.trial_specs(legacy, "pilot")] == PILOT_IDS


CLEAN = {"stage": "sustained", "returncode": 0, "timed_out": False, "guard_reason": None, "upstream_sources_unchanged": True, "profile_complete": True}


@pytest.mark.parametrize("overrides, returncode, infrastructure, performance", [
    ({"successful_requests": 2}, 2, [], ["unsuccessful_requests:1"]),
    ({"maintenance_failures": 4}, 2, [], ["maintenance_failures:4"]),
    ({"unexpected_backends": 1, "deadline_misses": 3}, 2, [], ["deadline_misses:3", "unexpected_backends:1"]),
    ({"idempotent_replays": 2}, 2, [], ["idempotent_replays:2"]),
    ({"deadline_misses": 5, "skipped_releases": 7}, 0, [], ["skipped_releases:7", "deadline_misses:5"]),
    ({}, 2, ["returncode:2"], []),
    ({"deadline_misses": 1}, 2, ["returncode:2"], ["deadline_misses:1"]),
    ({"successful_requests": 2}, 0, ["returncode_inconsistent:0"], ["unsuccessful_requests:1"]),
    ({"successful_requests": 2}, 1, ["returncode:1"], ["unsuccessful_requests:1"]),
    ({"successful_requests": 2}, -15, ["returncode:-15"], ["unsuccessful_requests:1"]),
    ({"successful_requests": 2, "fatal_error": {"type": "RuntimeError"}}, 2, ["worker_fatal_error:RuntimeError", "returncode:2"], ["unsuccessful_requests:1"]),
    ({"successful_requests": 2, "completed_duration": False}, 2, ["duration_not_completed", "returncode:2"], ["unsuccessful_requests:1"]),
    ({"successful_requests": 2, "interruption": "SIGTERM"}, 143, ["worker_interrupted:SIGTERM", "returncode:143"], ["unsuccessful_requests:1"]),
])
def test_exit_status_two_is_a_measured_result_only_for_intact_completed_runs(overrides, returncode, infrastructure, performance):
    summary = {**worker_summary(good_rows()), **overrides}
    assert ol.classify_trial({**CLEAN, "returncode": returncode}, summary) == {"infrastructure_failures": infrastructure, "performance_failures": performance}


def test_exit_status_two_with_incomplete_profile_is_infrastructure():
    summary = {**worker_summary(good_rows()), "successful_requests": 2}
    assert ol.classify_trial({**CLEAN, "returncode": 2, "profile_complete": False}, summary)["infrastructure_failures"] == ["profile_incomplete", "returncode:2"]


@pytest.mark.parametrize("phase, behaviour, reason", [
    ("allocation-profiling", {"diagnostics": {"errors": ["allocation: OSError: disk full"]}}, "diagnostics_errors:1"),
    ("allocation-profiling", {"diagnostics": {"kind": "calls"}}, "diagnostics_kind_mismatch"),
    ("allocation-profiling", {"diagnostics": {"allocation": {"checkpoints": [{"label": "start"}]}}}, "allocation_checkpoints_incomplete"),
    ("allocation-profiling", {"diagnostics": {"completed_requests": 1}}, "diagnostics_request_count_mismatch"),
    ("call-profiling", {"diagnostics": {"calls": {"windows": [], "late_window_reached": False}}}, "calls_late_window_missing"),
    ("pilot", {"no_counts": True}, "retained_counts_missing"),
])
def test_invalid_or_missing_diagnostics_stop_the_campaign(campaign, phase, behaviour, reason):
    records = campaign.run(FakeRunner({1: behaviour}), phase=phase)
    assert len(records) == 1 and records[0]["infrastructure_failures"] == [reason]
    assert events(campaign.output)[-1] == "campaign_stopped"


def test_failed_intrusive_preflight_stops_before_any_trial(campaign, monkeypatch):
    monkeypatch.setattr(ol, "intrusive_preflight", lambda upstream, profile: {"profile": profile, "returncode": 1, "failures": ["worker_fatal_error:FileExistsError"]})
    runner = FakeRunner()
    with pytest.raises(RuntimeError, match="preflight failed"):
        campaign.run(runner, phase="allocation-profiling")
    assert runner.calls == [] and events(campaign.output) == ["campaign_started", "intrusive_preflight", "exception"]
    campaign.output = campaign.output.with_name("all")
    with pytest.raises(RuntimeError, match="preflight failed"):
        campaign.run_all(runner, FakeLocalRunner())
    assert runner.calls == [] and [path.name for path in campaign.output.iterdir() if path.is_dir()] == []


def test_all_phases_run_in_order_with_separate_frozen_ledgers_and_results_summary(campaign):
    config = small_config()
    runner, local = FakeRunner(), FakeLocalRunner()
    results = campaign.run_all(runner, local, config=config)
    plans = ol.campaign_specs(config)
    assert list(results) == list(ol.ALL_PHASES)
    assert all(not row["infrastructure_failures"] for records in results.values() for row in records)
    assert runner.calls == [spec["trial_id"] for phase in ol.ALL_PHASES for spec in plans[phase] if spec["kind"] == "sustained"]
    assert local.calls == [spec["trial_id"] for spec in plans["local-control"]]
    assert campaign.sleeps == [60] * (sum(len(specs) for specs in plans.values()) - 1)
    assert campaign.preflights == ["allocation", "calls"]
    fingerprint = json.loads((campaign.output / "campaign.json").read_text())["source_sha256"]
    assert fingerprint == ol.source_fingerprint() and "evaluation/local_control_experiment.py" in fingerprint
    for phase in ol.ALL_PHASES:
        frozen = json.loads((campaign.output / phase / "frozen.json").read_text())
        assert frozen["phase"] == phase and frozen["source_sha256"] == fingerprint
        assert events(campaign.output / phase) == ["campaign_started"] + ["started", "finished"] * len(plans[phase])
    campaign_events = [event["event"] for event in ol.read_ledger_file(campaign.output / "campaign-ledger.jsonl")]
    assert campaign_events == ["campaign_started", "intrusive_preflight", "intrusive_preflight"] + ["phase_started", "phase_finished"] * len(ol.ALL_PHASES)

    summary = ol.analyze_campaign(campaign.output)
    assert summary["phases_without_output"] == []
    allocation = summary["phases"]["allocation-profiling"]
    assert allocation["conditions"]["direct@10Hz/allocation"]["timing_role"] == "diagnostic_not_primary"
    diagnostics = allocation["intrusive_diagnostics"]["allocation-profiling-r01-direct-10hz"]
    assert diagnostics["path"] == "workload/diagnostics/diagnostics.json" and "workload/diagnostics/allocation-end.json" in diagnostics["files"]
    assert [item["label"] for item in diagnostics["allocation_checkpoints"]] == ["start", "60s", "end"]
    attribution = summary["phases"]["attribution"]["conditions"]["orchestrated@10Hz"]
    assert attribution["timing_role"] == "primary" and attribution["across_trials"]["cycle_median_ms"]["median"] == 3.0
    assert attribution["trials"][0]["retained_last"] == {"lifecycle.history": 9}
    assert attribution["trials"][0]["power"]["idle_adjusted_w"] == pytest.approx(5.5 - 4.7)
    paired = summary["phases"]["paired-power"]["paired_power"]
    assert paired["difference"] == "orchestrated_minus_direct" and [row["order"] for row in paired["pairs"]] == ["AB", "BA"]
    assert paired["delta_steady_w"]["n"] == 2 and paired["delta_steady_w"]["median"] == pytest.approx(0.5)
    assert paired["pairs"][0]["platform_baseline_w"] == 4.5
    local_conditions = summary["phases"]["local-control"]["conditions"]
    assert set(local_conditions) == {f"{mode}/{scenario}" for mode in lce.MODES for scenario in lce.SCENARIOS}
    assert all(condition["status_counts"] == {"success": 1} for condition in local_conditions.values())
    assert (campaign.output / "results-summary.md").read_text().startswith("# Operating-limits results summary")
    status = ol.campaign_status(campaign.output)
    assert status["phases"]["sweep"]["state_counts"] == {"finished": 2} and status["phases"]["sweep"]["stops"] == []


def test_all_stops_at_first_infrastructure_failure_and_later_phases_never_start(campaign):
    config = small_config()
    runner, local = FakeRunner(), FakeLocalRunner({2: {"error": "TimeoutError"}})
    results = campaign.run_all(runner, local, config=config)
    assert list(results)[-1] == "local-control" and "paired-power" not in results
    assert results["local-control"][-1]["infrastructure_failures"] == ["controller_error:TimeoutError"]
    assert len(local.calls) == 2 and not (campaign.output / "paired-power").exists()
    assert ol.read_ledger_file(campaign.output / "campaign-ledger.jsonl")[-1]["event"] == "campaign_stopped"
    with pytest.raises(RuntimeError, match="stopped"):
        campaign.run_all(runner, local, config=config, resume=True)
    assert len(local.calls) == 2


def test_all_resume_continues_after_interrupt_without_redoing_trials(campaign, monkeypatch):
    config = small_config()
    runner, local = FakeRunner(), FakeLocalRunner()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 6:  # cooldown before the first allocation-profiling trial
            raise KeyboardInterrupt

    monkeypatch.setattr(ol, "time", SimpleNamespace(sleep=sleep, monotonic_ns=time.monotonic_ns))
    with pytest.raises(KeyboardInterrupt):
        campaign.run_all(runner, local, config=config)
    assert runner.calls == [*PILOT_IDS, *(spec["trial_id"] for spec in ol.trial_specs(config, "attribution"))]
    assert ol.read_ledger_file(campaign.output / "campaign-ledger.jsonl")[-1]["event"] == "interrupted"
    campaign.restore_sleep()
    changed = copy.deepcopy(config)
    changed["cooldown_s"] = 90
    with pytest.raises(ValueError, match="changed"):
        campaign.run_all(runner, local, config=changed, resume=True)
    results = campaign.run_all(runner, local, config=config, resume=True)
    assert list(results) == list(ol.ALL_PHASES)
    assert len(runner.calls) == len(set(runner.calls)) == 16 and len(local.calls) == 8


def test_source_change_during_campaign_stops_before_next_trial(campaign, monkeypatch):
    real = ol.source_fingerprint()
    calls = []

    def fingerprint():
        calls.append(1)
        return real if len(calls) <= 2 else {**real, "evaluation/upstream_sustained.py": "edited"}

    monkeypatch.setattr(ol, "source_fingerprint", fingerprint)
    runner = FakeRunner()
    with pytest.raises(RuntimeError, match="sources changed"):
        campaign.run(runner)
    assert runner.calls == PILOT_IDS[:1] and ol.read_ledger(campaign.output)[-1]["event"] == "exception"


def test_measurement_lock_excludes_concurrent_campaigns_and_analysis(campaign):
    runner = FakeRunner()
    campaign.run(runner)
    with ol.measurement_lock():
        with pytest.raises(RuntimeError, match="lock"):
            campaign.run(runner, resume=True)
        with pytest.raises(RuntimeError, match="lock"):
            ol.analyze_campaign(campaign.output)
    assert ol.analyze_campaign(campaign.output)["phases"]["pilot"]["status_counts"] == {"success": 3}


def test_failed_local_assertions_are_results_and_campaign_continues(campaign):
    local = FakeLocalRunner({1: {"fail": ["local_continues_during_outage"]}})
    records = campaign.run(FakeRunner(), phase="local-control", local=local, config=small_config())
    assert len(records) == 8 and records[0]["infrastructure_failures"] == []
    assert records[0]["performance_failures"] == ["assertion:local_continues_during_outage"]
    analysis = ol.analyze_phase(campaign.output)
    assert analysis["status_counts"] == {"performance_failure": 1, "success": 7}
    assert analysis["trials"][records[0]["trial_id"]]["local"]["failed_assertions"] == ["local_continues_during_outage"]


def test_local_archive_with_tampered_command_is_rejected(campaign):
    config = small_config()
    campaign.run(FakeRunner(), phase="local-control", local=FakeLocalRunner(), config=config)
    spec = ol.trial_specs(config, "local-control")[0]
    folder = campaign.output / spec["trial_id"]
    provenance = json.loads((folder / "provenance.json").read_text())
    provenance["command"] += ["--fault-at-s", "10"]
    (folder / "provenance.json").write_text(json.dumps(provenance))
    seal(folder)
    with pytest.raises(ValueError, match="does not match"):
        ol.assess_trial(folder, spec, config)


def test_local_classification_requires_consistent_exit_status_and_assertions():
    spec = ol.trial_specs(PLAN, "local-control")[0]
    result = {"returncode": 1, "timed_out": False, "upstream_sources_unchanged": True, "parent_error": None, "process_cleanup": {"group_remaining": False, "owned_stopped": [], "owned_remaining": []}}
    summary = {"trial_id": spec["trial_id"], "mode": spec["mode"], "scenario": spec["scenario"], "backend": "pinned", "status": "failed", "passed": False, "assertions": [{"name": "a", "passed": True}, {"name": "b", "passed": False}]}
    assert ol.classify_local_trial(result, summary, spec) == {"infrastructure_failures": [], "performance_failures": ["assertion:b"]}
    assert ol.classify_local_trial({**result, "returncode": 0}, summary, spec)["infrastructure_failures"] == ["returncode:0"]
    assert ol.classify_local_trial(result, {**summary, "passed": True}, spec)["infrastructure_failures"] == ["assertion_summary_inconsistent"]
    assert ol.classify_local_trial(result, {**summary, "assertions": []}, spec)["infrastructure_failures"] == ["assertions_invalid"]
    assert ol.classify_local_trial(result, {**summary, "trial_id": "other"}, spec)["infrastructure_failures"] == ["summary_identity_mismatch"]
    assert ol.classify_local_trial({**result, "timed_out": True, "returncode": -9}, None, spec)["infrastructure_failures"] == ["timed_out", "controller_summary_missing", "returncode:-9"]
    assert ol.classify_local_trial({**result, "process_cleanup": {"owned_stopped": [12], "owned_remaining": []}}, summary, spec)["infrastructure_failures"] == ["owned_processes_left_by_controller"]


CHILD = """
import json, pathlib, subprocess, sys, time
out, spec, python, behaviour = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
out.mkdir()
if behaviour == "hang":
    subprocess.Popen([python, "-c", "import time; time.sleep(60)", str(out / "orphan")], start_new_session=True)
    time.sleep(60)
config = {key: spec[key] for key in ("trial_id", "mode", "partition", "fault", "duration_s", "rate_hz", "backend")}
(out / "config.json").write_text(json.dumps({**config, "supervisor_python": python}))
summary = {"trial_id": spec["trial_id"], "mode": spec["mode"], "scenario": spec["scenario"], "backend": spec["backend"], "status": "passed", "passed": True, "assertions": [{"name": "job_terminal_completed", "passed": True}]}
(out / "summary.json").write_text(json.dumps(summary))
"""


@pytest.mark.parametrize("behaviour", ["pass", "hang"])
def test_run_local_trial_supervises_seals_and_stops_owned_processes(campaign, monkeypatch, behaviour):
    monkeypatch.setattr(ol, "local_command", lambda spec, folder, upstream, python: [python, "-c", CHILD, str(folder / "controller"), json.dumps(spec), python, behaviour])
    monkeypatch.setattr(ol, "git_fact", lambda upstream, *args: ol.PINNED_COMMIT if args[0] == "rev-parse" else "")
    monkeypatch.setattr(ol, "source_hashes", lambda upstream: {"core/example.py": "0" * 64})
    monkeypatch.setattr(ol, "hardware_preflight", dict)
    monkeypatch.setattr(ol, "hardware_state", dict)
    spec = {**ol.trial_specs(PLAN, "local-control")[0], "timeout_s": 2 if behaviour == "hang" else 60}
    folder = campaign.output / spec["trial_id"]
    result = ol.run_local_trial(spec, folder, campaign.upstream)
    _, summary, classification = ol.assess_trial(folder, spec, PLAN)
    provenance = json.loads((folder / "provenance.json").read_text())
    assert provenance["resource_profile"].startswith("not_measured") and provenance["supervisor_python"] == sys.executable
    assert {"stdout.log", "stderr.log", "result.json", "provenance.json"} <= set(json.loads((folder / "reproduction-manifest.json").read_text()))
    if behaviour == "pass":
        assert result["returncode"] == 0 and summary["status"] == "passed"
        assert classification == {"infrastructure_failures": [], "performance_failures": []}
    else:
        cleanup = result["process_cleanup"]
        assert result["timed_out"] and summary is None and len(cleanup["owned_stopped"]) == 1 and cleanup["owned_remaining"] == []
        assert not any(str(folder) + os.sep in " ".join(process.info["cmdline"] or ()) for process in ol.psutil.process_iter(["cmdline"]))
        assert classification["infrastructure_failures"][:3] == ["timed_out", "owned_processes_left_by_controller", "controller_summary_missing"]


def test_local_command_runs_controller_with_campaign_interpreter_for_supervisor():
    spec = ol.trial_specs(PLAN, "local-control")[0]
    command = ol.local_command(spec, Path("/runs/x") / spec["trial_id"], Path("/pinned"), "/py312")
    assert command[:2] == ["/py312", str(lce.MODULE_PATH)] and command[command.index("--supervisor-python") + 1] == "/py312"
    assert command[command.index("--backend") + 1] == "pinned" and "--duration-s" not in command


@pytest.mark.parametrize("layout, returncode, failures", [
    ("top", 0, []),
    ("nested", 0, []),
    ("exists", 1, ["returncode:1", "worker_fatal_error:FileExistsError:[Errno 17] File exists", "diagnostics_json_count:0"]),
    ("wrong_profile", 0, ["summary_profile_mismatch:none"]),
])
def test_intrusive_preflight_runs_in_scratch_under_temp_and_reports_worker_failures(monkeypatch, tmp_path, layout, returncode, failures):
    seen = {}

    def fake_run(command, *, cwd, env, capture_output, timeout, check):
        workload = Path(command[command.index("--output-dir") + 1])
        seen.update(scratch=workload.parent, env=env, profile=command[command.index("--intrusive-profile") + 1])
        workload.mkdir()
        summary = {"fatal_error": None, "intrusive_profile": "none" if layout == "wrong_profile" else seen["profile"]}
        if layout == "exists":
            summary["fatal_error"] = {"type": "FileExistsError", "message": "[Errno 17] File exists", "phase": "active"}
        (workload / "summary.json").write_text(json.dumps(summary))
        if layout != "exists":
            target = workload / "diagnostics" if layout == "nested" else workload
            target.mkdir(exist_ok=True)
            (target / "diagnostics.json").write_text(json.dumps({"kind": seen["profile"], "errors": []}))
        return SimpleNamespace(returncode=returncode, stderr=b"tail")

    monkeypatch.setattr(ol, "SCRATCH_ROOT", tmp_path / "_temp")
    monkeypatch.setattr(ol, "subprocess", SimpleNamespace(run=fake_run, TimeoutExpired=ol.subprocess.TimeoutExpired))
    check = ol.intrusive_preflight(tmp_path, "allocation")
    assert check["failures"] == failures and check["returncode"] == returncode
    assert seen["scratch"].parent == tmp_path / "_temp" and seen["env"]["TMPDIR"] == str(seen["scratch"]) and not seen["scratch"].exists()
    expected_path = {"top": "workload/diagnostics.json", "nested": "workload/diagnostics/diagnostics.json"}.get(layout)
    assert check["diagnostics_path"] == (expected_path if layout != "wrong_profile" else "workload/diagnostics.json")


def test_worker_top_level_diagnostics_json_is_found_and_digested(tmp_path):
    summary = write_workload(tmp_path, good_rows(), diagnostic_counts=False, intrusive_profile="calls")
    (tmp_path / "workload/diagnostics.json").write_text(json.dumps(diagnostics_metadata("calls", summary["completed_requests"])))
    (tmp_path / "workload/calls-early.json").write_text("{}")
    seal(tmp_path)
    spec = {**ol.trial_specs(PLAN, "call-profiling")[0]}
    assert ol.diagnostic_failures(tmp_path, spec, summary) == []
    digest_ = ol.diagnostic_digest(tmp_path)["intrusive"]
    assert digest_["path"] == "workload/diagnostics.json" and digest_["files"] == ["workload/calls-early.json", "workload/diagnostics.json"]
