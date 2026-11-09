"""Thread-safe in-process assay records for the A4 prototype."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from core.orchestrator import OrchestrationRunResult
from core.task_model import TaskRequest
from mcp_surface.models import RunState


TERMINAL_STATES = {
    RunState.SUCCEEDED,
    RunState.FAILED,
    RunState.ABORTED,
}


class AssayRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    run_id: str
    owner_id: str
    resource_id: str
    preset_id: str
    lease_id: str
    lease_version: int
    approval_required: bool
    state: RunState = RunState.PREPARED
    created_at: datetime
    updated_at: datetime
    task: TaskRequest
    result: OrchestrationRunResult | None = None
    summary: dict = Field(default_factory=dict)


class RunRecordStore:
    """Atomic store with guarded lifecycle updates for agent-visible runs."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, AssayRunRecord] = {}

    def add(self, record: AssayRunRecord) -> AssayRunRecord:
        with self._lock:
            if record.run_id in self._records:
                raise ValueError(f"Run '{record.run_id}' already exists.")
            self._records[record.run_id] = record.model_copy(deep=True)
            return record.model_copy(deep=True)

    def get(self, run_id: str) -> AssayRunRecord:
        with self._lock:
            try:
                return self._records[run_id].model_copy(deep=True)
            except KeyError as exc:
                raise KeyError(f"Run '{run_id}' does not exist.") from exc

    def mark_running(self, run_id: str) -> AssayRunRecord:
        with self._lock:
            record = self._records[run_id]
            if RunState(record.state) != RunState.PREPARED:
                raise ValueError(
                    f"Run '{run_id}' is '{record.state}', not prepared."
                )
            updated = record.model_copy(
                update={
                    "state": RunState.RUNNING,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._records[run_id] = updated
            return updated.model_copy(deep=True)

    def finish(
        self,
        run_id: str,
        *,
        result: OrchestrationRunResult,
        summary: dict,
    ) -> AssayRunRecord:
        with self._lock:
            record = self._records[run_id]
            state = RunState(record.state)
            if state == RunState.ABORTED:
                final_state = RunState.ABORTED
            elif state != RunState.RUNNING:
                raise ValueError(f"Run '{run_id}' is not running.")
            else:
                final_state = (
                    RunState.SUCCEEDED if result.success else RunState.FAILED
                )
            updated = record.model_copy(
                update={
                    "state": final_state,
                    "updated_at": datetime.now(timezone.utc),
                    "result": result,
                    "summary": dict(summary),
                }
            )
            self._records[run_id] = updated
            return updated.model_copy(deep=True)

    def mark_aborted(self, run_id: str) -> AssayRunRecord:
        with self._lock:
            record = self._records[run_id]
            if RunState(record.state) in TERMINAL_STATES:
                return record.model_copy(deep=True)
            updated = record.model_copy(
                update={
                    "state": RunState.ABORTED,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._records[run_id] = updated
            return updated.model_copy(deep=True)

    def mark_failed(self, run_id: str, *, summary: dict) -> AssayRunRecord:
        with self._lock:
            record = self._records[run_id]
            updated = record.model_copy(
                update={
                    "state": RunState.FAILED,
                    "updated_at": datetime.now(timezone.utc),
                    "summary": dict(summary),
                }
            )
            self._records[run_id] = updated
            return updated.model_copy(deep=True)

    def abort_prepared_for_lease(
        self,
        *,
        owner_id: str,
        resource_id: str,
        lease_id: str,
    ) -> None:
        with self._lock:
            for run_id, record in list(self._records.items()):
                if (
                    record.owner_id == owner_id
                    and record.resource_id == resource_id
                    and record.lease_id == lease_id
                    and RunState(record.state) == RunState.PREPARED
                ):
                    self._records[run_id] = record.model_copy(
                        update={
                            "state": RunState.ABORTED,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
