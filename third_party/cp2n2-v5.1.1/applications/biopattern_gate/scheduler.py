"""Deterministic, blocked and class-balanced BioPattern Gate scheduler."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .config import ScheduleConfig


class TrialKind(str, Enum):
    PATTERN_A = "pattern_a"
    PATTERN_B = "pattern_b"
    SHAM = "sham"


@dataclass(frozen=True)
class TrialPlan:
    trial_index: int
    block_index: int
    position_in_block: int
    kind: TrialKind
    hidden_label: str | None

    @property
    def public_schedule_record(self) -> dict[str, int | str | bool]:
        """Pre-commit view that cannot reveal the expected class."""

        return {
            "trial_index": self.trial_index,
            "block_index": self.block_index,
            "position_in_block": self.position_in_block,
            "label_hidden": True,
        }


def build_trial_schedule(config: ScheduleConfig) -> tuple[TrialPlan, ...]:
    """Build the same schedule for the same versioned seed and configuration."""

    rng = random.Random(config.seed)
    plans: list[TrialPlan] = []
    trial_index = 0
    for block_index in range(config.block_count):
        kinds = (
            [TrialKind.PATTERN_A] * config.trials_per_class_per_block
            + [TrialKind.PATTERN_B] * config.trials_per_class_per_block
            + [TrialKind.SHAM] * config.shams_per_block
        )
        rng.shuffle(kinds)
        for position, kind in enumerate(kinds):
            hidden_label = {
                TrialKind.PATTERN_A: "A",
                TrialKind.PATTERN_B: "B",
                TrialKind.SHAM: None,
            }[kind]
            plans.append(
                TrialPlan(
                    trial_index=trial_index,
                    block_index=block_index,
                    position_in_block=position,
                    kind=kind,
                    hidden_label=hidden_label,
                )
            )
            trial_index += 1
    return tuple(plans)
