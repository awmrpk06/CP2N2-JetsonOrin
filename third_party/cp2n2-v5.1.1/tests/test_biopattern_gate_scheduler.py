from __future__ import annotations

from collections import Counter, defaultdict

from applications.biopattern_gate.config import ScheduleConfig
from applications.biopattern_gate.scheduler import TrialKind, build_trial_schedule


def test_schedule_is_deterministic_balanced_and_blocked() -> None:
    config = ScheduleConfig(
        seed=7,
        block_count=3,
        trials_per_class_per_block=5,
        shams_per_block=2,
    )

    first = build_trial_schedule(config)
    second = build_trial_schedule(config)

    assert first == second
    assert len(first) == config.trial_count

    per_block: dict[int, Counter] = defaultdict(Counter)
    for trial in first:
        per_block[trial.block_index][trial.kind] += 1
    for counts in per_block.values():
        assert counts[TrialKind.PATTERN_A] == 5
        assert counts[TrialKind.PATTERN_B] == 5
        assert counts[TrialKind.SHAM] == 2


def test_precommit_schedule_record_never_reveals_label() -> None:
    trial = build_trial_schedule(ScheduleConfig(block_count=1))[0]

    assert "hidden_label" not in trial.public_schedule_record
    assert trial.public_schedule_record["label_hidden"] is True

