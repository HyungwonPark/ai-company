"""Execution facts, immutable submission bindings, and a single-controller lock.

Workflow progress belongs exclusively to LangGraph checkpoints.
"""

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import time
from typing import Iterator

from ai_company.runtime import ExecutionBlocked, ReconciliationRequired


@contextmanager
def controller_lock(root: Path, *, blocking: bool = False):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ExecutionBlocked("another controller is using this state directory") from exc
        try:
            yield lock
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def suspended_lock(lock):
    """Release only a scheduling lock; callers retain task/repository ownership."""
    if lock is None:
        yield
        return
    fcntl.flock(lock, fcntl.LOCK_UN)
    try:
        yield
    finally:
        fcntl.flock(lock, fcntl.LOCK_EX)


class Ledger:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS submissions (
                task_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                task_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                request_json TEXT NOT NULL, state TEXT NOT NULL,
                result_json TEXT, reason TEXT,
                started_at REAL NOT NULL, ended_at REAL
            );
        """)

    def close(self) -> None:
        self.db.close()

    def bind(self, task_id: str, binding: str, task_json: str) -> float:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO submissions VALUES (?, ?, ?, ?)",
                            (task_id, binding, task_json, time.time()))
            saved = self.db.execute("SELECT binding, created_at FROM submissions WHERE task_id=?",
                                    (task_id,)).fetchone()
            if saved[0] != binding:
                raise ExecutionBlocked("task ID already belongs to a different spec, policy, or adapter configuration")
        return saved[1]

    def lookup(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT state, result_json, reason FROM runs WHERE run_id=?",
                              (run_id,)).fetchone()
        if row is None:
            return None
        if row[0] == "completed":
            return json.loads(row[1])
        if row[0] == "blocked":
            raise ExecutionBlocked(row[2])
        raise ReconciliationRequired(f"unfinished run requires reconciliation: {run_id}")

    def start(self, run_id: str, task_id: str, request_json: str, limit: int) -> None:
        with self.db:
            count = self.db.execute("SELECT COUNT(*) FROM runs WHERE task_id=?", (task_id,)).fetchone()[0]
            if count >= limit:
                raise ExecutionBlocked("execution count budget exhausted")
            self.db.execute("INSERT INTO runs VALUES (?, ?, ?, 'running', NULL, NULL, ?, NULL)",
                            (run_id, task_id, request_json, time.time()))

    def finish(self, run_id: str, result_json: str) -> None:
        with self.db:
            row = self.db.execute("SELECT state, result_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row == ("completed", result_json):
                return
            if row is None or row[0] != "running":
                raise ExecutionBlocked("result cannot replace an existing execution fact")
            self.db.execute("UPDATE runs SET state='completed', result_json=?, ended_at=? WHERE run_id=?",
                            (result_json, time.time(), run_id))

    def block(self, run_id: str, reason: str) -> None:
        with self.db:
            self.db.execute("UPDATE runs SET state='blocked', reason=?, ended_at=? WHERE run_id=? AND state='running'",
                            (reason, time.time(), run_id))
