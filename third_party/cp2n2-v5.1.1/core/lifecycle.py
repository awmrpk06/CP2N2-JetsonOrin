"""Atomic, versioned lifecycle state machine for CP²N² resources."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from core.errors import ControlPlaneErrorCode, ControlPlaneException
from descriptors.resource_contract import ResourceLifecycleState


ALLOWED_TRANSITIONS: dict[ResourceLifecycleState, frozenset[ResourceLifecycleState]] = {
    ResourceLifecycleState.DISCOVERED: frozenset(
        {ResourceLifecycleState.READY, ResourceLifecycleState.UNREACHABLE}
    ),
    ResourceLifecycleState.READY: frozenset(
        {
            ResourceLifecycleState.RESERVED,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.RESERVED: frozenset(
        {
            ResourceLifecycleState.PREPARING,
            ResourceLifecycleState.READY,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.PREPARING: frozenset(
        {
            ResourceLifecycleState.RUNNING,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.RUNNING: frozenset(
        {
            ResourceLifecycleState.VALIDATING,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.VALIDATING: frozenset(
        {
            ResourceLifecycleState.COOLDOWN,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.COOLDOWN: frozenset(
        {
            ResourceLifecycleState.READY,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.ABORTING: frozenset(
        {
            ResourceLifecycleState.READY,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.DEGRADED: frozenset(
        {
            ResourceLifecycleState.READY,
            ResourceLifecycleState.ABORTING,
            ResourceLifecycleState.FAILED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
    ResourceLifecycleState.FAILED: frozenset(
        {ResourceLifecycleState.READY, ResourceLifecycleState.UNREACHABLE}
    ),
    ResourceLifecycleState.UNREACHABLE: frozenset(
        {
            ResourceLifecycleState.DISCOVERED,
            ResourceLifecycleState.READY,
            ResourceLifecycleState.DEGRADED,
            ResourceLifecycleState.FAILED,
        }
    ),
    ResourceLifecycleState.UNKNOWN: frozenset(
        {
            ResourceLifecycleState.DISCOVERED,
            ResourceLifecycleState.UNREACHABLE,
        }
    ),
}


class LifecycleSnapshot(BaseModel):
    """Current state and optimistic-concurrency version of one resource."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    resource_id: str
    state: ResourceLifecycleState
    version: int = Field(ge=0)
    updated_at: datetime
    reason: str | None = None
    correlation_id: str | None = None


class LifecycleTransition(BaseModel):
    """Immutable audit record for one accepted transition."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    resource_id: str
    from_state: ResourceLifecycleState
    to_state: ResourceLifecycleState
    from_version: int
    to_version: int
    occurred_at: datetime
    reason: str
    correlation_id: str | None = None


class LifecycleStore:
    """Thread-safe state machine shared by all clients of one registry."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._states: dict[str, LifecycleSnapshot] = {}
        self._history: list[LifecycleTransition] = []

    def initialize(
        self,
        resource_id: str,
        *,
        state: ResourceLifecycleState = ResourceLifecycleState.DISCOVERED,
        reason: str = "resource registered",
    ) -> LifecycleSnapshot:
        with self._lock:
            existing = self._states.get(resource_id)
            if existing is not None:
                return existing.model_copy(deep=True)
            snapshot = LifecycleSnapshot(
                resource_id=resource_id,
                state=state,
                version=0,
                updated_at=datetime.now(timezone.utc),
                reason=reason,
            )
            self._states[resource_id] = snapshot
            return snapshot.model_copy(deep=True)

    def remove(self, resource_id: str) -> None:
        with self._lock:
            self._states.pop(resource_id, None)

    def snapshot(self, resource_id: str) -> LifecycleSnapshot:
        with self._lock:
            try:
                return self._states[resource_id].model_copy(deep=True)
            except KeyError as exc:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.RESOURCE_NOT_FOUND,
                    f"Resource '{resource_id}' has no lifecycle state.",
                ) from exc

    def transition(
        self,
        resource_id: str,
        to_state: ResourceLifecycleState,
        *,
        expected_version: int | None = None,
        reason: str,
        correlation_id: str | None = None,
    ) -> LifecycleSnapshot:
        with self._lock:
            current = self.snapshot(resource_id)
            target = ResourceLifecycleState(to_state)
            current_state = ResourceLifecycleState(current.state)

            if expected_version is not None and current.version != expected_version:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.STATE_VERSION_CONFLICT,
                    (
                        f"Resource '{resource_id}' is at state version "
                        f"{current.version}, expected {expected_version}."
                    ),
                    retryable=True,
                    details={
                        "resource_id": resource_id,
                        "actual_version": current.version,
                        "expected_version": expected_version,
                    },
                )

            if target == current_state:
                return current

            if target not in ALLOWED_TRANSITIONS[current_state]:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.INVALID_STATE_TRANSITION,
                    (
                        f"Invalid lifecycle transition for '{resource_id}': "
                        f"{current_state.value} -> {target.value}."
                    ),
                    details={
                        "resource_id": resource_id,
                        "from_state": current_state.value,
                        "to_state": target.value,
                        "version": current.version,
                    },
                )

            now = datetime.now(timezone.utc)
            updated = LifecycleSnapshot(
                resource_id=resource_id,
                state=target,
                version=current.version + 1,
                updated_at=now,
                reason=reason,
                correlation_id=correlation_id,
            )
            transition = LifecycleTransition(
                resource_id=resource_id,
                from_state=current_state,
                to_state=target,
                from_version=current.version,
                to_version=updated.version,
                occurred_at=now,
                reason=reason,
                correlation_id=correlation_id,
            )
            self._states[resource_id] = updated
            self._history.append(transition)
            return updated.model_copy(deep=True)

    def history(
        self,
        *,
        resource_id: str | None = None,
        correlation_id: str | None = None,
    ) -> list[LifecycleTransition]:
        with self._lock:
            records = self._history
            if resource_id is not None:
                records = [item for item in records if item.resource_id == resource_id]
            if correlation_id is not None:
                records = [
                    item for item in records if item.correlation_id == correlation_id
                ]
            return [item.model_copy(deep=True) for item in records]
