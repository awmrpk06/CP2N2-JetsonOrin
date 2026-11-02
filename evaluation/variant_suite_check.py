"""Run the pinned CP2N2 v5.1.1 test suite once per lifecycle variant and record the outcomes.

The pinned checkout is not modified: the variant is applied by a pytest plugin loaded from a scratch
directory that holds only lifecycle_variants.py. The deliberately tiny retention limit is a positive
control showing that the plugin really changes the store under test.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from evaluation.reproduce_original import (
    PINNED_COMMIT,
    ROOT,
    digest,
    git_fact,
    reproduction_environment,
)

CASES = (("pinned", None), ("indexed", None), ("bounded", 6000), ("bounded", 1))


def run_case(upstream: Path, python: Path, plugin_dir: Path, variant: str, limit: int | None) -> dict:
    environment = {**reproduction_environment(plugin_dir, "tests"), "PYTHONPATH": str(plugin_dir), "PHYS_MCP_LIFECYCLE_VARIANT": variant}
    if limit is not None:
        environment["PHYS_MCP_RETENTION_LIMIT"] = str(limit)
    completed = subprocess.run(
        [str(python), "-m", "pytest", "-q", "-rf", "-p", "lifecycle_variants", "-p", "no:cacheprovider"],
        cwd=upstream, env=environment, capture_output=True, text=True, check=False, timeout=900,
    )
    lines = completed.stdout.splitlines()
    return {
        "variant": variant, "retention_limit": limit, "returncode": completed.returncode,
        "plugin_header": next((line for line in lines if line.startswith("lifecycle variant:")), None),
        "summary": next((line.strip("= ") for line in reversed(lines) if re.search(r"\d+ (passed|failed)", line)), None),
        "failed_tests": [line.removeprefix("FAILED ").split(" - ")[0] for line in lines if line.startswith("FAILED ")],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", type=Path, default=ROOT / "_temp/cp2n2-v5.1.1")
    parser.add_argument("--python", type=Path, default=ROOT / "_temp/cp2n2-python312/bin/python")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    upstream = args.upstream.resolve()
    if git_fact(upstream, "rev-parse", "HEAD") != PINNED_COMMIT or git_fact(upstream, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Pinned upstream checkout is not at the unmodified v5.1.1 commit")
    module = ROOT / "evaluation/lifecycle_variants.py"
    (ROOT / "_temp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="variant-suite-", dir=ROOT / "_temp") as scratch:
        shutil.copy2(module, Path(scratch) / module.name)
        cases = [run_case(upstream, args.python, Path(scratch), variant, limit) for variant, limit in CASES]
    clean_after = not git_fact(upstream, "status", "--porcelain", "--untracked-files=no")
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(), "upstream_commit": PINNED_COMMIT, "upstream_unmodified_after": clean_after,
        "lifecycle_variants_sha256": digest(module), "python": str(args.python), "cases": cases,
        "note": "retention_limit=1 is a positive control expected to fail history-dependent tests; it is not a proposed setting.",
    }
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps([{key: case[key] for key in ("variant", "retention_limit", "returncode", "summary")} for case in cases], indent=2))


if __name__ == "__main__":
    main()
