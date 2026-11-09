"""Cortical Labs adapter for the CP²N² prototype."""

from __future__ import annotations

from datetime import datetime

from adapters.base_adapter import AdapterInvocationResult, AdapterPreparationResult, BaseAdapter
from adapters.contracts import (
    DeploymentMode,
    ReservationMode,
    make_adapter_capability_declaration,
)
from backends.cortical.cl_client import CLClient
from core.task_model import TaskRequest
from descriptors.capability_schema import (
    CapabilityDescriptor,
    IOContract,
    IOEncoding,
    LifecycleContract,
    Locality,
    ObservabilityLevel,
    PolicyConstraints,
    ResetMode,
    SignalModality,
    SubstrateClass,
    SubstrateDescriptor,
    TelemetryContract,
    TelemetryField,
    TenancyModel,
    TimingContract,
    TimingRegime,
    TrainingMode,
    TwinBinding,
)
from descriptors.resource_contract import (
    EXPECTED_EVIDENCE_LEVEL,
    EvidenceLevel,
    ObservationSource,
    RuntimeKind,
)
from runtimes.cortical_labs_runtime import CorticalLabsRuntime


class CorticalLabsAdapter(BaseAdapter):
    def __init__(
        self,
        backend_id: str = "cortical-labs-backend",
        *,
        use_simulator: bool | None = True,
    ) -> None:
        self._expected_runtime_kind = (
            "sdk_simulator"
            if use_simulator is True
            else "physical_hardware"
            if use_simulator is False
            else "unknown"
        )
        descriptor = self._build_descriptor(
            backend_id=backend_id,
            expected_runtime_kind=self._expected_runtime_kind,
        )
        runtime = CorticalLabsRuntime(
            backend_id,
            use_simulator=use_simulator,
        )
        evidence_ceiling = (
            EvidenceLevel.E3_SDK_SIMULATOR
            if use_simulator is True
            else EvidenceLevel.E5_PHYSICAL_HARDWARE
            if use_simulator is False
            else EvidenceLevel.E0_MOCK
        )
        super().__init__(
            descriptor=descriptor,
            runtime=runtime,
            capability_declaration=make_adapter_capability_declaration(
                adapter_id=f"{self.__class__.__module__}.{self.__class__.__qualname__}",
                descriptor=descriptor,
                runtime=runtime.capabilities,
                evidence_ceiling=evidence_ceiling,
                reservation_mode=ReservationMode.CONTROL_PLANE_LEASE,
                deployment_mode=DeploymentMode.PROVIDER_MANAGED,
                notes=(
                    "Optional CL SDK control adapter; simulator and physical "
                    "runtime evidence remain explicitly distinguished."
                ),
            ),
            runtime_kind=RuntimeKind.UNKNOWN,
            provider_id="cortical-labs",
            attestation_method="cl_sdk_is_simulator",
            telemetry_source=ObservationSource.OBSERVED,
        )

    @property
    def _cl_runtime(self) -> CorticalLabsRuntime:
        return self.runtime  # type: ignore[return-value]

    @property
    def _client(self) -> CLClient:
        """Compatibility seam retained for existing tests and demos."""
        return self._cl_runtime.client

    @_client.setter
    def _client(self, value: CLClient) -> None:
        self._cl_runtime.client = value

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

    def abort(self) -> bool:
        """Close the active CL session without issuing further stimulation."""
        return super().abort()

    def abort_supported(self) -> bool:
        return super().abort_supported()

    def _resource_contract_runtime_evidence(
        self,
    ) -> tuple[RuntimeKind, EvidenceLevel, str, datetime | None, dict]:
        try:
            runtime_kind = RuntimeKind(self._cl_runtime.last_runtime_kind)
        except ValueError:
            runtime_kind = RuntimeKind.UNKNOWN
        method = (
            "cl_sdk_is_simulator"
            if runtime_kind != RuntimeKind.UNKNOWN
            else "runtime_not_yet_attested"
        )
        return (
            runtime_kind,
            EXPECTED_EVIDENCE_LEVEL[runtime_kind],
            method,
            self._cl_runtime.last_attested_at,
            {"configured_expectation": self._expected_runtime_kind},
        )

    @staticmethod
    def _build_descriptor(
        backend_id: str,
        expected_runtime_kind: str,
    ) -> SubstrateDescriptor:
        return SubstrateDescriptor(
            backend_id=backend_id,
            display_name="Cortical Labs CL API Backend",
            version="0.1.1",
            description=(
                "Optional adapter targeting the public Cortical Labs CL API / "
                "CL SDK. The active runtime is attested as simulator, physical "
                "hardware, or unknown before a session is opened."
            ),
            input_contracts=[
                IOContract(
                    name="stimulation_input",
                    modality=SignalModality.SPIKES,
                    encoding=IOEncoding.EVENT_STREAM,
                    description="Spike-oriented stimulation requests mapped onto CL API stimulation calls.",
                ),
                IOContract(
                    name="control_input",
                    modality=SignalModality.CONTROL_SIGNAL,
                    encoding=IOEncoding.JSON,
                    required=False,
                    description="Optional session and recording configuration metadata.",
                ),
            ],
            output_contracts=[
                IOContract(
                    name="recording_and_state",
                    modality=SignalModality.SPIKES,
                    encoding=IOEncoding.JSON,
                    description="Recording metadata and session-visible wetware state.",
                )
            ],
            timing=TimingContract(
                regime=TimingRegime.MILLISECONDS,
                typical_latency_ms=100.0,
                latency_jitter_ms=20.0,
                warmup_required=True,
                streaming_supported=True,
            ),
            lifecycle=LifecycleContract(
                supported_reset_modes=[ResetMode.SOFT_RESET, ResetMode.REST, ResetMode.RECALIBRATE],
                reprogrammable=True,
                recalibration_supported=True,
                stateful=True,
                notes="Physical reset and wetware handling remain application-specific; the adapter models session-level recovery.",
            ),
            telemetry=TelemetryContract(
                metrics=[
                    TelemetryField(
                        name="backend_latency_ms",
                        units="ms",
                        description="Most recent CL API round-trip latency including the observation cycle.",
                        lower_is_better=True,
                    ),
                    TelemetryField(
                        name="observation_latency_ms",
                        units="ms",
                        description="Latency measured from stimulation until the observation cycle completes.",
                        lower_is_better=True,
                    ),
                    TelemetryField(
                        name="readiness_state",
                        units="state",
                        description="Current readiness state of the Cortical Labs session.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="health_status",
                        units="state",
                        description="Provider health status when available; otherwise unknown.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="recording_path",
                        units="path",
                        description="Path to the most recent recording artifact when available.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="channel_count",
                        units="count",
                        description="Channel count reported by the active CL session.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="fps",
                        units="frames_per_second",
                        description="Frames per second reported by the CL session.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="runtime_kind",
                        units="kind",
                        description="Attested CL runtime: sdk_simulator, physical_hardware, or unknown.",
                        lower_is_better=None,
                    ),
                    TelemetryField(
                        name="age_of_information_ms",
                        units="ms",
                        description="Elapsed time since local session attributes were observed.",
                        lower_is_better=True,
                    ),
                    TelemetryField(
                        name="telemetry_source",
                        units="source",
                        description="Provenance of the reported telemetry snapshot.",
                        lower_is_better=None,
                    ),
                ],
                supports_health_status=False,
                supports_confidence=False,
                supports_drift_reporting=False,
                supports_age_of_information=True,
            ),
            twin_binding=TwinBinding(
                twin_kind=expected_runtime_kind,
                fidelity_level="interface_compatibility_only",
                calibration_confidence=0.0,
                twin_notes=(
                    "No substrate calibration confidence is inferred by this adapter. "
                    "The active runtime is attested at session open."
                ),
            ),
            policy=PolicyConstraints(
                locality=Locality.LAB,
                tenancy=TenancyModel.RESERVED,
                safety_notes="Wetware access with explicit stimulation and recording semantics.",
                exclusive_access_required=True,
                human_supervision_required=True,
            ),
            capability=CapabilityDescriptor(
                substrate_class=SubstrateClass.WETWARE,
                supported_task_types=["monitoring", "control", "temporal_inference"],
                training_mode=TrainingMode.HYBRID,
                observability=ObservabilityLevel.PARTIAL,
                stochastic=True,
                resettable=True,
                programmable=True,
                health_sensitive=True,
                repeated_invocation_supported=True,
            ),
            custom_metadata={
                "paper_role": "existing wetware API integration target",
                "sdk_package": "cl-sdk",
                "expected_runtime_kind": expected_runtime_kind,
                "evidence_level": "E3" if expected_runtime_kind == "sdk_simulator" else "unattested",
            },
        )
