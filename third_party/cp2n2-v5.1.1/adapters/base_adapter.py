"""Abstract adapter interface for CP²N² backend integrations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.contracts import (
    AdapterCapabilityDeclaration,
    AdapterInvocationResult,
    AdapterPreparationResult,
    RuntimeArtifact,
)
from core.task_model import TaskRequest
from descriptors.capability_schema import ResetMode, SubstrateDescriptor
from descriptors.resource_contract import (
    CONTRACT_SCHEMA_VERSION,
    EXPECTED_EVIDENCE_LEVEL,
    AccessContract,
    BillingModel,
    CostContract,
    DataGovernanceContract,
    EvidenceLevel,
    ObservationSource,
    PhysicalNeuralResourceContract,
    ResourceIdentity,
    ResourceLifecycleState,
    ResourceState,
    RuntimeEvidence,
    RuntimeKind,
    SafetyContract,
    SafetyLimit,
    TelemetryObservation,
    Uncertainty,
    UncertaintyKind,
)
from runtimes.base_runtime import SubstrateRuntime


class BaseAdapter:
    """Common adapter interface used by the CP²N² control plane.

    Each concrete adapter translates generic control-plane operations into the
    substrate-specific behavior of one backend or one digital twin.
    """

    def __init__(
        self,
        descriptor: SubstrateDescriptor,
        *,
        runtime: SubstrateRuntime,
        capability_declaration: AdapterCapabilityDeclaration,
        runtime_kind: RuntimeKind = RuntimeKind.SYNTHETIC_TWIN,
        provider_id: str = "cp2n2",
        hardware_id: str | None = None,
        attestation_method: str = "in_process_adapter_construction",
        telemetry_source: ObservationSource = ObservationSource.ESTIMATED,
    ) -> None:
        self._descriptor = descriptor
        self._runtime = runtime
        self._capability_declaration = capability_declaration
        self._contract_runtime_kind = runtime_kind
        self._contract_provider_id = provider_id
        self._contract_hardware_id = hardware_id
        self._contract_attestation_method = attestation_method
        self._contract_telemetry_source = telemetry_source
        self._contract_attested_at = (
            None if runtime_kind == RuntimeKind.UNKNOWN else datetime.now(timezone.utc)
        )
        self.validate_conformance()

    @property
    def descriptor(self) -> SubstrateDescriptor:
        """Return the immutable descriptor published by this adapter."""
        return self._descriptor

    @property
    def runtime(self) -> SubstrateRuntime:
        """Return the separate substrate-side runtime bound to this adapter."""
        return self._runtime

    @property
    def capability_declaration(self) -> AdapterCapabilityDeclaration:
        """Return the required, versioned A5 adapter capability declaration."""
        return self._capability_declaration.model_copy(deep=True)

    def validate_conformance(self) -> bool:
        """Validate structural alignment of adapter, descriptor, and runtime."""

        declaration = self._capability_declaration
        if not declaration.explicit:
            raise ValueError("adapter capability declarations must be explicit")
        if declaration.backend_id != self._descriptor.backend_id:
            raise ValueError(
                "capability declaration backend_id must match the descriptor"
            )
        if declaration.substrate_class != str(
            self._descriptor.capability.substrate_class
        ):
            raise ValueError(
                "capability declaration substrate_class must match the descriptor"
            )
        if declaration.runtime != self._runtime.capabilities:
            raise ValueError(
                "adapter capability declaration must embed the bound runtime declaration"
            )
        if self._runtime is self:
            raise ValueError("control adapter and substrate runtime must be separate")
        return True

    def backend_id(self) -> str:
        """Return the backend identifier."""
        return self._descriptor.backend_id

    def resource_contract(self) -> PhysicalNeuralResourceContract:
        """Publish a versioned Physical Neural Resource Contract.

        The existing ``describe()`` method remains the compatibility API.
        This method adds evidence, provenance, and conservative safety
        semantics without changing substrate-specific invocation behavior.
        """

        now = datetime.now(timezone.utc)
        telemetry = self.collect_telemetry()
        source = self._resource_contract_telemetry_source()
        units = {metric.name: metric.units for metric in self.descriptor.telemetry.metrics}
        valid_until = now + timedelta(seconds=30)

        observations = {
            name: self._make_observation(
                value=value,
                unit=units.get(name),
                source=source,
                now=now,
                valid_until=valid_until,
            )
            for name, value in telemetry.items()
        }

        health_value = telemetry.get("health_status", "unknown")
        health_source = source if "health_status" in telemetry else ObservationSource.CONFIGURED
        health = self._make_observation(
            value=health_value,
            unit="state",
            source=health_source,
            now=now,
            valid_until=valid_until,
        )
        lifecycle_value = self._normalize_lifecycle_state(
            telemetry.get("readiness_state"),
            health_value,
        )
        lifecycle_source = (
            source
            if "readiness_state" in telemetry or "health_status" in telemetry
            else ObservationSource.CONFIGURED
        )
        lifecycle = self._make_observation(
            value=lifecycle_value,
            unit="state",
            source=lifecycle_source,
            now=now,
            valid_until=valid_until,
        )

        runtime_kind, evidence_level, method, attested_at, details = (
            self._resource_contract_runtime_evidence()
        )
        safety = SafetyContract(
            permitted_operations=self._resource_contract_permitted_operations(),
            hard_limits=self._resource_contract_hard_limits(),
            human_supervision_required=self.descriptor.policy.human_supervision_required,
            operator_acknowledgement_required=(
                True if self.descriptor.policy.human_supervision_required else False
            ),
            emergency_stop_supported=None,
            exclusive_access_required=self.descriptor.policy.exclusive_access_required,
            notes=self.descriptor.policy.safety_notes,
        )

        return PhysicalNeuralResourceContract(
            schema_version=CONTRACT_SCHEMA_VERSION,
            published_at=now,
            identity=ResourceIdentity(
                resource_id=self.backend_id(),
                provider_id=self._contract_provider_id,
                adapter_id=self._capability_declaration.adapter_id,
                substrate_id=str(self.descriptor.capability.substrate_class),
                hardware_id=self._contract_hardware_id,
            ),
            evidence=RuntimeEvidence(
                runtime_kind=runtime_kind,
                evidence_level=evidence_level,
                attestation_method=method,
                attested_at=attested_at,
                attestation_details=details,
            ),
            capabilities=self.describe(),
            state=ResourceState(
                lifecycle=lifecycle,
                occupancy=observations.get("occupancy"),
                health=health,
                calibration=observations.get("calibration_confidence"),
                drift=observations.get("drift_score"),
            ),
            telemetry=observations,
            safety=safety,
            access=AccessContract(
                locality=str(self.descriptor.policy.locality),
                tenancy=str(self.descriptor.policy.tenancy),
                authentication_required=None,
                reservation_required=(
                    True if str(self.descriptor.policy.tenancy) == "reserved" else False
                ),
            ),
            cost=CostContract(
                billing_model=BillingModel.UNKNOWN,
                notes="No cost information has been published by this prototype adapter.",
            ),
            data_governance=DataGovernanceContract(
                notes="No provider data-retention declaration is available."
            ),
        )

    def _resource_contract_runtime_evidence(
        self,
    ) -> tuple[RuntimeKind, EvidenceLevel, str, datetime | None, dict[str, Any]]:
        """Return runtime evidence; concrete adapters may override this hook."""

        return (
            self._contract_runtime_kind,
            EXPECTED_EVIDENCE_LEVEL[self._contract_runtime_kind],
            self._contract_attestation_method,
            self._contract_attested_at,
            {},
        )

    def _resource_contract_telemetry_source(self) -> ObservationSource:
        return self._contract_telemetry_source

    def _resource_contract_hard_limits(self) -> dict[str, SafetyLimit]:
        """Return explicitly known hard limits; an empty mapping means unknown."""

        return {}

    def _resource_contract_permitted_operations(self) -> list[str]:
        operations = ["describe", "collect_telemetry", "prepare", "invoke"]
        if self.descriptor.lifecycle.supported_reset_modes:
            operations.append("reset")
        if self.descriptor.lifecycle.recalibration_supported:
            operations.append("recalibrate")
        if self.abort_supported():
            operations.append("abort")
        return operations

    @staticmethod
    def _make_observation(
        *,
        value: Any,
        unit: str | None,
        source: ObservationSource,
        now: datetime,
        valid_until: datetime | None,
    ) -> TelemetryObservation:
        return TelemetryObservation(
            value=value,
            unit=unit,
            source=source,
            observed_at=None if source == ObservationSource.CONFIGURED else now,
            received_at=now,
            uncertainty=Uncertainty(kind=UncertaintyKind.UNKNOWN),
            valid_until=valid_until,
        )

    @staticmethod
    def _normalize_lifecycle_state(readiness: Any, health: Any) -> str:
        candidate = str(readiness or health or "unknown").strip().lower()
        aliases = {
            "healthy": ResourceLifecycleState.READY.value,
            "unavailable": ResourceLifecycleState.UNREACHABLE.value,
            "error": ResourceLifecycleState.FAILED.value,
            "rejected": ResourceLifecycleState.FAILED.value,
        }
        candidate = aliases.get(candidate, candidate)
        allowed = {state.value for state in ResourceLifecycleState}
        if candidate in allowed:
            return candidate
        return ResourceLifecycleState.UNKNOWN.value

    def describe(self) -> SubstrateDescriptor:
        """Return the published substrate descriptor."""
        return self.descriptor

    def prepare(self, task: TaskRequest) -> AdapterPreparationResult:
        """Ask the bound runtime to prepare for one task."""
        return self._runtime.prepare(task)

    def invoke(self, task: TaskRequest) -> AdapterInvocationResult:
        """Delegate time-critical execution to the bound runtime."""
        return self._runtime.execute(task)

    def collect_telemetry(self) -> dict[str, float | int | str | bool | None]:
        """Return the latest runtime telemetry snapshot."""
        return self._runtime.telemetry()

    def reset(self, mode: ResetMode | None = None) -> bool:
        """Reset or recover the bound runtime."""
        return self._runtime.reset(mode=mode)

    def recalibrate(self) -> bool:
        """Trigger runtime recalibration if supported."""
        return self._runtime.recalibrate()

    def abort(self) -> bool:
        """Attempt to stop an uncertain or active operation.

        Adapters must override this method only when their provider API offers
        a meaningful abort or session-close operation. Returning ``False`` is
        conservative: the control plane will not infer successful cancellation.
        """

        return self._runtime.abort()

    def abort_supported(self) -> bool:
        """Return whether ``abort()`` has provider-level semantics."""

        return self._runtime.capabilities.provider_abort_supported

    def deployment_status(self) -> dict[str, str]:
        """Return the declared deployment relationship without deploying work."""

        return {
            "mode": str(self._capability_declaration.deployment_mode),
            "runtime_id": self._runtime.capabilities.runtime_id,
        }

    def list_artifacts(self) -> list[RuntimeArtifact]:
        """Return normalized artifacts exposed by the bound runtime."""

        return [item.model_copy(deep=True) for item in self._runtime.artifacts()]
