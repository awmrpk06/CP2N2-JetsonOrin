"""Server-owned assay presets used by the constrained MCP surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.task_model import OutputPreference, TaskKind, TaskRequest
from descriptors.capability_schema import SignalModality, SubstrateDescriptor
from mcp_surface.models import AssayPresetId


@dataclass(frozen=True)
class AssayPreset:
    """A bounded assay definition that exposes no physical control primitive."""

    preset_id: AssayPresetId
    description: str
    compatible_substrates: frozenset[str]
    task_kind: TaskKind
    modalities: tuple[SignalModality, ...]
    preferred_output: OutputPreference
    latency_budget_ms: float
    metadata: dict[str, Any]
    continuous_monitoring_required: bool = False
    required_telemetry_fields: tuple[str, ...] = ()
    human_supervision_available: bool = False
    compatible_backend_ids: frozenset[str] = frozenset()
    max_twin_age_ms: float | None = None

    def is_compatible(self, descriptor: SubstrateDescriptor) -> bool:
        if str(descriptor.capability.substrate_class) not in self.compatible_substrates:
            return False
        return (
            not self.compatible_backend_ids
            or descriptor.backend_id in self.compatible_backend_ids
        )

    def build_task(
        self,
        *,
        run_id: str,
        principal_id: str,
        resource_id: str,
        lease_id: str | None,
        expected_lease_version: int | None,
    ) -> TaskRequest:
        return TaskRequest(
            task_id=f"mcp-assay-{run_id}",
            client_id=principal_id,
            task_kind=self.task_kind,
            summary=f"Constrained MCP assay preset {self.preset_id.value}.",
            required_input_modalities=list(self.modalities),
            preferred_output=self.preferred_output,
            latency_budget_ms=self.latency_budget_ms,
            continuous_monitoring_required=self.continuous_monitoring_required,
            required_telemetry_fields=list(self.required_telemetry_fields),
            human_supervision_available=self.human_supervision_available,
            allow_fallback=False,
            direct_backend_id=resource_id,
            lease_id=lease_id,
            expected_lease_version=expected_lease_version,
            max_twin_age_ms=self.max_twin_age_ms,
            metadata=dict(self.metadata),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id.value,
            "description": self.description,
            "task_kind": self.task_kind.value,
            "compatible_substrates": sorted(self.compatible_substrates),
            "physical_parameters_agent_editable": False,
        }


ASSAY_PRESETS: dict[AssayPresetId, AssayPreset] = {
    AssayPresetId.EDGE_VECTOR_CLASSIFICATION_V1: AssayPreset(
        preset_id=AssayPresetId.EDGE_VECTOR_CLASSIFICATION_V1,
        description="Fixed-vector low-latency classification assay.",
        compatible_substrates=frozenset({"edge_accelerator"}),
        task_kind=TaskKind.CLASSIFICATION,
        modalities=(SignalModality.DIGITAL_VECTOR,),
        preferred_output=OutputPreference.SCORE,
        latency_budget_ms=20.0,
        metadata={"input_vector": [0.1, 0.3, 0.5, 0.7]},
    ),
    AssayPresetId.CHEMICAL_SENSING_V1: AssayPreset(
        preset_id=AssayPresetId.CHEMICAL_SENSING_V1,
        description="Fixed-input concentration sensing assay.",
        compatible_substrates=frozenset({"chemical"}),
        task_kind=TaskKind.SENSING,
        modalities=(SignalModality.CONCENTRATION,),
        preferred_output=OutputPreference.STATE_ESTIMATE,
        latency_budget_ms=15_000.0,
        metadata={"input_level": 1.4},
    ),
    AssayPresetId.WETWARE_TEMPORAL_PROBE_V1: AssayPreset(
        preset_id=AssayPresetId.WETWARE_TEMPORAL_PROBE_V1,
        description="Bounded temporal-response preset for wetware-class resources.",
        compatible_substrates=frozenset({"wetware"}),
        task_kind=TaskKind.TEMPORAL_INFERENCE,
        modalities=(SignalModality.SPIKES,),
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=120.0,
        metadata={
            "stimulation_strength": 0.65,
            "observation_window_ms": 140.0,
        },
        continuous_monitoring_required=True,
        required_telemetry_fields=("health_status", "drift_score"),
        human_supervision_available=True,
    ),
    AssayPresetId.PATTERN_GATE_V1: AssayPreset(
        preset_id=AssayPresetId.PATTERN_GATE_V1,
        description=(
            "BioPattern Gate: a blinded temporal-pattern routing assay with a "
            "frozen readout; physical controls are never agent inputs."
        ),
        compatible_substrates=frozenset({"wetware"}),
        compatible_backend_ids=frozenset(
            {"cortical-labs-biopattern-gate-e3"}
        ),
        task_kind=TaskKind.CONTROL,
        modalities=(SignalModality.SPIKES,),
        preferred_output=OutputPreference.TELEMETRY_AWARE_RESULT,
        latency_budget_ms=500.0,
        metadata={
            "assay_preset": "pattern_gate_v1",
            "application_id": "cp2n2-biopattern-gate",
            "config_id": "technical-e3",
            "config_sha256": (
                "5fccaac3022e223fc181508833eaad387"
                "55279556b43b6b2df4e2f7e032a08e4"
            ),
            "decoder_sha256": (
                "42789a20ea16e048f1a23b28e601ff34"
                "45e64b125c48de8656b11e612991afbf"
            ),
            "application_source_sha256": (
                "fca1d699d09a57816ce7fefe073170c4"
                "a09e146155379bec0339d4119a26bbf7"
            ),
            "runtime_kind_required": "sdk_simulator",
            "evidence_ceiling": "E3",
        },
        required_telemetry_fields=("readiness_state", "health_status"),
        human_supervision_available=False,
        max_twin_age_ms=1_000.0,
    ),
}


def get_preset(preset_id: AssayPresetId | str) -> AssayPreset:
    return ASSAY_PRESETS[AssayPresetId(preset_id)]
