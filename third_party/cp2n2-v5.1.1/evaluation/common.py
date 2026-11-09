"""Shared helpers for prototype evaluation scripts."""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def bootstrap_project_root() -> Path:
    """Add the project root to ``sys.path`` and return it."""
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    return project_root


PROJECT_ROOT = bootstrap_project_root()
_configured_results_dir = os.getenv("CP2N2_RESULTS_DIR") or os.getenv(
    "PHYSMCP_RESULTS_DIR"
)
RESULTS_DIR = (
    Path(_configured_results_dir).expanduser().resolve()
    if _configured_results_dir
    else PROJECT_ROOT
    / "evaluation"
    / "results"
    / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

from adapters.chemical_adapter import ChemicalAdapter
from adapters.edge_adapter import EdgeAdapter
from adapters.wetware_adapter import WetwareAdapter
from demos.common import (
    build_default_orchestrator,
    make_chemical_task,
    make_edge_task,
    make_wetware_task,
)


def save_json(path: Path, payload: Any) -> None:
    """Write JSON with stable formatting."""
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def save_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to CSV."""
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
