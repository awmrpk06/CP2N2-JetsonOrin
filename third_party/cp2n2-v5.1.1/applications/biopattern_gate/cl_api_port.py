"""Documented CL API boundary for BioPattern Gate.

The same port is used with the CL SDK Simulator for E3 integration evidence
and, only after a separate provider-approved preset exists, on a physical CL1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cl

from .config import BioPatternGateConfig
from .decoder import GateDecision
from .features import SpikeEvent
from .port import TrialObservation
from .scheduler import TrialKind, TrialPlan


STREAM_NAMES = (
    "pattern_gate_session",
    "pattern_gate_trial",
    "pattern_gate_gate",
    "pattern_gate_features",
    "pattern_gate_decision",
    "pattern_gate_control_status",
)

_NON_STIMULATABLE_CHANNELS = frozenset({0, 4, 7, 56, 63})


@dataclass(frozen=True)
class CLApiRuntimeConfig:
    """Server-owned CL parameters; never exposed as agent-editable fields."""

    input_groups: tuple[tuple[str, tuple[int, ...]], ...]
    readout_groups: tuple[tuple[str, tuple[int, ...]], ...]
    stim_components: tuple[tuple[int, float], ...]
    base_lead_time_us: int = 200
    include_raw_samples: bool = False
    responsive_source_factory: str | None = None

    @property
    def input_group_map(self) -> dict[str, tuple[int, ...]]:
        return dict(self.input_groups)

    @property
    def readout_group_map(self) -> dict[str, tuple[int, ...]]:
        return dict(self.readout_groups)

    @property
    def all_input_channels(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    channel
                    for _, channels in self.input_groups
                    for channel in channels
                }
            )
        )

    def validate(self, config: BioPatternGateConfig) -> None:
        inputs = self.input_group_map
        readouts = self.readout_group_map
        if set(inputs) != set(config.input_group_refs):
            raise ValueError("runtime input groups do not match validated preset")
        if set(readouts) != set(config.readout_group_refs):
            raise ValueError("runtime readout groups do not match validated preset")
        input_channels = {
            channel for channels in inputs.values() for channel in channels
        }
        readout_channels = {
            channel for channels in readouts.values() for channel in channels
        }
        if not input_channels or not readout_channels:
            raise ValueError("runtime channel groups must not be empty")
        if input_channels & readout_channels:
            raise ValueError("runtime input and readout channels overlap")
        if input_channels & _NON_STIMULATABLE_CHANNELS:
            raise ValueError("runtime uses a non-stimulatable input channel")
        if any(not 0 <= channel < 64 for channel in input_channels | readout_channels):
            raise ValueError("runtime channel is outside the CL1 channel range")
        if self.base_lead_time_us < 80 or self.base_lead_time_us % 20:
            raise ValueError("base lead time violates the CL API contract")
        if not 1 <= len(self.stim_components) <= 3:
            raise ValueError("CL stimulation requires one to three components")
        signed_charge_nc = 0.0
        for duration_us, amplitude_ua in self.stim_components:
            if duration_us <= 0 or duration_us % 20:
                raise ValueError("pulse width violates the CL API contract")
            if not -3.0 <= amplitude_ua <= 3.0 or amplitude_ua == 0:
                raise ValueError("stimulation amplitude violates the CL API contract")
            if abs(duration_us * amplitude_ua / 1000.0) > 3.0:
                raise ValueError("stimulation component exceeds 3 nC")
            signed_charge_nc += duration_us * amplitude_ua / 1000.0
        if abs(signed_charge_nc) > 1e-9:
            raise ValueError("technical E3 stimulation must be charge-balanced")

    def source_config(self, config: BioPatternGateConfig) -> dict[str, object]:
        readout_channels = sorted(
            {
                channel
                for channels in self.readout_group_map.values()
                for channel in channels
            }
        )
        return {
            "input_groups": {
                name: list(channels) for name, channels in self.input_groups
            },
            "readout_channels": readout_channels,
            "inter_step_frames": round(config.timing.inter_step_ms * 25),
            "observation_start_frames": round(
                config.timing.observation_start_ms * 25
            ),
            "pattern_a_sequence": list(config.pattern_a.sequence),
            "pattern_b_sequence": list(config.pattern_b.sequence),
        }


def technical_e3_runtime_config(
    *,
    source_factory: str = (
        "applications.biopattern_gate.cl_sdk_source:"
        "create_biopattern_gate_source"
    ),
) -> CLApiRuntimeConfig:
    """Return the fixed non-biological SDK integration configuration."""

    return CLApiRuntimeConfig(
        input_groups=(
            ("sim-input-left", (8,)),
            ("sim-input-right", (9,)),
        ),
        readout_groups=(("sim-readout", (20, 21, 22, 23)),),
        stim_components=((160, -1.0), (160, 1.0)),
        base_lead_time_us=200,
        include_raw_samples=False,
        responsive_source_factory=source_factory,
    )


class CLApiBioPatternGatePort:
    """Run BioPattern Gate through the documented CL API surface."""

    runtime_kind = "sdk_simulator"

    def __init__(
        self,
        *,
        run_id: str,
        output_directory: Path,
        runtime_config: CLApiRuntimeConfig,
    ) -> None:
        self.run_id = run_id
        self.output_directory = Path(output_directory)
        self.runtime_config = runtime_config
        self.recording_path: Path | None = None
        self.system_attributes: dict[str, Any] = {}
        self._context: Any = None
        self._neurons: Any = None
        self._recording: Any = None
        self._streams: dict[str, Any] = {}
        self._last_stream_timestamp: dict[str, int] = {}
        self._prepared = False
        self._closed = False
        self._aborted_reason: str | None = None
        self._source_registered = False

    def prepare(self, config: BioPatternGateConfig) -> None:
        if self._prepared or self._closed:
            raise RuntimeError("CL API port cannot be prepared in its current state")
        self.runtime_config.validate(config)
        if not cl.is_simulator():
            raise RuntimeError(
                "technical E3 runtime refuses physical CL1 execution"
            )
        if config.evidence.runtime_kind != self.runtime_kind:
            raise ValueError("configuration/runtime mismatch")
        if self.runtime_config.responsive_source_factory is None:
            raise RuntimeError("technical E3 requires a registered simulator source")

        cl.sim.set_simulator_data_source(
            self.runtime_config.responsive_source_factory,
            config=self.runtime_config.source_config(config),
        )
        self._source_registered = True
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._context = cl.open(take_control=True, wait_until_recordable=True)
        self._neurons = self._context.__enter__()
        try:
            if self._neurons.get_frames_per_second() != 25_000:
                raise RuntimeError("unexpected CL sample rate")
            if self._neurons.get_channel_count() != 64:
                raise RuntimeError("unexpected CL channel count")
            self.system_attributes = dict(cl.get_system_attributes())
            for stream_name in STREAM_NAMES:
                self._streams[stream_name] = self._neurons.create_data_stream(
                    stream_name,
                    attributes={
                        "run_id": self.run_id,
                        "runtime_kind": self.runtime_kind,
                        "evidence_ceiling": "E3",
                    },
                )
            suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", self.run_id).strip("-")
            self._recording = self._neurons.record(
                file_suffix=f"cp2n2-{suffix or 'run'}",
                file_location=str(self.output_directory),
                attributes={
                    "run_id": self.run_id,
                    "runtime_kind": self.runtime_kind,
                    "evidence_ceiling": "E3",
                    "biological_claim": False,
                    "config_sha256": config.sha256(),
                    "protocol_version": config.protocol_version,
                    "preset_id": config.preset_id,
                },
                include_spikes=True,
                include_stims=True,
                include_raw_samples=self.runtime_config.include_raw_samples,
                include_data_streams=True,
            )
            self._prepared = True
            self._append_stream(
                "pattern_gate_session",
                {
                    "status": "running",
                    "runtime_kind": self.runtime_kind,
                    "evidence_level": "E3",
                    "biological_claim": False,
                    "system_attributes": self.system_attributes,
                },
            )
            self._append_stream(
                "pattern_gate_control_status",
                {
                    "lifecycle_state": "running",
                    "lease_state": "provider-managed",
                    "control_plane_source": "outside-cl-application",
                },
            )
        except Exception:
            self._cleanup()
            raise

    def observe_trial(
        self,
        plan: TrialPlan,
        *,
        logical_sequence: tuple[str, str] | None,
        config: BioPatternGateConfig,
    ) -> TrialObservation:
        if not self._prepared or self._closed or self._neurons is None:
            raise RuntimeError("CL API port is not available for observation")
        before = int(self._neurons.timestamp())
        fps = int(self._neurons.get_frames_per_second())
        base_lead_frames = round(self.runtime_config.base_lead_time_us * fps / 1_000_000)
        inter_step_frames = round(config.timing.inter_step_ms * fps / 1000)
        observation_start_frames = round(
            config.timing.observation_start_ms * fps / 1000
        )
        observation_frames = round(
            config.timing.observation_duration_ms * fps / 1000
        )
        inter_trial_frames = round(config.timing.inter_trial_ms * fps / 1000)
        margin_frames = round(50 * fps / 1000)
        expected_final_stim = before + base_lead_frames + inter_step_frames

        self._append_stream(
            "pattern_gate_trial",
            {
                "trial_index": plan.trial_index,
                "block_index": plan.block_index,
                "status": "stimulating" if logical_sequence else "sham",
                "label_hidden": True,
            },
            timestamp=before,
        )

        if logical_sequence is not None:
            stimulation_plan = self._neurons.create_stim_plan()
            stimulation_plan.channels_to_interrupt = cl.ChannelSet(
                self.runtime_config.all_input_channels
            )
            stim_args = [
                value
                for component in self.runtime_config.stim_components
                for value in component
            ]
            stim_design = cl.StimDesign(*stim_args)
            first_channels = cl.ChannelSet(
                self.runtime_config.input_group_map[logical_sequence[0]]
            )
            second_channels = cl.ChannelSet(
                self.runtime_config.input_group_map[logical_sequence[1]]
            )
            stimulation_plan.stim(
                first_channels,
                stim_design,
                lead_time_us=self.runtime_config.base_lead_time_us,
            )
            stimulation_plan.stim(
                second_channels,
                stim_design,
                lead_time_us=(
                    self.runtime_config.base_lead_time_us
                    + config.timing.inter_step_ms * 1000
                ),
            )
            stimulation_plan.run()

        frame_count = (
            base_lead_frames
            + inter_step_frames
            + observation_start_frames
            + observation_frames
            + inter_trial_frames
            + margin_frames
        )
        detection = self._neurons.read(frame_count, before, analysis=True)
        input_channels = set(self.runtime_config.all_input_channels)
        observed_stims = sorted(
            (
                int(stim.timestamp),
                int(stim.channel),
            )
            for stim in detection.stims
            if int(stim.channel) in input_channels
        )
        if logical_sequence is None:
            if observed_stims:
                raise RuntimeError("sham trial contains input-channel stimulation")
            final_stim_timestamp = expected_final_stim
        else:
            final_stim_timestamp = self._validate_stimulation_acknowledgement(
                observed_stims,
                logical_sequence=logical_sequence,
                inter_step_frames=inter_step_frames,
            )

        observation_start = final_stim_timestamp + observation_start_frames
        observation_stop = observation_start + observation_frames
        channel_to_group = {
            channel: group
            for group, channels in self.runtime_config.readout_group_map.items()
            for channel in channels
        }
        events = tuple(
            SpikeEvent(
                timestamp_ms=(
                    (int(spike.timestamp) - observation_start) * 1000.0 / fps
                ),
                readout_group_ref=channel_to_group[int(spike.channel)],
            )
            for spike in detection.spikes
            if (
                int(spike.channel) in channel_to_group
                and observation_start <= int(spike.timestamp) < observation_stop
            )
        )
        self._append_stream(
            "pattern_gate_gate",
            {
                "trial_index": plan.trial_index,
                "status": "observed",
                "event_count": len(events),
                "stim_count": len(observed_stims),
                "label_hidden": True,
            },
        )
        return TrialObservation(
            trial_index=plan.trial_index,
            events=events,
            runtime_kind=self.runtime_kind,
            telemetry={
                "source": "cl-sdk-responsive-e3",
                "biological_claim": False,
                "logical_sequence_present": logical_sequence is not None,
                "stim_count": len(observed_stims),
                "event_count": len(events),
                "observation_start_timestamp": observation_start,
                "observation_stop_timestamp": observation_stop,
            },
        )

    def _validate_stimulation_acknowledgement(
        self,
        observed_stims: list[tuple[int, int]],
        *,
        logical_sequence: tuple[str, str],
        inter_step_frames: int,
    ) -> int:
        by_timestamp: dict[int, set[int]] = {}
        for timestamp, channel in observed_stims:
            by_timestamp.setdefault(timestamp, set()).add(channel)
        if len(by_timestamp) != 2:
            raise RuntimeError("trial does not contain exactly two stimulation times")
        timestamps = sorted(by_timestamp)
        expected_first = set(
            self.runtime_config.input_group_map[logical_sequence[0]]
        )
        expected_second = set(
            self.runtime_config.input_group_map[logical_sequence[1]]
        )
        if by_timestamp[timestamps[0]] != expected_first:
            raise RuntimeError("first stimulation group acknowledgement mismatch")
        if by_timestamp[timestamps[1]] != expected_second:
            raise RuntimeError("second stimulation group acknowledgement mismatch")
        if abs((timestamps[1] - timestamps[0]) - inter_step_frames) > 1:
            raise RuntimeError("stimulation interval acknowledgement mismatch")
        return timestamps[1]

    def record_features(
        self,
        plan: TrialPlan,
        *,
        feature_values: dict[str, float],
    ) -> None:
        self._append_stream(
            "pattern_gate_features",
            {
                "trial_index": plan.trial_index,
                "schema_version": "pattern-gate-features-v1",
                "values": feature_values,
            },
        )

    def record_decision(
        self,
        plan: TrialPlan,
        *,
        decision: GateDecision,
        decision_commit_sha256: str,
    ) -> None:
        self._append_stream(
            "pattern_gate_decision",
            {
                "trial_index": plan.trial_index,
                "predicted_label": decision.predicted_label,
                "route": decision.route,
                "probability_a": decision.probability_a,
                "linear_score": decision.linear_score,
                "decision_commit_sha256": decision_commit_sha256,
                "label_hidden_at_commit": True,
            },
        )

    def abort(self, reason: str) -> None:
        self._aborted_reason = reason
        if self._neurons is not None:
            try:
                self._neurons.interrupt(
                    cl.ChannelSet(self.runtime_config.all_input_channels)
                )
            except Exception:
                pass
        if self._recording is not None:
            try:
                self._recording.update_attributes(
                    {
                        "terminal_state": "aborted",
                        "abort_reason": reason,
                    }
                )
            except Exception:
                pass
        if self._streams:
            try:
                self._append_stream(
                    "pattern_gate_control_status",
                    {
                        "lifecycle_state": "aborted",
                        "lease_state": "provider-managed",
                        "reason": reason,
                    },
                )
            except Exception:
                pass

    def close(self) -> None:
        if self._closed:
            return
        if self._recording is not None:
            terminal_state = "aborted" if self._aborted_reason else "complete"
            try:
                self._recording.update_attributes(
                    {"terminal_state": terminal_state}
                )
                self._append_stream(
                    "pattern_gate_session",
                    {
                        "status": terminal_state,
                        "runtime_kind": self.runtime_kind,
                        "evidence_level": "E3",
                        "biological_claim": False,
                    },
                )
                self._recording.stop()
                recording_path = self._recording.file.get("path")
                if recording_path:
                    self.recording_path = Path(recording_path)
            finally:
                self._recording = None
        self._cleanup()
        self._closed = True

    def _cleanup(self) -> None:
        if self._context is not None:
            context = self._context
            self._context = None
            self._neurons = None
            context.__exit__(None, None, None)
        if self._source_registered:
            cl.sim.clear_simulator_data_source()
            self._source_registered = False

    def _append_stream(
        self,
        stream_name: str,
        payload: dict[str, Any],
        *,
        timestamp: int | None = None,
    ) -> None:
        if stream_name not in self._streams or self._neurons is None:
            return
        candidate = int(self._neurons.timestamp() if timestamp is None else timestamp)
        timestamp_value = max(
            candidate,
            self._last_stream_timestamp.get(stream_name, candidate - 1) + 1,
        )
        self._streams[stream_name].append(timestamp_value, payload)
        self._last_stream_timestamp[stream_name] = timestamp_value
