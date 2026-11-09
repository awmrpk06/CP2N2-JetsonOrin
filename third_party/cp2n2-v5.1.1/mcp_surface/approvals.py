"""Human-approval hook for real biological execution."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class ApprovalRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    resource_id: str
    preset_id: str
    principal_id: str


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    requirement: ApprovalRequirement
    approver_id: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


class ApprovalDenied(PermissionError):
    """Raised when a valid external human approval is absent."""


class ApprovalVerifier(Protocol):
    def verify_and_consume(
        self,
        token: str | None,
        requirement: ApprovalRequirement,
    ) -> ApprovalGrant:
        """Verify a one-time token bound to the exact run and consume it."""


class DenyAllApprovalVerifier:
    """Safe default until an institution-specific approval hook is configured."""

    def verify_and_consume(
        self,
        token: str | None,
        requirement: ApprovalRequirement,
    ) -> ApprovalGrant:
        raise ApprovalDenied(
            "Real biological execution requires an external human approval verifier."
        )


class InMemoryHumanApprovalAuthority:
    """Test/reference authority; issuance is intentionally not an MCP tool."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._grants: dict[str, ApprovalGrant] = {}

    def issue(
        self,
        requirement: ApprovalRequirement,
        *,
        approver_id: str,
        ttl_seconds: int = 300,
    ) -> ApprovalGrant:
        if ttl_seconds < 1 or ttl_seconds > 900:
            raise ValueError("approval ttl_seconds must be between 1 and 900")
        now = datetime.now(timezone.utc)
        grant = ApprovalGrant(
            token=secrets.token_urlsafe(32),
            requirement=requirement,
            approver_id=approver_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        with self._lock:
            self._grants[grant.token] = grant
        return grant.model_copy(deep=True)

    def verify_and_consume(
        self,
        token: str | None,
        requirement: ApprovalRequirement,
    ) -> ApprovalGrant:
        if token is None:
            raise ApprovalDenied("A human approval token is required.")
        with self._lock:
            grant = self._grants.get(token)
            if grant is None:
                raise ApprovalDenied("The human approval token is unknown.")
            if grant.consumed_at is not None:
                raise ApprovalDenied("The human approval token was already consumed.")
            if grant.expires_at <= datetime.now(timezone.utc):
                raise ApprovalDenied("The human approval token has expired.")
            if grant.requirement != requirement:
                raise ApprovalDenied(
                    "The human approval token is not bound to this exact run."
                )
            consumed = grant.model_copy(
                update={"consumed_at": datetime.now(timezone.utc)}
            )
            self._grants[token] = consumed
            return consumed.model_copy(deep=True)
