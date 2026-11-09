from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from applications.biopattern_gate.config import BioPatternGateConfig


PRESET = (
    Path(__file__).parents[1]
    / "applications"
    / "biopattern_gate"
    / "presets"
    / "simulator"
    / "technical-e3.json"
)


def load_config_dict() -> dict:
    return json.loads(PRESET.read_text(encoding="utf-8"))


def test_simulator_preset_is_valid_frozen_and_hashable() -> None:
    config = BioPatternGateConfig.model_validate(load_config_dict())

    assert config.schedule.trial_count == 14
    assert len(config.sha256()) == 64
    assert config.sha256() == BioPatternGateConfig.model_validate(
        load_config_dict()
    ).sha256()
    with pytest.raises(ValidationError):
        config.timeout_s = 999


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda raw: raw["readout_group_refs"].append("sim-input-left"),
            "disjoint",
        ),
        (
            lambda raw: raw["pattern_b"].update(
                {"sequence": ["sim-input-left", "sim-input-right"]}
            ),
            "temporal reverse",
        ),
        (
            lambda raw: raw["pattern_b"].update(
                {"charge_equivalence_token": "different-charge"}
            ),
            "charge-equivalence",
        ),
        (
            lambda raw: raw["timing"].update({"observation_start_ms": 4}),
            "artefact blanking",
        ),
        (
            lambda raw: raw.update({"timeout_s": 1}),
            "shorter",
        ),
    ],
)
def test_invalid_protocols_fail_closed(mutation, message: str) -> None:
    raw = load_config_dict()
    mutation(raw)

    with pytest.raises(ValidationError, match=message):
        BioPatternGateConfig.model_validate(raw)


def test_unknown_configuration_fields_are_rejected() -> None:
    raw = load_config_dict()
    raw["agent_selected_electrode"] = 42

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BioPatternGateConfig.model_validate(raw)


def test_hardware_mode_cannot_reuse_simulator_preset() -> None:
    raw = load_config_dict()
    raw["mode"] = "pilot_e5"

    with pytest.raises(ValidationError, match="provider-approved"):
        BioPatternGateConfig.model_validate(raw)


def test_hardware_mode_is_unavailable_until_a_provider_preset_is_registered() -> None:
    raw = load_config_dict()
    raw["mode"] = "pilot_e5"
    raw["preset_namespace"] = "provider_approved"
    raw["evidence"] = {
        "runtime_kind": "cl1",
        "evidence_ceiling": "E5",
        "provider_contract_verified": True,
        "approval_refs": [],
        "calibration_ref": None,
    }

    with pytest.raises(ValidationError, match="not registered"):
        BioPatternGateConfig.model_validate(raw)
