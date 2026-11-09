"""Substrate runtimes for the three local, non-CL synthetic twins."""

from __future__ import annotations

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
from twins.chemical_twin import ChemicalTwin
from twins.edge_twin import EdgeTwin
from twins.wetware_twin import WetwareTwin


def _twin_capabilities(backend_id: str) -> RuntimeCapabilityDeclaration:
    return RuntimeCapabilityDeclaration(
        runtime_id=f"{backend_id}:synthetic-runtime",
        runtime_kind=RuntimeKind.SYNTHETIC_TWIN,
        execution_location=ExecutionLocation.IN_PROCESS,
        operations={
            RuntimeOperation.PREPARE,
            RuntimeOperation.EXECUTE,
            RuntimeOperation.TELEMETRY,
            RuntimeOperation.RESET,
            RuntimeOperation.RECALIBRATE,
        },
        artifact_kinds=[],
        time_critical_execution_local=True,
        provider_abort_supported=False,
    )


class EdgeTwinRuntime(SubstrateRuntime):
    def __init__(self, backend_id: str, twin: EdgeTwin | None = None) -> None:
        super().__init__(_twin_capabilities(backend_id))
        self.twin = twin or EdgeTwin()
        self.backend_id = backend_id

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        prepared, details = self.twin.prepare(
            input_vector=self._input_vector(task)
        )
        return AdapterPreparationResult(prepared=prepared, details=details)

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        result = self.twin.run(input_vector=self._input_vector(task))
        return AdapterInvocationResult(
            backend_id=self.backend_id,
            task_id=task.task_id,
            output_payload=result.output_payload,
            confidence=result.confidence,
            execution_latency_ms=result.execution_latency_ms,
            backend_state=result.backend_state,
            notes="Fast edge-style vector inference runtime.",
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        return self.twin.telemetry()

    def reset(self, mode: ResetMode | None = None) -> bool:
        return self.twin.reset(mode=mode)

    def recalibrate(self) -> bool:
        return self.twin.recalibrate()

    @staticmethod
    def _input_vector(task: TaskRequest) -> list[float]:
        raw_value = task.metadata.get("input_vector")
        if isinstance(raw_value, list) and raw_value:
            try:
                return [float(item) for item in raw_value]
            except (TypeError, ValueError):
                pass
        return [0.2, 0.4, 0.6, 0.8]


class ChemicalTwinRuntime(SubstrateRuntime):
    def __init__(
        self,
        backend_id: str,
        twin: ChemicalTwin | None = None,
    ) -> None:
        super().__init__(_twin_capabilities(backend_id))
        self.twin = twin or ChemicalTwin()
        self.backend_id = backend_id

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        prepared, details = self.twin.prepare(
            input_level=self._input_level(task)
        )
        return AdapterPreparationResult(prepared=prepared, details=details)

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        result = self.twin.run(input_level=self._input_level(task))
        return AdapterInvocationResult(
            backend_id=self.backend_id,
            task_id=task.task_id,
            output_payload=result.output_payload,
            confidence=result.confidence,
            execution_latency_ms=result.execution_latency_ms,
            backend_state=result.backend_state,
            notes="Chemical/DNA-inspired synthetic runtime.",
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        return self.twin.telemetry()

    def reset(self, mode: ResetMode | None = None) -> bool:
        return self.twin.reset(mode=mode)

    def recalibrate(self) -> bool:
        return self.twin.recalibrate()

    @staticmethod
    def _input_level(task: TaskRequest) -> float:
        try:
            return float(task.metadata.get("input_level", 1.0))
        except (TypeError, ValueError):
            return 1.0


class WetwareTwinRuntime(SubstrateRuntime):
    def __init__(
        self,
        backend_id: str,
        twin: WetwareTwin | None = None,
    ) -> None:
        super().__init__(_twin_capabilities(backend_id))
        self.twin = twin or WetwareTwin()
        self.backend_id = backend_id

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        prepared, details = self.twin.prepare(
            stimulation_strength=self._stimulation_strength(task)
        )
        return AdapterPreparationResult(prepared=prepared, details=details)

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        result = self.twin.run(
            stimulation_strength=self._stimulation_strength(task),
            observation_window_ms=self._observation_window(task),
        )
        return AdapterInvocationResult(
            backend_id=self.backend_id,
            task_id=task.task_id,
            output_payload=result.output_payload,
            confidence=result.confidence,
            execution_latency_ms=result.execution_latency_ms,
            backend_state=result.backend_state,
            notes="Wetware-inspired synthetic runtime.",
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        return self.twin.telemetry()

    def reset(self, mode: ResetMode | None = None) -> bool:
        return self.twin.reset(mode=mode)

    def recalibrate(self) -> bool:
        return self.twin.recalibrate()

    @staticmethod
    def _stimulation_strength(task: TaskRequest) -> float:
        try:
            return float(task.metadata.get("stimulation_strength", 0.55))
        except (TypeError, ValueError):
            return 0.55

    @staticmethod
    def _observation_window(task: TaskRequest) -> float:
        try:
            return float(task.metadata.get("observation_window_ms", 120.0))
        except (TypeError, ValueError):
            return 120.0
