"""Tests for the current Cortical Labs adapter."""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.cortical_labs_adapter import CorticalLabsAdapter
from demos.common import make_cortical_task


class _FakeRecording:
    name = "fake_recording.h5"
    path = "C:/tmp/fake_recording.h5"
    uri_path = "fake_recording.h5"

    def wait_until_stopped(self) -> None:
        return None


class _FakeNeurons:
    def get_channel_count(self) -> int:
        return 64

    def get_frames_per_second(self) -> float:
        return 25000.0

    def get_frame_duration_us(self) -> int:
        return 40

    def record(self, stop_after_seconds: float = 1.0):
        return _FakeRecording()

    def stim(self, channel: int, amplitude_ua: float) -> None:
        return None


class _FakeContextManager:
    def __enter__(self):
        return _FakeNeurons()

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeCLModule:
    @staticmethod
    def is_simulator() -> bool:
        return True

    @staticmethod
    def open(*, take_control=True, wait_until_recordable=True):
        return _FakeContextManager()

    @staticmethod
    def get_system_attributes():
        return {"backend": "fake-cl", "mode": "simulator"}


def _install_fake_cl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.ModuleType("cl")
    fake_module.open = _FakeCLModule.open
    fake_module.is_simulator = _FakeCLModule.is_simulator
    fake_module.get_system_attributes = _FakeCLModule.get_system_attributes
    monkeypatch.setitem(sys.modules, "cl", fake_module)


def test_cortical_descriptor_structure_is_present() -> None:
    adapter = CorticalLabsAdapter()
    descriptor = adapter.describe()

    assert descriptor.backend_id == "cortical-labs-backend"
    assert descriptor.display_name == "Cortical Labs CL API Backend"
    assert str(descriptor.policy.locality) == "lab"
    assert descriptor.telemetry.supports_health_status is False

    metric_names = {m.name for m in descriptor.telemetry.metrics}
    assert "backend_latency_ms" in metric_names
    assert "readiness_state" in metric_names
    assert "health_status" in metric_names
    assert "runtime_kind" in metric_names
    assert descriptor.custom_metadata["expected_runtime_kind"] == "sdk_simulator"


def test_cortical_prepare_reports_unavailable_without_sdk() -> None:
    adapter = CorticalLabsAdapter()
    adapter._client._cl = None

    task = make_cortical_task()
    prep = adapter.prepare(task)

    assert prep.prepared is False
    assert "not installed" in prep.details.lower() or "importable" in prep.details.lower()


def test_cortical_prepare_invoke_and_telemetry_with_fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cl(monkeypatch)

    adapter = CorticalLabsAdapter()
    adapter._client = adapter._client.__class__()

    task = make_cortical_task()

    prep = adapter.prepare(task)
    assert prep.prepared is True

    inv = adapter.invoke(task)
    assert inv.backend_id == "cortical-labs-backend"
    assert inv.backend_state == "ready"
    assert inv.confidence is None
    assert inv.output_payload["response_fingerprint"] == "recording_completed"
    assert inv.output_payload["stim_channel"] == 1
    assert "recording_artifact" in inv.output_payload

    tel = adapter.collect_telemetry()
    assert tel["readiness_state"] == "ready"
    assert tel["health_status"] == "unknown"
    assert tel["runtime_kind"] == "sdk_simulator"
    assert tel["drift_score"] is None
    assert "backend_latency_ms" in tel
    assert "channel_count" in tel
    assert "fps" in tel

    artifacts = adapter.list_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0].kind == "recording"
    assert artifacts[0].media_type == "application/x-hdf5"

    adapter.reset()


def test_cortical_prepare_rejects_without_supervision(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cl(monkeypatch)

    adapter = CorticalLabsAdapter()
    adapter._client = adapter._client.__class__()

    task = make_cortical_task()
    task.human_supervision_available = False

    prep = adapter.prepare(task)
    assert prep.prepared is False
    assert "requires human supervision" in prep.details.lower()


def test_cortical_prepare_rejects_runtime_kind_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cl(monkeypatch)

    adapter = CorticalLabsAdapter(use_simulator=False)
    adapter._client = adapter._client.__class__(use_simulator=False)

    prep = adapter.prepare(make_cortical_task())

    assert prep.prepared is False
    assert "runtime mismatch" in prep.details.lower()
