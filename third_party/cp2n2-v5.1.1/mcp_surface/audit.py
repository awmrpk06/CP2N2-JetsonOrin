"""Append-only, hash-chained JSONL audit trail for every delivered tool call."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


GENESIS_HASH = "0" * 64


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    event_id: str
    occurred_at: datetime
    request_id: str
    principal_id: str
    tool: str
    phase: str
    outcome: str
    argument_digest: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str
    event_hash: str


class JsonlHashChainAuditTrail:
    """Durable append-only log with verification on open and explicit fsync."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._events = self._load_and_verify()

    def record(
        self,
        *,
        request_id: str,
        principal_id: str,
        tool: str,
        phase: str,
        outcome: str,
        arguments: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        raw_arguments = arguments or {}
        redacted = self._redact(raw_arguments)
        argument_digest = self._digest(raw_arguments)
        with self._lock:
            sequence = len(self._events) + 1
            previous_hash = (
                self._events[-1].event_hash if self._events else GENESIS_HASH
            )
            payload = {
                "sequence": sequence,
                "event_id": str(uuid4()),
                "occurred_at": datetime.now(timezone.utc),
                "request_id": request_id,
                "principal_id": principal_id,
                "tool": tool,
                "phase": phase,
                "outcome": outcome,
                "argument_digest": argument_digest,
                "arguments": redacted,
                "details": details or {},
                "previous_hash": previous_hash,
            }
            draft = AuditEvent.model_validate(
                {**payload, "event_hash": GENESIS_HASH}
            )
            canonical = draft.model_dump(mode="json", exclude={"event_hash"})
            event_hash = self._digest(canonical)
            event = draft.model_copy(update={"event_hash": event_hash})
            encoded = json.dumps(
                event.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._events.append(event)
            return event.model_copy(deep=True)

    def events(self) -> list[AuditEvent]:
        with self._lock:
            return [event.model_copy(deep=True) for event in self._events]

    def verify(self) -> bool:
        with self._lock:
            self._verify_events(self._events)
        return True

    def _load_and_verify(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        events: list[AuditEvent] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                events.append(AuditEvent.model_validate_json(line))
            except Exception as exc:
                raise ValueError(
                    f"Invalid audit event at line {line_number}: {exc}"
                ) from exc
        self._verify_events(events)
        return events

    @classmethod
    def _verify_events(cls, events: list[AuditEvent]) -> None:
        previous_hash = GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence:
                raise ValueError("Audit sequence is not contiguous.")
            if event.previous_hash != previous_hash:
                raise ValueError("Audit hash chain is broken.")
            payload = event.model_dump(mode="json", exclude={"event_hash"})
            if cls._digest(payload) != event.event_hash:
                raise ValueError("Audit event hash does not match its payload.")
            previous_hash = event.event_hash

    @classmethod
    def _redact(cls, value: Any) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                lowered = key.lower()
                if any(marker in lowered for marker in ("token", "secret", "key")):
                    redacted[key] = "<redacted>"
                    redacted[f"{key}_sha256"] = cls._digest(item)
                else:
                    redacted[key] = cls._redact(item)
            return redacted
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
