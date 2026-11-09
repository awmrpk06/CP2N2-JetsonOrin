"""Synchronize the canonical core into the self-contained CL app package."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).parents[1].resolve()
SOURCE = (ROOT / "applications" / "biopattern_gate").resolve()
TARGET = (
    ROOT
    / "cl-apps"
    / "cp2n2-biopattern-gate"
    / "src"
    / "biopattern_gate"
).resolve()


def main() -> int:
    if SOURCE.parent != (ROOT / "applications").resolve():
        raise RuntimeError("source escaped canonical applications directory")
    expected_parent = (
        ROOT / "cl-apps" / "cp2n2-biopattern-gate" / "src"
    ).resolve()
    if TARGET.parent != expected_parent:
        raise RuntimeError("target escaped CL application source directory")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(
        SOURCE,
        TARGET,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    print(f"synchronized {SOURCE} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
