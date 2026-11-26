"""Application pipeline state machine (PRD FR-043)."""

from __future__ import annotations

from typing import Final

STATUSES: Final = (
    "discovered",
    "shortlisted",
    "preparing",
    "awaiting_approval",
    "approved",
    "submitted",
    "assessment",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "failed",
)

TERMINAL: Final = frozenset({"offer", "rejected", "withdrawn"})
ACTIVE_PIPELINE: Final = (
    "discovered",
    "shortlisted",
    "preparing",
    "awaiting_approval",
    "approved",
    "submitted",
    "assessment",
    "interview",
    "offer",
)

_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "discovered": frozenset({"shortlisted", "preparing", "withdrawn"}),
    "shortlisted": frozenset({"preparing", "withdrawn"}),
    "preparing": frozenset({"awaiting_approval", "shortlisted", "withdrawn"}),
    "awaiting_approval": frozenset({"approved", "preparing", "withdrawn"}),
    # approved → submitted happens only through the submission service (requires acknowledgement
    # bookkeeping); "failed" records a failed submission attempt.
    "approved": frozenset({"submitted", "failed", "preparing", "withdrawn"}),
    "failed": frozenset({"approved", "preparing", "withdrawn"}),
    "submitted": frozenset({"assessment", "interview", "offer", "rejected", "withdrawn"}),
    "assessment": frozenset({"interview", "offer", "rejected", "withdrawn"}),
    "interview": frozenset({"offer", "rejected", "withdrawn", "assessment"}),
    "offer": frozenset(),
    "rejected": frozenset(),
    "withdrawn": frozenset(),
}

# Transitions that must never be performed by a plain "move" request — they have dedicated,
# guarded service operations.
GUARDED: Final = frozenset({("awaiting_approval", "approved"), ("approved", "submitted"), ("failed", "approved")})


class InvalidTransition(ValueError):
    pass


def allowed_next(status: str) -> frozenset[str]:
    return _TRANSITIONS.get(status, frozenset())


def check_transition(current: str, target: str, *, guarded_ok: bool = False) -> None:
    if target not in STATUSES:
        raise InvalidTransition(f"Unknown status '{target}'.")
    if target not in allowed_next(current):
        raise InvalidTransition(f"Cannot move an application from '{current}' to '{target}'.")
    if (current, target) in GUARDED and not guarded_ok:
        raise InvalidTransition(f"'{current}' → '{target}' requires the dedicated approval or submission action.")
