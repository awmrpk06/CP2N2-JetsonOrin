"""Append-only correlated spans and derived testbed metrics."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns
from typing import Iterator
from uuid import uuid4


class JsonlSpanRecorder:
    """Thread-safe JSONL span recorder with no external collector dependency."""

    def __init__(self, path: str | Path | None, service: str) -> None:
        self.path = None if path is None else Path(path)
        self.service = service
        self._lock = threading.RLock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def span(
        self,
        *,
        trace_id: str,
        parent_span_id: str | None,
        operation: str,
        profile_id: str,
        attributes: dict | None = None,
    ) -> Iterator[str]:
        span_id = uuid4().hex
        started_at = datetime.now(timezone.utc)
        start_ns = perf_counter_ns()
        status = "ok"
        error_type: str | None = None
        try:
            yield span_id
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            ended_at = datetime.now(timezone.utc)
            status_code = (attributes or {}).get("status_code")
            if (
                status == "ok"
                and isinstance(status_code, int)
                and status_code >= 400
            ):
                status = "error"
                error_type = str(
                    (attributes or {}).get("error_type") or "HTTPError"
                )
            record = {
                "schema_version": "1.0",
                "service": self.service,
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "operation": operation,
                "profile_id": profile_id,
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
                "duration_ms": round((perf_counter_ns() - start_ns) / 1_000_000, 6),
                "status": status,
                "error_type": error_type,
                "attributes": attributes or {},
            }
            self.record(record)

    def record(self, record: dict) -> None:
        if self.path is None:
            return
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
