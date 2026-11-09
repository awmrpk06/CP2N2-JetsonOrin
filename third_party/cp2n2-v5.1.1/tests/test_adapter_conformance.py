from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from adapters.chemical_adapter import ChemicalAdapter
from adapters.contracts import (
    ADAPTER_ARCHITECTURE_VERSION,
    ControlOperation,
    DeploymentMode,
    ExecutionLocation,
    ReservationMode,
    RuntimeOperation,
)
from adapters.cortical_labs_adapter import CorticalLabsAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.fault_injecting_adapter import FaultInjectingAdapter
from adapters.remote_edge_adapter import RemoteEdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from core.twin_registry import TwinRegistry
from demos.common import build_live_target_orchestrator, make_edge_task
from descriptors.resource_contract import EvidenceLevel, RuntimeKind
from evaluation.backend_matrix import (
    CORE_EVALUATION_BACKEND_MATRIX,
    NON_CL_BACKEND_IDS,
    validate_core_evaluation_coverage,
)
from remote.service_controller import start_remote_edge_service
from runtimes.base_runtime import SubstrateRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CONTROL_OPERATIONS = {item.value for item in ControlOperation}
REQUIRED_RUNTIME_OPERATIONS = {
    RuntimeOperation.PREPARE.value,
    RuntimeOperation.EXECUTE.value,
    RuntimeOperation.TELEMETRY.value,
}


@pytest.mark.parametrize(
    "adapter",
    [
        ChemicalAdapter(),
        WetwareAdapter(),
        EdgeAdapter(),
        CorticalLabsAdapter(use_simulator=True),
        FaultInjectingAdapter(EdgeAdapter(backend_id="fault-wrapper-edge")),
    ],
)
def test_every_local_control_adapter_has_separate_declared_runtime(adapter) -> None:
    declaration = adapter.capability_declaration
    descriptor = adapter.describe()

    assert adapter.validate_conformance()
    assert isinstance(adapter.runtime, SubstrateRuntime)
    assert adapter.runtime is not adapter
    assert declaration.schema_version == ADAPTER_ARCHITECTURE_VERSION
    assert declaration.explicit is True
    assert declaration.backend_id == descriptor.backend_id
    assert declaration.substrate_class == str(
        descriptor.capability.substrate_class
    )
    assert set(declaration.control_operations) == REQUIRED_CONTROL_OPERATIONS
    assert REQUIRED_RUNTIME_OPERATIONS.issubset(
        set(declaration.runtime.operations)
    )
    assert declaration.runtime == adapter.runtime.capabilities
    assert declaration.reservation_mode == ReservationMode.CONTROL_PLANE_LEASE
    assert adapter.deployment_status()["runtime_id"] == (
        declaration.runtime.runtime_id
    )
    assert adapter.resource_contract().identity.adapter_id == (
        declaration.adapter_id
    )


@pytest.mark.parametrize(
    ("adapter", "expected_kind", "expected_evidence"),
    [
        (
            ChemicalAdapter(),
            RuntimeKind.SYNTHETIC_TWIN,
            EvidenceLevel.E1_SYNTHETIC_TWIN,
        ),
        (
            WetwareAdapter(),
            RuntimeKind.SYNTHETIC_TWIN,
            EvidenceLevel.E1_SYNTHETIC_TWIN,
        ),
        (
            EdgeAdapter(),
            RuntimeKind.SYNTHETIC_TWIN,
            EvidenceLevel.E1_SYNTHETIC_TWIN,
        ),
        (
            CorticalLabsAdapter(use_simulator=True),
            RuntimeKind.SDK_SIMULATOR,
            EvidenceLevel.E3_SDK_SIMULATOR,
        ),
    ],
)
def test_runtime_declaration_preserves_evidence_ceiling(
    adapter,
    expected_kind,
    expected_evidence,
) -> None:
    declaration = adapter.capability_declaration
    assert declaration.runtime.runtime_kind == expected_kind
    assert declaration.evidence_ceiling == expected_evidence


def test_registry_rejects_misaligned_adapter_declaration() -> None:
    adapter = EdgeAdapter()
    adapter._capability_declaration = adapter.capability_declaration.model_copy(
        update={"backend_id": "different-backend"}
    )

    with pytest.raises(ValueError, match="backend_id"):
        TwinRegistry().register(adapter)


def test_remote_edge_adapter_conforms_through_separate_http_runtime() -> None:
    handle = start_remote_edge_service(PROJECT_ROOT)
    try:
        adapter = RemoteEdgeAdapter(handle.base_url)
        declaration = adapter.capability_declaration
        assert adapter.validate_conformance()
        assert adapter.runtime is not adapter
        assert (
            declaration.runtime.execution_location
            == ExecutionLocation.SAME_HOST_SERVICE
        )
        assert declaration.runtime.time_critical_execution_local is False
        assert declaration.evidence_ceiling == EvidenceLevel.E2_SAME_HOST_SERVICE
        assert declaration.deployment_mode == DeploymentMode.PREDEPLOYED
        assert adapter.prepare(make_edge_task()).prepared is True
        assert adapter.invoke(make_edge_task()).backend_id == "remote-edge-backend"
    finally:
        handle.stop()


def test_core_operates_without_importing_or_registering_cl_adapter() -> None:
    orchestrator = build_live_target_orchestrator(
        include_cortical_labs=False
    )
    backend_ids = {
        descriptor["backend_id"]
        for descriptor in orchestrator.discover_backends()
    }

    assert "cortical-labs-backend" not in backend_ids
    assert {"chemical-backend", "wetware-backend", "edge-backend"}.issubset(
        backend_ids
    )
    result = orchestrator.execute_task(
        make_edge_task(direct_backend_id="edge-backend")
    )
    assert result.success is True
    assert result.decision.selected_backend_id == "edge-backend"
    declarations = orchestrator.discover_adapter_capabilities()
    assert {item["backend_id"] for item in declarations} == backend_ids


def test_generic_core_imports_and_executes_when_cl_modules_are_unavailable() -> None:
    script = """
import importlib.abc
import sys

class BlockCortical(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if "cortical" in fullname:
            raise ImportError("CL integration intentionally unavailable")
        return None

sys.meta_path.insert(0, BlockCortical())
from demos.common import build_default_orchestrator, make_edge_task
orchestrator = build_default_orchestrator()
result = orchestrator.execute_task(
    make_edge_task(direct_backend_id="edge-backend")
)
assert result.success
assert len(orchestrator.discover_resource_contracts()) == 3
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_every_core_evaluation_declares_non_cl_backend_coverage() -> None:
    assert validate_core_evaluation_coverage()
    assert CORE_EVALUATION_BACKEND_MATRIX
    for backend_ids in CORE_EVALUATION_BACKEND_MATRIX.values():
        assert backend_ids.intersection(NON_CL_BACKEND_IDS)
