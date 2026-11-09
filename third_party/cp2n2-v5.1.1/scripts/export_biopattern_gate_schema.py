"""Export or verify the versioned BioPattern Gate configuration schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from applications.biopattern_gate.config import BioPatternGateConfig  # noqa: E402


TARGET = ROOT / "schemas" / "biopattern-gate-config-v1.schema.json"


def rendered_schema() -> str:
    schema = BioPatternGateConfig.model_json_schema()
    schema["$id"] = "https://cp2n2.dev/schemas/biopattern-gate-config-v1.schema.json"
    schema["title"] = "CP²N² BioPattern Gate Configuration v1"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in schema differs from the model",
    )
    args = parser.parse_args()
    rendered = rendered_schema()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != rendered:
            print(f"schema is stale: {TARGET}")
            return 1
        print(f"schema is current: {TARGET}")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
