"""Bounded tests for the loopback local-control disconnection experiment.

Only loopback sockets and tmp_path are used; no host network, provider or environment changes.
"""

import json
import os
from pathlib import Path

import pytest

from evaluation import local_control_experiment as lce

FAST = {
    "duration_s": 1.6, "rate_hz": 20.0, "partition_start_s": 0.5, "partition_end_s": 1.1, "fault_at_s": 0.8,
    "accept_deadline_s": 0.5, "poll_interval_s": 0.15, "ready_timeout_s": 20.0, "finalize_timeout_s": 10.0,
}
PINNED_PYTHON = lce.DEFAULT_PINNED_PYTHON
PINNED_AVAILABLE = PINNED_PYTHON.exists() and (lce.DEFAULT_UPSTREAM / ".git").exists()


class Clock:
    def __init__(self, value=1_000_000_000):
        self.value = value

    def __call__(self):
        return self.value


def make_supervisor(tmp_path, mode="client", rate_hz=10.0):
    clock = Clock()
    supervisor = lce.Supervisor(lce.FakeBackend(), tmp_path, owner="owner", token="tok", clock=clock, run_scheduler=False)
    config = lce.TrialConfig(mode=mode, partition=False, fault=False, duration_s=0.5, rate_hz=rate_hz, partition_start_s=0.1, partition_end_s=0.2, accept_deadline_s=0.1, owner="owner")
    plan = lce.make_plan(config, "job-1", clock.value)
    return supervisor, clock, plan


def start(supervisor, plan, *, owner="owner", token="tok", digest=None):
    return supervisor.start_job(owner, token, plan, digest or lce.plan_hash(plan))


def test_job_start_is_deduplicated_and_bound_to_owner_and_plan_hash(tmp_path):
    supervisor, _, plan = make_supervisor(tmp_path)
    assert start(supervisor, plan, token="wrong")[0] == 403
    assert start(supervisor, plan, digest="0" * 64) == (400, {"error": "plan_hash_mismatch"})
    status, body = start(supervisor, plan)
    assert status == 201 and body["duplicate"] is False
    status, body = start(supervisor, plan)
    assert status == 200 and body["duplicate"] is True and body["job"]["plan_hash"] == lce.plan_hash(plan)
    other = {**plan, "job_id": "job-2"}
    assert start(supervisor, other)[0] == 409
    assert supervisor.job_status("owner", "tok", "job-2")[0] == 404
    assert supervisor.counters["duplicate_starts"] == 1
    supervisor.stop()


def test_client_step_lost_ack_retry_does_not_reexecute_and_late_steps_are_not_replayed(tmp_path):
    supervisor, clock, plan = make_supervisor(tmp_path)
    start(supervisor, plan)
    period = round(1e9 / plan["rate_hz"])
    assert supervisor.trigger_step("owner", "tok", "job-1", 1, clock.value)[0] == 425
    status, first = supervisor.trigger_step("owner", "tok", "job-1", 0, clock.value + 10)
    assert status == 200 and first["deduplicated"] is False and first["record"]["status"] == "executed"
    status, retry = supervisor.trigger_step("owner", "tok", "job-1", 0, clock.value + 20)
    assert status == 200 and retry["deduplicated"] is True and retry["record"]["seq"] == first["record"]["seq"]
    # A replayed native request with the same owner-scoped key replays instead of invoking again.
    replay = supervisor.backend.execute(task_id=lce.step_task_id("job-1", 0), owner="owner", key=lce.step_idempotency_key("job-1", 0), correlation_id="another-trace")
    assert replay["idempotent_replay"] is True
    assert supervisor.backend.invocation_report()["total"] == 1
    assert supervisor.trigger_step("owner", "tok", "job-1", 1, clock.value + 2 * period)[0] == 410
    supervisor.close_window(1)
    status, body = supervisor.results("owner", "tok", "job-1", 0, 10)
    assert [(item["step"], item["status"]) for item in body["items"]] == [(0, "executed"), (1, "missed")]
    assert body["items"][1]["reason"] == "no_trigger_in_window"
    assert supervisor.backend.invocation_report() == {"by_backend": {lce.PRIMARY_ID: 1, lce.FALLBACK_ID: 0}, "total": 1, "steps_with_multiple": [], "native_idempotency_entries": 1}
    supervisor.stop()


def test_correlation_ids_are_not_idempotency_keys():
    backend = lce.FakeBackend()
    first = backend.execute(task_id="t", owner="a", key="job/step/0000", correlation_id="same-trace")
    other_owner = backend.execute(task_id="t", owner="b", key="job/step/0000", correlation_id="same-trace")
    retried = backend.execute(task_id="t", owner="a", key="job/step/0000", correlation_id="new-trace")
    assert not first["idempotent_replay"] and not other_owner["idempotent_replay"] and retried["idempotent_replay"]
    assert backend.invocation_report()["total"] == 2
    assert lce.step_idempotency_key("job", 3) == "job/step/0003" != lce.step_task_id("job", 3)


def test_cursor_pages_are_bounded_repeatable_and_reject_stale_cursors(tmp_path):
    supervisor, clock, plan = make_supervisor(tmp_path, mode="local")
    start(supervisor, plan)
    for step in range(5):
        clock.value = supervisor.release_ns(step) + 1
        supervisor.run_local_step(step)
    first = supervisor.results("owner", "tok", "job-1", 0, 2)[1]
    assert [item["seq"] for item in first["items"]] == [0, 1] and first["next_cursor"] == 2 and not first["complete"]
    assert supervisor.results("owner", "tok", "job-1", 0, 2)[1] == first
    assert supervisor.results("owner", "tok", "job-1", 6, 2)[0] == 409
    assert supervisor.results("owner", "tok", "job-1", -1, 2)[0] == 400
    assert supervisor.results("owner", "tok", "job-1", 0, lce.MAX_PAGE + 1)[0] == 400
    assert supervisor.results("owner", "wrong", "job-1", 0, 2)[0] == 403
    supervisor.finish_job("completed")
    last = supervisor.results("owner", "tok", "job-1", 4, 100)[1]
    assert last["complete"] and last["next_cursor"] == 5
    assert len(lce.read_jsonl(supervisor.results_path)) == 5
    supervisor.stop()


def test_local_mode_skips_late_slots_without_catch_up_and_rejects_client_triggers(tmp_path):
    supervisor, clock, plan = make_supervisor(tmp_path, mode="local")
    start(supervisor, plan)
    clock.value = supervisor.release_ns(3)
    supervisor.run_local_step(0)
    supervisor.run_local_step(3)
    records = lce.read_jsonl(supervisor.results_path)
    assert [(item["step"], item["status"]) for item in records] == [(0, "missed"), (3, "executed")]
    assert records[0]["reason"] == "late_local_dispatch"
    assert supervisor.backend.invocation_report()["total"] == 1
    assert supervisor.trigger_step("owner", "tok", "job-1", 4, clock.value)[0] == 409
    supervisor.stop()


def test_armed_fault_is_consumed_once_by_the_next_step_with_fallback(tmp_path):
    supervisor, clock, plan = make_supervisor(tmp_path, mode="local")
    start(supervisor, plan)
    supervisor.arm_fault_now()
    for step in range(2):
        clock.value = supervisor.release_ns(step)
        supervisor.run_local_step(step)
    records = lce.read_jsonl(supervisor.results_path)
    assert [(item["selected_backend"], item["used_fallback"]) for item in records] == [(lce.FALLBACK_ID, True), (lce.PRIMARY_ID, False)]
    assert supervisor.backend.fault["consumed_task_id"] == lce.step_task_id("job-1", 0)
    supervisor.stop()


def test_matrix_config_validation_and_campaign_command(tmp_path):
    matrix = lce.build_matrix(3)
    assert len(matrix) == 24 and len({item.trial_id for item in matrix}) == 24
    assert {(item.mode, item.scenario) for item in matrix} == {(mode, scenario) for mode in lce.MODES for scenario in lce.SCENARIOS}
    assert [item.trial_id for item in matrix] == [item.trial_id for item in lce.build_matrix(3)]
    assert all(item.steps == 900 for item in matrix)
    with pytest.raises(ValueError):
        lce.TrialConfig(mode="local", partition=True, fault=False, accept_deadline_s=31).validate()
    with pytest.raises(ValueError):
        lce.TrialConfig(mode="local", partition=True, fault=False, rate_hz=20).validate()
    config = lce.TrialConfig(mode="client", partition=True, fault=True, **FAST)
    command = lce.trial_command(config, tmp_path / "trial", backend="fake")
    assert command[2:4] == ["controller", "--mode"] and "--partition" in command and "--fault" in command
    assert command[command.index("--duration-s") + 1] == "1.6" and "--repetition" not in command
    assert lce.plan_hash(lce.make_plan(config, "j", 1)) != lce.plan_hash(lce.make_plan(config, "j", 2))


def assert_trial_passed(summary, code):
    failed = [item for item in summary["assertions"] if not item["passed"]]
    assert code == 0 and summary["status"] == "passed", (summary.get("error"), failed)
    assert all(item["returncode"] == 0 and item["forced"] is None for item in summary["processes"].values())


@pytest.mark.parametrize("mode", ["local", "client"])
def test_partition_with_fault_end_to_end_with_fake_backend(tmp_path, mode):
    config = lce.TrialConfig(mode=mode, partition=True, fault=True, **FAST)
    summary, code = lce.run_trial(config, tmp_path / "trial", backend="fake")
    assert_trial_passed(summary, code)
    timeline, during = summary["timeline"], summary["steps"]["by_phase"]["during"]
    assert summary["steps"]["committed"] == config.steps == len(lce.read_jsonl(tmp_path / "trial/supervisor/results.jsonl"))
    assert summary["proxy"]["client_requests_reaching_supervisor_during_partition"] == 0
    assert len(summary["steps"]["fallback_steps"]) == 1
    if mode == "local":
        assert during["executed"] == during["opportunities"] and timeline["fault_consumed_phase"] == "during"
    else:
        assert during["missed"] >= during["opportunities"] - 2 and summary["client"]["counts"]["trigger_link_errors"] > 0
        assert timeline["fault_consumed_offset_s"] >= timeline["link_up_offset_s"] and timeline["fault_consumed_phase"] == "after"
    assert json.loads((tmp_path / "trial/summary.json").read_text())["status"] == "passed"


def test_cli_connected_client_without_fault(tmp_path, capsys):
    output = tmp_path / "trial"
    args = ["controller", "--mode", "client", "--no-partition", "--no-fault", "--output-dir", str(output), "--backend", "fake"]
    for name, value in FAST.items():
        args += [f"--{name.replace('_', '-')}", str(value)]
    assert lce.main(args) == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["summary_path"] == str(output / "summary.json") and printed["passed"] and printed["failed_assertions"] == []
    summary = json.loads(Path(printed["summary_path"]).read_text())
    assert summary["steps"]["executed"] == summary["steps"]["planned"] and summary["steps"]["fallback_steps"] == []


def test_failed_assertions_exit_nonzero_and_keep_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(lce, "analyse_trial", lambda *args: {"passed": False, "assertions": [{"name": "forced", "passed": False, "detail": None}]})
    config = lce.TrialConfig(mode="local", partition=False, fault=False, duration_s=0.3, rate_hz=20, partition_start_s=0.1, partition_end_s=0.2, fault_at_s=0.15, accept_deadline_s=0.1, poll_interval_s=0.1, ready_timeout_s=20)
    summary, code = lce.run_trial(config, tmp_path / "trial", backend="fake")
    assert code == 1 and summary["status"] == "failed"
    assert (tmp_path / "trial/supervisor/results.jsonl").exists() and (tmp_path / "trial/summary.json").exists()


def test_startup_error_records_traceback_and_stops_children(tmp_path):
    config = lce.TrialConfig(mode="local", partition=True, fault=False, ready_timeout_s=0.0)
    summary, code = lce.run_trial(config, tmp_path / "trial", backend="fake")
    assert code == 2 and summary["status"] == "error"
    assert "Traceback" in json.loads((tmp_path / "trial/error.json").read_text())["traceback"]
    supervisor = summary["processes"]["supervisor"]
    assert supervisor["returncode"] is not None
    with pytest.raises(ProcessLookupError):
        os.kill(supervisor["pid"], 0)
    with pytest.raises(FileExistsError):
        lce.run_trial(config, tmp_path / "trial", backend="fake")


@pytest.mark.skipif(not PINNED_AVAILABLE, reason="pinned CP2N2 checkout or interpreter not present")
def test_pinned_cp2n2_partition_with_fault_end_to_end(tmp_path):
    config = lce.TrialConfig(mode="local", partition=True, fault=True, **FAST)
    summary, code = lce.run_trial(config, tmp_path / "trial", backend="pinned")
    assert_trial_passed(summary, code)
    assert summary["upstream_revision"] == lce.PINNED_REVISION
    provenance = summary["provenance"]
    assert provenance["cp2n2_imported"] and not provenance["root_legacy_core_loaded"]
    assert provenance["orchestrator_module"].startswith(str(lce.DEFAULT_UPSTREAM.resolve()))
    assert summary["backend"]["resources"][lce.PRIMARY_ID] == {**summary["backend"]["resources"][lce.PRIMARY_ID], "lifecycle": "ready", "lease_id": None}
