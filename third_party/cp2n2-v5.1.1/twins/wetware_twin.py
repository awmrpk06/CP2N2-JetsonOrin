"""Lightweight wetware-inspired twin for the CP²N² prototype."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from descriptors.capability_schema import ResetMode


@dataclass
class WetwareTwinRunResult:
    """Normalized result produced by the wetware twin."""

    output_payload: dict[str, float | int | str | bool]
    confidence: float
    execution_latency_ms: float
    backend_state: str


class WetwareTwin:
    """A small synthetic spike-response twin.

    The goal is not biological realism. The twin only emulates:
    - stimulation/observation semantics,
    - viability-sensitive state,
    - rest/recalibration lifecycle operations.
    """

    def __init__(self, seed: int = 11) -> None:
        self._rng = np.random.default_rng(seed)
        self._excitability = 0.72
        self._noise_level = 0.12
        self._viability_score = 0.94
        self._recent_load = 0.08
        self._last_firing_rate_hz = 0.0
        self._last_response_delay_ms = 0.0
        self._last_confidence = 0.0
        self._health_status = "ready"

    def prepare(self, stimulation_strength: float = 0.5) -> tuple[bool, str]:
        """Prepare the twin for a stimulation/observation cycle."""
        if self._viability_score < 0.20:
            self._health_status = "degraded"
            return False, "Wetware backend viability is too low for safe stimulation."
        if stimulation_strength <= 0.0:
            return False, "Stimulation strength must be positive."
        return True, "Wetware backend is ready for stimulation."

    def run(self, stimulation_strength: float = 0.5, observation_window_ms: float = 120.0) -> WetwareTwinRunResult:
        """Execute one stimulation/observation cycle."""
        effective_drive = stimulation_strength * self._excitability * self._viability_score
        noisy_drive = float(max(0.0, effective_drive + self._rng.normal(0.0, self._noise_level * 0.05)))

        spike_count = max(1, int(round(30.0 * noisy_drive + self._rng.integers(0, 5))))
        firing_rate_hz = float(1000.0 * spike_count / max(observation_window_ms, 1.0))
        response_delay_ms = float(22.0 + 35.0 * self._recent_load + self._rng.uniform(0.0, 8.0))
        decoded_label = "engaged" if firing_rate_hz >= 90.0 else "quiescent"

        confidence = float(
            np.clip(
                self._viability_score * (1.0 - self._noise_level) * (0.75 + 0.25 * self._excitability),
                0.05,
                0.99,
            )
        )

        self._last_firing_rate_hz = firing_rate_hz
        self._last_response_delay_ms = response_delay_ms
        self._last_confidence = confidence

        self._recent_load = float(min(1.0, self._recent_load + 0.10))
        self._viability_score = float(max(0.0, self._viability_score - 0.025 - 0.02 * stimulation_strength))
        self._noise_level = float(min(0.45, self._noise_level + 0.01))

        self._health_status = "degraded" if self._viability_score < 0.55 else "ready"

        output_payload = {
            "decoded_label": decoded_label,
            "spike_count": spike_count,
            "firing_rate_hz": round(firing_rate_hz, 3),
            "observation_window_ms": round(observation_window_ms, 3),
        }

        return WetwareTwinRunResult(
            output_payload=output_payload,
            confidence=confidence,
            execution_latency_ms=response_delay_ms,
            backend_state=self._health_status,
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        """Return a telemetry snapshot."""
        drift_score = float(np.clip((1.0 - self._viability_score) * 0.8 + self._noise_level * 0.8, 0.0, 1.0))
        return {
            "health_status": self._health_status,
            "viability_score": round(self._viability_score, 4),
            "noise_level": round(self._noise_level, 4),
            "recent_load": round(self._recent_load, 4),
            "firing_rate_hz": round(self._last_firing_rate_hz, 3),
            "response_delay_ms": round(self._last_response_delay_ms, 3),
            "last_confidence": round(self._last_confidence, 4),
            "drift_score": round(drift_score, 4),
        }

    def reset(self, mode: ResetMode | None = None) -> bool:
        """Apply a recovery operation."""
        selected_mode = mode or ResetMode.REST

        if selected_mode == ResetMode.REST:
            self._recent_load = max(0.02, self._recent_load - 0.35)
            self._viability_score = min(1.0, self._viability_score + 0.10)
        elif selected_mode == ResetMode.RECALIBRATE:
            self._noise_level = max(0.04, self._noise_level - 0.04)
        elif selected_mode in {ResetMode.SOFT_RESET, ResetMode.HARD_RESET}:
            self._excitability = 0.72
            self._noise_level = 0.10
            self._viability_score = 0.92
            self._recent_load = 0.05
        else:
            return False

        self._health_status = "ready"
        return True

    def recalibrate(self) -> bool:
        """Improve signal quality without a full reset."""
        self._noise_level = float(max(0.03, self._noise_level - 0.03))
        self._excitability = float(min(1.0, self._excitability + 0.04))
        self._health_status = "ready"
        return True
