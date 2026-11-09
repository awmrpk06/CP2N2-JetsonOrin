"""Typed descriptor models for the CP²N² prototype.

These models define the substrate-aware metadata needed by the control plane
to discover, compare, and invoke heterogeneous PNN-like backends.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SubstrateClass(str, Enum):
    """High-level class of the represented physical or quasi-physical substrate."""

    CHEMICAL = "chemical"
    DNA = "dna"
    WETWARE = "wetware"
    MEMRISTIVE = "memristive"
    PHOTONIC = "photonic"
    EDGE_ACCELERATOR = "edge_accelerator"
    GENERIC_SIMULATION = "generic_simulation"


class SignalModality(str, Enum):
    """Primary physical or logical modality used for inputs/outputs."""

    CONCENTRATION = "concentration"
    SPIKES = "spikes"
    ANALOG_VECTOR = "analog_vector"
    DIGITAL_VECTOR = "digital_vector"
    TENSOR = "tensor"
    CONTROL_SIGNAL = "control_signal"
    TELEMETRY_STREAM = "telemetry_stream"


class IOEncoding(str, Enum):
    """How the data is represented at the interface level."""

    SCALAR = "scalar"
    VECTOR = "vector"
    MATRIX = "matrix"
    TIME_SERIES = "time_series"
    EVENT_STREAM = "event_stream"
    JSON = "json"
    BINARY = "binary"


class TimingRegime(str, Enum):
    """Coarse-grained execution regime used for scheduling and matching."""

    MICROSECONDS = "microseconds"
    MILLISECONDS = "milliseconds"
    SECONDS = "seconds"
    MINUTES = "minutes"
    VARIABLE = "variable"


class ObservabilityLevel(str, Enum):
    """How directly the internal state of a backend can be observed."""

    LOW = "low"
    PARTIAL = "partial"
    HIGH = "high"


class TrainingMode(str, Enum):
    """Supported learning/programming mode for a backend."""

    NONE = "none"
    PRETRAINED = "pretrained"
    EX_SITU = "ex_situ"
    IN_SITU = "in_situ"
    HYBRID = "hybrid"


class ResetMode(str, Enum):
    """Lifecycle reset/recovery mode for a backend."""

    NONE = "none"
    SOFT_RESET = "soft_reset"
    HARD_RESET = "hard_reset"
    FLUSH = "flush"
    RECHARGE = "recharge"
    REST = "rest"
    RECALIBRATE = "recalibrate"


class TenancyModel(str, Enum):
    """How a backend is intended to be shared."""

    SINGLE_TENANT = "single_tenant"
    MULTI_TENANT = "multi_tenant"
    RESERVED = "reserved"


class Locality(str, Enum):
    """Deployment locality from the control plane perspective."""

    EDGE = "edge"
    FOG = "fog"
    CLOUD = "cloud"
    LAB = "lab"
    LOCAL = "local"


class IOContract(BaseModel):
    """Describes input or output interface constraints."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: str = Field(..., description="Human-readable interface name.")
    modality: SignalModality = Field(..., description="Primary signal modality.")
    encoding: IOEncoding = Field(..., description="Interface encoding.")
    shape_hint: list[int] | None = Field(
        default=None,
        description="Optional shape hint, e.g. [128] for a vector or [32, 32] for a matrix.",
    )
    units: str | None = Field(default=None, description="Optional physical or logical units.")
    required: bool = Field(default=True, description="Whether the interface is mandatory.")
    description: str | None = Field(default=None, description="Additional interface details.")

    @field_validator("shape_hint")
    @classmethod
    def validate_shape_hint(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if not value:
            raise ValueError("shape_hint must not be an empty list.")
        if any(d <= 0 for d in value):
            raise ValueError("shape_hint dimensions must be positive integers.")
        return value


class TimingContract(BaseModel):
    """Execution-time characteristics exposed to the matcher and scheduler."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    regime: TimingRegime = Field(..., description="Coarse execution regime.")
    typical_latency_ms: float = Field(..., ge=0.0, description="Expected latency in milliseconds.")
    latency_jitter_ms: float = Field(default=0.0, ge=0.0, description="Expected jitter in milliseconds.")
    warmup_required: bool = Field(default=False, description="Whether the backend requires warmup/preparation.")
    streaming_supported: bool = Field(default=False, description="Whether streaming interaction is supported.")

    @model_validator(mode="after")
    def validate_jitter(self) -> "TimingContract":
        if self.latency_jitter_ms > self.typical_latency_ms and self.typical_latency_ms > 0:
            raise ValueError("latency_jitter_ms should not exceed typical_latency_ms for this prototype.")
        return self


class LifecycleContract(BaseModel):
    """Lifecycle operations and constraints for a backend."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    supported_reset_modes: list[ResetMode] = Field(default_factory=list)
    reprogrammable: bool = Field(default=False)
    recalibration_supported: bool = Field(default=False)
    stateful: bool = Field(default=True)
    notes: str | None = Field(default=None)

    @field_validator("supported_reset_modes")
    @classmethod
    def deduplicate_reset_modes(cls, value: list[ResetMode]) -> list[ResetMode]:
        return list(dict.fromkeys(value))


class TelemetryField(BaseModel):
    """One telemetry metric exposed by a backend."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    name: str
    units: str | None = None
    description: str
    lower_is_better: bool | None = None


class TelemetryContract(BaseModel):
    """Describes telemetry made available by the backend."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    metrics: list[TelemetryField] = Field(default_factory=list)
    supports_health_status: bool = Field(default=True)
    supports_confidence: bool = Field(default=True)
    supports_drift_reporting: bool = Field(default=True)
    supports_age_of_information: bool = Field(default=False)


class TwinBinding(BaseModel):
    """Link between a physical/logical backend and its digital twin representation."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    twin_kind: str = Field(..., description="Type of twin, e.g. ode_simulation or replay_model.")
    fidelity_level: str = Field(..., description="Human-readable fidelity label.")
    calibration_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    last_calibrated_at: str | None = Field(default=None, description="ISO 8601 timestamp if known.")
    twin_notes: str | None = Field(default=None)


class PolicyConstraints(BaseModel):
    """Policy metadata that constrains scheduling and use."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    locality: Locality = Field(default=Locality.LOCAL)
    tenancy: TenancyModel = Field(default=TenancyModel.SINGLE_TENANT)
    safety_notes: str | None = Field(default=None)
    exclusive_access_required: bool = Field(default=False)
    human_supervision_required: bool = Field(default=False)


class CapabilityDescriptor(BaseModel):
    """Backend capabilities relevant to control-plane decisions."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    substrate_class: SubstrateClass
    supported_task_types: list[str] = Field(default_factory=list)
    training_mode: TrainingMode = Field(default=TrainingMode.NONE)
    observability: ObservabilityLevel = Field(default=ObservabilityLevel.PARTIAL)
    stochastic: bool = Field(default=False)
    resettable: bool = Field(default=True)
    programmable: bool = Field(default=True)
    health_sensitive: bool = Field(default=False)
    repeated_invocation_supported: bool = Field(default=True)

    @field_validator("supported_task_types")
    @classmethod
    def normalize_task_types(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value if item.strip()]
        if not normalized:
            raise ValueError("supported_task_types must contain at least one task type.")
        return list(dict.fromkeys(normalized))


class SubstrateDescriptor(BaseModel):
    """Top-level descriptor published by a CP²N² backend adapter."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    backend_id: str = Field(..., description="Stable backend identifier.")
    display_name: str = Field(..., description="Human-readable backend name.")
    version: str = Field(default="0.1.0")
    description: str
    input_contracts: list[IOContract] = Field(default_factory=list)
    output_contracts: list[IOContract] = Field(default_factory=list)
    timing: TimingContract
    lifecycle: LifecycleContract
    telemetry: TelemetryContract
    twin_binding: TwinBinding
    policy: PolicyConstraints
    capability: CapabilityDescriptor
    custom_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("backend_id")
    @classmethod
    def validate_backend_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("backend_id must not be empty.")
        return stripped

    @model_validator(mode="after")
    def validate_contracts(self) -> "SubstrateDescriptor":
        if not self.input_contracts:
            raise ValueError("At least one input contract must be defined.")
        if not self.output_contracts:
            raise ValueError("At least one output contract must be defined.")
        return self

    def supports_task_type(self, task_type: str) -> bool:
        """Return True if the backend declares support for the given task type."""
        return task_type.strip().lower() in self.capability.supported_task_types

    def to_public_dict(self) -> dict[str, Any]:
        """Return a serialized representation suitable for registry publication."""
        return self.model_dump(mode="json")
