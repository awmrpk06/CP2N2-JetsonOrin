"""Versioned Physical Neural Resource Contract for CP²N².

The contract is deliberately substrate-neutral.  It wraps the existing
capability descriptor with runtime evidence, state, telemetry provenance,
safety, access, cost, and data-governance information needed for conservative
admission decisions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from descriptors.capability_schema import SubstrateDescriptor


CONTRACT_SCHEMA_VERSION = "1.0"
SUPPORTED_CONTRACT_SCHEMA_VERSIONS = frozenset({CONTRACT_SCHEMA_VERSION})


class EvidenceLevel(str, Enum):
    """Strength of evidence behind a published resource."""

    E0_MOCK = "E0"
    E1_SYNTHETIC_TWIN = "E1"
    E2_SAME_HOST_SERVICE = "E2"
    E3_SDK_SIMULATOR = "E3"
    E4_REMOTE_SIMULATOR = "E4"
    E5_PHYSICAL_HARDWARE = "E5"


class RuntimeKind(str, Enum):
    """Attested execution environment, independent of substrate family."""

    UNKNOWN = "unknown"
    MOCK = "mock"
    SYNTHETIC_TWIN = "synthetic_twin"
    SAME_HOST_SERVICE = "same_host_service"
    SDK_SIMULATOR = "sdk_simulator"
    REMOTE_SIMULATOR = "remote_simulator"
    PHYSICAL_HARDWARE = "physical_hardware"


EXPECTED_EVIDENCE_LEVEL: dict[RuntimeKind, EvidenceLevel] = {
    RuntimeKind.UNKNOWN: EvidenceLevel.E0_MOCK,
    RuntimeKind.MOCK: EvidenceLevel.E0_MOCK,
    RuntimeKind.SYNTHETIC_TWIN: EvidenceLevel.E1_SYNTHETIC_TWIN,
    RuntimeKind.SAME_HOST_SERVICE: EvidenceLevel.E2_SAME_HOST_SERVICE,
    RuntimeKind.SDK_SIMULATOR: EvidenceLevel.E3_SDK_SIMULATOR,
    RuntimeKind.REMOTE_SIMULATOR: EvidenceLevel.E4_REMOTE_SIMULATOR,
    RuntimeKind.PHYSICAL_HARDWARE: EvidenceLevel.E5_PHYSICAL_HARDWARE,
}


class ObservationSource(str, Enum):
    """Provenance of a dynamic field."""

    OBSERVED = "observed"
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    CONFIGURED = "configured"


class ResourceLifecycleState(str, Enum):
    """Normalized resource lifecycle state."""

    UNKNOWN = "unknown"
    DISCOVERED = "discovered"
    READY = "ready"
    RESERVED = "reserved"
    PREPARING = "preparing"
    RUNNING = "running"
    VALIDATING = "validating"
    COOLDOWN = "cooldown"
    ABORTING = "aborting"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNREACHABLE = "unreachable"


class UncertaintyKind(str, Enum):
    """Representation used for uncertainty attached to an observation."""

    UNKNOWN = "unknown"
    NONE = "none"
    STANDARD_DEVIATION = "standard_deviation"
    INTERVAL = "interval"
    QUALITATIVE = "qualitative"


class BillingModel(str, Enum):
    FREE = "free"
    PER_INVOCATION = "per_invocation"
    PER_SECOND = "per_second"
    RESERVATION = "reservation"
    PROVIDER_DEFINED = "provider_defined"
    UNKNOWN = "unknown"


class Uncertainty(BaseModel):
    """Explicit uncertainty metadata; unknown is never encoded as zero."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: UncertaintyKind
    value: float | str | list[float] | None = None
    unit: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_uncertainty(self) -> "Uncertainty":
        if self.kind in {UncertaintyKind.UNKNOWN, UncertaintyKind.NONE} and self.value is not None:
            raise ValueError(f"uncertainty value must be null when kind is '{self.kind}'.")
        if self.kind == UncertaintyKind.INTERVAL:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("interval uncertainty requires a two-element value.")
            if self.value[0] > self.value[1]:
                raise ValueError("interval uncertainty lower bound must not exceed upper bound.")
        return self


class TelemetryObservation(BaseModel):
    """One value together with mandatory provenance and freshness fields."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    value: Any | None
    unit: str | None
    source: ObservationSource
    observed_at: datetime | None
    received_at: datetime
    uncertainty: Uncertainty
    valid_until: datetime | None

    @field_validator("observed_at", "received_at", "valid_until")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_temporal_semantics(self) -> "TelemetryObservation":
        if self.source != ObservationSource.CONFIGURED and self.observed_at is None:
            raise ValueError("non-configured observations require observed_at.")
        if self.observed_at is not None and self.observed_at > self.received_at:
            raise ValueError("observed_at must not be later than received_at.")
        if self.valid_until is not None:
            baseline = self.observed_at or self.received_at
            if self.valid_until < baseline:
                raise ValueError("valid_until must not predate the observation.")
        return self

    def is_fresh(self, at: datetime | None = None) -> bool:
        """Return whether the observation is still valid at *at*."""
        if self.valid_until is None:
            return True
        point = at or datetime.now(timezone.utc)
        return point <= self.valid_until


class ResourceIdentity(BaseModel):
    """Stable identities at control-plane, provider, adapter, and substrate levels."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(..., min_length=1)
    provider_id: str = Field(..., min_length=1)
    adapter_id: str = Field(..., min_length=1)
    substrate_id: str | None = None
    hardware_id: str | None = None


class RuntimeEvidence(BaseModel):
    """Evidence supporting the claimed runtime kind."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    runtime_kind: RuntimeKind
    evidence_level: EvidenceLevel
    attestation_method: str = Field(..., min_length=1)
    attested_at: datetime | None
    attestation_details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attested_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("attested_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_evidence_level(self) -> "RuntimeEvidence":
        expected = EXPECTED_EVIDENCE_LEVEL[RuntimeKind(self.runtime_kind)]
        if self.evidence_level != expected:
            raise ValueError(
                f"runtime_kind '{self.runtime_kind}' requires evidence_level '{expected.value}'."
            )
        if self.runtime_kind != RuntimeKind.UNKNOWN and self.attested_at is None:
            raise ValueError("known runtime kinds require attested_at.")
        return self


class ResourceState(BaseModel):
    """Dynamic state used by schedulers and policy checks."""

    model_config = ConfigDict(extra="forbid")

    lifecycle: TelemetryObservation | None = None
    occupancy: TelemetryObservation | None = None
    health: TelemetryObservation | None = None
    calibration: TelemetryObservation | None = None
    drift: TelemetryObservation | None = None


class SafetyLimit(BaseModel):
    """One configured, provider-reported, or observed hard operational limit."""

    model_config = ConfigDict(extra="forbid")

    minimum: float | None = None
    maximum: float | None = None
    unit: str = Field(..., min_length=1)
    source: ObservationSource
    description: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "SafetyLimit":
        if self.minimum is None and self.maximum is None:
            raise ValueError("a safety limit requires at least one bound.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("safety limit minimum must not exceed maximum.")
        return self


class SafetyContract(BaseModel):
    """Operations and constraints that must be known before execution."""

    model_config = ConfigDict(extra="forbid")

    permitted_operations: list[str] = Field(default_factory=list)
    hard_limits: dict[str, SafetyLimit] = Field(default_factory=dict)
    human_supervision_required: bool | None = None
    operator_acknowledgement_required: bool | None = None
    emergency_stop_supported: bool | None = None
    exclusive_access_required: bool | None = None
    notes: str | None = None

    @field_validator("permitted_operations")
    @classmethod
    def normalize_operations(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value if item.strip()]
        return list(dict.fromkeys(normalized))


class AccessContract(BaseModel):
    """How the resource can be reached and allocated."""

    model_config = ConfigDict(extra="forbid")

    locality: str = Field(..., min_length=1)
    tenancy: str = Field(..., min_length=1)
    authentication_required: bool | None = None
    authorization_scope: str | None = None
    reservation_required: bool | None = None


class CostContract(BaseModel):
    """Commercial or accounting properties without assuming a provider model."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    billing_model: BillingModel = BillingModel.UNKNOWN
    currency: str | None = None
    estimated_cost: TelemetryObservation | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_currency(self) -> "CostContract":
        if self.estimated_cost is not None and not self.currency:
            raise ValueError("currency is required when estimated_cost is present.")
        return self


class DataGovernanceContract(BaseModel):
    """Input/output handling and retention declarations."""

    model_config = ConfigDict(extra="forbid")

    stores_inputs: bool | None = None
    stores_outputs: bool | None = None
    retention_days: int | None = Field(default=None, ge=0)
    data_residency: list[str] = Field(default_factory=list)
    provider_terms_uri: str | None = None
    notes: str | None = None


class PhysicalNeuralResourceContract(BaseModel):
    """Top-level, versioned resource publication envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    published_at: datetime
    identity: ResourceIdentity
    evidence: RuntimeEvidence
    capabilities: SubstrateDescriptor
    state: ResourceState
    telemetry: dict[str, TelemetryObservation] = Field(default_factory=dict)
    safety: SafetyContract | None = None
    access: AccessContract | None = None
    cost: CostContract | None = None
    data_governance: DataGovernanceContract | None = None

    @field_validator("schema_version")
    @classmethod
    def require_supported_version(cls, value: str) -> str:
        if value not in SUPPORTED_CONTRACT_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version '{value}'; "
                f"supported versions: {sorted(SUPPORTED_CONTRACT_SCHEMA_VERSIONS)}"
            )
        return value

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_identity_alignment(self) -> "PhysicalNeuralResourceContract":
        if self.identity.resource_id != self.capabilities.backend_id:
            raise ValueError("identity.resource_id must equal capabilities.backend_id.")
        return self


class ContractAdmissionResult(BaseModel):
    """Conservative schedulability decision for a structurally valid contract."""

    model_config = ConfigDict(extra="forbid")

    admissible: bool
    reasons: list[str] = Field(default_factory=list)


class UnsupportedContractVersionError(ValueError):
    """Raised when no explicit migration path exists."""


def migrate_contract_payload(
    payload: dict[str, Any],
    *,
    target_version: str = CONTRACT_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Return a defensive copy when no migration is required.

    A1 intentionally provides no implicit migration from unversioned or
    pre-contract descriptors.  Future migrations must be explicit,
    deterministic functions and may not silently cross a major version.
    """

    source_version = payload.get("schema_version")
    if target_version not in SUPPORTED_CONTRACT_SCHEMA_VERSIONS:
        raise UnsupportedContractVersionError(
            f"unsupported target contract version '{target_version}'."
        )
    if source_version != target_version:
        raise UnsupportedContractVersionError(
            f"no migration path from '{source_version}' to '{target_version}'."
        )
    return deepcopy(payload)


def assess_contract_admission(
    contract: PhysicalNeuralResourceContract,
    *,
    operation: str = "invoke",
    at: datetime | None = None,
) -> ContractAdmissionResult:
    """Conservatively decide whether *operation* may be scheduled."""

    reasons: list[str] = []
    point = at or datetime.now(timezone.utc)
    operation_name = operation.strip().lower()

    if contract.evidence.runtime_kind == RuntimeKind.UNKNOWN:
        reasons.append("runtime kind is not attested")

    if contract.evidence.runtime_kind == RuntimeKind.PHYSICAL_HARDWARE:
        if not contract.identity.hardware_id:
            reasons.append("physical hardware requires identity.hardware_id")
        if contract.evidence.attested_at is None:
            reasons.append("physical hardware requires a timestamped runtime attestation")

    if contract.safety is None:
        reasons.append("safety contract is missing")
    else:
        if operation_name not in contract.safety.permitted_operations:
            reasons.append(f"operation '{operation_name}' is not explicitly permitted")
        if contract.safety.human_supervision_required is None:
            reasons.append("human supervision requirement is unknown")
        if contract.safety.exclusive_access_required is None:
            reasons.append("exclusive-access requirement is unknown")
        if contract.evidence.runtime_kind == RuntimeKind.PHYSICAL_HARDWARE:
            if not contract.safety.hard_limits:
                reasons.append("physical hardware has no declared hard limits")
            if contract.safety.emergency_stop_supported is None:
                reasons.append("physical hardware emergency-stop support is unknown")
            if contract.safety.operator_acknowledgement_required is None:
                reasons.append("physical hardware operator acknowledgement is unknown")

    if contract.state.lifecycle is None:
        reasons.append("lifecycle state is missing")
    else:
        lifecycle = contract.state.lifecycle
        if not lifecycle.is_fresh(point):
            reasons.append("lifecycle state is stale")
        if lifecycle.value not in {
            ResourceLifecycleState.READY.value,
            ResourceLifecycleState.DISCOVERED.value,
        }:
            reasons.append(f"lifecycle state is '{lifecycle.value}'")

    if contract.state.health is None:
        reasons.append("health state is missing")
    else:
        health = contract.state.health
        if not health.is_fresh(point):
            reasons.append("health state is stale")
        if health.value not in {"ready", "healthy", "unknown"}:
            reasons.append(f"health state is '{health.value}'")
        if (
            contract.evidence.runtime_kind == RuntimeKind.PHYSICAL_HARDWARE
            and health.value == "unknown"
        ):
            reasons.append("physical hardware health is unknown")

    return ContractAdmissionResult(admissible=not reasons, reasons=reasons)
