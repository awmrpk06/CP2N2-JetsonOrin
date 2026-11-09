"""Official CL application shell for the deterministic E3 campaign."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import override

import cl
from cl.app import BaseApplication, OutputType, RunSummary

from .biopattern_gate.cl_api_port import (
    CLApiBioPatternGatePort,
    STREAM_NAMES,
    technical_e3_runtime_config,
)
from .biopattern_gate.cl_recording import (
    reconstruct_cl_recording,
    verify_online_result_against_recording,
)
from .biopattern_gate.config import BioPatternGateConfig
from .biopattern_gate.decoder import load_decoder
from .biopattern_gate.runner import run_session
from .config import BioPatternGateApplicationConfig


class BioPatternGateApplication(
    BaseApplication[BioPatternGateApplicationConfig]
):
    """E3 shell exercising the documented CL API against its SDK simulator."""

    @override
    def run(
        self,
        config: BioPatternGateApplicationConfig,
        output_directory: str,
    ) -> RunSummary:
        if not cl.is_simulator():
            raise RuntimeError(
                "technical-e3 is simulator-only; no provider-approved CL1 preset exists"
            )
        output = Path(output_directory)
        output.mkdir(parents=True, exist_ok=True)
        source_root = Path(__file__).parent
        source_root_text = str(source_root)
        if source_root_text not in sys.path:
            # The CL SDK constructs custom simulator sources in a child
            # process. Exposing the bundled source root makes the factory
            # importable there without installing an additional package.
            sys.path.insert(0, source_root_text)
        package_root = source_root / "biopattern_gate"
        core_config = BioPatternGateConfig.model_validate_json(
            (
                package_root / "presets" / "simulator" / "technical-e3.json"
            ).read_text(encoding="utf-8")
        )
        decoder = load_decoder(
            package_root
            / "artifacts"
            / "simulator"
            / "pattern-gate-linear-v1.json"
        )
        if core_config.sha256() != config.config_sha256:
            raise RuntimeError("bundled configuration hash mismatch")
        if decoder.sha256() != config.decoder_sha256:
            raise RuntimeError("bundled decoder hash mismatch")

        port = CLApiBioPatternGatePort(
            run_id=config.run_id,
            output_directory=output,
            runtime_config=technical_e3_runtime_config(
                source_factory=(
                    "biopattern_gate.cl_sdk_source:"
                    "create_biopattern_gate_source"
                )
            ),
        )
        result = run_session(
            run_id=config.run_id,
            config=core_config,
            decoder=decoder,
            port=port,
        )
        if port.recording_path is None:
            raise RuntimeError("CL SDK run produced no recording path")
        recording_evidence = reconstruct_cl_recording(
            port.recording_path,
            expected_run_id=config.run_id,
            expected_runtime_kind="sdk_simulator",
            expected_stream_names=STREAM_NAMES,
        )
        verify_online_result_against_recording(result, recording_evidence)
        summary = {
            **result.summary(),
            "substrate_executed": False,
            "pnn_evidence": False,
            "biological_claim": False,
            "attestation": "cl.is_simulator() == true",
            "cl_api_exercised": True,
            "recording_artifact": {
                "filename": port.recording_path.name,
                "stim_count": recording_evidence.stim_count,
                "spike_count": recording_evidence.spike_count,
                "terminal_state": recording_evidence.terminal_state,
                "online_offline_match": True,
            },
        }
        result_document = {
            **summary,
            "trials": [asdict(trial) for trial in result.trials],
        }
        (output / "result.json").write_text(
            json.dumps(result_document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        content = (
            "# BioPattern Gate — E3 technical run\n\n"
            f"- Trials: {summary['trial_count']}\n"
            f"- Scored trials: {summary['scored_trial_count']}\n"
            f"- Sham trials: {summary['sham_trial_count']}\n"
            f"- Deterministic pipeline accuracy: {summary['accuracy']:.3f}\n"
            f"- Native HDF5 stims: {recording_evidence.stim_count}\n"
            f"- Native HDF5 spikes: {recording_evidence.spike_count}\n"
            "- Online/offline decision match: yes\n"
            f"- Config: `{summary['config_sha256']}`\n"
            f"- Decoder: `{summary['decoder_sha256']}`\n\n"
            "**This simulator run is technical evidence only. It is not a "
            "biological PNN result.**\n"
        )
        (output / "summary.md").write_text(content, encoding="utf-8")
        return RunSummary(
            type=OutputType.MARKDOWN,
            content=content,
            runtime_kind="sdk_simulator",
            evidence_ceiling="E3",
            cl_api_exercised=True,
            recording_artifact=summary["recording_artifact"],
            result=summary,
        )

    @staticmethod
    @override
    def config_class() -> type[BioPatternGateApplicationConfig]:
        return BioPatternGateApplicationConfig
