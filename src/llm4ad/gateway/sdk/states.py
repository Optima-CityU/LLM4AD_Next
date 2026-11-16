"""Explicit local execution transitions, independent of transport and persistence."""

from types import SimpleNamespace

from transitions import Machine, MachineError

STATES = ("accepted", "running", "retrying", "cancelling", "completed", "failed", "cancelled")
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_LEGACY = {"interrupted": "retrying", "recovery_required": "retrying"}
_TRANSITIONS = [
    {"trigger": "launch", "source": ["accepted", "retrying"], "dest": "running"},
    {"trigger": "retry", "source": ["accepted", "running", "retrying"], "dest": "retrying"},
    {"trigger": "cancel", "source": ["accepted", "running", "retrying"], "dest": "cancelling"},
    {"trigger": "succeed", "source": ["running", "retrying"], "dest": "completed"},
    {"trigger": "fail", "source": ["accepted", "running", "retrying", "cancelling"], "dest": "failed"},
    {"trigger": "finish_cancel", "source": "cancelling", "dest": "cancelled"},
]


def normalize_state(state: str) -> str:
    """Normalize a persisted local state without changing any stored record.

    Args:
        state: Current state, including supported legacy recovery states.

    Returns:
        The canonical execution state.

    Raises:
        ValueError: The state is not part of the local execution lifecycle.
    """
    normalized = _LEGACY.get(state, state)
    if normalized not in STATES:
        raise ValueError(f"Unknown execution state: {state!r}")
    return normalized


def transition(state: str, event: str) -> str:
    """Validate one explicit event; callers own durable transactional persistence.

    Args:
        state: Current persisted execution state.
        event: One of launch, retry, cancel, succeed, fail or finish_cancel.

    Returns:
        The canonical destination state.

    Raises:
        ValueError: The event or transition is invalid, including terminal events.
    """
    current = normalize_state(state)
    if event not in {item["trigger"] for item in _TRANSITIONS} or current in TERMINAL_STATES:
        raise ValueError(f"Invalid execution transition: {state!r} / {event!r}")
    model = SimpleNamespace()
    Machine(model=model, states=list(STATES), transitions=_TRANSITIONS, initial=current, auto_transitions=False)
    try:
        model.trigger(event)
    except MachineError as error:
        raise ValueError(f"Invalid execution transition: {state!r} / {event!r}") from error
    return model.state
