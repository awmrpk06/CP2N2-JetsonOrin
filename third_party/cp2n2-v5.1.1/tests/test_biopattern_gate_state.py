from __future__ import annotations

import pytest

from applications.biopattern_gate.state import (
    SessionState,
    StateTransitionError,
    TrialState,
    new_session_machine,
    new_trial_machine,
)


def test_trial_happy_path_commits_before_label_reveal() -> None:
    machine = new_trial_machine()
    path = [
        TrialState.PRE_STIMULUS,
        TrialState.STIMULATING,
        TrialState.ARTEFACT_BLANKING,
        TrialState.OBSERVING,
        TrialState.FEATURIZING,
        TrialState.DECISION_COMMITTED,
        TrialState.LABEL_REVEALED,
        TrialState.INTER_TRIAL,
        TrialState.COMPLETE,
    ]

    for target in path:
        machine.transition(target)

    assert machine.state == TrialState.COMPLETE
    committed_index = path.index(TrialState.DECISION_COMMITTED)
    revealed_index = path.index(TrialState.LABEL_REVEALED)
    assert committed_index < revealed_index


def test_label_cannot_be_revealed_before_decision() -> None:
    machine = new_trial_machine()

    with pytest.raises(StateTransitionError, match="invalid transition"):
        machine.transition(TrialState.LABEL_REVEALED)


def test_abort_is_terminal_and_requires_a_reason() -> None:
    machine = new_trial_machine()
    machine.transition(TrialState.PRE_STIMULUS)

    with pytest.raises(ValueError, match="reason"):
        machine.abort(" ")
    machine.abort("operator requested safe stop")
    assert machine.state == TrialState.ABORTED
    with pytest.raises(StateTransitionError, match="terminal"):
        machine.transition(TrialState.STIMULATING)


def test_session_happy_path() -> None:
    machine = new_session_machine()
    for target in [
        SessionState.PREFLIGHT,
        SessionState.PRE_BASELINE,
        SessionState.MAPPING,
        SessionState.CALIBRATION,
        SessionState.FROZEN_VALIDATION,
        SessionState.CONFIRMATORY_TEST,
        SessionState.POST_BASELINE,
        SessionState.FINALIZING,
        SessionState.COMPLETE,
    ]:
        machine.transition(target)

    assert machine.state == SessionState.COMPLETE
