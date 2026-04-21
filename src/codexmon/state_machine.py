"""Run state definitions and transition guards."""

from __future__ import annotations

from typing import Final


INITIAL_STATE: Final[str] = "queued"
TERMINAL_STATES: Final[frozenset[str]] = frozenset({"completed", "halted", "cancelled"})
EXTERNAL_OUTCOME_BY_STATE: Final[dict[str, str]] = {
    "completed": "PR opened",
    "halted": "blocked with explicit reason",
    "cancelled": "blocked with explicit reason",
    "awaiting_human": "needs human decision",
}

_ALLOWED_TRANSITIONS: Final[dict[str | None, frozenset[str]]] = {
    None: frozenset({INITIAL_STATE}),
    "queued": frozenset({"preflight"}),
    "preflight": frozenset({"workspace_allocated", "halted"}),
    "workspace_allocated": frozenset({"running"}),
    "running": frozenset({"pr_handoff", "analyzing_failure", "awaiting_human", "halted"}),
    "analyzing_failure": frozenset({"retry_pending", "awaiting_human", "halted"}),
    "retry_pending": frozenset({"running"}),
    "awaiting_human": frozenset({"retry_pending", "cancelled"}),
    "pr_handoff": frozenset({"completed", "halted"}),
    "completed": frozenset(),
    "halted": frozenset(),
    "cancelled": frozenset(),
}


class InvalidStateTransitionError(ValueError):
    """Raised when a requested transition violates the canonical state model."""


def validate_transition(current_state: str | None, next_state: str) -> None:
    """Validate a single transition against the canonical state machine."""

    if current_state in TERMINAL_STATES:
        raise InvalidStateTransitionError(
            f"terminal state '{current_state}' cannot transition to '{next_state}'"
        )

    if current_state is not None and next_state == "halted":
        return

    allowed_next_states = _ALLOWED_TRANSITIONS.get(current_state)
    if allowed_next_states is None or next_state not in allowed_next_states:
        raise InvalidStateTransitionError(
            f"invalid state transition: {current_state!r} -> {next_state!r}"
        )


def outcome_for_state(state: str) -> str:
    """Return the external outcome contract for a state when it exists."""

    return EXTERNAL_OUTCOME_BY_STATE.get(state, "")
