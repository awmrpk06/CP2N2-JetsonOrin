from __future__ import annotations

from pathlib import Path

import pytest
from cl.sim import DataSourceStim

from applications.biopattern_gate.cl_api_port import (
    CLApiBioPatternGatePort,
    STREAM_NAMES,
    technical_e3_runtime_config,
)
from applications.biopattern_gate.cl_recording import (
    reconstruct_cl_recording,
    verify_online_result_against_recording,
)
from applications.biopattern_gate.cl_sdk_source import (
    BioPatternGateResponsiveSource,
)
from applications.biopattern_gate.config import BioPatternGateConfig
from applications.biopattern_gate.decoder import load_decoder
from applications.biopattern_gate.runner import run_session


ROOT = Path(__file__).parents[1]
APPLICATION_ROOT = ROOT / "applications" / "biopattern_gate"
PRESET = APPLICATION_ROOT / "presets" / "simulator" / "technical-e3.json"
DECODER = (
    APPLICATION_ROOT
    / "artifacts"
    / "simulator"
    / "pattern-gate-linear-v1.json"
)


def load_fixture_pair():
    return (
        BioPatternGateConfig.model_validate_json(
            PRESET.read_text(encoding="utf-8")
        ),
        load_decoder(DECODER),
    )


def test_live_stream_names_are_hdf5_safe_and_include_native_events() -> None:
    assert len(STREAM_NAMES) == len(set(STREAM_NAMES))
    assert all("/" not in name for name in STREAM_NAMES)
    assert all(len(name.encode("utf-8")) <= 64 for name in STREAM_NAMES)

    visualiser = (
        ROOT / "cl-apps" / "cp2n2-biopattern-gate" / "web" / "vis.mjs"
    ).read_text(encoding="utf-8")
    for name in (*STREAM_NAMES, "cl_spikes", "cl_stims"):
        assert f'"{name}"' in visualiser
    assert "pattern_gate/session" not in visualiser
    assert "function process(dataStreamName, timestamp, data)" in visualiser
    assert "function draw(browserTimestampMs, dataStreamTimestamp)" in visualiser


def test_responsive_source_emits_pattern_specific_spikes() -> None:
    source = BioPatternGateResponsiveSource(
        input_groups={"left": [8], "right": [9]},
        readout_channels=[20],
        inter_step_frames=500,
        observation_start_frames=250,
        pattern_a_sequence=["left", "right"],
        pattern_b_sequence=["right", "left"],
    )
    source.on_stims(
        [
            DataSourceStim(timestamp=100, channel=8),
            DataSourceStim(timestamp=600, channel=9),
        ]
    )

    batch = source.read(850, 2_500)

    assert [spike.channel for spike in batch.spikes] == [20] * 5
    assert [spike.timestamp - 850 for spike in batch.spikes] == [
        200,
        375,
        675,
        875,
        1_900,
    ]


def test_cl_api_port_records_and_reconstructs_complete_e3_run(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CL_SDK_VISUALISATION", "0")
    monkeypatch.setenv("CL_SDK_ACCELERATED_TIME", "1")
    config, decoder = load_fixture_pair()
    port = CLApiBioPatternGatePort(
        run_id="cl-api-e3-integration",
        output_directory=tmp_path,
        runtime_config=technical_e3_runtime_config(),
    )

    result = run_session(
        run_id="cl-api-e3-integration",
        config=config,
        decoder=decoder,
        port=port,
    )

    assert result.accuracy == 1.0
    assert port.recording_path is not None
    evidence = reconstruct_cl_recording(
        port.recording_path,
        expected_run_id=result.run_id,
        expected_runtime_kind="sdk_simulator",
        expected_stream_names=STREAM_NAMES,
    )
    verify_online_result_against_recording(result, evidence)
    assert evidence.terminal_state == "complete"
    assert evidence.evidence_ceiling == "E3"
    assert evidence.stim_count == 24
    assert evidence.spike_count == 60
    assert len(evidence.feature_records) == len(result.trials)
    assert len(evidence.decision_records) == len(result.trials)


class _FailAfterFirstFeaturePort(CLApiBioPatternGatePort):
    def record_features(self, plan, *, feature_values):
        super().record_features(plan, feature_values=feature_values)
        raise RuntimeError("injected post-observation failure")


def test_cl_api_port_preserves_aborted_recording(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CL_SDK_VISUALISATION", "0")
    monkeypatch.setenv("CL_SDK_ACCELERATED_TIME", "1")
    config, decoder = load_fixture_pair()
    port = _FailAfterFirstFeaturePort(
        run_id="cl-api-e3-aborted",
        output_directory=tmp_path,
        runtime_config=technical_e3_runtime_config(),
    )

    with pytest.raises(RuntimeError, match="injected post-observation failure"):
        run_session(
            run_id="cl-api-e3-aborted",
            config=config,
            decoder=decoder,
            port=port,
        )

    assert port.recording_path is not None
    evidence = reconstruct_cl_recording(
        port.recording_path,
        expected_run_id="cl-api-e3-aborted",
        expected_runtime_kind="sdk_simulator",
        expected_stream_names=STREAM_NAMES,
    )
    assert evidence.terminal_state == "aborted"
    assert len(evidence.feature_records) == 1
    assert len(evidence.decision_records) == 0
