"""Regenerate the A1 JSON Schema and versioned contract examples."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.chemical_adapter import ChemicalAdapter  # noqa: E402
from adapters.cortical_labs_adapter import CorticalLabsAdapter  # noqa: E402
from adapters.edge_adapter import EdgeAdapter  # noqa: E402
from descriptors.resource_contract import (  # noqa: E402
    CONTRACT_SCHEMA_VERSION,
    PhysicalNeuralResourceContract,
)


PUBLISHED_AT = "2026-07-23T12:00:00Z"
# Long-lived only so checked-in illustrative examples remain CLI-testable.
# Live adapters publish a 30-second validity horizon instead.
VALID_UNTIL = "2099-01-01T00:00:00Z"


def _stabilize_timestamps(value: Any) -> Any:
    if isinstance(value, dict):
        stable: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"published_at", "attested_at", "observed_at", "received_at"}:
                stable[key] = None if item is None and key == "observed_at" else PUBLISHED_AT
            elif key == "valid_until":
                stable[key] = None if item is None else VALID_UNTIL
            else:
                stable[key] = _stabilize_timestamps(item)
        return stable
    if isinstance(value, list):
        return [_stabilize_timestamps(item) for item in value]
    return value


def _adapter_payload(adapter: Any) -> dict[str, Any]:
    payload = adapter.resource_contract().model_dump(mode="json")
    return _stabilize_timestamps(payload)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _cl_simulator_payload() -> dict[str, Any]:
    payload = _adapter_payload(CorticalLabsAdapter(use_simulator=True))
    payload["evidence"] = {
        "runtime_kind": "sdk_simulator",
        "evidence_level": "E3",
        "attestation_method": "cl_sdk_is_simulator",
        "attested_at": PUBLISHED_AT,
        "attestation_details": {"configured_expectation": "sdk_simulator"},
    }
    payload["state"]["lifecycle"]["value"] = "ready"
    payload["state"]["health"]["value"] = "unknown"
    payload["telemetry"]["runtime_kind"]["value"] = "sdk_simulator"
    payload["telemetry"]["readiness_state"]["value"] = "ready"
    payload["telemetry"]["sdk_available"]["value"] = True
    PhysicalNeuralResourceContract.model_validate(payload)
    return payload


def main() -> int:
    schema = PhysicalNeuralResourceContract.model_json_schema(
        ref_template="#/$defs/{model}"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://raw.githubusercontent.com/fischesn/cp2n2/main/schemas/"
        f"physical-neural-resource-contract-v{CONTRACT_SCHEMA_VERSION}.schema.json"
    )
    schema_path = (
        PROJECT_ROOT
        / "schemas"
        / f"physical-neural-resource-contract-v{CONTRACT_SCHEMA_VERSION}.schema.json"
    )
    _write_json(schema_path, schema)

    examples = PROJECT_ROOT / "examples" / "resource-contract-v1.0"
    chemical = _adapter_payload(ChemicalAdapter())
    edge = _adapter_payload(EdgeAdapter())
    cl_simulator = _cl_simulator_payload()
    for payload in (chemical, edge, cl_simulator):
        PhysicalNeuralResourceContract.model_validate(payload)

    _write_json(examples / "valid-chemical-synthetic-twin.json", chemical)
    _write_json(examples / "valid-edge-synthetic-twin.json", edge)
    _write_json(examples / "valid-cl-sdk-simulator.json", cl_simulator)

    physical_without_safety = deepcopy(cl_simulator)
    physical_without_safety["evidence"] = {
        "runtime_kind": "physical_hardware",
        "evidence_level": "E5",
        "attestation_method": "provider_runtime_attestation",
        "attested_at": PUBLISHED_AT,
        "attestation_details": {},
    }
    physical_without_safety["identity"]["hardware_id"] = None
    physical_without_safety["safety"] = None
    PhysicalNeuralResourceContract.model_validate(physical_without_safety)
    _write_json(
        examples / "inadmissible-physical-wetware-missing-safety.json",
        physical_without_safety,
    )

    invalid_provenance = deepcopy(chemical)
    invalid_provenance["telemetry"]["contamination_level"]["source"] = "invented"
    _write_json(
        examples / "invalid-chemical-bad-provenance.json",
        invalid_provenance,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
