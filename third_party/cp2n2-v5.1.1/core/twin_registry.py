"""Twin/adapter registry for the CP²N² prototype."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from adapters.base_adapter import BaseAdapter
from adapters.contracts import AdapterCapabilityDeclaration
from core.errors import ControlPlaneErrorCode, ControlPlaneException
from core.idempotency import IdempotencyStore
from core.leases import InMemoryLeaseStore
from core.lifecycle import LifecycleStore
from descriptors.capability_schema import SubstrateDescriptor
from descriptors.resource_contract import (
    ObservationSource,
    PhysicalNeuralResourceContract,
    ResourceLifecycleState,
    TelemetryObservation,
    Uncertainty,
    UncertaintyKind,
)


class TwinRegistry:
    """Registry holding all currently available backend adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}
        self._lifecycle_store = LifecycleStore()
        self._lease_store = InMemoryLeaseStore()
        self._idempotency_store = IdempotencyStore()

    @property
    def lifecycle_store(self) -> LifecycleStore:
        return self._lifecycle_store

    @property
    def lease_store(self) -> InMemoryLeaseStore:
        return self._lease_store

    @property
    def idempotency_store(self) -> IdempotencyStore:
        return self._idempotency_store

    def register(self, adapter: BaseAdapter, *, overwrite: bool = False) -> None:
        """Register one backend adapter."""
        adapter.validate_conformance()
        backend_id = adapter.backend_id()
        if backend_id in self._adapters and not overwrite:
            raise ValueError(f"Backend '{backend_id}' is already registered.")
        if (
            backend_id in self._adapters
            and overwrite
            and self._lease_store.current(backend_id) is not None
        ):
            raise ControlPlaneException(
                ControlPlaneErrorCode.RESOURCE_BUSY,
                f"Backend '{backend_id}' cannot be replaced while leased.",
                retryable=True,
            )
        self._adapters[backend_id] = adapter
        snapshot = self._lifecycle_store.initialize(backend_id)
        if ResourceLifecycleState(snapshot.state) == ResourceLifecycleState.DISCOVERED:
            self._lifecycle_store.transition(
                backend_id,
                ResourceLifecycleState.READY,
                expected_version=snapshot.version,
                reason="adapter registered and discoverable",
            )

    def unregister(self, backend_id: str) -> None:
        """Remove one backend adapter from the registry."""
        if backend_id not in self._adapters:
            raise KeyError(f"Backend '{backend_id}' is not registered.")
        active_lease = self._lease_store.current(backend_id)
        if active_lease is not None:
            raise ControlPlaneException(
                ControlPlaneErrorCode.RESOURCE_BUSY,
                f"Backend '{backend_id}' cannot be removed while leased.",
                retryable=True,
            )
        del self._adapters[backend_id]
        self._lifecycle_store.remove(backend_id)

    def has_backend(self, backend_id: str) -> bool:
        """Return True if the backend is known."""
        return backend_id in self._adapters

    def get_adapter(self, backend_id: str) -> BaseAdapter:
        """Return the registered adapter for one backend."""
        try:
            return self._adapters[backend_id]
        except KeyError as exc:
            raise KeyError(f"Backend '{backend_id}' is not registered.") from exc

    def list_backend_ids(self) -> list[str]:
        """Return all registered backend identifiers."""
        return sorted(self._adapters.keys())

    def list_adapters(self) -> list[BaseAdapter]:
        """Return all registered adapters."""
        return [self._adapters[key] for key in self.list_backend_ids()]

    def list_descriptors(self) -> list[SubstrateDescriptor]:
        """Return descriptors for all registered backends."""
        return [adapter.describe() for adapter in self.list_adapters()]

    def list_resource_contracts(self) -> list[PhysicalNeuralResourceContract]:
        """Return versioned resource contracts for all registered backends."""
        return [
            self.resource_contract_for(adapter.backend_id())
            for adapter in self.list_adapters()
        ]

    def list_adapter_capability_declarations(
        self,
    ) -> list[AdapterCapabilityDeclaration]:
        """Return A5 control/runtime declarations in stable backend order."""

        return [
            adapter.capability_declaration
            for adapter in self.list_adapters()
        ]

    def adapter_capability_for(
        self,
        backend_id: str,
    ) -> AdapterCapabilityDeclaration:
        """Return one adapter's explicit A5 capability declaration."""

        return self.get_adapter(backend_id).capability_declaration

    def resource_contract_for(
        self,
        backend_id: str,
    ) -> PhysicalNeuralResourceContract:
        """Overlay control-plane lifecycle state on a provider contract."""

        contract = self.get_adapter(backend_id).resource_contract()
        snapshot = self._lifecycle_store.snapshot(backend_id)
        now = datetime.now(timezone.utc)
        lifecycle = TelemetryObservation(
            value=snapshot.state,
            unit="state",
            source=ObservationSource.OBSERVED,
            observed_at=now,
            received_at=now,
            uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
            valid_until=now + timedelta(seconds=30),
        )
        version = TelemetryObservation(
            value=snapshot.version,
            unit="version",
            source=ObservationSource.OBSERVED,
            observed_at=now,
            received_at=now,
            uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
            valid_until=now + timedelta(seconds=30),
        )
        state = contract.state.model_copy(update={"lifecycle": lifecycle})
        telemetry = dict(contract.telemetry)
        telemetry["control_plane_lifecycle"] = lifecycle
        telemetry["control_plane_state_version"] = version
        return contract.model_copy(
            update={"state": state, "telemetry": telemetry, "published_at": now}
        )

    def iter_adapters(self) -> Iterable[BaseAdapter]:
        """Yield all adapters in stable backend-id order."""
        for backend_id in self.list_backend_ids():
            yield self._adapters[backend_id]

    def telemetry_snapshot(self) -> dict[str, dict[str, float | int | str | bool | None]]:
        """Return a snapshot of telemetry across all registered backends."""
        return {
            adapter.backend_id(): adapter.collect_telemetry()
            for adapter in self.iter_adapters()
        }

    def size(self) -> int:
        """Return the number of registered backends."""
        return len(self._adapters)
