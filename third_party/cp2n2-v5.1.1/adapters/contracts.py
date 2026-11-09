"""Versioned contracts for the A5 adapter/runtime composition boundary."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from descriptors.capability_schema import SubstrateDescriptor
from descriptors.resource_contract import (
    EXPECTED_EVIDENCE_LEVEL,
    EvidenceLevel,
    RuntimeKind,
)


ADAPTER_ARCHITECTURE_VERSION = "1.0"


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ExecutionLocation(str, Enum):
    IN_PROCESS = "in_process"
    SAME_HOST_SERVICE = "same_host_service"
    REMOTE_PROVIDER = "remote_provider"


class ReservationMode(str, Enum):
    CONTROL_PLANE_LEASE = "control_plane_lease"
    PROVIDER_MANAGED = "provider_managed"
    NONE = "none"


class DeploymentMode(str, Enum):
    PREDEPLOYED = "predeployed"
    ON_DEMAND = "on_demand"
    PROVIDER_MANAGED = "provider_managed"


class ControlOperation(str, Enum):
    DISCOVER = "discover"
    RESERVE = "reserve"
    DEPLOY = "deploy"
    STATE = "state"
    ABORT = "abort"
    ARTIFACTS = "artifacts"


class RuntimeOperation(str, Enum):
    PREPARE = "prepare"
    EXECUTE = "execute"
    TELEMETRY = "telemetry"
    RESET = "reset"
    RECALIBRATE = "recalibrate"
    ABORT = "abort"
    ARTIFACTS = "artifacts"


class RuntimeArtifact(StrictContractModel):
    """Normalized artifact reference produced by a substrate runtime."""

    artifact_id: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=128)
    uri: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeCapabilityDeclaration(StrictContractModel):
    """Capabilities of the time-critical substrate-side runtime."""

    runtime_id: str = Field(min_length=1, max_length=256)
    runtime_kind: RuntimeKind
    execution_location: ExecutionLocation
    operations: set[RuntimeOperation]
    artifact_kinds: list[str] = Field(default_factory=list)
    time_critical_execution_local: bool
    provider_abort_supported: bool = False

    @model_validator(mode="after")
    def validate_runtime_operations(self) -> "RuntimeCapabilityDeclaration":
        required = {
            RuntimeOperation.PREPARE.value,
            RuntimeOperation.EXECUTE.value,
            RuntimeOperation.TELEMETRY.value,
        }
        if not required.issubset(set(self.operations)):
            raise ValueError(
                "runtime operations must include prepare, execute, and telemetry"
            )
        if (
            self.provider_abort_supported
            and RuntimeOperation.ABORT.value not in self.operations
        ):
            raise ValueError(
                "provider_abort_supported requires the abort runtime operation"
            )
        return self


class AdapterCapabilityDeclaration(StrictContractModel):
    """Required declaration published by every control adapter."""

    schema_version: Literal["1.0"] = ADAPTER_ARCHITECTURE_VERSION
    adapter_id: str = Field(min_length=1, max_length=256)
    backend_id: str = Field(min_length=1, max_length=128)
    substrate_class: str = Field(min_length=1, max_length=128)
    control_operations: set[ControlOperation]
    reservation_mode: ReservationMode
    deployment_mode: DeploymentMode
    evidence_ceiling: EvidenceLevel
    runtime: RuntimeCapabilityDeclaration
    explicit: bool = True
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_control_operations(self) -> "AdapterCapabilityDeclaration":
        required = {
            ControlOperation.DISCOVER.value,
            ControlOperation.RESERVE.value,
            ControlOperation.DEPLOY.value,
            ControlOperation.STATE.value,
            ControlOperation.ABORT.value,
            ControlOperation.ARTIFACTS.value,
        }
        if not required.issubset(set(self.control_operations)):
            raise ValueError(
                "control operations must declare discovery, reservation, "
                "deployment, state, abort, and artifacts"
            )
        expected_ceiling = EXPECTED_EVIDENCE_LEVEL[
            RuntimeKind(self.runtime.runtime_kind)
        ]
        if self.evidence_ceiling != expected_ceiling:
            raise ValueError(
                "evidence_ceiling must match the configured runtime kind; "
                "runtime evidence is attested separately"
            )
        return self


class AdapterPreparationResult(StrictContractModel):
    """Outcome of a runtime preparation step."""

    prepared: bool = Field(..., description="Whether preparation succeeded.")
    details: str = Field(default="", description="Short preparation summary.")


class AdapterInvocationResult(StrictContractModel):
    """Normalized result returned by a substrate runtime."""

    backend_id: str = Field(
        ...,
        description="Identifier of the backend that produced the result.",
    )
    task_id: str = Field(
        ...,
        description="Identifier of the task that was executed.",
    )
    output_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend output payload normalized into a Python dictionary.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence value reported by the backend.",
    )
    execution_latency_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Observed or estimated execution latency.",
    )
    backend_state: str = Field(
        default="ready",
        description="Backend-reported lifecycle state after invocation.",
    )
    notes: str | None = Field(default=None, description="Optional result notes.")


def make_adapter_capability_declaration(
    *,
    adapter_id: str,
    descriptor: SubstrateDescriptor,
    runtime: RuntimeCapabilityDeclaration,
    evidence_ceiling: EvidenceLevel,
    reservation_mode: ReservationMode = ReservationMode.CONTROL_PLANE_LEASE,
    deployment_mode: DeploymentMode = DeploymentMode.PREDEPLOYED,
    notes: str | None = None,
) -> AdapterCapabilityDeclaration:
    """Build the complete required declaration without implicit capabilities."""

    return AdapterCapabilityDeclaration(
        adapter_id=adapter_id,
        backend_id=descriptor.backend_id,
        substrate_class=str(descriptor.capability.substrate_class),
        control_operations=set(ControlOperation),
        reservation_mode=reservation_mode,
        deployment_mode=deployment_mode,
        evidence_ceiling=evidence_ceiling,
        runtime=runtime,
        explicit=True,
        notes=notes,
    )
