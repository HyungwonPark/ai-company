#!/usr/bin/env python3
"""Read-only evidence report for one UNKNOWN shared-call reservation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time


def report(ledger_path: Path, reservation_id: str) -> dict:
    db = sqlite3.connect(f"file:{ledger_path.resolve()}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT reservation_id,owner,group_id,state,created_at,started_at,"
            "process_identity,event_id,result,closed_at FROM reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ValueError("reservation not found")
        names = ("reservation_id", "owner", "group_id", "state", "created_at",
                 "started_at", "process_identity", "event_id", "result", "closed_at")
        reservation = dict(zip(names, row))
        account = db.execute(
            "SELECT provider,credential_ref,group_id,state,resume_at,reason,calls,"
            "runtime_seconds,cost_usd,cost_unknown FROM accounts WHERE group_id=?",
            (reservation["group_id"],),
        ).fetchall()
        settlement = db.execute(
            "SELECT event_id,reservation_id,result,at FROM settlement_events WHERE reservation_id=?",
            (reservation_id,),
        ).fetchall()
        return {
            "at": time.time(),
            "reservation": reservation,
            "account": account,
            "settlement_events": settlement,
            "evidence": {
                "process_identity_present": reservation["process_identity"] is not None,
                "termination_confirmed": reservation["closed_at"] is not None,
                "usage_or_cost_saved": bool(reservation["result"]),
                "settlement_confirmed": bool(settlement),
                "conclusion": "UNKNOWN" if not reservation["closed_at"] or not settlement else reservation["state"],
            },
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", type=Path)
    parser.add_argument("reservation_id")
    args = parser.parse_args()
    print(json.dumps(report(args.ledger, args.reservation_id), ensure_ascii=False,
                      sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
