"""Deterministic test double for access-independent development and replay."""

from __future__ import annotations

import random

from .config import BioPatternGateConfig
from .decoder import GateDecision
from .features import SpikeEvent
from .port import TrialObservation
from .scheduler import TrialKind, TrialPlan


class DeterministicReservoirSimulator:
    """Produces temporal readout signatures without claiming biological evidence."""

    runtime_kind = "sdk_simulator"

    def __init__(self) -> None:
        self.prepared = False
        self.aborted_reason: str | None = None
        self.closed = False

    def prepare(self, config: BioPatternGateConfig) -> None:
        if config.evidence.runtime_kind != self.runtime_kind:
            raise ValueError("configuration/runtime mismatch")
        self.prepared = True

    def observe_trial(
        self,
        plan: TrialPlan,
        *,
        logical_sequence: tuple[str, str] | None,
        config: BioPatternGateConfig,
    ) -> TrialObservation:
        if not self.prepared or self.closed or self.aborted_reason is not None:
            raise RuntimeError("simulator is not available for observation")
        rng = random.Random(config.schedule.seed + plan.trial_index * 104729)
        readout = config.readout_group_refs[0]
        if plan.kind == TrialKind.PATTERN_A:
            signature = (8.0, 15.0, 27.0, 35.0, 76.0)
        elif plan.kind == TrialKind.PATTERN_B:
            signature = (14.0, 62.0, 71.0, 83.0, 91.0)
        else:
            signature = () if plan.trial_index % 2 else (48.0,)
        events = tuple(
            SpikeEvent(
                timestamp_ms=max(
                    0.0,
                    min(
                        config.timing.observation_duration_ms - 0.001,
                        timestamp + rng.uniform(-1.25, 1.25),
                    ),
                ),
                readout_group_ref=readout,
            )
            for timestamp in signature
        )
        return TrialObservation(
            trial_index=plan.trial_index,
            events=events,
            runtime_kind=self.runtime_kind,
            telemetry={
                "source": "deterministic-reservoir-simulator",
                "biological_claim": False,
                "logical_sequence_present": logical_sequence is not None,
            },
        )

    def record_features(
        self,
        plan: TrialPlan,
        *,
        feature_values: dict[str, float],
    ) -> None:
        del plan, feature_values

    def record_decision(
        self,
        plan: TrialPlan,
        *,
        decision: GateDecision,
        decision_commit_sha256: str,
    ) -> None:
        del plan, decision, decision_commit_sha256

    def abort(self, reason: str) -> None:
        self.aborted_reason = reason

    def close(self) -> None:
        self.closed = True
