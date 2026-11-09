from __future__ import annotations

from adapters.edge_adapter import EdgeAdapter
from adapters.fault_injecting_adapter import FaultInjectingAdapter, FaultProfile
from demos.common import build_default_orchestrator, build_extended_orchestrator, make_edge_task, make_wetware_task
from evaluation.common import PROJECT_ROOT
from remote.service_controller import start_remote_edge_service
from descriptors.resource_contract import assess_contract_admission


def test_unsupervised_wetware_task_is_rejected() -> None:
    orchestrator = build_default_orchestrator()
    task = make_wetware_task(task_id="pytest-wetware", human_supervision_available=False)
    result = orchestrator.execute_task(task)
    assert not result.success
    assert result.decision.selected_backend_id is None


def test_prepare_failure_uses_remote_fallback() -> None:
    service = start_remote_edge_service(PROJECT_ROOT)
    try:
        orchestrator = build_extended_orchestrator(remote_base_url=service.base_url)
        orchestrator.register_adapter(
            FaultInjectingAdapter(
                EdgeAdapter(),
                FaultProfile(prepare_failure_message="pytest prepare failure"),
            ),
            overwrite=True,
        )
        task = make_edge_task(task_id="pytest-edge", preferred_locality=None)
        result = orchestrator.execute_task(task)
        assert result.success
        assert result.decision.used_fallback
        assert result.decision.selected_backend_id == "remote-edge-backend"
        remote_contract = orchestrator.registry.get_adapter(
            "remote-edge-backend"
        ).resource_contract()
        assert remote_contract.evidence.runtime_kind == "same_host_service"
        assert remote_contract.evidence.evidence_level == "E2"
        assert assess_contract_admission(remote_contract).admissible
    finally:
        service.stop()
