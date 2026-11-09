"""Regenerate the deterministic BioPattern Gate visual replay bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from applications.biopattern_gate.config import BioPatternGateConfig  # noqa: E402
from applications.biopattern_gate.control_plane_contract import (  # noqa: E402
    APPLICATION_ID,
    BACKEND_ID,
)
from applications.biopattern_gate.decoder import load_decoder  # noqa: E402
from applications.biopattern_gate.replay import load_replay_bundle  # noqa: E402
from applications.biopattern_gate.runner import run_session  # noqa: E402
from mcp_surface.audit import JsonlHashChainAuditTrail  # noqa: E402


APP_ROOT = ROOT / "applications" / "biopattern_gate"
SOURCE_REPLAY = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-replay-success-v1.json"
)
CONTROL_PLANE_TRANSCRIPT = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-control-plane-e3-transcript-v1.json"
)
CONTROL_PLANE_AUDIT = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-control-plane-e3-audit-v1.jsonl"
)
OUTPUT = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-demo-success-v1.json"
)


def _sha256(path: Path) -> str:
    """Hash repository text evidence using its canonical LF representation."""

    canonical = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_control_plane_evidence() -> tuple[dict, list]:
    transcript = json.loads(
        CONTROL_PLANE_TRANSCRIPT.read_text(encoding="utf-8")
    )
    audit_trail = JsonlHashChainAuditTrail(CONTROL_PLANE_AUDIT)
    events = audit_trail.events()
    audit_trail.verify()

    summary = transcript["result_summary"]
    application = summary["application"]
    request_ids = transcript["audit_request_ids"]
    event_request_ids = {event.request_id for event in events}
    lifecycle = transcript["lifecycle_history"]

    _require(transcript["audit_chain_verified"] is True, "audit not verified")
    _require(summary["success"] is True, "control-plane run did not succeed")
    _require(summary["evidence_level"] == "E3", "unexpected evidence level")
    _require(
        summary["runtime_kind"] == "sdk_simulator",
        "unexpected runtime kind",
    )
    _require(
        summary["raw_output_included"] is False
        and transcript["raw_substrate_output_exposed"] is False,
        "raw substrate output crossed the MCP boundary",
    )
    _require(
        set(request_ids).issubset(event_request_ids),
        "transcript request is missing from audit trail",
    )
    _require(
        len(events) == len(request_ids) * 2,
        "each MCP request must have received and completed audit events",
    )
    for request_id in request_ids:
        correlated = [
            event for event in events if event.request_id == request_id
        ]
        _require(
            [(event.phase, event.outcome) for event in correlated]
            == [("received", "pending"), ("completed", "success")],
            f"audit pair is incomplete for request {request_id}",
        )
    _require(
        [transition["to_state"] for transition in lifecycle]
        == ["preparing", "running", "validating", "cooldown", "ready"],
        "recorded lifecycle is incomplete",
    )
    _require(
        transcript["steps"][-1]["result"]
        == {
            "resource_id": BACKEND_ID,
            "lifecycle_state": "ready",
            "lease_present": False,
            "release_semantics": "orchestrator_finalization",
        },
        "resource was not automatically released",
    )
    _require(
        application["application_status"] == "complete"
        and application["biological_claim"] is False,
        "application evidence boundary is invalid",
    )
    return transcript, events


def build_demo_bundle() -> dict:
    transcript, audit_events = load_control_plane_evidence()
    summary = transcript["result_summary"]
    application = summary["application"]
    config = BioPatternGateConfig.model_validate_json(
        (
            APP_ROOT / "presets" / "simulator" / "technical-e3.json"
        ).read_text(encoding="utf-8")
    )
    decoder = load_decoder(
        APP_ROOT / "artifacts" / "simulator" / "pattern-gate-linear-v1.json"
    )
    raw_replay = json.loads(SOURCE_REPLAY.read_text(encoding="utf-8"))
    observations = {
        int(item["trial_index"]): item for item in raw_replay["observations"]
    }
    result = run_session(
        run_id="technical-e3-visual-replay-v1",
        config=config,
        decoder=decoder,
        port=load_replay_bundle(SOURCE_REPLAY),
    )
    _require(
        result.config_sha256 == application["config_sha256"],
        "visual replay config does not match the MCP run",
    )
    _require(
        result.decoder_sha256 == application["decoder_sha256"],
        "visual replay decoder does not match the MCP run",
    )
    _require(
        len(result.trials) == application["trial_count"],
        "visual replay trial count does not match the MCP run",
    )
    _require(
        result.accuracy == application["pipeline_assertion_accuracy"],
        "visual replay result does not match the MCP run",
    )
    trials = [
        {
            "trial_index": trial.trial_index,
            "block_index": trial.block_index,
            "kind": trial.kind,
            "expected_label": trial.expected_label,
            "predicted_label": trial.predicted_label,
            "route": trial.route,
            "probability_a": trial.probability_a,
            "decision_commit_sha256": trial.decision_commit_sha256,
            "correct": trial.correct,
            "feature_values": trial.feature_values,
            "event_timestamps_ms": [
                event["timestamp_ms"]
                for event in observations[trial.trial_index]["events"]
            ],
        }
        for trial in result.trials
    ]
    completed_audit_events = [
        {
            "sequence": event.sequence,
            "occurred_at": event.occurred_at.isoformat(),
            "request_id": event.request_id,
            "tool": event.tool,
            "outcome": event.outcome,
            "event_sha256": event.event_hash,
            "previous_sha256": event.previous_hash,
        }
        for event in audit_events
        if event.phase == "completed"
    ]
    completed_by_request = {
        event["request_id"]: event for event in completed_audit_events
    }
    mcp_steps = [
        {
            "stage": step["stage"],
            "tool": step["tool"],
            "request_id": step["request_id"],
            "ok": step["ok"],
            "audit_event_sha256": (
                completed_by_request[step["request_id"]]["event_sha256"]
                if step["request_id"]
                else None
            ),
        }
        for step in transcript["steps"]
    ]
    lifecycle_evidence = [
        {
            "state": "discovered",
            "source": "discover_resources audit event",
            "occurred_at": completed_audit_events[0]["occurred_at"],
            "proof_sha256": completed_audit_events[0]["event_sha256"],
        },
        {
            "state": "reserved",
            "source": "reserve_resource audit event",
            "occurred_at": next(
                event["occurred_at"]
                for event in completed_audit_events
                if event["tool"] == "reserve_resource"
            ),
            "proof_sha256": next(
                event["event_sha256"]
                for event in completed_audit_events
                if event["tool"] == "reserve_resource"
            ),
        },
        *[
            {
                "state": transition["to_state"],
                "source": "LifecycleStore transition",
                "occurred_at": transition["occurred_at"],
                "reason": transition["reason"],
                "from_version": transition["from_version"],
                "to_version": transition["to_version"],
                "proof_sha256": _sha256(CONTROL_PLANE_TRANSCRIPT),
            }
            for transition in transcript["lifecycle_history"]
        ],
    ]
    result_artifact = summary["artifact_references"][0]
    return {
        "demo_bundle_version": "1.1",
        "application_id": APPLICATION_ID,
        "mode": "replay",
        "evidence": {
            "label": "E3_REPLAY",
            "level": "E3",
            "runtime_kind": "sdk_simulator",
            "biological_claim": False,
            "claim_boundary": (
                "Deterministic technical pipeline evidence only; "
                "not a biological PNN result."
            ),
        },
        "hashes": {
            "config_sha256": application["config_sha256"],
            "decoder_sha256": application["decoder_sha256"],
            "application_source_sha256": application[
                "application_source_sha256"
            ],
            "source_replay_sha256": _sha256(SOURCE_REPLAY),
            "control_plane_transcript_sha256": _sha256(
                CONTROL_PLANE_TRANSCRIPT
            ),
            "control_plane_audit_sha256": _sha256(CONTROL_PLANE_AUDIT),
            "result_artifact_sha256": result_artifact["metadata"]["sha256"],
        },
        "run": {
            "run_id": application["application_run_id"],
            "orchestration_correlation_id": summary[
                "orchestration_correlation_id"
            ],
            "status": "complete",
            "trial_count": len(result.trials),
            "scored_trial_count": sum(
                trial.expected_label is not None for trial in result.trials
            ),
            "sham_trial_count": result.sham_trial_count,
            "pipeline_assertion_accuracy": result.accuracy,
        },
        "control_plane": {
            "resource_id": BACKEND_ID,
            "preset_id": summary["preset_id"],
            "provenance": "recorded_audited_mcp_e3_run",
            "approval_required": False,
            "exclusive_lease": True,
            "lease_released": True,
            "raw_substrate_output_exposed_to_agent": False,
            "audit_chain_verified": True,
            "audit_event_count": len(audit_events),
            "audit_request_count": len(transcript["audit_request_ids"]),
            "audit_head_sha256": audit_events[-1].event_hash,
            "audit_principal_id": audit_events[-1].principal_id,
            "result_artifact_id": result_artifact["artifact_id"],
            "result_artifact_sha256": result_artifact["metadata"]["sha256"],
            "mcp_steps": mcp_steps,
            "completed_audit_events": completed_audit_events,
            "lifecycle_evidence": lifecycle_evidence,
            "source_artifacts": {
                "transcript": str(
                    CONTROL_PLANE_TRANSCRIPT.relative_to(ROOT)
                ).replace("\\", "/"),
                "audit": str(
                    CONTROL_PLANE_AUDIT.relative_to(ROOT)
                ).replace("\\", "/"),
            },
        },
        "trials": trials,
    }


def render_bundle(bundle: dict) -> str:
    return json.dumps(bundle, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rendered = render_bundle(build_demo_bundle())
    if args.check:
        if not args.output.exists() or args.output.read_text(
            encoding="utf-8"
        ) != rendered:
            print(f"demo bundle is stale: {args.output}", file=sys.stderr)
            return 1
        print(f"demo bundle is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        output.write(rendered)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
