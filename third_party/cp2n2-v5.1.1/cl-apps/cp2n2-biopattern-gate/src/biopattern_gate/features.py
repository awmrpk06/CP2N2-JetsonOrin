"""Versioned feature extraction from readout-group spike events."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .config import FeatureConfig


@dataclass(frozen=True, order=True)
class SpikeEvent:
    """A provider-neutral event timestamp relative to observation start."""

    timestamp_ms: float
    readout_group_ref: str


@dataclass(frozen=True)
class FeatureVector:
    schema_version: str
    values: dict[str, float]


class InvalidObservation(ValueError):
    pass


def extract_features(
    events: Iterable[SpikeEvent],
    *,
    readout_group_refs: tuple[str, ...],
    observation_duration_ms: int,
    config: FeatureConfig,
) -> FeatureVector:
    """Extract fixed-shape count and latency features.

    Unknown groups and out-of-window timestamps invalidate the observation
    rather than being silently discarded.
    """

    groups = tuple(sorted(readout_group_refs))
    allowed_groups = set(groups)
    grouped: dict[str, list[float]] = {group: [] for group in groups}
    for event in sorted(events):
        if event.readout_group_ref not in allowed_groups:
            raise InvalidObservation(
                f"unknown readout group {event.readout_group_ref!r}"
            )
        if not 0.0 <= event.timestamp_ms < observation_duration_ms:
            raise InvalidObservation(
                f"event timestamp {event.timestamp_ms} is outside observation window"
            )
        grouped[event.readout_group_ref].append(event.timestamp_ms)

    bin_count = math.ceil(observation_duration_ms / config.bin_width_ms)
    values: dict[str, float] = {}
    for group in groups:
        timestamps = grouped[group]
        for bin_index in range(bin_count):
            start = bin_index * config.bin_width_ms
            end = min(start + config.bin_width_ms, observation_duration_ms)
            values[f"spike_count::{group}::bin-{bin_index:02d}"] = float(
                sum(start <= timestamp < end for timestamp in timestamps)
            )
        if config.include_first_spike_latency:
            values[f"first_spike_latency_ms::{group}"] = (
                min(timestamps) if timestamps else float(observation_duration_ms)
            )
        if config.include_burst_count:
            values[f"burst_count::{group}"] = float(_count_bursts(timestamps))

    if config.include_active_group_count:
        values["active_readout_group_count"] = float(
            sum(bool(timestamps) for timestamps in grouped.values())
        )
    return FeatureVector(config.schema_version, values)


def _count_bursts(timestamps: list[float], *, max_gap_ms: float = 10.0) -> int:
    if len(timestamps) < 2:
        return 0
    return sum(
        1
        for previous, current in zip(timestamps, timestamps[1:])
        if current - previous <= max_gap_ms
    )
