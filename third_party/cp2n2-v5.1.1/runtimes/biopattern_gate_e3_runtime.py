"""E3 runtime binding the BioPattern Gate application to CP²N²."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import perf_counter

from adapters.contracts import (
    AdapterInvocationResult,
    AdapterPreparationResult,
    ExecutionLocation,
    RuntimeArtifact,
    RuntimeCapabilityDeclaration,
    RuntimeOperation,
)
from applications.biopattern_gate.config import BioPatternGateConfig
from applications.biopattern_gate.control_plane_contract import (
    APPLICATION_ID,
    BACKEND_ID,
    CONFIG_SHA256,
    DECODER_SHA256,
    TASK_METADATA,
)
from applications.biopattern_gate.decoder import load_decoder
from applications.biopattern_gate.simulator import DeterministicReservoirSimulator
from applications.biopattern_gate.runner import run_session
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode
from descriptors.resource_contract import RuntimeKind
from runtimes.base_runtime import SubstrateRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "applications" / "biopattern_gate"
CONFIG_PATH = APP_ROOT / "presets" / "simulator" / "technical-e3.json"
DECODER_PATH = (
    APP_ROOT / "artifacts" / "simulator" / "pattern-gate-linear-v1.json"
)


def application_source_digest(app_root: Path = APP_ROOT) -> str:
    """Hash textual application inputs independently of platform newlines."""

    digest = hashlib.sha256()
    files = sorted(
        path
        for path in app_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix in {".py", ".json"}
    )
    for path in files:
        canonical_text = (
            path.read_text(encoding="utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        digest.update(path.relative_to(app_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(canonical_text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


class _FailAfterTrialSimulator(DeterministicReservoirSimulator):
    """Test-only provider fault that preserves a sanitized partial count."""

    def __init__(self, fail_after_trials: int) -> None:
        super().__init__()
        self.fail_after_trials = fail_after_trials
        self.completed_trials = 0

    def observe_trial(self, plan, *, logical_sequence, config):
        if self.completed_trials >= self.fail_after_trials:
            raise RuntimeError("injected E3 provider interruption")
        observation = super().observe_trial(
            plan,
            logical_sequence=logical_sequence,
            config=config,
        )
        self.completed_trials += 1
        return observation


class BioPatternGateE3Runtime(SubstrateRuntime):
    """Execute one frozen E3 application, never arbitrary stimulation."""

    def __init__(
        self,
        *,
        backend_id: str = BACKEND_ID,
        telemetry_age_ms: float = 0.0,
        fail_after_trials: int | None = None,
    ) -> None:
        super().__init__(
            RuntimeCapabilityDeclaration(
                runtime_id=f"{backend_id}:biopattern-gate-e3-runtime",
                runtime_kind=RuntimeKind.SDK_SIMULATOR,
                execution_location=ExecutionLocation.IN_PROCESS,
                operations={
                    RuntimeOperation.PREPARE,
                    RuntimeOperation.EXECUTE,
                    RuntimeOperation.TELEMETRY,
                    RuntimeOperation.RESET,
                    RuntimeOperation.ABORT,
                    RuntimeOperation.ARTIFACTS,
                },
                artifact_kinds=[
                    "biopattern_gate_result_summary",
                    "biopattern_gate_partial_summary",
                ],
                time_critical_execution_local=True,
                provider_abort_supported=True,
            )
        )
        self.backend_id = backend_id
        self.telemetry_age_ms = telemetry_age_ms
        self.fail_after_trials = fail_after_trials
        self._prepared_task_id: str | None = None
        self._active_port: DeterministicReservoirSimulator | None = None
        self._artifacts: list[RuntimeArtifact] = []
        self._last_status = "idle"
        self._aborted = False

        self.config = BioPatternGateConfig.model_validate_json(
            CONFIG_PATH.read_text(encoding="utf-8")
        )
        self.decoder = load_decoder(DECODER_PATH)
        if self.config.sha256() != CONFIG_SHA256:
            raise RuntimeError("BioPattern Gate E3 config hash drifted")
        if self.decoder.sha256() != DECODER_SHA256:
            raise RuntimeError("BioPattern Gate E3 decoder hash drifted")
        self.application_source_sha256 = application_source_digest()

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        self._validate_task(task)
        self._prepared_task_id = task.task_id
        self._last_status = "prepared"
        self._aborted = False
        self._artifacts = []
        return AdapterPreparationResult(
            prepared=True,
            details=(
                "Frozen BioPattern Gate E3 application admitted; no physical "
                "control primitive was accepted."
            ),
        )

    def execute(self, task: TaskRequest) -> AdapterInvocationResult:
        self._validate_task(task)
        if task.task_id != self._prepared_task_id:
            raise RuntimeError("task was not prepared by this runtime")
        if self._aborted:
            raise RuntimeError("prepared application run was aborted")

        started = perf_counter()
        port: DeterministicReservoirSimulator
        if self.fail_after_trials is None:
            port = DeterministicReservoirSimulator()
        else:
            port = _FailAfterTrialSimulator(self.fail_after_trials)
        self._active_port = port
        self._last_status = "running"
        application_run_id = task.task_id.removeprefix("mcp-assay-")
        try:
            result = run_session(
                run_id=application_run_id,
                config=self.config,
                decoder=self.decoder,
                port=port,
            )
        except Exception:
            completed = getattr(port, "completed_trials", 0)
            partial = {
                "application_id": APPLICATION_ID,
                "application_run_id": application_run_id,
                "application_status": "partial",
                "completed_trial_count": completed,
                "config_sha256": CONFIG_SHA256,
                "decoder_sha256": DECODER_SHA256,
                "application_source_sha256": self.application_source_sha256,
                "runtime_kind": "sdk_simulator",
                "evidence_level": "E3",
                "biological_claim": False,
            }
            self._artifacts = [self._artifact("partial", partial)]
            self._last_status = "failed"
            raise
        finally:
            self._active_port = None

        summary = {
            "application_id": APPLICATION_ID,
            "application_run_id": result.run_id,
            "application_status": "complete",
            "config_sha256": result.config_sha256,
            "decoder_sha256": result.decoder_sha256,
            "application_source_sha256": self.application_source_sha256,
            "runtime_kind": result.runtime_kind,
            "evidence_level": result.evidence_ceiling,
            "biological_claim": False,
            "trial_count": len(result.trials),
            "scored_trial_count": sum(
                trial.expected_label is not None for trial in result.trials
            ),
            "sham_trial_count": result.sham_trial_count,
            "pipeline_assertion_accuracy": result.accuracy,
        }
        self._artifacts = [self._artifact("result", summary)]
        self._last_status = "complete"
        return AdapterInvocationResult(
            backend_id=self.backend_id,
            task_id=task.task_id,
            output_payload={"application_summary": summary},
            confidence=None,
            execution_latency_ms=(perf_counter() - started) * 1000.0,
            backend_state="ready",
            notes="Deterministic E3 pipeline assertion; no biological claim.",
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        return {
            "readiness_state": "ready",
            "health_status": "healthy",
            "age_of_information_ms": self.telemetry_age_ms,
            "application_status": self._last_status,
            "runtime_kind": "sdk_simulator",
            "evidence_level": "E3",
            "biological_claim": False,
        }

    def reset(self, mode: ResetMode | None = None) -> bool:
        self._prepared_task_id = None
        self._active_port = None
        self._last_status = "idle"
        self._aborted = False
        return True

    def recalibrate(self) -> bool:
        return False

    def abort(self) -> bool:
        self._aborted = True
        if self._active_port is not None:
            self._active_port.abort("CP2N2 controlled abort")
        self._last_status = "aborted"
        return True

    def artifacts(self) -> list[RuntimeArtifact]:
        return [item.model_copy(deep=True) for item in self._artifacts]

    def _validate_task(self, task: TaskRequest) -> None:
        if task.direct_backend_id != self.backend_id:
            raise ValueError("BioPattern Gate E3 requires its bound backend")
        expected_metadata = {
            **TASK_METADATA,
            "application_source_sha256": self.application_source_sha256,
        }
        if task.metadata != expected_metadata:
            unknown = sorted(set(task.metadata) - set(expected_metadata))
            if unknown:
                raise ValueError(
                    f"unapproved BioPattern Gate task metadata: {unknown}"
                )
            raise ValueError("BioPattern Gate task metadata/hash binding mismatch")

    @staticmethod
    def _artifact(label: str, payload: dict[str, object]) -> RuntimeArtifact:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return RuntimeArtifact(
            artifact_id=f"biopattern-gate-{label}-{digest[:16]}",
            kind=f"biopattern_gate_{label}_summary",
            uri=f"memory://biopattern-gate/{digest}",
            media_type="application/json",
            metadata={
                "sha256": digest,
                "application_status": payload["application_status"],
                "completed_trial_count": payload.get(
                    "completed_trial_count", payload.get("trial_count", 0)
                ),
                "runtime_kind": "sdk_simulator",
                "evidence_level": "E3",
                "biological_claim": False,
                "application_source_sha256": payload.get(
                    "application_source_sha256"
                ),
            },
        )
