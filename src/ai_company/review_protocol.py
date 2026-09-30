"""Fail-closed evidence rules for a Claude review attempt.

The functions in this module only derive decisions.  They never settle a
reservation, estimate a charge, or change an account.  A caller must persist
the returned evidence and use the shared ledger's explicit transition methods.
"""

from __future__ import annotations

from typing import Any, Mapping


REQUIRED_TIMEOUT_FIELDS = frozenset({
    "event", "reason", "main_process_state", "child_tasks_terminated",
    "workflow_completed", "result_saved", "usage_saved", "reservation_state",
    "account_protection_state", "settlement_allowed", "cost_estimate_allowed",
})


def workflow_completion(
    *,
    main_process_terminated: bool,
    child_tasks_terminated: bool,
    workflow_completed: bool,
    result_saved: bool,
    usage_saved: bool,
    reservation_settled: bool,
    account_protection_confirmed: bool,
    timed_out: bool = False,
) -> dict[str, Any]:
    """Return a decision that is safe to use for a PASS/normal completion.

    A main process result alone is deliberately insufficient.  In particular,
    ``workflow_completed`` is independent from the process exit and must be
    proven by the provider's completion event.
    """

    checks = {
        "main_process_terminated": main_process_terminated is True,
        "child_tasks_terminated": child_tasks_terminated is True,
        "workflow_completed": workflow_completed is True,
        "result_saved": result_saved is True,
        "usage_saved": usage_saved is True,
        "reservation_settled": reservation_settled is True,
        "account_protection_confirmed": account_protection_confirmed is True,
        "timed_out": timed_out is True,
    }
    ready = all(checks[name] for name in checks
                if name not in {"timed_out", "reservation_settled"}) and not checks["timed_out"]
    complete = ready and checks["reservation_settled"]
    reason = None if complete else (
        "timed_out" if checks["timed_out"] else next(
            (name for name in (
                "main_process_terminated", "child_tasks_terminated",
                "workflow_completed", "result_saved", "usage_saved",
                "reservation_settled", "account_protection_confirmed",
            ) if not checks[name]),
            "evidence_incomplete",
        )
    )
    return {"complete": complete, "ready_for_settlement": ready,
            "reason_code": reason, "checks": checks}


def timeout_evidence(
    *,
    main_process_state: str,
    child_tasks_terminated: bool,
    workflow_completed: bool,
    result_saved: bool,
    usage_saved: bool,
    reservation_state: str,
    account_protection_state: str,
) -> dict[str, Any]:
    """Build the atomic, non-settling timeout fact stored with UNKNOWN."""

    record = {
        "event": "review_timeout",
        "reason": "timeout",
        "main_process_state": main_process_state,
        "child_tasks_terminated": child_tasks_terminated is True,
        "workflow_completed": workflow_completed is True,
        "result_saved": result_saved is True,
        "usage_saved": usage_saved is True,
        "reservation_state": reservation_state,
        "account_protection_state": account_protection_state,
        "settlement_allowed": False,
        "cost_estimate_allowed": False,
    }
    validate_timeout_evidence(record)
    return record


def validate_timeout_evidence(record: Mapping[str, Any]) -> None:
    if set(record) != REQUIRED_TIMEOUT_FIELDS:
        raise ValueError("timeout evidence fields are incomplete")
    if record["event"] != "review_timeout" or record["reason"] != "timeout":
        raise ValueError("timeout evidence identity is invalid")
    if not isinstance(record["main_process_state"], str) or not record["main_process_state"]:
        raise ValueError("timeout process state is missing")
    for key in ("child_tasks_terminated", "workflow_completed", "result_saved", "usage_saved"):
        if not isinstance(record[key], bool):
            raise ValueError("timeout evidence flags must be boolean")
    for key in ("reservation_state", "account_protection_state"):
        if not isinstance(record[key], str) or not record[key]:
            raise ValueError("timeout protection state is missing")
    if record["settlement_allowed"] is not False or record["cost_estimate_allowed"] is not False:
        raise ValueError("timeout evidence cannot authorize settlement or cost estimation")


def settled_review(*, reservation_state: str, settlement_event: str | None,
                   result_saved: bool, usage_saved: bool) -> bool:
    """Verify the post-settlement half of the completion protocol."""

    return (reservation_state == "SETTLED" and isinstance(settlement_event, str)
            and bool(settlement_event) and result_saved is True and usage_saved is True)
