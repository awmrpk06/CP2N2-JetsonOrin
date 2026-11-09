"""Substrate-side runtime interface separated from A5 control adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adapters.contracts import (
    AdapterInvocationResult,
    AdapterPreparationResult,
    RuntimeArtifact,
    RuntimeCapabilityDeclaration,
)
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode


class SubstrateRuntime(ABC):
    """Execute time-critical substrate work behind a control adapter."""

    def __init__(self, capabilities: RuntimeCapabilityDeclaration) -> None:
        self._runtime_capabilities = capabilities

    @property
    def capabilities(self) -> RuntimeCapabilityDeclaration:
        return self._runtime_capabilities

    @abstractmethod
    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        raise NotImplementedError

    @abstractmethod
    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        raise NotImplementedError

    @abstractmethod
    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        raise NotImplementedError

    @abstractmethod
    def reset(self, mode: ResetMode | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def recalibrate(self) -> bool:
        raise NotImplementedError

    def abort(self) -> bool:
        return False

    def artifacts(self) -> list[RuntimeArtifact]:
        return []
