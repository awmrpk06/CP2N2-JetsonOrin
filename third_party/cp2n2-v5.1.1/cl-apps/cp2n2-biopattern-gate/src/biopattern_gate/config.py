"""Frozen, fail-closed configuration contract for BioPattern Gate.

The contract deliberately models logical groups and approved references rather
than CL1 electrodes or stimulation primitives. Provider-specific values belong
to a separately attested preset that does not exist until access and approval
are available.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CONFIG_VERSION = "1.0"
SIMULATOR_PRESET_IDS = frozenset({"technical-e3"})
# Deliberately empty until a reviewed provider contract and preset exist.
PROVIDER_APPROVED_PRESET_IDS: frozenset[str] = frozenset()


class FrozenModel(BaseModel):
    """Immutable strict base for configuration and protocol records."""

    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)


class RunMode(str, Enum):
    TECHNICAL_E3 = "technical_e3"
    MAPPING_E5 = "mapping_e5"
    PILOT_E5 = "pilot_e5"
    MEASUREMENT_E5 = "measurement_e5"


class PresetNamespace(str, Enum):
    SIMULATOR = "simulator"
    PROVIDER_APPROVED = "provider_approved"


class RuntimeKind(str, Enum):
    SDK_SIMULATOR = "sdk_simulator"
    CLOUD_SIMULATOR = "cloud_simulator"
    CL1 = "cl1"


class EvidenceLevel(str, Enum):
    E3 = "E3"
    E5 = "E5"


class EvidenceContext(FrozenModel):
    runtime_kind: RuntimeKind
    evidence_ceiling: EvidenceLevel
    provider_contract_verified: bool = False
    approval_refs: tuple[str, ...] = ()
    calibration_ref: str | None = None


class PatternDefinition(FrozenModel):
    """One hidden class expressed only through logical input groups."""

    label: Literal["A", "B"]
    sequence: tuple[str, str]
    stimulus_design_ref: str = Field(min_length=1)
    charge_equivalence_token: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sequence(self) -> "PatternDefinition":
        if self.sequence[0] == self.sequence[1]:
            raise ValueError("a pattern must use two distinct logical input groups")
        return self


class ScheduleConfig(FrozenModel):
    seed: int = 20260728
    block_count: int = Field(default=4, ge=1, le=100)
    trials_per_class_per_block: int = Field(default=4, ge=1, le=100)
    shams_per_block: int = Field(default=1, ge=0, le=20)

    @property
    def trial_count(self) -> int:
        return self.block_count * (
            2 * self.trials_per_class_per_block + self.shams_per_block
        )


class TimingConfig(FrozenModel):
    """Simulator timing; hardware values require a provider-approved preset."""

    inter_step_ms: int = Field(gt=0)
    artefact_blanking_ms: int = Field(ge=0)
    observation_start_ms: int = Field(ge=0)
    observation_duration_ms: int = Field(gt=0)
    inter_trial_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def observation_must_follow_blanking(self) -> "TimingConfig":
        if self.observation_start_ms < self.artefact_blanking_ms:
            raise ValueError("observation window overlaps artefact blanking")
        return self

    @property
    def estimated_trial_duration_ms(self) -> int:
        return (
            self.inter_step_ms
            + self.observation_start_ms
            + self.observation_duration_ms
            + self.inter_trial_ms
        )


class FeatureConfig(FrozenModel):
    schema_version: Literal["pattern-gate-features-v1"] = "pattern-gate-features-v1"
    bin_width_ms: int = Field(gt=0)
    include_first_spike_latency: bool = True
    include_active_group_count: bool = True
    include_burst_count: bool = False


class DecoderConfig(FrozenModel):
    kind: Literal["frozen_linear"] = "frozen_linear"
    artifact_ref: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_run_ref: str = Field(min_length=1)
    decision_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)


class RecordingConfig(FrozenModel):
    policy_ref: str = Field(min_length=1)
    retain_raw_events: bool = True
    retain_feature_vectors: bool = True
    retain_state_transitions: bool = True


class SafetyConfig(FrozenModel):
    admission_policy_ref: str = Field(min_length=1)
    abort_policy_ref: str = Field(min_length=1)
    cooldown_policy_ref: str = Field(min_length=1)
    completeness_policy_ref: str = Field(min_length=1)


class BioPatternGateConfig(FrozenModel):
    """Versioned application configuration with cross-field validation."""

    name: str = Field(default="cp2n2-biopattern-gate", min_length=1)
    config_version: Literal["1.0"] = CONFIG_VERSION
    protocol_version: Literal["pattern-gate-v1"] = "pattern-gate-v1"
    timeout_s: int = Field(gt=0)
    mode: RunMode
    preset_namespace: PresetNamespace
    preset_id: str = Field(min_length=1)
    input_group_refs: tuple[str, str]
    readout_group_refs: tuple[str, ...] = Field(min_length=1)
    pattern_a: PatternDefinition
    pattern_b: PatternDefinition
    schedule: ScheduleConfig
    timing: TimingConfig
    features: FeatureConfig
    decoder: DecoderConfig
    recording: RecordingConfig
    safety: SafetyConfig
    evidence: EvidenceContext

    @model_validator(mode="after")
    def validate_protocol_contract(self) -> "BioPatternGateConfig":
        inputs = set(self.input_group_refs)
        readouts = set(self.readout_group_refs)
        if len(inputs) != 2:
            raise ValueError("exactly two distinct logical input groups are required")
        if not readouts:
            raise ValueError("at least one logical readout group is required")
        if inputs & readouts:
            raise ValueError("input and readout groups must be disjoint")

        if self.pattern_a.label != "A" or self.pattern_b.label != "B":
            raise ValueError("pattern labels must be A and B")
        if set(self.pattern_a.sequence) != inputs:
            raise ValueError("pattern A must use both configured input groups once")
        if self.pattern_b.sequence != tuple(reversed(self.pattern_a.sequence)):
            raise ValueError("pattern B must be the temporal reverse of pattern A")
        if self.pattern_a.stimulus_design_ref != self.pattern_b.stimulus_design_ref:
            raise ValueError("patterns must use the same stimulus design")
        if (
            self.pattern_a.charge_equivalence_token
            != self.pattern_b.charge_equivalence_token
        ):
            raise ValueError("patterns must carry the same charge-equivalence token")

        estimated_s = (
            self.schedule.trial_count
            * self.timing.estimated_trial_duration_ms
            / 1000.0
        )
        if self.timeout_s < estimated_s:
            raise ValueError("timeout is shorter than the estimated trial schedule")

        if self.mode == RunMode.TECHNICAL_E3:
            if self.preset_namespace != PresetNamespace.SIMULATOR:
                raise ValueError("technical_e3 requires the simulator namespace")
            if self.preset_id not in SIMULATOR_PRESET_IDS:
                raise ValueError("unknown simulator preset")
            if (
                self.evidence.runtime_kind != RuntimeKind.SDK_SIMULATOR
                or self.evidence.evidence_ceiling != EvidenceLevel.E3
            ):
                raise ValueError("technical_e3 is limited to SDK simulator evidence E3")
        else:
            if self.preset_namespace != PresetNamespace.PROVIDER_APPROVED:
                raise ValueError("E5 modes require the provider-approved namespace")
            if self.preset_id not in PROVIDER_APPROVED_PRESET_IDS:
                raise ValueError(
                    "provider-approved preset is not registered; E5 is unavailable"
                )
            if self.evidence.runtime_kind != RuntimeKind.CL1:
                raise ValueError("E5 modes require an attested CL1 runtime")
            if self.evidence.evidence_ceiling != EvidenceLevel.E5:
                raise ValueError("E5 modes require an E5 evidence context")
            if not self.evidence.provider_contract_verified:
                raise ValueError("E5 modes require a verified provider contract")
            if not self.evidence.approval_refs or not self.evidence.calibration_ref:
                raise ValueError("E5 modes require approval and calibration references")
        return self

    def canonical_json(self) -> str:
        """Return deterministic JSON used for provenance and admission."""

        return self.model_dump_json(by_alias=True, exclude_none=False)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
