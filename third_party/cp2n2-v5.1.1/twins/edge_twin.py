"""Lightweight fast edge backend for the CP²N² prototype."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from descriptors.capability_schema import ResetMode


@dataclass
class EdgeTwinRunResult:
    """Normalized result produced by the fast edge twin."""

    output_payload: dict[str, float | int | str | bool | list[float]]
    confidence: float
    execution_latency_ms: float
    backend_state: str


class EdgeTwin:
    """A tiny vector/tensor-oriented backend with device-like semantics."""

    def __init__(self, seed: int = 19, input_dim: int = 4, output_dim: int = 3) -> None:
        self._rng = np.random.default_rng(seed)
        self._input_dim = input_dim
        self._output_dim = output_dim
        self._weights = self._rng.normal(0.0, 0.9, size=(output_dim, input_dim))
        self._bias = self._rng.normal(0.0, 0.2, size=(output_dim,))
        self._drift_score = 0.08
        self._programming_noise = 0.02
        self._invocation_count = 0
        self._last_latency_ms = 0.0
        self._last_confidence = 0.0
        self._last_energy_proxy_mj = 0.0
        self._health_status = "ready"

    def prepare(self, input_vector: list[float] | None = None) -> tuple[bool, str]:
        """Prepare the backend for one inference."""
        if input_vector is not None and len(input_vector) != self._input_dim:
            return False, f"Expected input vector length {self._input_dim}, got {len(input_vector)}."
        return True, "Edge backend ready for inference."

    def run(self, input_vector: list[float] | None = None) -> EdgeTwinRunResult:
        """Execute one low-latency inference."""
        if input_vector is None:
            input_vector = [0.2, 0.4, 0.6, 0.8]

        vector = np.asarray(input_vector, dtype=float)
        noise = self._rng.normal(0.0, self._programming_noise, size=self._weights.shape)
        effective_weights = self._weights + self._drift_score * noise
        logits = effective_weights @ vector + self._bias

        logits = logits - np.max(logits)
        probs = np.exp(logits) / np.sum(np.exp(logits))
        label_index = int(np.argmax(probs))
        confidence = float(np.max(probs))

        latency_ms = float(3.5 + 2.5 * self._drift_score + self._rng.uniform(0.0, 0.6))
        energy_proxy_mj = float(0.08 + 0.04 * np.linalg.norm(vector) + 0.03 * self._drift_score)

        self._invocation_count += 1
        self._last_latency_ms = latency_ms
        self._last_confidence = confidence
        self._last_energy_proxy_mj = energy_proxy_mj
        self._drift_score = float(min(1.0, self._drift_score + 0.015))
        self._health_status = "degraded" if self._drift_score > 0.82 else "ready"

        output_payload = {
            "label_index": label_index,
            "probabilities": [round(float(value), 4) for value in probs],
            "vector_norm": round(float(np.linalg.norm(vector)), 4),
        }

        return EdgeTwinRunResult(
            output_payload=output_payload,
            confidence=confidence,
            execution_latency_ms=latency_ms,
            backend_state=self._health_status,
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        """Return a telemetry snapshot."""
        return {
            "health_status": self._health_status,
            "drift_score": round(self._drift_score, 4),
            "programming_noise": round(self._programming_noise, 4),
            "invocation_count": self._invocation_count,
            "last_latency_ms": round(self._last_latency_ms, 4),
            "last_confidence": round(self._last_confidence, 4),
            "energy_proxy_mj": round(self._last_energy_proxy_mj, 6),
        }

    def reset(self, mode: ResetMode | None = None) -> bool:
        """Apply a reset or recovery operation."""
        selected_mode = mode or ResetMode.SOFT_RESET

        if selected_mode == ResetMode.SOFT_RESET:
            self._drift_score = max(0.03, self._drift_score - 0.18)
        elif selected_mode == ResetMode.RECALIBRATE:
            self._programming_noise = max(0.005, self._programming_noise - 0.005)
        elif selected_mode == ResetMode.HARD_RESET:
            self._drift_score = 0.05
            self._programming_noise = 0.015
        else:
            return False

        self._health_status = "ready"
        return True

    def recalibrate(self) -> bool:
        """Reduce drift and programming noise."""
        self._drift_score = float(max(0.02, self._drift_score - 0.12))
        self._programming_noise = float(max(0.005, self._programming_noise - 0.004))
        self._health_status = "ready"
        return True
