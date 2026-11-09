"""In-process idempotency records for state-changing CP²N² requests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any

from core.errors import ControlPlaneErrorCode, ControlPlaneException


@dataclass
class _IdempotencyEntry:
    fingerprint: str
    in_progress: bool = True
    result: Any = None


class IdempotencyStore:
    """Atomically reserves keys and replays completed results."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[tuple[str, str], _IdempotencyEntry] = {}

    def begin(
        self,
        client_id: str,
        key: str,
        fingerprint: str,
    ) -> Any | None:
        scoped_key = (client_id, key)
        with self._lock:
            current = self._entries.get(scoped_key)
            if current is None:
                self._entries[scoped_key] = _IdempotencyEntry(fingerprint=fingerprint)
                return None
            if current.fingerprint != fingerprint:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.IDEMPOTENCY_CONFLICT,
                    (
                        f"Idempotency key '{key}' was already used for a "
                        "different request."
                    ),
                    details={"client_id": client_id, "idempotency_key": key},
                )
            if current.in_progress:
                raise ControlPlaneException(
                    ControlPlaneErrorCode.REQUEST_IN_PROGRESS,
                    f"Request for idempotency key '{key}' is still in progress.",
                    retryable=True,
                )
            return deepcopy(current.result)

    def complete(self, client_id: str, key: str, result: Any) -> None:
        with self._lock:
            current = self._entries[(client_id, key)]
            current.in_progress = False
            current.result = deepcopy(result)

    def abandon(self, client_id: str, key: str) -> None:
        with self._lock:
            self._entries.pop((client_id, key), None)
