from __future__ import annotations

import json

from scripts.export_biopattern_gate_demo_bundle import (
    CONTROL_PLANE_AUDIT,
    CONTROL_PLANE_TRANSCRIPT,
    OUTPUT,
    _sha256,
    build_demo_bundle,
    render_bundle,
)
from scripts.serve_biopattern_gate_demo import WEB_ROOT, build_demo_page
from mcp_surface.audit import JsonlHashChainAuditTrail


def test_demo_bundle_is_current_and_preserves_evidence_boundary() -> None:
    checked_in = OUTPUT.read_text(encoding="utf-8")
    bundle = json.loads(checked_in)

    assert checked_in == render_bundle(build_demo_bundle())
    assert bundle["demo_bundle_version"] == "1.1"
    assert bundle["mode"] == "replay"
    assert bundle["evidence"] == {
        "label": "E3_REPLAY",
        "level": "E3",
        "runtime_kind": "sdk_simulator",
        "biological_claim": False,
        "claim_boundary": (
            "Deterministic technical pipeline evidence only; "
            "not a biological PNN result."
        ),
    }
    assert bundle["run"]["status"] == "complete"
    assert bundle["run"]["trial_count"] == 14
    assert bundle["run"]["scored_trial_count"] == 12
    assert bundle["run"]["sham_trial_count"] == 2
    assert bundle["control_plane"]["lease_released"] is True
    assert bundle["control_plane"]["provenance"] == (
        "recorded_audited_mcp_e3_run"
    )
    assert bundle["control_plane"]["audit_chain_verified"] is True
    assert bundle["control_plane"]["audit_request_count"] == 8
    assert bundle["control_plane"]["audit_event_count"] == 16
    assert len(bundle["control_plane"]["mcp_steps"]) == 9
    assert [
        record["state"]
        for record in bundle["control_plane"]["lifecycle_evidence"]
    ] == [
        "discovered",
        "reserved",
        "preparing",
        "running",
        "validating",
        "cooldown",
        "ready",
    ]
    assert (
        bundle["control_plane"]["raw_substrate_output_exposed_to_agent"]
        is False
    )
    assert len(bundle["trials"]) == 14
    assert all("decision_commit_sha256" in trial for trial in bundle["trials"])


def test_recorded_control_plane_sources_are_present_and_hash_chain_valid() -> None:
    transcript = json.loads(
        CONTROL_PLANE_TRANSCRIPT.read_text(encoding="utf-8")
    )
    audit = JsonlHashChainAuditTrail(CONTROL_PLANE_AUDIT)

    assert transcript["audit_chain_verified"] is True
    assert transcript["result_summary"]["success"] is True
    assert transcript["steps"][-1]["result"]["lease_present"] is False
    assert audit.verify() is True
    assert len(audit.events()) == 16


def test_evidence_hash_is_invariant_to_lf_and_crlf(tmp_path) -> None:
    lf = tmp_path / "evidence-lf.jsonl"
    crlf = tmp_path / "evidence-crlf.jsonl"
    lines = ('{"sequence":1}', '{"sequence":2}', "")

    lf.write_bytes("\n".join(lines).encode("utf-8"))
    crlf.write_bytes("\r\n".join(lines).encode("utf-8"))

    assert _sha256(lf) == _sha256(crlf)


def test_visualizer_contains_four_coordinated_panels_and_replay_controls() -> None:
    html = (WEB_ROOT / "vis.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "vis.css").read_text(encoding="utf-8")
    javascript = (WEB_ROOT / "vis.mjs").read_text(encoding="utf-8")

    for panel in ("gate", "neural", "decision", "control"):
        assert f"panel--{panel}" in html
    for action in ("previous", "play", "next", "speed"):
        assert f'data-action="{action}"' in html
    assert "SDK simulator · no biological claim" in html
    assert "E3" in html
    assert "REPLAY" in html
    assert "MCP AUDITED" in html
    assert "Recorded CP2N2 execution" in html
    assert "data-mcp-flow" in html
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "validateReplayBundle" in javascript
    assert "mountReplayDemo" in javascript
    assert "renderLifecycleEvidence" in javascript
    assert "renderMcpFlow" in javascript
    for stream in (
        "pattern_gate_session",
        "pattern_gate_trial",
        "pattern_gate_gate",
        "pattern_gate_features",
        "pattern_gate_decision",
        "pattern_gate_control_status",
        "cl_spikes",
        "cl_stims",
    ):
        assert stream in javascript


def test_local_demo_page_uses_the_same_cl_visualizer_assets() -> None:
    fragment = (WEB_ROOT / "vis.html").read_text(encoding="utf-8")
    page = build_demo_page(fragment)

    assert fragment in page
    assert 'href="/assets/vis.css"' in page
    assert 'from "/assets/vis.mjs"' in page
    assert 'mountReplayDemo(root, "/data/demo.json")' in page
