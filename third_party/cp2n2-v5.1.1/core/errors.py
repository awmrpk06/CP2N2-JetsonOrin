"""Normalized control-plane errors for CP²N²."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneErrorCode(str, Enum):
    """Stable error codes returned by lifecycle-aware operations."""

    INADMISSIBLE = "INADMISSIBLE"
    POLICY_DENIED = "POLICY_DENIED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_BUSY = "RESOURCE_BUSY"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_NOT_FOUND = "LEASE_NOT_FOUND"
    STATE_VERSION_CONFLICT = "STATE_VERSION_CONFLICT"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    TELEMETRY_STALE = "TELEMETRY_STALE"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    PREPARATION_TIMEOUT = "PREPARATION_TIMEOUT"
    INVOCATION_FAILED = "INVOCATION_FAILED"
    EXECUTION_STATUS_UNKNOWN = "EXECUTION_STATUS_UNKNOWN"
    VALIDATION_TIMEOUT = "VALIDATION_TIMEOUT"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    ABORT_FAILED = "ABORT_FAILED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"


class ControlPlaneError(BaseModel):
    """Serializable error envelope used by orchestration results."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    code: ControlPlaneErrorCode
    message: str = Field(..., min_length=1)
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ControlPlaneException(RuntimeError):
    """Internal exception carrying a normalized public error."""

    def __init__(
        self,
        code: ControlPlaneErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error = ControlPlaneError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(message)
