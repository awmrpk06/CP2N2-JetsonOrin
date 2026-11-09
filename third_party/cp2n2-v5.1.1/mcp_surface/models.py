"""Strict input and output models for the agent-facing MCP tools."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class StrictModel(BaseModel):
    """Base model that rejects fields not present in the public tool schema."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class AssayPresetId(str, Enum):
    """Server-owned assay presets; agents cannot edit physical primitives."""

    EDGE_VECTOR_CLASSIFICATION_V1 = "edge_vector_classification_v1"
    CHEMICAL_SENSING_V1 = "chemical_sensing_v1"
    WETWARE_TEMPORAL_PROBE_V1 = "wetware_temporal_probe_v1"
    PATTERN_GATE_V1 = "pattern_gate_v1"


class RunState(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


class SurfaceErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    INVALID_STATE = "INVALID_STATE"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class SurfaceError(StrictModel):
    code: SurfaceErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResponse(StrictModel):
    """Common structured result envelope used by every MCP tool."""

    ok: bool
    request_id: str
    tool: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: SurfaceError | None = None

    @model_validator(mode="after")
    def validate_error_shape(self) -> "ToolResponse":
        if self.ok and self.error is not None:
            raise ValueError("successful responses cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed responses require an error")
        return self


class DiscoverResourcesInput(StrictModel):
    include_unavailable: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class DescribeResourceInput(StrictModel):
    resource_id: str = Field(pattern=IDENTIFIER_PATTERN)


class ReserveResourceInput(StrictModel):
    resource_id: str = Field(pattern=IDENTIFIER_PATTERN)
    ttl_seconds: int = Field(default=60, ge=30, le=600)
    expected_state_version: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=IDENTIFIER_PATTERN,
    )


class RenewLeaseInput(StrictModel):
    resource_id: str = Field(pattern=IDENTIFIER_PATTERN)
    lease_id: UUID
    ttl_seconds: int = Field(ge=30, le=600)
    expected_lease_version: int | None = Field(default=None, ge=0)


class PrepareAssayInput(StrictModel):
    resource_id: str = Field(pattern=IDENTIFIER_PATTERN)
    preset_id: AssayPresetId
    dry_run: bool = False
    lease_id: UUID | None = None
    expected_lease_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_lease_mode(self) -> "PrepareAssayInput":
        if self.dry_run and self.lease_id is not None:
            raise ValueError("dry_run must not carry or commit a lease")
        if not self.dry_run and self.lease_id is None:
            raise ValueError("non-dry preparation requires an existing lease")
        if self.expected_lease_version is not None and self.lease_id is None:
            raise ValueError("expected_lease_version requires lease_id")
        return self


class RunAssayInput(StrictModel):
    run_id: UUID
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=IDENTIFIER_PATTERN,
    )
    approval_token: str | None = Field(default=None, min_length=16, max_length=512)


class GetRunStatusInput(StrictModel):
    run_id: UUID


class AbortRunInput(StrictModel):
    run_id: UUID


class GetResultSummaryInput(StrictModel):
    run_id: UUID


class ReleaseResourceInput(StrictModel):
    resource_id: str = Field(pattern=IDENTIFIER_PATTERN)
    lease_id: UUID
    expected_state_version: int | None = Field(default=None, ge=0)


class MCPPrincipal(StrictModel):
    """Identity supplied by the server environment, never by an agent call."""

    principal_id: str = Field(pattern=IDENTIFIER_PATTERN)
    authenticated: bool = True
    scopes: list[str] = Field(default_factory=list)

    @field_validator("scopes")
    @classmethod
    def normalize_scopes(cls, value: list[str]) -> list[str]:
        return sorted(set(item.strip() for item in value if item.strip()))
