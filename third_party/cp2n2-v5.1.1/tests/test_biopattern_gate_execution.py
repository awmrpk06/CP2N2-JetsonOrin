from __future__ import annotations

import json
from pathlib import Path

import pytest

from applications.biopattern_gate.config import BioPatternGateConfig
from applications.biopattern_gate.analysis import (
    ReconstructionError,
    reconstruct_session,
)
from applications.biopattern_gate.decoder import load_decoder
from applications.biopattern_gate.features import (
    InvalidObservation,
    SpikeEvent,
    extract_features,
)
from applications.biopattern_gate.runner import run_session
from applications.biopattern_gate.replay import load_replay_bundle
from applications.biopattern_gate.simulator import DeterministicReservoirSimulator
from applications.biopattern_gate.state import TrialState
from mcp_surface.catalog import get_preset


ROOT = Path(__file__).parents[1] / "applications" / "biopattern_gate"
PRESET = ROOT / "presets" / "simulator" / "technical-e3.json"
DECODER = ROOT / "artifacts" / "simulator" / "pattern-gate-linear-v1.json"
REPLAY = (
    Path(__file__).parents[1]
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-replay-success-v1.json"
)


def load_fixture_pair():
    config = BioPatternGateConfig.model_validate_json(
        PRESET.read_text(encoding="utf-8")
    )
    decoder = load_decoder(DECODER)
    return config, decoder


def test_decoder_artifact_matches_validated_preset() -> None:
    config, decoder = load_fixture_pair()

    assert decoder.sha256() == config.decoder.artifact_sha256
    assert decoder.training_run_ref == config.decoder.training_run_ref


def test_end_to_end_simulator_run_is_reproducible_and_blinded() -> None:
    config, decoder = load_fixture_pair()

    first = run_session(
        run_id="technical-e3-golden",
        config=config,
        decoder=decoder,
        port=DeterministicReservoirSimulator(),
    )
    second = run_session(
        run_id="technical-e3-golden",
        config=config,
        decoder=decoder,
        port=DeterministicReservoirSimulator(),
    )

    assert first == second
    assert first.accuracy == 1.0
    assert first.sham_trial_count == config.schedule.block_count
    assert first.evidence_ceiling == "E3"
    assert all(
        trial.telemetry["biological_claim"] is False for trial in first.trials
    )
    for trial in first.trials:
        transition_targets = [target for _, target, _ in trial.state_history]
        assert transition_targets.index(TrialState.DECISION_COMMITTED.value) < (
            transition_targets.index(TrialState.LABEL_REVEALED.value)
        )
        assert len(trial.decision_commit_sha256) == 64


def test_result_summary_contains_provenance_not_physical_controls() -> None:
    config, decoder = load_fixture_pair()
    result = run_session(
        run_id="summary-test",
        config=config,
        decoder=decoder,
        port=DeterministicReservoirSimulator(),
    )

    encoded = json.dumps(result.summary())
    assert result.summary()["config_sha256"] == config.sha256()
    assert result.summary()["decoder_sha256"] == decoder.sha256()
    assert "electrode" not in encoded
    assert "amplitude" not in encoded


def test_agent_facing_catalog_exposes_preset_not_physical_primitives() -> None:
    preset = get_preset("pattern_gate_v1")
    encoded = json.dumps(preset.metadata)

    assert preset.public_dict()["physical_parameters_agent_editable"] is False
    assert preset.metadata["evidence_ceiling"] == "E3"
    assert "channel" not in encoded
    assert "electrode" not in encoded
    assert "amplitude" not in encoded


def test_unknown_or_out_of_window_events_fail_closed() -> None:
    config, _ = load_fixture_pair()

    with pytest.raises(InvalidObservation, match="unknown readout group"):
        extract_features(
            [SpikeEvent(10.0, "unapproved-readout")],
            readout_group_refs=config.readout_group_refs,
            observation_duration_ms=config.timing.observation_duration_ms,
            config=config.features,
        )
    with pytest.raises(InvalidObservation, match="outside observation window"):
        extract_features(
            [SpikeEvent(100.0, "sim-readout")],
            readout_group_refs=config.readout_group_refs,
            observation_duration_ms=config.timing.observation_duration_ms,
            config=config.features,
        )


class InvalidEventSimulator(DeterministicReservoirSimulator):
    def observe_trial(self, plan, *, logical_sequence, config):
        observation = super().observe_trial(
            plan,
            logical_sequence=logical_sequence,
            config=config,
        )
        return type(observation)(
            trial_index=observation.trial_index,
            events=(SpikeEvent(999.0, "sim-readout"),),
            runtime_kind=observation.runtime_kind,
            telemetry=observation.telemetry,
        )


def test_execution_aborts_port_when_observation_is_invalid() -> None:
    config, decoder = load_fixture_pair()
    port = InvalidEventSimulator()

    with pytest.raises(InvalidObservation):
        run_session(
            run_id="must-abort",
            config=config,
            decoder=decoder,
            port=port,
        )

    assert port.aborted_reason is not None
    assert port.closed is True


def test_golden_replay_reproduces_live_simulator_decisions_offline() -> None:
    config, decoder = load_fixture_pair()
    live = run_session(
        run_id="same-run",
        config=config,
        decoder=decoder,
        port=DeterministicReservoirSimulator(),
    )
    replay = run_session(
        run_id="same-run",
        config=config,
        decoder=decoder,
        port=load_replay_bundle(REPLAY),
    )
    report = reconstruct_session(replay, decoder)

    assert [trial.predicted_label for trial in replay.trials] == [
        trial.predicted_label for trial in live.trials
    ]
    assert [trial.decision_commit_sha256 for trial in replay.trials] == [
        trial.decision_commit_sha256 for trial in live.trials
    ]
    assert report.decision_match_count == len(replay.trials)
    assert report.commitment_match_count == len(replay.trials)
    assert report.balanced_accuracy == replay.accuracy


def test_offline_reconstruction_rejects_wrong_decoder_hash() -> None:
    config, decoder = load_fixture_pair()
    replay = run_session(
        run_id="tamper-test",
        config=config,
        decoder=decoder,
        port=load_replay_bundle(REPLAY),
    )
    wrong = type(decoder)(
        feature_schema_version=decoder.feature_schema_version,
        weights=decoder.weights,
        bias=decoder.bias + 0.01,
        threshold=decoder.threshold,
        training_run_ref=decoder.training_run_ref,
    )

    with pytest.raises(ReconstructionError, match="decoder hash"):
        reconstruct_session(replay, wrong)
