"""Validated replay port for deterministic demos and offline reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

from .config import BioPatternGateConfig
from .decoder import GateDecision
from .features import SpikeEvent
from .port import TrialObservation
from .scheduler import TrialPlan


class ReplayBundleError(ValueError):
    pass


class ReplayPort:
    """Serve recorded provider-neutral observations through the normal port."""

    def __init__(
        self,
        *,
        runtime_kind: str,
        config_sha256: str,
        observations: dict[int, TrialObservation],
    ) -> None:
        self.runtime_kind = runtime_kind
        self.config_sha256 = config_sha256
        self.observations = observations
        self.prepared = False
        self.aborted_reason: str | None = None
        self.closed = False

    def prepare(self, config: BioPatternGateConfig) -> None:
        if config.sha256() != self.config_sha256:
            raise ReplayBundleError("replay/config hash mismatch")
        if config.evidence.runtime_kind != self.runtime_kind:
            raise ReplayBundleError("replay/runtime mismatch")
        self.prepared = True

    def observe_trial(
        self,
        plan: TrialPlan,
        *,
        logical_sequence: tuple[str, str] | None,
        config: BioPatternGateConfig,
    ) -> TrialObservation:
        del logical_sequence, config
        if not self.prepared or self.closed or self.aborted_reason is not None:
            raise ReplayBundleError("replay port is not available")
        try:
            return self.observations[plan.trial_index]
        except KeyError as exc:
            raise ReplayBundleError(
                f"replay is missing trial {plan.trial_index}"
            ) from exc

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


def load_replay_bundle(path: Path) -> ReplayPort:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "bundle_version",
        "config_sha256",
        "runtime_kind",
        "evidence_label",
        "observations",
    }
    if set(raw) != expected or raw["bundle_version"] != "1.0":
        raise ReplayBundleError("unknown replay bundle shape or version")
    if raw["evidence_label"] != "E3_REPLAY":
        raise ReplayBundleError("replay bundle must be explicitly labeled E3_REPLAY")
    observations: dict[int, TrialObservation] = {}
    for item in raw["observations"]:
        if set(item) != {"trial_index", "events", "telemetry"}:
            raise ReplayBundleError("unknown replay observation shape")
        trial_index = int(item["trial_index"])
        if trial_index in observations:
            raise ReplayBundleError(f"duplicate replay trial {trial_index}")
        events = tuple(
            SpikeEvent(
                timestamp_ms=float(event["timestamp_ms"]),
                readout_group_ref=str(event["readout_group_ref"]),
            )
            for event in item["events"]
        )
        observations[trial_index] = TrialObservation(
            trial_index=trial_index,
            events=events,
            runtime_kind=str(raw["runtime_kind"]),
            telemetry={
                **dict(item["telemetry"]),
                "source": "validated-replay",
                "live": False,
                "biological_claim": False,
            },
        )
    return ReplayPort(
        runtime_kind=str(raw["runtime_kind"]),
        config_sha256=str(raw["config_sha256"]),
        observations=observations,
    )
