"""Independent reconstruction checks for online BioPattern Gate decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .decoder import FrozenLinearDecoder
from .features import FeatureVector
from .results import SessionResult
from .runner import decision_commit


class ReconstructionError(ValueError):
    pass


@dataclass(frozen=True)
class ReconstructionReport:
    trial_count: int
    decision_match_count: int
    commitment_match_count: int
    balanced_accuracy: float
    confusion: dict[str, int]


def reconstruct_session(
    result: SessionResult,
    decoder: FrozenLinearDecoder,
) -> ReconstructionReport:
    """Regenerate decisions from archived features without online state."""

    if result.decoder_sha256 != decoder.sha256():
        raise ReconstructionError("archived decoder hash does not match artifact")
    decision_matches = 0
    commitment_matches = 0
    confusion = {"A_as_A": 0, "A_as_B": 0, "B_as_A": 0, "B_as_B": 0}
    for trial in result.trials:
        decision = decoder.decide(
            FeatureVector(decoder.feature_schema_version, trial.feature_values)
        )
        if (
            decision.predicted_label == trial.predicted_label
            and decision.route == trial.route
            and abs(decision.probability_a - trial.probability_a) < 1e-12
        ):
            decision_matches += 1
        expected_commit = decision_commit(
            run_id=result.run_id,
            trial_index=trial.trial_index,
            feature_values=trial.feature_values,
            predicted_label=decision.predicted_label,
            route=decision.route,
        )
        if expected_commit == trial.decision_commit_sha256:
            commitment_matches += 1
        if trial.expected_label is not None:
            confusion[
                f"{trial.expected_label}_as_{decision.predicted_label}"
            ] += 1
    if decision_matches != len(result.trials):
        raise ReconstructionError("one or more online decisions do not reconstruct")
    if commitment_matches != len(result.trials):
        raise ReconstructionError("one or more decision commitments do not verify")
    sensitivity_a = confusion["A_as_A"] / (
        confusion["A_as_A"] + confusion["A_as_B"]
    )
    sensitivity_b = confusion["B_as_B"] / (
        confusion["B_as_A"] + confusion["B_as_B"]
    )
    return ReconstructionReport(
        trial_count=len(result.trials),
        decision_match_count=decision_matches,
        commitment_match_count=commitment_matches,
        balanced_accuracy=(sensitivity_a + sensitivity_b) / 2.0,
        confusion=confusion,
    )
