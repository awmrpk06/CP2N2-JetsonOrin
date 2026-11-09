"""Validate and conservatively assess a Physical Neural Resource Contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from descriptors.resource_contract import (  # noqa: E402
    PhysicalNeuralResourceContract,
    assess_contract_admission,
    migrate_contract_payload,
)


def validate_contract_file(
    path: Path,
    *,
    operation: str = "invoke",
) -> tuple[PhysicalNeuralResourceContract, bool, list[str]]:
    """Parse *path* and return the contract and its admission decision."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    migrated = migrate_contract_payload(payload)
    contract = PhysicalNeuralResourceContract.model_validate(migrated)
    admission = assess_contract_admission(contract, operation=operation)
    return contract, admission.admissible, admission.reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--operation", default="invoke")
    parser.add_argument(
        "--allow-inadmissible",
        action="store_true",
        help="Return success for structurally valid but conservatively inadmissible contracts.",
    )
    args = parser.parse_args()

    try:
        contract, admissible, reasons = validate_contract_file(
            args.contract,
            operation=args.operation,
        )
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2

    print(
        f"VALID schema={contract.schema_version} "
        f"resource={contract.identity.resource_id} "
        f"runtime={contract.evidence.runtime_kind}"
    )
    if admissible:
        print(f"ADMISSIBLE operation={args.operation}")
        return 0

    print(f"INADMISSIBLE operation={args.operation}")
    for reason in reasons:
        print(f"- {reason}")
    return 0 if args.allow_inadmissible else 3


if __name__ == "__main__":
    raise SystemExit(main())
