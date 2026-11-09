"""HTTP substrate runtime for the externalized edge integration."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from adapters.contracts import (
    AdapterInvocationResult,
    AdapterPreparationResult,
    ExecutionLocation,
    RuntimeCapabilityDeclaration,
    RuntimeOperation,
)
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode
from descriptors.resource_contract import RuntimeKind
from runtimes.base_runtime import SubstrateRuntime
from testbed.context import propagation_headers


class RemoteEdgeRuntime(SubstrateRuntime):
    def __init__(self, backend_id: str, base_url: str) -> None:
        super().__init__(
            RuntimeCapabilityDeclaration(
                runtime_id=f"{backend_id}:http-runtime",
                runtime_kind=RuntimeKind.SAME_HOST_SERVICE,
                execution_location=ExecutionLocation.SAME_HOST_SERVICE,
                operations={
                    RuntimeOperation.PREPARE,
                    RuntimeOperation.EXECUTE,
                    RuntimeOperation.TELEMETRY,
                    RuntimeOperation.RESET,
                    RuntimeOperation.RECALIBRATE,
                },
                artifact_kinds=[],
                time_critical_execution_local=False,
                provider_abort_supported=False,
            )
        )
        self.backend_id = backend_id
        self.base_url = base_url.rstrip("/")

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        payload = self._request_json(
            "POST",
            "/prepare",
            {"task": task.model_dump(mode="json")},
        )
        return AdapterPreparationResult.model_validate(payload)

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        payload = self._request_json(
            "POST",
            "/invoke",
            {"task": task.model_dump(mode="json")},
        )
        return AdapterInvocationResult.model_validate(payload)

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        return self._request_json("GET", "/telemetry")

    def reset(self, mode: ResetMode | None = None) -> bool:
        response = self._request_json(
            "POST",
            "/reset",
            {"mode": str(mode) if mode is not None else None},
        )
        return bool(response.get("success", False))

    def recalibrate(self) -> bool:
        response = self._request_json("POST", "/recalibrate", {})
        return bool(response.get("success", False))

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
    ) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                **propagation_headers(),
            },
        )
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))
