from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from adapters.edge_adapter import EdgeAdapter
from core.errors import ControlPlaneErrorCode, ControlPlaneException
from core.leases import InMemoryLeaseStore
from core.lifecycle import ALLOWED_TRANSITIONS, LifecycleStore
from core.orchestrator import CP2N2Orchestrator
from demos.common import make_edge_task
from descriptors.resource_contract import ResourceLifecycleState


def _ready_lifecycle(resource_id: str = "resource") -> LifecycleStore:
    store = LifecycleStore()
    initial = store.initialize(resource_id)
    store.transition(
        resource_id,
        ResourceLifecycleState.READY,
        expected_version=initial.version,
        reason="test setup",
    )
    return store


def test_happy_path_follows_the_normative_lifecycle() -> None:
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(EdgeAdapter())
    task = make_edge_task(task_id="lifecycle-happy")
    task.allow_fallback = False

    result = orchestrator.execute_task(task)

    assert result.success
    assert [transition.to_state for transition in result.lifecycle_history] == [
        "reserved",
        "preparing",
        "running",
        "validating",
        "cooldown",
        "ready",
    ]
    assert result.final_lifecycle is not None
    assert result.final_lifecycle.state == "ready"
    assert orchestrator.registry.lease_store.current("edge-backend") is None


def test_all_declared_lifecycle_edges_are_accepted() -> None:
    for index, (source, targets) in enumerate(ALLOWED_TRANSITIONS.items()):
        for target_index, target in enumerate(targets):
            resource_id = f"edge-{index}-{target_index}"
            store = LifecycleStore()
            snapshot = store.initialize(resource_id, state=source)
            updated = store.transition(
                resource_id,
                target,
                expected_version=snapshot.version,
                reason="model edge test",
            )
            assert updated.state == target.value
            assert updated.version == snapshot.version + 1


def test_invalid_transition_and_stale_version_are_rejected() -> None:
    store = _ready_lifecycle()
    ready = store.snapshot("resource")

    with pytest.raises(ControlPlaneException) as invalid:
        store.transition(
            "resource",
            ResourceLifecycleState.RUNNING,
            expected_version=ready.version,
            reason="skip reservation",
        )
    assert invalid.value.error.code == ControlPlaneErrorCode.INVALID_STATE_TRANSITION

    reserved = store.transition(
        "resource",
        ResourceLifecycleState.RESERVED,
        expected_version=ready.version,
        reason="valid reservation",
    )
    with pytest.raises(ControlPlaneException) as stale:
        store.transition(
            "resource",
            ResourceLifecycleState.PREPARING,
            expected_version=ready.version,
            reason="stale client",
        )
    assert stale.value.error.code == ControlPlaneErrorCode.STATE_VERSION_CONFLICT
    assert store.snapshot("resource").version == reserved.version


def test_atomic_lease_store_allows_only_one_competing_client() -> None:
    store = InMemoryLeaseStore()
    barrier = threading.Barrier(8)
    acquired: list[str] = []
    rejected: list[str] = []
    lock = threading.Lock()

    def compete(index: int) -> None:
        barrier.wait()
        try:
            lease = store.acquire(
                "exclusive-resource",
                f"client-{index}",
                ttl_ms=5_000,
            )
            with lock:
                acquired.append(lease.owner_id)
        except ControlPlaneException as exc:
            with lock:
                rejected.append(str(exc.error.code))

    threads = [threading.Thread(target=compete, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(acquired) == 1
    assert rejected == [ControlPlaneErrorCode.RESOURCE_BUSY.value] * 7


def test_expired_lease_cannot_be_renewed_or_validated() -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    clock_value = [now]
    store = InMemoryLeaseStore(clock=lambda: clock_value[0])
    lease = store.acquire("resource", "client", ttl_ms=100)
    clock_value[0] = now + timedelta(milliseconds=101)

    with pytest.raises(ControlPlaneException) as expired:
        store.validate(lease.lease_id, "resource", "client")

    assert expired.value.error.code == ControlPlaneErrorCode.LEASE_EXPIRED
    assert store.current("resource") is None

    clock_value[0] = now
    second = store.acquire("resource", "client", ttl_ms=100)
    clock_value[0] = now + timedelta(milliseconds=101)
    with pytest.raises(ControlPlaneException) as release_expired:
        store.release(second.lease_id, "resource", "client")
    assert release_expired.value.error.code == ControlPlaneErrorCode.LEASE_EXPIRED


class CountingEdgeAdapter(EdgeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.invocation_count = 0

    def invoke(self, task):
        self.invocation_count += 1
        return super().invoke(task)


class SlowEdgeAdapter(CountingEdgeAdapter):
    def __init__(self, delay_seconds: float = 0.2) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds

    def invoke(self, task):
        self.invocation_count += 1
        time.sleep(self.delay_seconds)
        return EdgeAdapter.invoke(self, task)


class AbortableEdgeAdapter(SlowEdgeAdapter):
    def abort(self) -> bool:
        return True


class SlowPrepareAdapter(EdgeAdapter):
    def prepare(self, task):
        time.sleep(0.08)
        return super().prepare(task)


class SlowValidationTelemetryAdapter(EdgeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.telemetry_calls = 0

    def collect_telemetry(self):
        self.telemetry_calls += 1
        if self.telemetry_calls >= 4:
            time.sleep(0.06)
        return super().collect_telemetry()


def test_idempotency_replays_result_without_second_invocation() -> None:
    adapter = CountingEdgeAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    task = make_edge_task(task_id="idempotent")
    task.client_id = "client-a"
    task.idempotency_key = "request-42"
    task.allow_fallback = False

    first = orchestrator.execute_task(task)
    second = orchestrator.execute_task(task)

    assert first.success
    assert second.success
    assert second.idempotent_replay
    assert adapter.invocation_count == 1
    assert second.invocation == first.invocation


def test_reusing_idempotency_key_for_different_request_is_rejected() -> None:
    adapter = CountingEdgeAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    first_task = make_edge_task(task_id="idempotency-conflict")
    first_task.client_id = "client-a"
    first_task.idempotency_key = "same-key"
    first_task.allow_fallback = False
    assert orchestrator.execute_task(first_task).success

    changed_task = first_task.model_copy(deep=True)
    changed_task.metadata["input_vector"] = [0.9, 0.8, 0.7, 0.6]
    conflict = orchestrator.execute_task(changed_task)

    assert not conflict.success
    assert conflict.error is not None
    assert conflict.error.code == ControlPlaneErrorCode.IDEMPOTENCY_CONFLICT
    assert adapter.invocation_count == 1


def test_competing_orchestrator_clients_cannot_invoke_concurrently() -> None:
    adapter = SlowEdgeAdapter(delay_seconds=0.15)
    first_orchestrator = CP2N2Orchestrator()
    first_orchestrator.register_adapter(adapter)
    second_orchestrator = CP2N2Orchestrator(
        registry=first_orchestrator.registry
    )
    barrier = threading.Barrier(2)
    results = []
    lock = threading.Lock()

    def execute(client_id: str, orchestrator: CP2N2Orchestrator) -> None:
        task = make_edge_task(task_id=f"race-{client_id}")
        task.client_id = client_id
        task.allow_fallback = False
        barrier.wait()
        result = orchestrator.execute_task(task)
        with lock:
            results.append(result)

    threads = [
        threading.Thread(
            target=execute,
            args=("client-a", first_orchestrator),
        ),
        threading.Thread(
            target=execute,
            args=("client-b", second_orchestrator),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(result.success for result in results) == 1
    rejected = next(result for result in results if not result.success)
    assert rejected.error is not None
    assert rejected.error.code == ControlPlaneErrorCode.RESOURCE_BUSY
    assert adapter.invocation_count == 1


def test_explicit_reservation_renewal_ownership_and_execution() -> None:
    adapter = CountingEdgeAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)

    reservation = orchestrator.reserve_backend(
        "edge-backend",
        owner_id="client-a",
        ttl_ms=5_000,
    )
    assert reservation.success
    assert reservation.lease is not None
    assert reservation.final_lifecycle is not None
    assert reservation.final_lifecycle.state == "reserved"

    denied = orchestrator.renew_lease(
        "edge-backend",
        lease_id=reservation.lease.lease_id,
        owner_id="client-b",
        ttl_ms=5_000,
    )
    assert not denied.success
    assert denied.error is not None
    assert denied.error.code == ControlPlaneErrorCode.POLICY_DENIED

    renewed = orchestrator.renew_lease(
        "edge-backend",
        lease_id=reservation.lease.lease_id,
        owner_id="client-a",
        ttl_ms=5_000,
        expected_lease_version=reservation.lease.version,
    )
    assert renewed.success
    assert renewed.lease is not None
    assert renewed.lease.version == 1

    task = make_edge_task(task_id="use-pre-acquired-lease")
    task.client_id = "client-a"
    task.direct_backend_id = "edge-backend"
    task.lease_id = renewed.lease.lease_id
    task.expected_lease_version = renewed.lease.version
    task.allow_fallback = False
    result = orchestrator.execute_task(task)

    assert result.success
    assert adapter.invocation_count == 1
    assert orchestrator.registry.lease_store.current("edge-backend") is None


def test_unused_lease_release_updates_published_contract_state() -> None:
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(EdgeAdapter())
    reservation = orchestrator.reserve_backend(
        "edge-backend",
        owner_id="client-a",
        ttl_ms=5_000,
    )
    assert reservation.success
    assert reservation.lease is not None

    contract = orchestrator.registry.resource_contract_for("edge-backend")
    assert contract.state.lifecycle is not None
    assert contract.state.lifecycle.value == "reserved"
    assert contract.telemetry["control_plane_state_version"].value == (
        reservation.final_lifecycle.version
    )

    denied = orchestrator.release_lease(
        "edge-backend",
        lease_id=reservation.lease.lease_id,
        owner_id="client-b",
    )
    assert not denied.success
    assert denied.error is not None
    assert denied.error.code == ControlPlaneErrorCode.POLICY_DENIED

    released = orchestrator.release_lease(
        "edge-backend",
        lease_id=reservation.lease.lease_id,
        owner_id="client-a",
    )
    assert released.success
    assert released.final_lifecycle is not None
    assert released.final_lifecycle.state == "ready"


def test_invocation_timeout_never_becomes_silent_success() -> None:
    adapter = SlowEdgeAdapter(delay_seconds=0.15)
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    task = make_edge_task(task_id="timeout")
    task.allow_fallback = False
    task.invocation_timeout_ms = 10

    result = orchestrator.execute_task(task)
    time.sleep(0.2)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ControlPlaneErrorCode.EXECUTION_STATUS_UNKNOWN
    assert result.final_lifecycle is not None
    assert result.final_lifecycle.state == "unreachable"
    assert orchestrator.registry.lifecycle_store.snapshot(
        "edge-backend"
    ).state == "unreachable"
    assert orchestrator.registry.lease_store.current("edge-backend") is None


def test_preparation_timeout_has_its_own_error_and_uncertain_state() -> None:
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(SlowPrepareAdapter())
    task = make_edge_task(task_id="prepare-timeout")
    task.allow_fallback = False
    task.preparation_timeout_ms = 5

    result = orchestrator.execute_task(task)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ControlPlaneErrorCode.PREPARATION_TIMEOUT
    assert result.final_lifecycle is not None
    assert result.final_lifecycle.state == "unreachable"


def test_validation_timeout_cannot_be_reported_as_execution_success() -> None:
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(SlowValidationTelemetryAdapter())
    task = make_edge_task(task_id="validation-timeout")
    task.allow_fallback = False
    task.validation_timeout_ms = 5

    result = orchestrator.execute_task(task)

    assert not result.success
    assert result.invocation is not None
    assert result.error is not None
    assert result.error.code == ControlPlaneErrorCode.VALIDATION_TIMEOUT


def test_explicit_abort_and_reconciliation_restore_known_state() -> None:
    adapter = AbortableEdgeAdapter(delay_seconds=0.2)
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    lifecycle = orchestrator.registry.lifecycle_store
    ready = lifecycle.snapshot("edge-backend")
    lease = orchestrator.registry.lease_store.acquire(
        "edge-backend",
        "client-a",
        ttl_ms=5_000,
    )
    reserved = lifecycle.transition(
        "edge-backend",
        ResourceLifecycleState.RESERVED,
        expected_version=ready.version,
        reason="test lease",
    )
    preparing = lifecycle.transition(
        "edge-backend",
        ResourceLifecycleState.PREPARING,
        expected_version=reserved.version,
        reason="test prepare",
    )
    lifecycle.transition(
        "edge-backend",
        ResourceLifecycleState.RUNNING,
        expected_version=preparing.version,
        reason="test run",
    )

    aborted = orchestrator.abort_backend(
        "edge-backend",
        lease_id=lease.lease_id,
        owner_id="client-a",
    )

    assert aborted.success
    assert aborted.final_lifecycle is not None
    assert aborted.final_lifecycle.state == "ready"
    assert orchestrator.registry.lease_store.current("edge-backend") is None


def test_timeout_state_can_only_recover_through_explicit_reconciliation() -> None:
    adapter = SlowEdgeAdapter(delay_seconds=0.08)
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    task = make_edge_task(task_id="reconcile-timeout")
    task.allow_fallback = False
    task.invocation_timeout_ms = 5
    timed_out = orchestrator.execute_task(task)
    assert timed_out.final_lifecycle is not None
    assert timed_out.final_lifecycle.state == "unreachable"

    time.sleep(0.12)
    current = orchestrator.registry.lifecycle_store.snapshot("edge-backend")
    reconciled = orchestrator.reconcile_backend(
        "edge-backend",
        expected_state_version=current.version,
    )

    assert reconciled.success
    assert reconciled.final_lifecycle is not None
    assert reconciled.final_lifecycle.state == "ready"


def test_directed_request_can_require_resource_state_version() -> None:
    adapter = CountingEdgeAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    task = make_edge_task(task_id="stale-state-version")
    task.direct_backend_id = "edge-backend"
    task.expected_resource_state_version = 999
    task.allow_fallback = False

    result = orchestrator.execute_task(task)

    assert not result.success
    assert result.error is not None
    assert result.error.code == ControlPlaneErrorCode.STATE_VERSION_CONFLICT
    assert adapter.invocation_count == 0
