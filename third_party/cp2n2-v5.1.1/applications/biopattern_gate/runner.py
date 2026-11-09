"""End-to-end access-independent execution of the BioPattern Gate protocol."""

from __future__ import annotations

import hashlib
import json

from .config import BioPatternGateConfig
from .decoder import FrozenLinearDecoder
from .features import extract_features
from .port import BioPatternGatePort
from .results import SessionResult, TrialResult
from .scheduler import TrialKind, build_trial_schedule
from .state import (
    SessionState,
    TrialState,
    new_session_machine,
    new_trial_machine,
)


class ProtocolExecutionError(RuntimeError):
    pass


def run_session(
    *,
    run_id: str,
    config: BioPatternGateConfig,
    decoder: FrozenLinearDecoder,
    port: BioPatternGatePort,
) -> SessionResult:
    """Execute a blinded confirmatory session and retain partial-safe semantics."""

    if decoder.sha256() != config.decoder.artifact_sha256:
        raise ProtocolExecutionError("decoder hash does not match validated config")
    if decoder.training_run_ref != config.decoder.training_run_ref:
        raise ProtocolExecutionError("decoder training provenance does not match config")
    if decoder.threshold != config.decoder.decision_threshold:
        raise ProtocolExecutionError("decoder threshold does not match config")
    if port.runtime_kind != config.evidence.runtime_kind:
        raise ProtocolExecutionError("runtime kind does not match evidence context")

    session = new_session_machine()
    results: list[TrialResult] = []
    try:
        session.transition(SessionState.PREFLIGHT)
        port.prepare(config)
        session.transition(SessionState.PRE_BASELINE)
        session.transition(SessionState.MAPPING)
        session.transition(SessionState.CALIBRATION)
        session.transition(SessionState.FROZEN_VALIDATION)
        session.transition(SessionState.CONFIRMATORY_TEST)

        for plan in build_trial_schedule(config.schedule):
            trial = new_trial_machine()
            trial.transition(TrialState.PRE_STIMULUS)
            trial.transition(TrialState.STIMULATING)
            sequence = {
                TrialKind.PATTERN_A: config.pattern_a.sequence,
                TrialKind.PATTERN_B: config.pattern_b.sequence,
                TrialKind.SHAM: None,
            }[plan.kind]
            observation = port.observe_trial(
                plan,
                logical_sequence=sequence,
                config=config,
            )
            if observation.trial_index != plan.trial_index:
                raise ProtocolExecutionError("observation/trial identity mismatch")
            if observation.runtime_kind != config.evidence.runtime_kind:
                raise ProtocolExecutionError("observation/runtime mismatch")

            trial.transition(TrialState.ARTEFACT_BLANKING)
            trial.transition(TrialState.OBSERVING)
            trial.transition(TrialState.FEATURIZING)
            features = extract_features(
                observation.events,
                readout_group_refs=config.readout_group_refs,
                observation_duration_ms=config.timing.observation_duration_ms,
                config=config.features,
            )
            port.record_features(
                plan,
                feature_values=features.values,
            )
            decision = decoder.decide(features)
            commit = decision_commit(
                run_id=run_id,
                trial_index=plan.trial_index,
                feature_values=features.values,
                predicted_label=decision.predicted_label,
                route=decision.route,
            )
            port.record_decision(
                plan,
                decision=decision,
                decision_commit_sha256=commit,
            )
            trial.transition(TrialState.DECISION_COMMITTED)
            expected = plan.hidden_label
            trial.transition(TrialState.LABEL_REVEALED)
            trial.transition(TrialState.INTER_TRIAL)
            trial.transition(TrialState.COMPLETE)
            results.append(
                TrialResult(
                    trial_index=plan.trial_index,
                    block_index=plan.block_index,
                    kind=plan.kind.value,
                    expected_label=expected,
                    predicted_label=decision.predicted_label,
                    route=decision.route,
                    probability_a=decision.probability_a,
                    decision_commit_sha256=commit,
                    correct=(
                        None if expected is None else decision.predicted_label == expected
                    ),
                    feature_values=features.values,
                    telemetry=observation.telemetry,
                    state_history=tuple(trial.history),
                )
            )

        session.transition(SessionState.POST_BASELINE)
        session.transition(SessionState.FINALIZING)
        session.transition(SessionState.COMPLETE)
    except Exception as exc:
        if session.state not in {
            SessionState.COMPLETE,
            SessionState.ABORTED,
            SessionState.INVALID,
        }:
            session.invalidate(f"{type(exc).__name__}: {exc}")
        port.abort(str(exc))
        raise
    finally:
        port.close()

    scored = [trial for trial in results if trial.correct is not None]
    accuracy = sum(bool(trial.correct) for trial in scored) / len(scored)
    return SessionResult(
        run_id=run_id,
        config_sha256=config.sha256(),
        decoder_sha256=decoder.sha256(),
        runtime_kind=port.runtime_kind,
        evidence_ceiling=str(config.evidence.evidence_ceiling),
        trials=tuple(results),
        accuracy=accuracy,
        sham_trial_count=sum(trial.expected_label is None for trial in results),
        state_history=tuple(session.history),
    )


def decision_commit(
    *,
    run_id: str,
    trial_index: int,
    feature_values: dict[str, float],
    predicted_label: str,
    route: str,
) -> str:
    canonical = json.dumps(
        {
            "run_id": run_id,
            "trial_index": trial_index,
            "feature_values": feature_values,
            "predicted_label": predicted_label,
            "route": route,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
