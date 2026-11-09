"""Fast edge backend adapter for the CP²N² prototype."""

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
from runtimes.twin_runtimes import EdgeTwinRuntime
from twins.edge_twin import EdgeTwin


class EdgeAdapter(BaseAdapter):
    """Adapter exposing a fast edge-accelerator-style backend."""

    def __init__(self, backend_id: str = "edge-backend", twin: EdgeTwin | None = None) -> None:
        descriptor = self._build_descriptor(backend_id=backend_id)
        runtime = EdgeTwinRuntime(backend_id, twin=twin)
        self._twin = runtime.twin
        super().__init__(
            descriptor=descriptor,
            runtime=runtime,
            capability_declaration=make_adapter_capability_declaration(
                adapter_id=f"{self.__class__.__module__}.{self.__class__.__qualname__}",
                descriptor=descriptor,
                runtime=runtime.capabilities,
                evidence_ceiling=EvidenceLevel.E1_SYNTHETIC_TWIN,
                notes="Generic local edge-twin control adapter.",
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
            display_name="Fast Edge Twin Backend",
            version="0.1.0",
            description="A low-latency vector/tensor backend with device-like reprogramming and drift semantics.",
            input_contracts=[
                IOContract(
                    name="vector_input",
                    modality=SignalModality.DIGITAL_VECTOR,
                    encoding=IOEncoding.VECTOR,
                    shape_hint=[4],
                    description="Digital vector input for low-latency inference.",
                ),
                IOContract(
                    name="tensor_input",
                    modality=SignalModality.TENSOR,
                    encoding=IOEncoding.VECTOR,
                    shape_hint=[4],
                    required=False,
                    description="Optional tensor-like input representation collapsed to a vector for this prototype.",
                ),
            ],
            output_contracts=[
                IOContract(
                    name="classification_output",
                    modality=SignalModality.DIGITAL_VECTOR,
                    encoding=IOEncoding.JSON,
                    description="Class probabilities and selected label index.",
                )
            ],
            timing=TimingContract(
                regime=TimingRegime.MILLISECONDS,
                typical_latency_ms=4.5,
                latency_jitter_ms=0.8,
                warmup_required=False,
                streaming_supported=True,
            ),
            lifecycle=LifecycleContract(
                supported_reset_modes=[ResetMode.SOFT_RESET, ResetMode.RECALIBRATE, ResetMode.HARD_RESET],
                reprogrammable=True,
                recalibration_supported=True,
                stateful=True,
                notes="Repeated use may increase drift, but the backend remains fast and easy to recover.",
            ),
            telemetry=TelemetryContract(
                metrics=[
                    TelemetryField(name="drift_score", units="fraction", description="Device drift proxy.", lower_is_better=True),
                    TelemetryField(name="last_latency_ms", units="ms", description="Most recent execution latency.", lower_is_better=True),
                    TelemetryField(name="energy_proxy_mj", units="mJ", description="Energy proxy for the last invocation.", lower_is_better=True),
                ],
                supports_health_status=True,
                supports_confidence=True,
                supports_drift_reporting=True,
                supports_age_of_information=False,
            ),
            twin_binding=TwinBinding(
                twin_kind="vector_inference_simulation",
                fidelity_level="lightweight",
                calibration_confidence=0.95,
                twin_notes="Simple fast-inference twin representing an edge-near accelerator regime.",
            ),
            policy=PolicyConstraints(
                locality=Locality.EDGE,
                tenancy=TenancyModel.MULTI_TENANT,
                safety_notes="Represents an edge-near low-latency backend.",
                exclusive_access_required=False,
                human_supervision_required=False,
            ),
            capability=CapabilityDescriptor(
                substrate_class=SubstrateClass.EDGE_ACCELERATOR,
                supported_task_types=["classification", "monitoring", "control", "temporal_inference"],
                training_mode=TrainingMode.PRETRAINED,
                observability=ObservabilityLevel.HIGH,
                stochastic=False,
                resettable=True,
                programmable=True,
                health_sensitive=False,
                repeated_invocation_supported=True,
            ),
            custom_metadata={"paper_role": "fast low-latency substrate regime"},
        )
