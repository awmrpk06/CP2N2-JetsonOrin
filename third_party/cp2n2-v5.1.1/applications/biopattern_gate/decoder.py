"""Frozen linear readout used after the dynamic PNN/reservoir response."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from .features import FeatureVector


class DecoderArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class GateDecision:
    predicted_label: str
    route: str
    probability_a: float
    linear_score: float


@dataclass(frozen=True)
class FrozenLinearDecoder:
    """Immutable decoder artifact; fitting is intentionally outside execution."""

    feature_schema_version: str
    weights: dict[str, float]
    bias: float
    threshold: float
    training_run_ref: str

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "feature_schema_version": self.feature_schema_version,
                "weights": self.weights,
                "bias": self.bias,
                "threshold": self.threshold,
                "training_run_ref": self.training_run_ref,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def decide(self, features: FeatureVector) -> GateDecision:
        if features.schema_version != self.feature_schema_version:
            raise DecoderArtifactError("feature schema does not match decoder artifact")
        unknown = set(self.weights) - set(features.values)
        if unknown:
            raise DecoderArtifactError(
                f"decoder requires missing features: {sorted(unknown)}"
            )
        score = self.bias + sum(
            weight * features.values[name] for name, weight in self.weights.items()
        )
        probability_a = _sigmoid(score)
        if probability_a >= self.threshold:
            return GateDecision("A", "left", probability_a, score)
        return GateDecision("B", "right", probability_a, score)


def load_decoder(path: Path) -> FrozenLinearDecoder:
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected_fields = {
        "feature_schema_version",
        "weights",
        "bias",
        "threshold",
        "training_run_ref",
    }
    if set(raw) != expected_fields:
        raise DecoderArtifactError("decoder artifact has missing or unknown fields")
    if not isinstance(raw["weights"], dict) or not raw["weights"]:
        raise DecoderArtifactError("decoder artifact requires non-empty weights")
    return FrozenLinearDecoder(
        feature_schema_version=str(raw["feature_schema_version"]),
        weights={str(key): float(value) for key, value in raw["weights"].items()},
        bias=float(raw["bias"]),
        threshold=float(raw["threshold"]),
        training_run_ref=str(raw["training_run_ref"]),
    )


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)
