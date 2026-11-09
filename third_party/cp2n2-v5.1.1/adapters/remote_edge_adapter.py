"""HTTP-backed adapter exposing an externalized edge-style backend."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from adapters.base_adapter import AdapterInvocationResult, AdapterPreparationResult, BaseAdapter
from adapters.contracts import (
    DeploymentMode,
    make_adapter_capability_declaration,
)
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode, SubstrateDescriptor
from descriptors.resource_contract import (
    EvidenceLevel,
    ObservationSource,
    RuntimeKind,
)
from runtimes.remote_edge_runtime import RemoteEdgeRuntime


class RemoteEdgeAdapter(BaseAdapter):
    """Adapter that talks to a remote HTTP service instead of an in-process twin."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        descriptor_payload = self._request_json("GET", "/describe")
        descriptor = SubstrateDescriptor.model_validate(descriptor_payload)
        runtime = RemoteEdgeRuntime(descriptor.backend_id, self._base_url)
        super().__init__(
            descriptor=descriptor,
            runtime=runtime,
            capability_declaration=make_adapter_capability_declaration(
                adapter_id=f"{self.__class__.__module__}.{self.__class__.__qualname__}",
                descriptor=descriptor,
                runtime=runtime.capabilities,
                evidence_ceiling=EvidenceLevel.E2_SAME_HOST_SERVICE,
                deployment_mode=DeploymentMode.PREDEPLOYED,
                notes="HTTP control adapter for an externalized edge runtime.",
            ),
            runtime_kind=RuntimeKind.SAME_HOST_SERVICE,
            provider_id="cp2n2-remote-edge-service",
            attestation_method="http_same_host_service_descriptor",
            telemetry_source=ObservationSource.PROVIDER_REPORTED,
        )

    def describe(self) -> SubstrateDescriptor:
        return self.descriptor

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        return super().prepare(task)

    def invoke(self, task: TaskRequest) -> AdapterInvocationResult:
        return super().invoke(task)

    def collect_telemetry(self) -> dict[str, float | int | str | bool | None]:
        return super().collect_telemetry()

    def reset(self, mode: ResetMode | None = None) -> bool:
        return super().reset(mode=mode)

    def recalibrate(self) -> bool:
        return super().recalibrate()

    def _request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self._base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))
