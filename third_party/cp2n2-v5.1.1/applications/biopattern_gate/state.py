"""Fail-closed state machines for BioPattern Gate trials and sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StateTransitionError(RuntimeError):
    pass


class TrialState(str, Enum):
    SCHEDULED = "scheduled"
    PRE_STIMULUS = "pre_stimulus"
    STIMULATING = "stimulating"
    ARTEFACT_BLANKING = "artefact_blanking"
    OBSERVING = "observing"
    FEATURIZING = "featurizing"
    DECISION_COMMITTED = "decision_committed"
    LABEL_REVEALED = "label_revealed"
    INTER_TRIAL = "inter_trial"
    COMPLETE = "complete"
    ABORTED = "aborted"
    INVALID = "invalid"


class SessionState(str, Enum):
    CREATED = "created"
    PREFLIGHT = "preflight"
    PRE_BASELINE = "pre_baseline"
    MAPPING = "mapping"
    CALIBRATION = "calibration"
    FROZEN_VALIDATION = "frozen_validation"
    CONFIRMATORY_TEST = "confirmatory_test"
    POST_BASELINE = "post_baseline"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    ABORTED = "aborted"
    INVALID = "invalid"


TRIAL_TRANSITIONS: dict[TrialState, frozenset[TrialState]] = {
    TrialState.SCHEDULED: frozenset({TrialState.PRE_STIMULUS}),
    TrialState.PRE_STIMULUS: frozenset({TrialState.STIMULATING}),
    TrialState.STIMULATING: frozenset({TrialState.ARTEFACT_BLANKING}),
    TrialState.ARTEFACT_BLANKING: frozenset({TrialState.OBSERVING}),
    TrialState.OBSERVING: frozenset({TrialState.FEATURIZING}),
    TrialState.FEATURIZING: frozenset({TrialState.DECISION_COMMITTED}),
    TrialState.DECISION_COMMITTED: frozenset({TrialState.LABEL_REVEALED}),
    TrialState.LABEL_REVEALED: frozenset({TrialState.INTER_TRIAL}),
    TrialState.INTER_TRIAL: frozenset({TrialState.COMPLETE}),
    TrialState.COMPLETE: frozenset(),
    TrialState.ABORTED: frozenset(),
    TrialState.INVALID: frozenset(),
}

SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.PREFLIGHT}),
    SessionState.PREFLIGHT: frozenset({SessionState.PRE_BASELINE}),
    SessionState.PRE_BASELINE: frozenset({SessionState.MAPPING}),
    SessionState.MAPPING: frozenset({SessionState.CALIBRATION}),
    SessionState.CALIBRATION: frozenset({SessionState.FROZEN_VALIDATION}),
    SessionState.FROZEN_VALIDATION: frozenset({SessionState.CONFIRMATORY_TEST}),
    SessionState.CONFIRMATORY_TEST: frozenset({SessionState.POST_BASELINE}),
    SessionState.POST_BASELINE: frozenset({SessionState.FINALIZING}),
    SessionState.FINALIZING: frozenset({SessionState.COMPLETE}),
    SessionState.COMPLETE: frozenset(),
    SessionState.ABORTED: frozenset(),
    SessionState.INVALID: frozenset(),
}


@dataclass
class StateMachine:
    state: TrialState | SessionState
    transitions: dict
    history: list[tuple[str, str, str | None]] = field(default_factory=list)

    def transition(
        self,
        target: TrialState | SessionState,
        *,
        reason: str | None = None,
    ) -> None:
        if self.state.value in {"complete", "aborted", "invalid"}:
            raise StateTransitionError(f"{self.state.value} is terminal")
        if target not in self.transitions[self.state]:
            raise StateTransitionError(
                f"invalid transition {self.state.value} -> {target.value}"
            )
        self.history.append((self.state.value, target.value, reason))
        self.state = target

    def abort(self, reason: str) -> None:
        self._terminal_transition("aborted", reason)

    def invalidate(self, reason: str) -> None:
        self._terminal_transition("invalid", reason)

    def _terminal_transition(self, value: str, reason: str) -> None:
        if not reason.strip():
            raise ValueError("terminal transitions require a reason")
        if self.state.value in {"complete", "aborted", "invalid"}:
            raise StateTransitionError(f"{self.state.value} is terminal")
        target = type(self.state)(value)
        self.history.append((self.state.value, target.value, reason))
        self.state = target


def new_trial_machine() -> StateMachine:
    return StateMachine(TrialState.SCHEDULED, TRIAL_TRANSITIONS)


def new_session_machine() -> StateMachine:
    return StateMachine(SessionState.CREATED, SESSION_TRANSITIONS)
