"""Run BioPattern Gate through the complete constrained CP²N² MCP surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from agent.biopattern_gate_client import (  # noqa: E402
    BioPatternGateDeterministicClient,
)
from demos.common import build_biopattern_gate_e3_orchestrator  # noqa: E402
from mcp_surface.audit import JsonlHashChainAuditTrail  # noqa: E402
from mcp_surface.auth import Scope  # noqa: E402
from mcp_surface.models import MCPPrincipal  # noqa: E402
from mcp_surface.service import MCPControlSurface  # noqa: E402


def transcript_exit_code(transcript: dict) -> int:
    """Fail the CLI when execution or audit verification did not succeed."""

    summary = transcript.get("result_summary", {})
    return (
        0
        if summary.get("success") is True
        and transcript.get("audit_chain_verified") is True
        else 1
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit",
        type=Path,
        default=ROOT / ".physmcp" / "biopattern-gate-e3-audit.jsonl",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.audit.parent.mkdir(parents=True, exist_ok=True)

    surface = MCPControlSurface(
        orchestrator=build_biopattern_gate_e3_orchestrator(),
        principal=MCPPrincipal(
            principal_id="biopattern-gate-e3-client",
            scopes=[scope.value for scope in Scope],
        ),
        audit_trail=JsonlHashChainAuditTrail(args.audit),
    )
    transcript = BioPatternGateDeterministicClient(surface).run().as_dict()
    transcript["audit_chain_verified"] = surface.audit_trail.verify()
    rendered = json.dumps(transcript, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return transcript_exit_code(transcript)


if __name__ == "__main__":
    raise SystemExit(main())

