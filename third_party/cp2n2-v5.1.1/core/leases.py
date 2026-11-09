"""Thread-safe, time-bounded exclusive leases for CP²N² resources."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from core.errors import ControlPlaneErrorCode, ControlPlaneException


class ResourceLease(BaseModel):
    """One exclusive, renewable resource lease."""

    model_config = ConfigDict(extra="forbid")

    lease_id: str
    resource_id: str
    owner_id: str
    acquired_at: datetime
    expires_at: datetime
    version: int = Field(ge=0)
    idempotency_key: str | None = None

    def is_expired(self, at: datetime | None = None) -> bool:
        return (at or datetime.now(timezone.utc)) >= self.expires_at


class InMemoryLeaseStore:
    """Atomic in-process lease store shared by a registry."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._leases_by_resource: dict[str, ResourceLease] = {}
        self._resource_by_lease: dict[str, str] = {}

    def acquire(
        self,
        resource_id: str,
        owner_id: str,
        *,
        ttl_ms: float,
        idempotency_key: str | None = None,
    ) -> ResourceLease:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive.")
        with self._lock:
            now = self._clock()
            self._expire_locked(resource_id, now)
            current = self._leases_by_resource.get(resource_id)
            if current is not None:
                if (
                    current.owner_id == owner_id
                    and idempotency_key is not None
                    and current.idempotency_key == idempotency_key
                ):
                    return current.model_copy(deep=True)
                raise ControlPlaneException(
                    ControlPlaneErrorCode.RESOURCE_BUSY,
                    f"Resource '{resource_id}' is leased by another client.",
                    retryable=True,
                    details={
                        "resource_id": resource_id,
                        "lease_expires_at": current.expires_at.isoformat(),
                    },
                )

            lease = ResourceLease(
                lease_id=str(uuid4()),
                resource_id=resource_id,
                owner_id=owner_id,
                acquired_at=now,
                expires_at=now + timedelta(milliseconds=ttl_ms),
                version=0,
                idempotency_key=idempotency_key,
            )
            self._leases_by_resource[resource_id] = lease
            self._resource_by_lease[lease.lease_id] = resource_id
            return lease.model_copy(deep=True)

    def validate(
        self,
        lease_id: str,
        resource_id: str,
        owner_id: str,
        *,
        expected_version: int | None = None,
    ) -> ResourceLease:
        with self._lock:
            now = self._clock()
            current = self._leases_by_resource.get(resource_id)
            if current is None or current.lease_id != lease_id:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.LEASE_NOT_FOUND,
                    f"No active lease '{lease_id}' exists for '{resource_id}'.",
                    retryable=True,
                )
            if current.is_expired(now):
                self._remove_locked(current)
                raise ControlPlaneException(
                    ControlPlaneErrorCode.LEASE_EXPIRED,
                    f"Lease '{lease_id}' for '{resource_id}' has expired.",
                    retryable=True,
                    details={"expired_at": current.expires_at.isoformat()},
                )
            if current.owner_id != owner_id:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.POLICY_DENIED,
                    f"Client '{owner_id}' does not own lease '{lease_id}'.",
                )
            if expected_version is not None and current.version != expected_version:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Lease '{lease_id}' is at version {current.version}, "
                        f"expected {expected_version}."
                    ),
                    retryable=True,
                )
            return current.model_copy(deep=True)

    def renew(
        self,
        lease_id: str,
        resource_id: str,
        owner_id: str,
        *,
        ttl_ms: float,
        expected_version: int | None = None,
    ) -> ResourceLease:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive.")
        with self._lock:
            current = self.validate(
                lease_id,
                resource_id,
                owner_id,
                expected_version=expected_version,
            )
            now = self._clock()
            renewed = current.model_copy(
                update={
                    "expires_at": now + timedelta(milliseconds=ttl_ms),
                    "version": current.version + 1,
                }
            )
            self._leases_by_resource[resource_id] = renewed
            return renewed.model_copy(deep=True)

    def release(self, lease_id: str, resource_id: str, owner_id: str) -> bool:
        with self._lock:
            current = self._leases_by_resource.get(resource_id)
            if current is None:
                return False
            if current.lease_id != lease_id:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.LEASE_NOT_FOUND,
                    f"Lease '{lease_id}' does not own resource '{resource_id}'.",
                )
            if current.owner_id != owner_id:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.POLICY_DENIED,
                    f"Client '{owner_id}' cannot release lease '{lease_id}'.",
                )
            if current.is_expired(self._clock()):
                self._remove_locked(current)
                raise ControlPlaneException(
                    ControlPlaneErrorCode.LEASE_EXPIRED,
                    f"Lease '{lease_id}' for '{resource_id}' has expired.",
                    retryable=True,
                    details={"expired_at": current.expires_at.isoformat()},
                )
            self._remove_locked(current)
            return True

    def current(self, resource_id: str) -> ResourceLease | None:
        with self._lock:
            self._expire_locked(resource_id, self._clock())
            current = self._leases_by_resource.get(resource_id)
            return None if current is None else current.model_copy(deep=True)

    def _expire_locked(self, resource_id: str, now: datetime) -> None:
        current = self._leases_by_resource.get(resource_id)
        if current is not None and current.is_expired(now):
            self._remove_locked(current)

    def _remove_locked(self, lease: ResourceLease) -> None:
        self._leases_by_resource.pop(lease.resource_id, None)
        self._resource_by_lease.pop(lease.lease_id, None)
