"""Fail-closed policy for UNKNOWN executions whose termination is uncertain.

This module deliberately does not settle a reservation, estimate cost, or
reuse the quota reconciliation path. It classifies an observed state so a
caller can record why a new call remains blocked without changing historical
ledger rows.
"""

from dataclasses import dataclass
from typing import Mapping


UNKNOWN_TIMEOUT_UNRESOLVED = "UNKNOWN_TIMEOUT_UNRESOLVED"
UNKNOWN_RESERVATION_UNRESOLVED = "UNKNOWN_RESERVATION_UNRESOLVED"
UNKNOWN_ACCOUNT_UNRESOLVED = "UNKNOWN_ACCOUNT_UNRESOLVED"
ABANDONED_UNKNOWN = "ABANDONED_UNKNOWN"


@dataclass(frozen=True)
class UnknownRecoveryDecision:
    """A derived, non-persistent decision for an unresolved UNKNOWN state."""

    state: str
    reason_code: str
    allows_new_calls: bool = False
    requires_terminal_evidence: bool = True


def classify_unknown_recovery(
    account: Mapping[str, object],
    reservation: Mapping[str, object] | None = None,
    *,
    operator_abandoned: bool = False,
) -> UnknownRecoveryDecision | None:
    """Classify an unresolved account/reservation without changing either.

    ``operator_abandoned`` is an explicit future operator decision, never an
    inference from elapsed time. All derived UNKNOWN states remain blocked;
    separate terminal proof and settlement review are required before a call
    can be admitted.
    """

    if operator_abandoned:
        return UnknownRecoveryDecision(ABANDONED_UNKNOWN, "operator_abandoned_unknown")

    if account.get("state") == "UNKNOWN":
        if account.get("reason") == "timeout":
            return UnknownRecoveryDecision(UNKNOWN_TIMEOUT_UNRESOLVED, "account_unknown_timeout")
        return UnknownRecoveryDecision(UNKNOWN_ACCOUNT_UNRESOLVED, "account_unknown")

    if reservation and reservation.get("state") == "UNKNOWN":
        return UnknownRecoveryDecision(UNKNOWN_RESERVATION_UNRESOLVED, "reservation_unknown")

    return None
