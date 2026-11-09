"""Cortical Labs substrate runtime used by the generic A5 control adapter."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from adapters.contracts import (
    AdapterInvocationResult,
    AdapterPreparationResult,
    ExecutionLocation,
    RuntimeArtifact,
    RuntimeCapabilityDeclaration,
    RuntimeOperation,
)
from backends.cortical.cl_client import (
    CLClient,
    CorticalLabsInvocationError,
    CorticalLabsUnavailableError,
)
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode
from descriptors.resource_contract import RuntimeKind
from runtimes.base_runtime import SubstrateRuntime


class CorticalLabsRuntime(SubstrateRuntime):
    """Run the fixed CL SDK application behind a control-plane boundary."""

    def __init__(
        self,
        backend_id: str,
        *,
        use_simulator: bool | None = True,
        client: CLClient | None = None,
    ) -> None:
        configured_kind = (
            RuntimeKind.SDK_SIMULATOR
            if use_simulator is True
            else RuntimeKind.PHYSICAL_HARDWARE
            if use_simulator is False
            else RuntimeKind.UNKNOWN
        )
        super().__init__(
            RuntimeCapabilityDeclaration(
                runtime_id=f"{backend_id}:cl-sdk-runtime",
                runtime_kind=configured_kind,
                execution_location=ExecutionLocation.IN_PROCESS,
                operations={
                    RuntimeOperation.PREPARE,
                    RuntimeOperation.EXECUTE,
                    RuntimeOperation.TELEMETRY,
                    RuntimeOperation.RESET,
                    RuntimeOperation.RECALIBRATE,
                    RuntimeOperation.ABORT,
                    RuntimeOperation.ARTIFACTS,
                },
                artifact_kinds=["application/x-hdf5"],
                time_critical_execution_local=True,
                provider_abort_supported=True,
            )
        )
        self.backend_id = backend_id
        self.use_simulator = use_simulator
        self.expected_runtime_kind = configured_kind.value
        self.client = client or CLClient(use_simulator=use_simulator)
        self.last_runtime_kind = RuntimeKind.UNKNOWN.value
        self.last_attested_at: datetime | None = None
        self._session_open = False
        self._last_backend_latency_ms: float | None = None
        self._last_observation_latency_ms: float | None = None
        self._last_recording_artifact: dict | None = None
        self._last_health_status = "unknown"
        self._last_readiness_state = "unavailable"
        self._last_channel_count: int | None = None
        self._last_fps: float | None = None
        self._last_prepare_timestamp: float | None = None

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        if not getattr(task, "human_supervision_available", True):
            self._last_readiness_state = "rejected"
            self._last_health_status = "unknown"
            return AdapterPreparationResult(
                prepared=False,
                details="Cortical Labs backend requires human supervision.",
            )
        if not self.client.is_available():
            self._session_open = False
            self._last_readiness_state = "unavailable"
            self._last_health_status = "unknown"
            return AdapterPreparationResult(
                prepared=False,
                details="Cortical Labs SDK is not installed or importable.",
            )
        try:
            info = self.client.open_session()
        except CorticalLabsUnavailableError as exc:
            self._session_open = False
            self._last_readiness_state = "unavailable"
            self._last_health_status = "unknown"
            return AdapterPreparationResult(prepared=False, details=str(exc))

        self._session_open = True
        self._last_readiness_state = "ready"
        self._last_health_status = "unknown"
        self.last_runtime_kind = info.runtime_kind
        self.last_attested_at = datetime.now(timezone.utc)
        self._last_channel_count = info.channel_count
        self._last_fps = info.fps
        self._last_prepare_timestamp = time.perf_counter()
        details = "Cortical Labs session opened successfully."
        if info.channel_count is not None:
            details += f" channels={info.channel_count}"
        if info.fps is not None:
            details += f", fps={info.fps}"
        return AdapterPreparationResult(prepared=True, details=details)

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        if not self._session_open:
            return AdapterInvocationResult(
                backend_id=self.backend_id,
                task_id=task.task_id,
                output_payload={},
                confidence=None,
                execution_latency_ms=0.0,
                backend_state="unavailable",
                notes="Cortical Labs session is not open; call prepare() first.",
            )

        channel, amplitude_ua = self._stimulation(task)
        observation_window_ms = self._integer_metadata(
            task,
            "observation_window_ms",
            100,
        )
        pre_delay_ms = self._integer_metadata(task, "pre_delay_ms", 20)
        try:
            result = self.client.stimulate_and_record(
                channel=channel,
                amplitude_ua=amplitude_ua,
                observation_window_ms=observation_window_ms,
                pre_delay_ms=pre_delay_ms,
            )
        except CorticalLabsInvocationError as exc:
            self._last_health_status = "degraded"
            self._last_readiness_state = "ready"
            return AdapterInvocationResult(
                backend_id=self.backend_id,
                task_id=task.task_id,
                output_payload={},
                confidence=None,
                execution_latency_ms=0.0,
                backend_state="error",
                notes=str(exc),
            )

        self._last_backend_latency_ms = result.backend_latency_ms
        self._last_observation_latency_ms = result.observation_latency_ms
        self._last_recording_artifact = result.recording_artifact
        self._last_health_status = "unknown"
        self._last_readiness_state = "ready"
        self.last_runtime_kind = self.client.runtime_kind()
        output_payload = {
            "response_fingerprint": result.response_summary.get(
                "response_fingerprint",
                "recording_completed",
            ),
            "observation_window_ms": observation_window_ms,
            "stim_channel": channel,
            "stim_amplitude_ua": amplitude_ua,
            "recording_artifact": result.recording_artifact,
            "raw_backend_metadata": result.raw_backend_metadata,
        }
        notes = (
            "Cortical Labs stimulation/recording cycle completed "
            f"on {self.last_runtime_kind}."
        )
        if result.recording_artifact and result.recording_artifact.get("path"):
            notes += f" recording_path={result.recording_artifact['path']}"
        return AdapterInvocationResult(
            backend_id=self.backend_id,
            task_id=task.task_id,
            output_payload=output_payload,
            confidence=None,
            execution_latency_ms=result.backend_latency_ms,
            backend_state="ready",
            notes=notes,
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        health = self.client.get_health_status()
        age_of_information_ms = None
        if self._last_prepare_timestamp is not None:
            age_of_information_ms = (
                time.perf_counter() - self._last_prepare_timestamp
            ) * 1000.0
        telemetry: dict[str, float | int | str | bool | None] = {
            "readiness_state": str(
                health.get("readiness_state", self._last_readiness_state)
            ),
            "health_status": str(
                health.get("health_status", self._last_health_status)
            ),
            "backend_latency_ms": self._last_backend_latency_ms,
            "observation_latency_ms": self._last_observation_latency_ms,
            "channel_count": health.get(
                "channel_count",
                self._last_channel_count,
            ),
            "fps": health.get("fps", self._last_fps),
            "runtime_kind": health.get(
                "runtime_kind",
                self.last_runtime_kind,
            ),
            "drift_score": None,
            "age_of_information_ms": age_of_information_ms,
            "telemetry_source": "local_session_observation",
            "sdk_available": self.client.is_available(),
        }
        if self._last_recording_artifact and self._last_recording_artifact.get(
            "path"
        ):
            telemetry["recording_path"] = self._last_recording_artifact["path"]
        return telemetry

    def reset(self, mode: ResetMode | None = None) -> bool:
        self.client.close_session()
        self._session_open = False
        self._last_readiness_state = "unavailable"
        self._last_health_status = "unknown"
        self._last_backend_latency_ms = None
        self._last_observation_latency_ms = None
        self._last_recording_artifact = None
        self.last_runtime_kind = RuntimeKind.UNKNOWN.value
        self.last_attested_at = None
        return True

    def recalibrate(self) -> bool:
        self.reset()
        if not self.client.is_available():
            return False
        try:
            info = self.client.open_session()
        except CorticalLabsUnavailableError:
            return False
        self._session_open = True
        self._last_readiness_state = "ready"
        self._last_health_status = "unknown"
        self.last_runtime_kind = info.runtime_kind
        self.last_attested_at = datetime.now(timezone.utc)
        self._last_channel_count = info.channel_count
        self._last_fps = info.fps
        self._last_prepare_timestamp = time.perf_counter()
        return True

    def abort(self) -> bool:
        return self.reset(mode=ResetMode.SOFT_RESET)

    def artifacts(self) -> list[RuntimeArtifact]:
        artifact = self._last_recording_artifact
        if not artifact:
            return []
        artifact_id = str(
            artifact.get("name")
            or artifact.get("path")
            or "cl-recording"
        )
        uri = artifact.get("uri_path") or artifact.get("path")
        metadata = {
            key: value
            for key, value in artifact.items()
            if key not in {"name", "path", "uri_path"}
        }
        return [
            RuntimeArtifact(
                artifact_id=artifact_id,
                kind="recording",
                uri=None if uri is None else str(uri),
                media_type="application/x-hdf5",
                metadata=metadata,
            )
        ]

    @staticmethod
    def _stimulation(task: TaskRequest) -> tuple[int, float]:
        pattern = task.metadata.get("stimulation_pattern", {})
        channels = pattern.get("channels", [1])
        amplitude = pattern.get("amplitude", 0.4)
        try:
            channel = int(channels[0]) if channels else 1
        except (TypeError, ValueError, IndexError):
            channel = 1
        try:
            amplitude_ua = float(amplitude)
        except (TypeError, ValueError):
            amplitude_ua = 0.4
        return channel, amplitude_ua

    @staticmethod
    def _integer_metadata(
        task: TaskRequest,
        name: str,
        default: int,
    ) -> int:
        try:
            return int(task.metadata.get(name, default))
        except (TypeError, ValueError):
            return default
