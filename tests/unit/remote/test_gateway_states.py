"""Explicit lifecycle decisions and finite read retries need no worker process."""

import pytest

from llm4ad.gateway.sdk.errors import EvaluationUnavailableError
from llm4ad.gateway.sdk.retries import is_retryable, retry_read
from llm4ad.gateway.sdk.states import STATES, TERMINAL_STATES, normalize_state, transition

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "state,event,expected",
    [
        ("accepted", "launch", "running"),
        ("running", "retry", "retrying"),
        ("retrying", "launch", "running"),
        ("running", "succeed", "completed"),
        ("accepted", "fail", "failed"),
        ("running", "cancel", "cancelling"),
        ("cancelling", "finish_cancel", "cancelled"),
        ("recovery_required", "launch", "running"),
        ("interrupted", "retry", "retrying"),
    ],
)
def test_explicit_execution_events(state, event, expected):
    """Only declared events produce canonical destination states."""
    assert transition(state, event) == expected
    assert expected in STATES


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
@pytest.mark.parametrize("event", ["launch", "retry", "cancel", "succeed", "fail", "finish_cancel"])
def test_terminal_states_reject_every_event(state, event):
    """Completed work cannot restart through an accidental generic transition."""
    with pytest.raises(ValueError):
        transition(state, event)


def test_unknown_events_and_implicit_transitions_are_rejected():
    """Legacy aliases do not enable the FSM's implicit to-state methods."""
    assert normalize_state("recovery_required") == "retrying"
    for state, event in [("accepted", "succeed"), ("running", "to_failed"), ("missing", "launch")]:
        with pytest.raises(ValueError):
            transition(state, event)


@pytest.mark.parametrize("error", [OSError("read"), TimeoutError("read"), EvaluationUnavailableError("read")])
def test_transient_reads_are_bounded_to_three_total_calls(error):
    """A persistent transient failure preserves its original exception and budget."""
    calls = []

    def fail():
        calls.append(None)
        raise error

    assert is_retryable(error)
    with pytest.raises(type(error)) as raised:
        retry_read(fail)
    assert raised.value is error
    assert len(calls) == 3


def test_permanent_read_error_is_not_retried():
    """Invalid input cannot consume the transient retry policy."""
    calls = []

    def fail():
        calls.append(None)
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        retry_read(fail)
    assert len(calls) == 1
    assert retry_read(lambda left, right: left + right, 2, right=3) == 5
