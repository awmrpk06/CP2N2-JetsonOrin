"""Narrow provider boundary used by both simulator and future CL1 adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import BioPatternGateConfig
from .decoder import GateDecision
from .features import SpikeEvent
from .scheduler import TrialPlan


@dataclass(frozen=True)
class TrialObservation:
    trial_index: int
    events: tuple[SpikeEvent, ...]
    runtime_kind: str
    telemetry: dict[str, Any] = field(default_factory=dict)


class BioPatternGatePort(Protocol):
    """Only boundary the application runner needs from Cortical Labs."""

    runtime_kind: str

    def prepare(self, config: BioPatternGateConfig) -> None: ...

    def observe_trial(
        self,
        plan: TrialPlan,
        *,
        logical_sequence: tuple[str, str] | None,
        config: BioPatternGateConfig,
    ) -> TrialObservation: ...

    def record_features(
        self,
        plan: TrialPlan,
        *,
        feature_values: dict[str, float],
    ) -> None: ...

    def record_decision(
        self,
        plan: TrialPlan,
        *,
        decision: GateDecision,
        decision_commit_sha256: str,
    ) -> None: ...

    def abort(self, reason: str) -> None: ...

    def close(self) -> None: ...
