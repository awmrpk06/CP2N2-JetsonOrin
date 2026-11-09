"""Run the deterministic E3 BioPattern Gate demonstration locally."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from applications.biopattern_gate.config import BioPatternGateConfig  # noqa: E402
from applications.biopattern_gate.decoder import load_decoder  # noqa: E402
from applications.biopattern_gate.runner import run_session  # noqa: E402
from applications.biopattern_gate.simulator import (  # noqa: E402
    DeterministicReservoirSimulator,
)


APP_ROOT = ROOT / "applications" / "biopattern_gate"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="technical-e3-demo")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional path for the machine-readable summary",
    )
    args = parser.parse_args()
    config = BioPatternGateConfig.model_validate_json(
        (
            APP_ROOT / "presets" / "simulator" / "technical-e3.json"
        ).read_text(encoding="utf-8")
    )
    decoder = load_decoder(
        APP_ROOT / "artifacts" / "simulator" / "pattern-gate-linear-v1.json"
    )
    result = run_session(
        run_id=args.run_id,
        config=config,
        decoder=decoder,
        port=DeterministicReservoirSimulator(),
    )
    rendered = json.dumps(result.summary(), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
