from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import time

from dotenv import load_dotenv

load_dotenv()


class CorticalLabsUnavailableError(RuntimeError):
    pass


class CorticalLabsInvocationError(RuntimeError):
    pass


@dataclass
class CLSessionInfo:
    success: bool
    message: str
    channel_count: Optional[int] = None
    fps: Optional[float] = None
    frame_duration_us: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    runtime_kind: str = "unknown"


@dataclass
class CLInvocationSummary:
    success: bool
    message: str
    backend_latency_ms: float
    observation_latency_ms: float
    response_summary: Dict[str, Any]
    recording_artifact: Optional[Dict[str, Any]]
    raw_backend_metadata: Dict[str, Any]


class CLClient:
    """
    Thin wrapper around the Cortical Labs Python API.

    Design goals:
    - degrade honestly if the external SDK is not installed
    - keep a small, stable interface for the CP²N² adapter
    - support simulator-first development
    """

    def __init__(
        self,
        use_simulator: bool | None = True,
        take_control: bool = True,
        wait_until_recordable: bool = True,
    ) -> None:
        self.use_simulator = use_simulator
        self.take_control = take_control
        self.wait_until_recordable = wait_until_recordable

        self._cl = None
        self._neurons = None
        self._ctx = None
        self._last_session_info: Optional[CLSessionInfo] = None

        try:
            import cl  # type: ignore
            self._cl = cl
        except Exception:
            self._cl = None

    def is_available(self) -> bool:
        return self._cl is not None

    def runtime_kind(self) -> str:
        """Return the observed CL runtime kind without guessing from names."""
        if self._cl is None:
            return "unknown"
        try:
            is_simulator = self._cl.is_simulator()
        except Exception:
            return "unknown"
        return "sdk_simulator" if is_simulator else "physical_hardware"

    def _validate_runtime_expectation(self, runtime_kind: str) -> None:
        if self.use_simulator is None:
            return
        expected = "sdk_simulator" if self.use_simulator else "physical_hardware"
        if runtime_kind == "unknown":
            raise CorticalLabsUnavailableError(
                "Unable to attest whether the active Cortical Labs runtime is a "
                "simulator or physical hardware."
            )
        if runtime_kind != expected:
            raise CorticalLabsUnavailableError(
                f"Cortical Labs runtime mismatch: expected {expected}, observed "
                f"{runtime_kind}."
            )

    def open_session(self) -> CLSessionInfo:
        if self._cl is None:
            raise CorticalLabsUnavailableError(
                "Cortical Labs SDK not installed or import failed."
            )

        try:
            runtime_kind = self.runtime_kind()
            self._validate_runtime_expectation(runtime_kind)
            self._ctx = self._cl.open(
                take_control=self.take_control,
                wait_until_recordable=self.wait_until_recordable,
            )
            self._neurons = self._ctx.__enter__()

            attrs: Dict[str, Any] = {}
            try:
                attrs = self._cl.get_system_attributes()
            except Exception:
                attrs = {}

            channel_count = self._safe_call(self._neurons, "get_channel_count")
            fps = self._safe_call(self._neurons, "get_frames_per_second")
            frame_duration_us = self._safe_call(self._neurons, "get_frame_duration_us")

            info = CLSessionInfo(
                success=True,
                message="Session opened successfully.",
                channel_count=channel_count,
                fps=fps,
                frame_duration_us=frame_duration_us,
                metadata=attrs,
                runtime_kind=runtime_kind,
            )
            self._last_session_info = info
            return info
        except Exception as exc:
            self.close_session()
            raise CorticalLabsUnavailableError(
                f"Failed to open Cortical Labs session: {exc}"
            ) from exc

    def close_session(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass
        self._ctx = None
        self._neurons = None

    def get_health_status(self) -> Dict[str, Any]:
        if self._neurons is None:
            return {
                "readiness_state": "unavailable",
                "health_status": "unknown",
            }

        return {
            "readiness_state": "ready",
            # A successfully opened CL session is not a provider health signal.
            "health_status": "unknown",
            "runtime_kind": self.runtime_kind(),
            "channel_count": self._safe_call(self._neurons, "get_channel_count"),
            "fps": self._safe_call(self._neurons, "get_frames_per_second"),
        }

    def stimulate_and_record(
        self,
        channel: int,
        amplitude_ua: float,
        observation_window_ms: int = 100,
        pre_delay_ms: int = 20,
    ) -> CLInvocationSummary:
        if self._neurons is None:
            raise CorticalLabsInvocationError("No active Cortical Labs session.")

        start = time.perf_counter()

        try:
            total_seconds = max((observation_window_ms + pre_delay_ms) / 1000.0, 0.05)
            recording = self._neurons.record(stop_after_seconds=total_seconds)

            if pre_delay_ms > 0:
                time.sleep(pre_delay_ms / 1000.0)

            stim_start = time.perf_counter()
            self._neurons.stim(channel, amplitude_ua)

            try:
                recording.wait_until_stopped()
            except Exception:
                time.sleep(total_seconds)

            end = time.perf_counter()

            backend_latency_ms = (end - start) * 1000.0
            observation_latency_ms = (end - stim_start) * 1000.0

            recording_artifact = self._normalize_recording_artifact(recording)

            response_summary = {
                "response_fingerprint": "recording_completed",
                "observation_window_ms": observation_window_ms,
                "stim_channel": channel,
                "stim_amplitude_ua": amplitude_ua,
            }

            raw_backend_metadata = {
                "system_attributes": (
                    self._last_session_info.metadata
                    if self._last_session_info is not None
                    else {}
                ),
                "channel_count": (
                    self._last_session_info.channel_count
                    if self._last_session_info is not None
                    else None
                ),
                "fps": (
                    self._last_session_info.fps
                    if self._last_session_info is not None
                    else None
                ),
            }

            return CLInvocationSummary(
                success=True,
                message="Stimulation/recording cycle completed.",
                backend_latency_ms=backend_latency_ms,
                observation_latency_ms=observation_latency_ms,
                response_summary=response_summary,
                recording_artifact=recording_artifact,
                raw_backend_metadata=raw_backend_metadata,
            )
        except Exception as exc:
            raise CorticalLabsInvocationError(
                f"Cortical Labs stimulation/recording failed: {exc}"
            ) from exc

    @staticmethod
    def _normalize_recording_artifact(recording: Any) -> Optional[Dict[str, Any]]:
        """
        Return a structured recording artifact description.

        For the CL SDK simulator, the useful metadata is exposed on:
        - recording.file
        - recording._file_path
        - recording.attributes
        """
        if recording is None:
            return None

        artifact: Dict[str, Any] = {}

        # Best source: the SDK exposes a dict-like "file" field.
        file_info = getattr(recording, "file", None)
        if isinstance(file_info, dict):
            for key in ("name", "path", "uri_path"):
                value = file_info.get(key)
                if value is not None:
                    artifact[key] = str(value)

        # Fallback: internal file path
        file_path = getattr(recording, "_file_path", None)
        if file_path is not None and "path" not in artifact:
            artifact["path"] = str(file_path)
            try:
                artifact["name"] = Path(file_path).name
            except Exception:
                pass

        # Optional metadata
        attributes = getattr(recording, "attributes", None)
        if isinstance(attributes, dict):
            artifact["attributes"] = attributes

        # Last resort only if nothing useful was found
        if not artifact:
            artifact["repr"] = repr(recording)

        return artifact

    @staticmethod
    def _safe_call(obj: Any, name: str) -> Any:
        try:
            fn = getattr(obj, name)
            return fn()
        except Exception:
            return None
