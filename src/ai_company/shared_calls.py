"""Explicit, cross-queue account and host reservations for model processes.

This is an opt-in non-operating control.  A caller must supply the same database
and account registry as every other caller of a credential.  An absent account,
unreadable database, or uncertain process fails closed; lease age never frees a
slot.  The database contains no passwords or provider tokens.
"""

import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


class SharedCallError(RuntimeError):
    pass


class CapacityUnavailable(SharedCallError):
    def __init__(self, reason, resume_at=None):
        self.reason, self.resume_at = reason, resume_at
        super().__init__(reason)


class SharedCallLedger:
    def __init__(self, path: Path, *, clock=time.time, host_slots=2):
        if host_slots != 2:
            raise SharedCallError("host capacity is fixed at two until a reviewed migration")
        self.path, self.clock = Path(path).resolve(), clock
        if not self.path.is_file():
            raise SharedCallError("shared call ledger is absent; explicit initialization is required")
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SharedCallError("shared call ledger integrity check failed")
            version = self.db.execute("SELECT value FROM shared_meta WHERE key='schema_version'").fetchone()
            if version != ("2",):
                raise SharedCallError("shared call ledger schema is unknown")
        except sqlite3.Error as exc:
            self.db.close()
            raise SharedCallError("shared call ledger cannot be read") from exc

    @classmethod
    def initialize(cls, path: Path, accounts, *, clock=time.time, legacy_review_imports=()):
        """Create only from an operator-reviewed, complete account inventory.

        `accounts` entries are (provider, credential_ref, group_id, state,
        resume_at, reason, historical_calls, historical_seconds, historical_usd,
        cost_unknown). Existing active work must be reconciled before this
        is used: this function refuses to overwrite an existing file.
        """
        path = Path(path).resolve()
        if path.exists() or not accounts:
            raise SharedCallError("initialization requires a new path and nonempty inventory")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE shared_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO shared_meta VALUES ('schema_version','2');
                CREATE TABLE accounts(
                    provider TEXT NOT NULL, credential_ref TEXT NOT NULL,
                    group_id TEXT NOT NULL, state TEXT NOT NULL,
                    resume_at REAL, reason TEXT, calls INTEGER NOT NULL DEFAULT 0,
                    runtime_seconds REAL NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0, cost_unknown INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(provider,credential_ref));
                CREATE TABLE reservations(
                    reservation_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    state TEXT NOT NULL, created_at REAL NOT NULL,
                    started_at REAL, process_identity TEXT, event_id TEXT,
                    result TEXT, closed_at REAL);
                CREATE INDEX active_reservations ON reservations(state,group_id);
                CREATE TABLE settlement_events(
                    event_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL UNIQUE,
                    result TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE legacy_review_imports(
                    receipt_sha256 TEXT PRIMARY KEY, group_id TEXT NOT NULL,
                    reset_at REAL NOT NULL);
            """)
            groups = {}
            identities = set()
            connection.execute("BEGIN IMMEDIATE")
            for item in accounts:
                if len(item) != 10:
                    raise SharedCallError("incomplete account inventory")
                provider, credential, group, state, resume, reason, calls, seconds, cost, unknown = item
                if (not all(isinstance(value, str) and value for value in (provider, credential, group))
                        or state not in ("AVAILABLE", "COOLDOWN", "DISABLED", "UNKNOWN")
                        or (state == "COOLDOWN" and (isinstance(resume, bool)
                            or not isinstance(resume, (int, float)) or not math.isfinite(resume) or resume <= 0))
                        or not isinstance(calls, int) or isinstance(calls, bool) or calls < 0
                        or any(isinstance(value, bool) or not isinstance(value, (int, float))
                               or not math.isfinite(value) or value < 0 for value in (seconds, cost))
                        or unknown not in (0, 1)
                        or group == '@host-only'
                        or group in groups and groups[group] != (state, resume, reason, calls, seconds, cost, unknown)
                        or (provider, credential) in identities):
                    raise SharedCallError("ambiguous or invalid account inventory")
                groups[group] = (state, resume, reason, calls, seconds, cost, unknown)
                identities.add((provider, credential))
                connection.execute("""INSERT INTO accounts(provider,credential_ref,group_id,state,resume_at,reason,
                    calls,runtime_seconds,cost_usd,cost_unknown) VALUES (?,?,?,?,?,?,?,?,?,?)""", item)
            for receipt_sha, group, reset in legacy_review_imports:
                if (not isinstance(receipt_sha, str) or len(receipt_sha) != 64
                        or any(char not in '0123456789abcdef' for char in receipt_sha)
                        or group not in groups or isinstance(reset, bool)
                        or not isinstance(reset, (int, float)) or not math.isfinite(reset) or reset <= 0):
                    raise SharedCallError('legacy review import is incomplete or unregistered')
                connection.execute('INSERT INTO legacy_review_imports VALUES (?,?,?)',
                                   (receipt_sha, group, reset))
            connection.commit()
            path.chmod(0o600)
        except BaseException:
            connection.close()
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            raise
        connection.close()
        return cls(path, clock=clock)

    def close(self):
        self.db.close()

    def account(self, provider, credential_ref, group_id):
        try:
            row = self.db.execute("SELECT group_id,state,resume_at,reason,calls,runtime_seconds,cost_usd,cost_unknown "
                                  "FROM accounts WHERE provider=? AND credential_ref=?", (provider, credential_ref)).fetchone()
        except sqlite3.Error as exc:
            raise SharedCallError("shared account cannot be read") from exc
        if not row or row[0] != group_id:
            raise SharedCallError("credential is absent or mapped to a different shared group")
        aliases = self.db.execute("SELECT state,resume_at,reason,calls,runtime_seconds,cost_usd,cost_unknown "
                                  "FROM accounts WHERE group_id=?", (group_id,)).fetchall()
        if any(alias != row[1:] for alias in aliases):
            raise SharedCallError("shared account aliases disagree; reconciliation is required")
        return dict(zip(("group_id", "state", "resume_at", "reason", "calls", "runtime_seconds", "cost_usd", "cost_unknown"), row))

    def _transaction(self, operation):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            value = operation()
            self.db.commit()
            return value
        except BaseException:
            self.db.rollback()
            raise

    def reservation(self, reservation_id):
        row = self.db.execute("SELECT owner,group_id,state,process_identity,event_id,result FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        return dict(zip(("owner", "group_id", "state", "process_identity", "event_id", "result"), row)) if row else None

    def legacy_review_imported(self, receipt_sha256, group_id, reset_at):
        row = self.db.execute('SELECT group_id,reset_at FROM legacy_review_imports WHERE receipt_sha256=?',
                              (receipt_sha256,)).fetchone()
        return bool(row and row == (group_id, reset_at))

    def _reopen_cancelled(self, reservation_id):
        """Archive the proven-unstarted cancellation before reusing its stable ID."""
        archived_id = reservation_id + ':cancel:' + uuid4().hex
        self.db.execute("""INSERT INTO reservations
            (reservation_id,owner,group_id,state,created_at,started_at,process_identity,event_id,result,closed_at)
            SELECT ?,owner,group_id,state,created_at,started_at,process_identity,event_id,?,closed_at
            FROM reservations WHERE reservation_id=? AND state='CANCELLED'""",
            (archived_id, json.dumps({'original_reservation_id': reservation_id,
                                      'evidence': 'queue_unclaimed_no_guard_no_process'}), reservation_id))
        self.db.execute("""UPDATE reservations SET state='RESERVED',created_at=?,closed_at=NULL
            WHERE reservation_id=? AND state='CANCELLED'""", (self.clock(), reservation_id))
        return self.reservation(reservation_id)

    def reserve(self, reservation_id, owner, provider, credential_ref, group_id):
        """Atomically reserve one account and one of two host slots.

        Retrying the same immutable ID is idempotent; it cannot change owner or
        account.  Another queue cannot book the same account or a third slot.
        """
        if not all(isinstance(x, str) and x for x in (reservation_id, owner, provider, credential_ref, group_id)):
            raise SharedCallError("reservation identity is incomplete")

        def apply():
            existing = self.reservation(reservation_id)
            if existing:
                if existing["owner"] != owner or existing["group_id"] != group_id:
                    raise SharedCallError("reservation identity was reused")
                if existing['state'] != 'CANCELLED':
                    return existing
            account = self.account(provider, credential_ref, group_id)
            if account["state"] in ("UNKNOWN", "DISABLED"):
                raise CapacityUnavailable("shared account requires reconciliation")
            if account["state"] == "COOLDOWN" and (account["resume_at"] is None or account["resume_at"] > self.clock()):
                raise CapacityUnavailable("shared account is cooling down", account["resume_at"])
            if self.db.execute("SELECT 1 FROM reservations WHERE group_id=? AND state IN ('RESERVED','STARTED','UNKNOWN')", (group_id,)).fetchone():
                raise CapacityUnavailable("shared account is reserved")
            occupied = self.db.execute("SELECT count(*) FROM reservations WHERE state IN ('RESERVED','STARTED','UNKNOWN')").fetchone()[0]
            if occupied >= 2:
                raise CapacityUnavailable("all host slots are reserved")
            if existing:
                return self._reopen_cancelled(reservation_id)
            self.db.execute("INSERT INTO reservations(reservation_id,owner,group_id,state,created_at) VALUES (?,?,?,?,?)",
                            (reservation_id, owner, group_id, "RESERVED", self.clock()))
            return self.reservation(reservation_id)

        try:
            return self._transaction(apply)
        except sqlite3.Error as exc:
            raise SharedCallError("shared reservation failed closed") from exc

    def reserve_host(self, reservation_id, owner):
        """Reserve one global slot for a local check without an account call."""
        def apply():
            existing = self.reservation(reservation_id)
            if existing:
                if existing['owner'] != owner or existing['group_id'] != '@host-only':
                    raise SharedCallError('host reservation identity was reused')
                if existing['state'] != 'CANCELLED':
                    return existing
            occupied = self.db.execute("SELECT count(*) FROM reservations WHERE state IN ('RESERVED','STARTED','UNKNOWN')").fetchone()[0]
            if occupied >= 2:
                raise CapacityUnavailable('all host slots are reserved')
            if existing:
                return self._reopen_cancelled(reservation_id)
            self.db.execute("INSERT INTO reservations(reservation_id,owner,group_id,state,created_at) VALUES (?,?,?,?,?)",
                            (reservation_id, owner, '@host-only', 'RESERVED', self.clock()))
            return self.reservation(reservation_id)
        return self._transaction(apply)

    def started(self, reservation_id, owner, process_identity):
        if not process_identity:
            raise SharedCallError("process identity is required before start")

        def apply():
            row = self.reservation(reservation_id)
            if not row or row["owner"] != owner or row["state"] not in ("RESERVED", "STARTED"):
                raise SharedCallError("start has no matching live reservation")
            identity = json.dumps(process_identity, sort_keys=True)
            if (row["state"] == "STARTED" and row["process_identity"] != identity
                    and json.loads(row["process_identity"]).get("kind") != "executor_invocation"):
                raise SharedCallError("process identity changed after start")
            self.db.execute("UPDATE reservations SET state='STARTED',started_at=COALESCE(started_at,?),process_identity=? WHERE reservation_id=?",
                            (self.clock(), identity, reservation_id))
        self._transaction(apply)

    def uncertain(self, reservation_id, owner, reason):
        def apply():
            row = self.reservation(reservation_id)
            if not row or row["owner"] != owner or row["state"] == "SETTLED":
                raise SharedCallError("unknown reservation cannot be reconciled")
            self.db.execute("UPDATE reservations SET state='UNKNOWN' WHERE reservation_id=?", (reservation_id,))
            if row['group_id'] != '@host-only':
                self.db.execute("UPDATE accounts SET state='UNKNOWN',reason=? WHERE group_id=?",
                                (reason, row["group_id"]))
        self._transaction(apply)

    def settle(self, reservation_id, owner, event_id, result, *, terminated):
        """Commit confirmed termination, shared usage and cooldown exactly once."""
        if not terminated or not isinstance(result, dict) or not event_id:
            raise SharedCallError("settlement requires a terminal result and stable event ID")
        category = result.get("category")
        if category not in ("success", "quota", "rate_limit", "transient_network", "authentication", "blocked",
                            "reconciliation", "context_exhausted", "permission", "approval_required", "test_failure",
                            "code_error", "unknown"):
            raise SharedCallError("result category is not recognized")
        duration = result.get("duration_seconds", 0)
        cost = result.get("total_cost_usd")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0
                or cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0)):
            raise SharedCallError("usage is invalid")
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)

        def apply():
            row = self.reservation(reservation_id)
            if not row or row["owner"] != owner:
                raise SharedCallError("settlement ownership mismatch")
            if row["state"] == "SETTLED":
                if row["event_id"] != event_id or row["result"] != encoded:
                    raise SharedCallError("settlement differs from committed fact")
                return False
            if self.db.execute("SELECT 1 FROM settlement_events WHERE event_id=?", (event_id,)).fetchone():
                raise SharedCallError("settlement event ID belongs to another reservation")
            if row["state"] not in ("STARTED", "UNKNOWN"):
                raise SharedCallError("unstarted reservation needs cancellation proof, not usage settlement")
            self.db.execute("INSERT INTO settlement_events VALUES (?,?,?,?)", (event_id, reservation_id, encoded, self.clock()))
            self.db.execute("UPDATE reservations SET state='SETTLED',event_id=?,result=?,closed_at=? WHERE reservation_id=?",
                            (event_id, encoded, self.clock(), reservation_id))
            state, resume, reason = "AVAILABLE", None, None
            if category in ("quota", "rate_limit"):
                state, resume, reason = "COOLDOWN", result.get("reset_at"), category
                if (isinstance(resume, bool) or not isinstance(resume, (int, float))
                        or not math.isfinite(resume) or resume <= 0):
                    raise SharedCallError("quota settlement needs a verified reset time")
                resume = max(self.clock(), resume)
            elif category == "authentication":
                state, reason = "DISABLED", category
            elif category == "reconciliation":
                state, reason = "UNKNOWN", category
            if row['group_id'] != '@host-only':
                self.db.execute("""UPDATE accounts SET calls=calls+1,runtime_seconds=runtime_seconds+?,
                    cost_usd=cost_usd+?,cost_unknown=MAX(cost_unknown,?),
                    state=CASE WHEN state IN ('DISABLED','UNKNOWN') THEN state ELSE ? END,
                    resume_at=CASE WHEN ?='COOLDOWN' THEN MAX(COALESCE(resume_at,0),?) ELSE resume_at END,
                    reason=COALESCE(?,reason) WHERE group_id=?""",
                    (duration, cost or 0, int(cost is None), state, state, resume, reason, row["group_id"]))
            return True
        try:
            return self._transaction(apply)
        except sqlite3.Error as exc:
            raise SharedCallError("shared settlement failed closed") from exc

    def cancel_unstarted(self, reservation_id, owner, *, evidence):
        """Release only with a caller's durable proof that no attempt was claimed."""
        if evidence != "queue_unclaimed_no_guard_no_process":
            raise SharedCallError("unstarted cancellation lacks queue proof")

        def apply():
            row = self.reservation(reservation_id)
            if not row or row["owner"] != owner or row["state"] != "RESERVED":
                raise SharedCallError("reservation may have started")
            self.db.execute("UPDATE reservations SET state='CANCELLED',closed_at=? WHERE reservation_id=?",
                            (self.clock(), reservation_id))
        self._transaction(apply)
