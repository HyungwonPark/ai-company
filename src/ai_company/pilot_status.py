"""Summarize role statuses without modifying their mapping."""

from collections.abc import Mapping


def summarize_role_states(states: Mapping[str, str]) -> dict[str, int]:
    """Count each role in exactly one status category."""
    counts = {
        "total": len(states),
        "completed": 0,
        "waiting": 0,
        "blocked": 0,
        "active": 0,
    }
    for status in states.values():
        if status in {"CONTRIBUTION_READY", "MERGE_READY", "DEMO_READY"}:
            counts["completed"] += 1
        elif status.startswith("WAITING_"):
            counts["waiting"] += 1
        elif status in {"BLOCKED", "NO_ELIGIBLE_AGENT", "NEEDS_RECONCILIATION"}:
            counts["blocked"] += 1
        else:
            counts["active"] += 1
    return counts
