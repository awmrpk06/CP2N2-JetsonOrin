from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from core.orchestrator import CP2N2Orchestrator
from core.twin_registry import TwinRegistry
from demos.common import make_edge_task
from descriptors.resource_contract import (
    CONTRACT_SCHEMA_VERSION,
    PhysicalNeuralResourceContract,
    UnsupportedContractVersionError,
    assess_contract_admission,
    migrate_contract_payload,
)
from scripts.validate_resource_contract import validate_contract_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples" / "resource-contract-v1.0"


@pytest.mark.parametrize(
    "adapter",
    [ChemicalAdapter(), WetwareAdapter(), EdgeAdapter()],
)
def test_local_twin_adapters_publish_admissible_e1_contracts(adapter) -> None:
    contract = adapter.resource_contract()

    assert contract.schema_version == CONTRACT_SCHEMA_VERSION
    assert contract.identity.resource_id == adapter.backend_id()
    assert contract.evidence.runtime_kind == "synthetic_twin"
    assert contract.evidence.evidence_level == "E1"
    assert assess_contract_admission(contract).admissible is True

    for observation in contract.telemetry.values():
        serialized = observation.model_dump(mode="json")
        assert set(serialized) == {
            "value",
            "unit",
            "source",
            "observed_at",
            "received_at",
            "uncertainty",
            "valid_until",
        }
        assert serialized["source"] == "estimated"


def test_registry_publishes_contracts_without_replacing_legacy_descriptors() -> None:
    registry = TwinRegistry()
    registry.register(ChemicalAdapter())
    registry.register(EdgeAdapter())

    assert len(registry.list_descriptors()) == 2
    contracts = registry.list_resource_contracts()
    assert [contract.identity.resource_id for contract in contracts] == [
        "chemical-backend",
        "edge-backend",
    ]


@pytest.mark.parametrize(
    "filename",
    [
        "valid-chemical-synthetic-twin.json",
        "valid-edge-synthetic-twin.json",
        "valid-cl-sdk-simulator.json",
    ],
)
def test_valid_cross_substrate_examples_are_conformant_and_admissible(filename: str) -> None:
    contract, admissible, reasons = validate_contract_file(EXAMPLES / filename)

    assert isinstance(contract, PhysicalNeuralResourceContract)
    assert admissible is True
    assert reasons == []


def test_schema_valid_physical_resource_with_missing_safety_is_inadmissible() -> None:
    contract, admissible, reasons = validate_contract_file(
        EXAMPLES / "inadmissible-physical-wetware-missing-safety.json"
    )

    assert contract.evidence.runtime_kind == "physical_hardware"
    assert admissible is False
    assert "safety contract is missing" in reasons
    assert "physical hardware requires identity.hardware_id" in reasons
    assert "physical hardware health is unknown" in reasons


def test_invalid_telemetry_provenance_fails_structural_validation() -> None:
    payload = json.loads(
        (EXAMPLES / "invalid-chemical-bad-provenance.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValidationError):
        PhysicalNeuralResourceContract.model_validate(payload)


def test_runtime_kind_and_evidence_level_cannot_be_overstated() -> None:
    payload = json.loads(
        (EXAMPLES / "valid-cl-sdk-simulator.json").read_text(encoding="utf-8")
    )
    payload["evidence"]["runtime_kind"] = "physical_hardware"
    payload["evidence"]["evidence_level"] = "E3"

    with pytest.raises(ValidationError, match="requires evidence_level 'E5'"):
        PhysicalNeuralResourceContract.model_validate(payload)


def test_unknown_runtime_is_not_admissible() -> None:
    from adapters.cortical_labs_adapter import CorticalLabsAdapter

    contract = CorticalLabsAdapter(use_simulator=True).resource_contract()
    admission = assess_contract_admission(contract)

    assert contract.evidence.runtime_kind == "unknown"
    assert contract.evidence.evidence_level == "E0"
    assert admission.admissible is False
    assert "runtime kind is not attested" in admission.reasons


def test_operation_requires_explicit_permission() -> None:
    payload = json.loads(
        (EXAMPLES / "valid-chemical-synthetic-twin.json").read_text(encoding="utf-8")
    )
    contract = PhysicalNeuralResourceContract.model_validate(payload)

    admission = assess_contract_admission(contract, operation="train")

    assert admission.admissible is False
    assert "operation 'train' is not explicitly permitted" in admission.reasons


def test_orchestrator_never_invokes_an_inadmissible_resource() -> None:
    class MissingSafetyAdapter(EdgeAdapter):
        invoked = False

        def resource_contract(self):
            return super().resource_contract().model_copy(update={"safety": None})

        def invoke(self, task):
            self.invoked = True
            return super().invoke(task)

    adapter = MissingSafetyAdapter()
    orchestrator = CP2N2Orchestrator()
    orchestrator.register_adapter(adapter)
    task = make_edge_task(task_id="inadmissible-contract")
    task.allow_fallback = False

    result = orchestrator.execute_task(task)

    assert result.success is False
    assert result.contract_admission is not None
    assert result.contract_admission.admissible is False
    assert result.failure_reason is not None
    assert result.failure_reason.startswith("INADMISSIBLE")
    assert adapter.invoked is False


def test_version_handling_is_explicit_and_non_lossy() -> None:
    payload = json.loads(
        (EXAMPLES / "valid-chemical-synthetic-twin.json").read_text(encoding="utf-8")
    )

    assert migrate_contract_payload(payload) == payload
    payload["schema_version"] = "0.9"
    with pytest.raises(UnsupportedContractVersionError):
        migrate_contract_payload(payload)


def test_checked_in_json_schema_matches_contract_version() -> None:
    schema_path = (
        PROJECT_ROOT
        / "schemas"
        / f"physical-neural-resource-contract-v{CONTRACT_SCHEMA_VERSION}.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(
        f"physical-neural-resource-contract-v{CONTRACT_SCHEMA_VERSION}.schema.json"
    )
    assert "TelemetryObservation" in schema["$defs"]
