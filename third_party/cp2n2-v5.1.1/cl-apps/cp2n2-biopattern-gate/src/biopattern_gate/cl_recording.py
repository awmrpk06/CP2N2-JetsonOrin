"""Offline validation of BioPattern Gate CL HDF5 recordings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cl import RecordingView

from .results import SessionResult


class CLRecordingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CLRecordingEvidence:
    recording_path: Path
    run_id: str
    runtime_kind: str
    evidence_ceiling: str
    terminal_state: str
    system_attributes: dict[str, Any]
    stream_names: tuple[str, ...]
    stim_count: int
    spike_count: int
    feature_records: tuple[dict[str, Any], ...]
    decision_records: tuple[dict[str, Any], ...]


def reconstruct_cl_recording(
    recording_path: Path,
    *,
    expected_run_id: str,
    expected_runtime_kind: str,
    expected_stream_names: tuple[str, ...],
) -> CLRecordingEvidence:
    """Load and validate the native CL recording as independent evidence."""

    path = Path(recording_path)
    if not path.is_file():
        raise CLRecordingValidationError(f"recording does not exist: {path}")
    with RecordingView(str(path)) as recording:
        attributes = dict(recording.attributes)
        application = attributes.get("application")
        if not isinstance(application, dict):
            raise CLRecordingValidationError("recording has no application metadata")
        if application.get("run_id") != expected_run_id:
            raise CLRecordingValidationError("recording run ID mismatch")
        if application.get("runtime_kind") != expected_runtime_kind:
            raise CLRecordingValidationError("recording runtime kind mismatch")
        if application.get("biological_claim") is not False:
            raise CLRecordingValidationError(
                "E3 recording does not deny biological claim eligibility"
            )
        terminal_state = str(application.get("terminal_state", "unknown"))
        if terminal_state not in {"complete", "aborted"}:
            raise CLRecordingValidationError(
                f"unsupported recording terminal state: {terminal_state}"
            )
        if recording.data_streams is None:
            raise CLRecordingValidationError("recording has no data streams")
        available_streams = tuple(sorted(recording.data_streams.keys()))
        missing = set(expected_stream_names) - set(available_streams)
        if missing:
            raise CLRecordingValidationError(
                f"recording is missing data streams: {sorted(missing)}"
            )
        feature_records = _load_stream_records(
            recording.data_streams["pattern_gate_features"]
        )
        decision_records = _load_stream_records(
            recording.data_streams["pattern_gate_decision"]
        )
        system_attributes = {
            key: attributes.get(key)
            for key in (
                "project_id",
                "chip_id",
                "cell_batch_id",
                "system_id",
                "hostname",
                "git_hash",
                "git_branch",
                "git_tags",
                "git_status",
            )
        }
        return CLRecordingEvidence(
            recording_path=path,
            run_id=expected_run_id,
            runtime_kind=expected_runtime_kind,
            evidence_ceiling=str(application.get("evidence_ceiling")),
            terminal_state=terminal_state,
            system_attributes=system_attributes,
            stream_names=available_streams,
            stim_count=0 if recording.stims is None else len(recording.stims),
            spike_count=0 if recording.spikes is None else len(recording.spikes),
            feature_records=feature_records,
            decision_records=decision_records,
        )


def verify_online_result_against_recording(
    result: SessionResult,
    evidence: CLRecordingEvidence,
) -> None:
    """Prove that recorded feature and decision streams match online output."""

    if result.run_id != evidence.run_id:
        raise CLRecordingValidationError("online/recording run ID mismatch")
    if result.runtime_kind != evidence.runtime_kind:
        raise CLRecordingValidationError("online/recording runtime mismatch")
    features_by_trial = _index_by_trial(
        evidence.feature_records,
        record_kind="feature",
    )
    decisions_by_trial = _index_by_trial(
        evidence.decision_records,
        record_kind="decision",
    )
    expected_indices = {trial.trial_index for trial in result.trials}
    if set(features_by_trial) != expected_indices:
        raise CLRecordingValidationError("recorded feature trial set mismatch")
    if set(decisions_by_trial) != expected_indices:
        raise CLRecordingValidationError("recorded decision trial set mismatch")
    for trial in result.trials:
        feature_record = features_by_trial[trial.trial_index]
        if feature_record.get("values") != trial.feature_values:
            raise CLRecordingValidationError(
                f"recorded features differ for trial {trial.trial_index}"
            )
        decision_record = decisions_by_trial[trial.trial_index]
        expected = {
            "predicted_label": trial.predicted_label,
            "route": trial.route,
            "probability_a": trial.probability_a,
            "decision_commit_sha256": trial.decision_commit_sha256,
        }
        if any(decision_record.get(key) != value for key, value in expected.items()):
            raise CLRecordingValidationError(
                f"recorded decision differs for trial {trial.trial_index}"
            )
        if decision_record.get("label_hidden_at_commit") is not True:
            raise CLRecordingValidationError(
                f"trial {trial.trial_index} lacks blinded-commit evidence"
            )


def _load_stream_records(stream: Any) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    previous_timestamp: int | None = None
    for timestamp, payload in stream.items():
        timestamp_int = int(timestamp)
        if previous_timestamp is not None and timestamp_int <= previous_timestamp:
            raise CLRecordingValidationError(
                "data stream timestamps are not strictly increasing"
            )
        if not isinstance(payload, dict):
            raise CLRecordingValidationError("data stream payload is not a mapping")
        records.append(dict(payload))
        previous_timestamp = timestamp_int
    return tuple(records)


def _index_by_trial(
    records: tuple[dict[str, Any], ...],
    *,
    record_kind: str,
) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for record in records:
        trial_index = record.get("trial_index")
        if not isinstance(trial_index, int):
            raise CLRecordingValidationError(
                f"{record_kind} record has no integer trial index"
            )
        if trial_index in indexed:
            raise CLRecordingValidationError(
                f"duplicate {record_kind} record for trial {trial_index}"
            )
        indexed[trial_index] = record
    return indexed
