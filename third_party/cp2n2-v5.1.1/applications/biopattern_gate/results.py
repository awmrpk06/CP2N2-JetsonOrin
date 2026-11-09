"""Provenance-bearing result records for the BioPattern Gate demo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrialResult:
    trial_index: int
    block_index: int
    kind: str
    expected_label: str | None
    predicted_label: str
    route: str
    probability_a: float
    decision_commit_sha256: str
    correct: bool | None
    feature_values: dict[str, float]
    telemetry: dict[str, Any]
    state_history: tuple[tuple[str, str, str | None], ...]


@dataclass(frozen=True)
class SessionResult:
    run_id: str
    config_sha256: str
    decoder_sha256: str
    runtime_kind: str
    evidence_ceiling: str
    trials: tuple[TrialResult, ...]
    accuracy: float
    sham_trial_count: int
    state_history: tuple[tuple[str, str, str | None], ...]

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "config_sha256": self.config_sha256,
            "decoder_sha256": self.decoder_sha256,
            "runtime_kind": self.runtime_kind,
            "evidence_ceiling": self.evidence_ceiling,
            "trial_count": len(self.trials),
            "scored_trial_count": sum(
                trial.expected_label is not None for trial in self.trials
            ),
            "sham_trial_count": self.sham_trial_count,
            "accuracy": self.accuracy,
        }

