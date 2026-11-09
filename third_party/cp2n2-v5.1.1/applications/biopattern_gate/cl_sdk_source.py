"""Deterministic stimulation-responsive data source for CL SDK E3 tests.

This source exists only to exercise the documented CL API path. It is not a
model of a culture and its output is never eligible for a biological claim.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from cl.sim import (
    DataSourceBatch,
    DataSourceSpike,
    DataSourceStim,
    SimulatorDataSource,
    SimulatorDataSourceMetadata,
)


class BioPatternGateResponsiveSource(SimulatorDataSource):
    """Emit fixed readout signatures after two recognised input-group stims."""

    def __init__(
        self,
        *,
        input_groups: dict[str, list[int]],
        readout_channels: list[int],
        inter_step_frames: int,
        observation_start_frames: int,
        pattern_a_sequence: list[str],
        pattern_b_sequence: list[str],
    ) -> None:
        self._input_groups = {
            str(name): frozenset(int(channel) for channel in channels)
            for name, channels in input_groups.items()
        }
        self._channel_to_group = {
            channel: group
            for group, channels in self._input_groups.items()
            for channel in channels
        }
        self._readout_channels = tuple(int(channel) for channel in readout_channels)
        self._inter_step_frames = int(inter_step_frames)
        self._observation_start_frames = int(observation_start_frames)
        self._pattern_a_sequence = tuple(str(item) for item in pattern_a_sequence)
        self._pattern_b_sequence = tuple(str(item) for item in pattern_b_sequence)
        self._pending_group_events: list[tuple[int, str]] = []
        self._scheduled_spikes: list[DataSourceSpike] = []
        self._metadata = SimulatorDataSourceMetadata(
            channel_count=64,
            frames_per_second=25_000,
            start_timestamp=0,
            duration_frames=None,
            seekable=True,
            realtime_only=False,
            supports_accelerated=True,
        )
        self._validate()

    @property
    def metadata(self) -> SimulatorDataSourceMetadata:
        return self._metadata

    def _validate(self) -> None:
        if len(self._input_groups) != 2:
            raise ValueError("responsive E3 source requires two input groups")
        if not self._readout_channels:
            raise ValueError("responsive E3 source requires readout channels")
        if self._inter_step_frames <= 0:
            raise ValueError("inter-step interval must be positive")
        if self._observation_start_frames < 0:
            raise ValueError("observation start must not be negative")
        if set(self._pattern_a_sequence) != set(self._input_groups):
            raise ValueError("pattern A does not match input groups")
        if self._pattern_b_sequence != tuple(reversed(self._pattern_a_sequence)):
            raise ValueError("pattern B must reverse pattern A")
        all_input_channels = set(self._channel_to_group)
        if all_input_channels & set(self._readout_channels):
            raise ValueError("input and readout channels must be disjoint")

    def on_stims(self, stims: Sequence[DataSourceStim]) -> None:
        timestamp_groups: dict[int, set[str]] = {}
        for stim in stims:
            group = self._channel_to_group.get(int(stim.channel))
            if group is None:
                continue
            timestamp_groups.setdefault(int(stim.timestamp), set()).add(group)

        for timestamp in sorted(timestamp_groups):
            groups = timestamp_groups[timestamp]
            if len(groups) != 1:
                continue
            event = (timestamp, next(iter(groups)))
            if event not in self._pending_group_events:
                self._pending_group_events.append(event)

        self._pending_group_events.sort()
        self._recognise_patterns()

    def _recognise_patterns(self) -> None:
        while len(self._pending_group_events) >= 2:
            first, second = self._pending_group_events[:2]
            interval = second[0] - first[0]
            sequence = (first[1], second[1])
            if (
                abs(interval - self._inter_step_frames) <= 1
                and sequence in {
                    self._pattern_a_sequence,
                    self._pattern_b_sequence,
                }
            ):
                self._schedule_signature(sequence, second[0])
                del self._pending_group_events[:2]
                continue
            del self._pending_group_events[0]

    def _schedule_signature(
        self,
        sequence: tuple[str, str],
        final_stim_timestamp: int,
    ) -> None:
        if sequence == self._pattern_a_sequence:
            signature_ms = (8.0, 15.0, 27.0, 35.0, 76.0)
        else:
            signature_ms = (14.0, 62.0, 71.0, 83.0, 91.0)
        observation_start = final_stim_timestamp + self._observation_start_frames
        readout = self._readout_channels[0]
        for timestamp_ms in signature_ms:
            timestamp = observation_start + round(timestamp_ms * 25)
            self._scheduled_spikes.append(
                DataSourceSpike(timestamp=timestamp, channel=readout)
            )
        self._scheduled_spikes.sort(key=lambda spike: spike.timestamp)

    def read(self, from_timestamp: int, frame_count: int) -> DataSourceBatch:
        stop_timestamp = from_timestamp + frame_count
        spikes = tuple(
            spike
            for spike in self._scheduled_spikes
            if from_timestamp <= spike.timestamp < stop_timestamp
        )
        return DataSourceBatch(
            frames=np.zeros((frame_count, 64), dtype=np.int16),
            spikes=spikes,
        )


def create_biopattern_gate_source(
    **config: object,
) -> BioPatternGateResponsiveSource:
    """Importable factory used by the CL simulator subprocess."""

    return BioPatternGateResponsiveSource(**config)  # type: ignore[arg-type]
