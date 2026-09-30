"""Operator-approved, non-generic recovery path for UNKNOWN reviews.

This module creates a new, separately identifiable reservation only after an
operator approval is bound to an immutable review target.  It does not change
the old UNKNOWN row or make the account globally available.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReviewTarget:
    pr: int
    head: str
    base: str
    patch_sha256: str


@dataclass(frozen=True)
class BatchState:
    next_index: int
    outcomes: tuple[str, ...] = ()
    stopped_reason: str | None = None


def _target_dict(target: ReviewTarget) -> dict[str, Any]:
    if (not isinstance(target.pr, int) or isinstance(target.pr, bool) or target.pr < 1
            or not HEX40.fullmatch(target.head) or not HEX40.fullmatch(target.base)
            or not HEX64.fullmatch(target.patch_sha256)):
        raise ValueError("review target is not pinned")
    return {"pr": target.pr, "head": target.head, "base": target.base,
            "patch_sha256": target.patch_sha256}


def approval_digest(approval: Mapping[str, Any]) -> str:
    """Return a stable digest for the complete approval record."""
    return hashlib.sha256(json.dumps(dict(approval), sort_keys=True,
                                       ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_approval(approval: Mapping[str, Any], target: ReviewTarget,
                     *, unknown_reservation_id: str) -> str:
    expected = {"approval_id", "pr", "head", "base", "patch_sha256",
                "unknown_reservation_id", "reason", "approved_by", "approved_at"}
    if set(approval) != expected:
        raise ValueError("operator approval is incomplete")
    _target_dict(target)
    if (approval["pr"], approval["head"], approval["base"], approval["patch_sha256"]) != (
            target.pr, target.head, target.base, target.patch_sha256):
        raise ValueError("operator approval target differs")
    if approval["unknown_reservation_id"] != unknown_reservation_id:
        raise ValueError("operator approval is not bound to the UNKNOWN reservation")
    if (not isinstance(approval["approval_id"], str) or not HEX64.fullmatch(approval["approval_id"])
            or not isinstance(approval["reason"], str) or not approval["reason"].strip()
            or not isinstance(approval["approved_by"], str) or not approval["approved_by"].strip()
            or not isinstance(approval["approved_at"], str) or not approval["approved_at"].strip()):
        raise ValueError("operator approval identity or reason is invalid")
    return approval_digest(approval)


def manual_reservation_id(approval: Mapping[str, Any], target: ReviewTarget) -> str:
    """Derive an idempotent new reservation id; no force/override flag exists."""
    _target_dict(target)
    payload = {"approval": dict(approval), "target": _target_dict(target)}
    return "manual-unknown-" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def advance_batch(state: BatchState, outcome: str, *, total: int) -> BatchState:
    """Advance exactly one sequential target; UNKNOWN stops the batch."""
    if state.stopped_reason is not None:
        return state
    if state.next_index >= total:
        return state
    if outcome not in {"PASS", "REVISE", "UNKNOWN", "INCOMPLETE"}:
        raise ValueError("unknown review outcome")
    if outcome in {"UNKNOWN", "INCOMPLETE"}:
        return BatchState(state.next_index, state.outcomes + (outcome,), outcome.lower())
    return BatchState(state.next_index + 1, state.outcomes + (outcome,))
