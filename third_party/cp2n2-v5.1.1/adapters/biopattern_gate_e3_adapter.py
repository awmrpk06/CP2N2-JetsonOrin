"""CP²N² adapter for the frozen BioPattern Gate E3 application."""

from __future__ import annotations

from adapters.base_adapter import BaseAdapter
from adapters.contracts import make_adapter_capability_declaration
from applications.biopattern_gate.control_plane_contract import BACKEND_ID
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
    EvidenceLevel,
    ObservationSource,
    RuntimeKind,
)
from runtimes.biopattern_gate_e3_runtime import BioPatternGateE3Runtime


class BioPatternGateE3Adapter(BaseAdapter):
    """Expose one exact application contract, not generic CL primitives."""

    def __init__(
        self,
        backend_id: str = BACKEND_ID,
        *,
        telemetry_age_ms: float = 0.0,
        fail_after_trials: int | None = None,
    ) -> None:
        descriptor = self._build_descriptor(backend_id)
        runtime = BioPatternGateE3Runtime(
            backend_id=backend_id,
            telemetry_age_ms=telemetry_age_ms,
            fail_after_trials=fail_after_trials,
        )
        super().__init__(
            descriptor,
            runtime=runtime,
            capability_declaration=make_adapter_capability_declaration(
                adapter_id=(
                    f"{self.__class__.__module__}.{self.__class__.__qualname__}"
                ),
                descriptor=descriptor,
                runtime=runtime.capabilities,
                evidence_ceiling=EvidenceLevel.E3_SDK_SIMULATOR,
                notes=(
                    "Frozen BioPattern Gate application running in the local "
                    "Cortical Labs SDK-compatible E3 simulator boundary."
                ),
            ),
            runtime_kind=RuntimeKind.SDK_SIMULATOR,
            provider_id="cortical-labs-sdk-simulator",
            attestation_method="frozen_application_config_and_decoder_sha256",
            telemetry_source=ObservationSource.OBSERVED,
        )

    @staticmethod
    def _build_descriptor(backend_id: str) -> SubstrateDescriptor:
        return SubstrateDescriptor(
            backend_id=backend_id,
            display_name="BioPattern Gate (Cortical Labs E3 simulator)",
            version="1.0.0",
            description=(
                "Frozen temporal-pattern routing application for control-plane "
                "validation. It is simulator evidence, not a biological result."
            ),
            input_contracts=[
                IOContract(
                    name="server_owned_pattern_gate_preset",
                    modality=SignalModality.SPIKES,
                    encoding=IOEncoding.JSON,
                    description=(
                        "Logical server-owned assay reference; no agent-editable "
                        "channels, electrodes, amplitudes, or pulse parameters."
                    ),
                )
            ],
            output_contracts=[
                IOContract(
                    name="sanitized_gate_summary",
                    modality=SignalModality.TELEMETRY_STREAM,
                    encoding=IOEncoding.JSON,
                    description="Provenance-bearing aggregate application result.",
                )
            ],
            timing=TimingContract(
                regime=TimingRegime.MILLISECONDS,
                typical_latency_ms=25.0,
                latency_jitter_ms=5.0,
                warmup_required=True,
                streaming_supported=False,
            ),
            lifecycle=LifecycleContract(
                supported_reset_modes=[ResetMode.SOFT_RESET],
                reprogrammable=False,
                recalibration_supported=False,
                stateful=True,
                notes="One prepared frozen application run per exclusive lease.",
            ),
            telemetry=TelemetryContract(
                metrics=[
                    TelemetryField(
                        name="readiness_state",
                        units="state",
                        description="Normalized readiness state.",
                    ),
                    TelemetryField(
                        name="health_status",
                        units="state",
                        description="E3 runtime health.",
                    ),
                    TelemetryField(
                        name="age_of_information_ms",
                        units="ms",
                        description="Freshness of the runtime snapshot.",
                        lower_is_better=True,
                    ),
                    TelemetryField(
                        name="application_status",
                        units="state",
                        description="Last BioPattern Gate application state.",
                    ),
                ],
                supports_health_status=True,
                supports_confidence=False,
                supports_drift_reporting=False,
                supports_age_of_information=True,
            ),
            twin_binding=TwinBinding(
                twin_kind="deterministic_reservoir_sdk_simulator",
                fidelity_level="technical-e3",
                calibration_confidence=1.0,
                twin_notes="Access-independent pipeline validation only.",
            ),
            policy=PolicyConstraints(
                locality=Locality.LOCAL,
                tenancy=TenancyModel.RESERVED,
                safety_notes=(
                    "Simulator-only application boundary; physical parameters "
                    "are absent from the MCP schema and task contract."
                ),
                exclusive_access_required=True,
                human_supervision_required=False,
            ),
            capability=CapabilityDescriptor(
                substrate_class=SubstrateClass.WETWARE,
                supported_task_types=["control"],
                training_mode=TrainingMode.EX_SITU,
                observability=ObservabilityLevel.HIGH,
                stochastic=False,
                resettable=True,
                programmable=False,
                health_sensitive=False,
                repeated_invocation_supported=True,
            ),
            custom_metadata={
                "application_id": "cp2n2-biopattern-gate",
                "runtime_kind": "sdk_simulator",
                "evidence_ceiling": "E3",
                "biological_claim": False,
            },
        )

