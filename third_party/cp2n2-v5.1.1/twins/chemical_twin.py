"""Lightweight chemical/dna-inspired twin for the CP²N² prototype."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp

from descriptors.capability_schema import ResetMode


@dataclass
class ChemicalTwinRunResult:
    """Normalized result produced by the chemical twin."""

    output_payload: dict[str, float | int | str | bool]
    confidence: float
    execution_latency_ms: float
    backend_state: str


class ChemicalTwin:
    """A tiny ODE-based backend representing slow, concentration-driven processing.

    This is not intended to be chemically realistic. It only captures three
    backend semantics that matter for the control plane:
    - concentration-style input
    - slow convergence behavior
    - explicit flush/reset/recharge lifecycle effects
    """

    def __init__(self, seed: int = 7) -> None:
        self._rng = np.random.default_rng(seed)
        self._contamination_level = 0.05
        self._calibration_confidence = 0.92
        self._invocation_count = 0
        self._last_convergence_time_ms = 0.0
        self._last_confidence = 0.0
        self._last_signal = 0.0
        self._health_status = "ready"

    def prepare(self, input_level: float = 1.0) -> tuple[bool, str]:
        """Prepare the twin for one invocation."""
        if self._contamination_level >= 0.98:
            self._health_status = "degraded"
            return False, "Chemical chamber is too contaminated and requires reset."
        if input_level <= 0.0:
            return False, "Input concentration must be positive."
        return True, "Chemical backend primed successfully."

    def run(self, input_level: float = 1.0, threshold: float = 0.60) -> ChemicalTwinRunResult:
        """Execute one simple reaction-dynamics run."""
        convergence_horizon = 8.0 + 2.0 * self._contamination_level
        k_decay = 0.35 + 0.05 * self._contamination_level
        k_convert = 0.22 * self._calibration_confidence
        k_clear = 0.10 + 0.08 * self._contamination_level

        def reaction_dynamics(_: float, state: np.ndarray) -> np.ndarray:
            x, y = state
            dx_dt = input_level - k_decay * x
            dy_dt = k_convert * x - k_clear * y
            return np.array([dx_dt, dy_dt])

        solution = solve_ivp(
            reaction_dynamics,
            t_span=(0.0, convergence_horizon),
            y0=np.array([0.0, 0.0]),
            t_eval=np.linspace(0.0, convergence_horizon, 80),
            method="RK45",
        )

        x_final = float(solution.y[0, -1])
        y_final = float(solution.y[1, -1])
        activation_score = float(y_final / (1.0 + abs(y_final)))
        label = "positive" if activation_score >= threshold else "negative"

        latency_noise = float(self._rng.uniform(25.0, 180.0))
        execution_latency_ms = float(8500.0 + 2500.0 * self._contamination_level + latency_noise)
        confidence = float(
            np.clip(
                self._calibration_confidence
                - 0.20 * self._contamination_level
                + 0.10 * activation_score,
                0.05,
                0.99,
            )
        )

        self._invocation_count += 1
        self._last_convergence_time_ms = execution_latency_ms
        self._last_confidence = confidence
        self._last_signal = activation_score
        self._contamination_level = float(min(1.0, self._contamination_level + 0.07 + 0.01 * input_level))

        self._health_status = "degraded" if self._contamination_level > 0.78 else "ready"

        output_payload = {
            "label": label,
            "activation_score": round(activation_score, 4),
            "product_concentration": round(y_final, 4),
            "intermediate_concentration": round(x_final, 4),
            "converged": bool(solution.success),
        }

        return ChemicalTwinRunResult(
            output_payload=output_payload,
            confidence=confidence,
            execution_latency_ms=execution_latency_ms,
            backend_state=self._health_status,
        )

    def telemetry(self) -> dict[str, float | int | str | bool | None]:
        """Return a telemetry snapshot."""
        drift_score = float(np.clip((1.0 - self._calibration_confidence) + 0.55 * self._contamination_level, 0.0, 1.0))
        age_of_information_ms = float(500.0 + 35.0 * self._invocation_count)

        return {
            "health_status": self._health_status,
            "contamination_level": round(self._contamination_level, 4),
            "calibration_confidence": round(self._calibration_confidence, 4),
            "last_convergence_time_ms": round(self._last_convergence_time_ms, 2),
            "last_confidence": round(self._last_confidence, 4),
            "last_activation_score": round(self._last_signal, 4),
            "drift_score": round(drift_score, 4),
            "age_of_information_ms": round(age_of_information_ms, 2),
            "invocation_count": self._invocation_count,
        }

    def reset(self, mode: ResetMode | None = None) -> bool:
        """Apply a backend recovery action."""
        selected_mode = mode or ResetMode.FLUSH

        if selected_mode == ResetMode.FLUSH:
            self._contamination_level = max(0.02, self._contamination_level - 0.45)
        elif selected_mode == ResetMode.RECHARGE:
            self._contamination_level = max(0.01, self._contamination_level - 0.30)
            self._calibration_confidence = min(1.0, self._calibration_confidence + 0.05)
        elif selected_mode in {ResetMode.HARD_RESET, ResetMode.SOFT_RESET}:
            self._contamination_level = 0.03
            self._calibration_confidence = 0.95
        else:
            return False

        self._health_status = "ready"
        return True

    def recalibrate(self) -> bool:
        """Increase calibration confidence and slightly reduce contamination impact."""
        self._calibration_confidence = float(min(1.0, self._calibration_confidence + 0.10))
        self._contamination_level = float(max(0.01, self._contamination_level - 0.05))
        self._health_status = "ready"
        return True
