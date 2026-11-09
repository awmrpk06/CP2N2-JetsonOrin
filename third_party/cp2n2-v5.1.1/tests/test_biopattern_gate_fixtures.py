from __future__ import annotations

import json
import hashlib
from pathlib import Path

from applications.biopattern_gate.config import BioPatternGateConfig


ROOT = Path(__file__).parents[1]


def test_checked_in_configuration_schema_is_current() -> None:
    checked_in = json.loads(
        (
            ROOT / "schemas" / "biopattern-gate-config-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    generated = BioPatternGateConfig.model_json_schema()

    checked_in.pop("$id")
    checked_in["title"] = generated["title"]
    assert checked_in == generated


def test_prompt_fixture_covers_canonical_and_adversarial_intents() -> None:
    fixture = json.loads(
        (
            ROOT
            / "evaluation"
            / "fixtures"
            / "biopattern-gate-prompts-v1.json"
        ).read_text(encoding="utf-8")
    )
    cases = {case["id"]: case for case in fixture["cases"]}

    assert cases["canonical-plan"]["required_arguments"]["dry_run"] is True
    assert cases["canonical-execute"]["external_approval_still_required"] is True
    assert cases["physical-parameter-injection"]["must_not_start_run"] is True
    assert cases["approval-bypass"]["prompt_is_approval"] is False
    assert cases["ambiguous-execution-intent"]["maximum_allowed_commitment"] == (
        "dry_run"
    )


def test_cl_package_contains_an_exact_copy_of_the_canonical_core() -> None:
    canonical = ROOT / "applications" / "biopattern_gate"
    bundled = (
        ROOT
        / "cl-apps"
        / "cp2n2-biopattern-gate"
        / "src"
        / "biopattern_gate"
    )
    canonical_files = {
        path.relative_to(canonical)
        for path in canonical.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    bundled_files = {
        path.relative_to(bundled)
        for path in bundled.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert bundled_files == canonical_files
    for relative in canonical_files:
        assert (bundled / relative).read_bytes() == (canonical / relative).read_bytes()


def test_package_manifest_entry_checksums_match_source_tree() -> None:
    app_root = ROOT / "cl-apps" / "cp2n2-biopattern-gate"
    manifest = json.loads(
        (
            ROOT
            / "cl-apps"
            / "cp2n2-biopattern-gate.package-manifest.json"
        ).read_text(encoding="utf-8")
    )

    for archive_path, record in manifest["entries"].items():
        relative = Path(archive_path).relative_to("cp2n2-biopattern-gate")
        payload = (app_root / relative).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
        assert len(payload) == record["size_bytes"]
