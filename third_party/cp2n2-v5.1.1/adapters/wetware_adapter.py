"""Wetware backend adapter for the CP²N² prototype."""

from __future__ import annotations

from adapters.base_adapter import AdapterInvocationResult, AdapterPreparationResult, BaseAdapter
from adapters.contracts import make_adapter_capability_declaration
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
from descriptors.resource_contract import EvidenceLevel
from runtimes.twin_runtimes import WetwareTwinRuntime
from twins.wetware_twin import WetwareTwin


class WetwareAdapter(BaseAdapter):
    """Adapter exposing a closed-loop wetware-inspired backend."""

    def __init__(self, backend_id: str = "wetware-backend", twin: WetwareTwin | None = None) -> None:
        descriptor = self._build_descriptor(backend_id=backend_id)
        runtime = WetwareTwinRuntime(backend_id, twin=twin)
        self._twin = runtime.twin
        super().__init__(
            descriptor=descriptor,
            runtime=runtime,
            capability_declaration=make_adapter_capability_declaration(
                adapter_id=f"{self.__class__.__module__}.{self.__class__.__qualname__}",
                descriptor=descriptor,
                runtime=runtime.capabilities,
                evidence_ceiling=EvidenceLevel.E1_SYNTHETIC_TWIN,
                notes="Generic local wetware-twin control adapter.",
            ),
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

    @staticmethod
    def _build_descriptor(backend_id: str) -> SubstrateDescriptor:
        return SubstrateDescriptor(
            backend_id=backend_id,
            display_name="Wetware Twin Backend",
            version="0.1.0",
            description="A spike-response backend with viability-sensitive lifecycle behavior.",
            input_contracts=[
                IOContract(
                    name="stimulation_input",
                    modality=SignalModality.SPIKES,
                    encoding=IOEncoding.EVENT_STREAM,
                    description="Stimulus pattern represented as a spike/event stream abstraction.",
                ),
                IOContract(
                    name="control_input",
                    modality=SignalModality.CONTROL_SIGNAL,
                    encoding=IOEncoding.JSON,
                    required=False,
                    description="Optional observation and stimulation control metadata.",
                ),
            ],
            output_contracts=[
                IOContract(
                    name="response_output",
                    modality=SignalModality.SPIKES,
                    encoding=IOEncoding.JSON,
                    description="Observed response summary and decoded state.",
                )
            ],
            timing=TimingContract(
                regime=TimingRegime.MILLISECONDS,
                typical_latency_ms=45.0,
                latency_jitter_ms=10.0,
                warmup_required=True,
                streaming_supported=True,
            ),
            lifecycle=LifecycleContract(
                supported_reset_modes=[ResetMode.REST, ResetMode.RECALIBRATE, ResetMode.HARD_RESET],
                reprogrammable=False,
                recalibration_supported=True,
                stateful=True,
                notes="Repeated stimulation degrades viability and may require rest.",
            ),
            telemetry=TelemetryContract(
                metrics=[
                    TelemetryField(name="viability_score", units="fraction", description="Backend viability proxy."),
                    TelemetryField(name="noise_level", units="fraction", description="Signal noise proxy.", lower_is_better=True),
                    TelemetryField(name="response_delay_ms", units="ms", description="Most recent response delay.", lower_is_better=True),
                ],
                supports_health_status=True,
                supports_confidence=True,
                supports_drift_reporting=True,
                supports_age_of_information=False,
            ),
            twin_binding=TwinBinding(
                twin_kind="synthetic_spike_response",
                fidelity_level="lightweight",
                calibration_confidence=0.84,
                twin_notes="Synthetic spike-response twin used to validate closed-loop lifecycle semantics.",
            ),
            policy=PolicyConstraints(
                locality=Locality.LAB,
                tenancy=TenancyModel.RESERVED,
                safety_notes="Represents a sensitive backend that may need careful handling and observation.",
                exclusive_access_required=True,
                human_supervision_required=True,
            ),
            capability=CapabilityDescriptor(
                substrate_class=SubstrateClass.WETWARE,
                supported_task_types=["classification", "monitoring", "control", "temporal_inference"],
                training_mode=TrainingMode.HYBRID,
                observability=ObservabilityLevel.PARTIAL,
                stochastic=True,
                resettable=True,
                programmable=False,
                health_sensitive=True,
                repeated_invocation_supported=True,
            ),
            custom_metadata={"paper_role": "closed-loop state-sensitive substrate regime"},
        )
