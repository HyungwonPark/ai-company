"""Durable, one-shot CLI session scheduling; no model call while waiting for quota.

The queue completes an agent session, not a development task or a merge gate.
An expired lease never authorizes replay of an uncertain external execution.
"""

from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from typing import Callable, Iterator, Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import Field, field_validator

from ai_company.contracts import Contract, Key, Task, Text, digest
from ai_company.runtime import ExecutionBlocked
from ai_company.storage import controller_lock, suspended_lock


class RetryPolicy(Contract):
    reset_grace_seconds: int = Field(default=30, ge=1, le=300)
    backoff_initial_seconds: int = Field(default=60, ge=1, le=3600)
    backoff_max_seconds: int = Field(default=3600, ge=1, le=86400)
    retries_per_cycle: int = Field(default=3, ge=1, le=100)
    cycle_cooldown_seconds: int = Field(default=3600, ge=1, le=86400)
    execution_timeout_seconds: int = Field(default=300, ge=1, le=3600)

    def next_time(self, now: float, retry_count: int, cycle_retries: int,
                  reset_at: float | None) -> tuple[float, int]:
        if reset_at is not None and math.isfinite(reset_at) and reset_at > 0:
            scheduled = max(now, reset_at) + self.reset_grace_seconds
        else:
            delay = min(self.backoff_max_seconds,
                        self.backoff_initial_seconds * (2 ** min(retry_count, 20)))
            scheduled = now + delay
        if cycle_retries >= self.retries_per_cycle:
            scheduled = max(scheduled, now + self.cycle_cooldown_seconds)
            cycle_retries = 0
        return scheduled, cycle_retries


class SessionSpec(Contract):
    task: Task
    agent_id: Key
    provider: Literal["codex", "claude"]
    worktree: Text
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    last_completed_stage: Key = "submitted"
    checkpoint: dict = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)

    @field_validator("session_id")
    @classmethod
    def valid_session(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value):
            raise ValueError("session ID must be an explicit identifier, not an option")
        return value


def _git(worktree: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(worktree), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecutionBlocked("cannot inspect the session worktree") from exc


def repository_snapshot(worktree: Path) -> dict:
    """Fingerprint HEAD, branch, index, tracked edits and non-ignored untracked data."""
    root = worktree.resolve(strict=True)
    top = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != root:
        raise ExecutionBlocked("worktree must be the repository top-level directory")
    common = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir")).strip())
    common = (root / common).resolve() if not common.is_absolute() else common.resolve()
    remote = os.fsdecode(_git(root, "remote", "get-url", "origin")).strip()
    if remote.startswith("git@github.com:"):
        repository = remote[len("git@github.com:"):]
    else:
        parsed = urlparse(remote)
        if parsed.hostname != "github.com":
            raise ExecutionBlocked("worktree origin must identify the task's GitHub repository")
        repository = parsed.path.lstrip("/")
    repository = repository.removesuffix(".git")
    fingerprint = sha256()
    for args in (("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                 ("diff", "--no-ext-diff", "--no-textconv", "--binary"),
                 ("diff", "--cached", "--no-ext-diff", "--no-textconv", "--binary")):
        content = _git(root, *args)
        fingerprint.update(len(content).to_bytes(8, "big"))
        fingerprint.update(content)
    for name in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not name:
            continue
        path = root / os.fsdecode(name)
        fingerprint.update(name + b"\0")
        if path.is_symlink():
            fingerprint.update(b"symlink\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            fingerprint.update(str(path.stat().st_mode).encode() + b"\0")
            with path.open("rb") as data:
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    fingerprint.update(chunk)
        else:
            raise ExecutionBlocked("unsupported untracked entry in session worktree")
    return {"worktree": str(root), "git_common_dir": str(common), "repository": repository,
            "head_commit": _git(root, "rev-parse", "HEAD").decode().strip(),
            "head_ref": _git(root, "rev-parse", "--symbolic-full-name", "HEAD").decode().strip(),
            "dirty_digest": fingerprint.hexdigest()}


@contextmanager
def repository_lock(snapshot: dict) -> Iterator[None]:
    # Shared by different state directories and worktrees of the same repository.
    path = Path(snapshot["git_common_dir"]) / "ai-company-session.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ExecutionBlocked("another session owns this repository") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def execution_alive(identity: dict | None) -> bool:
    if not identity:
        return False
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if identity.get("boot_id") != boot:
            return False
        if identity.get("systemd_unit"):
            from ai_company.adapters.session_cli import service_alive
            if service_alive(identity["systemd_unit"]):
                return True
        pgid = int(identity["pgid"])
        if pgid <= 1:
            return True
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (KeyError, OSError, ValueError):
        # An unknown process identity cannot authorize another execution.
        return True


class SessionQueue:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time):
        self.root, self.clock = root.resolve(), clock
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / "sessions.sqlite", timeout=5)
        os.chmod(self.root / "sessions.sqlite", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS session_jobs (
                job_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                state TEXT NOT NULL, resume_at REAL,
                lease_owner TEXT, lease_until REAL, document TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS session_due ON session_jobs(state, resume_at);
            CREATE TABLE IF NOT EXISTS session_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL, occurred_at REAL NOT NULL, document TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def get(self, job_id: str) -> dict:
        row = self.db.execute("SELECT document FROM session_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise ExecutionBlocked("unknown session job")
        return json.loads(row[0])

    def list_jobs(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT document FROM session_jobs ORDER BY job_id")]

    def _save(self, job: dict, *, owner: str | None = None) -> None:
        job["updated_at"] = self.clock()
        sql = """UPDATE session_jobs SET state=?, resume_at=?, lease_owner=?,
                 lease_until=?, document=? WHERE job_id=?"""
        values = [job["status"], job["resume_at"], job["lease_owner"], job["lease_until"],
                  json.dumps(job, ensure_ascii=False), job["job_id"]]
        if owner is not None:
            sql += " AND lease_owner=? AND state='RUNNING'"
            values.append(owner)
        if self.db.execute(sql, values).rowcount != 1:
            raise ExecutionBlocked("session lease lost; result cannot replace an execution fact")
        self.db.execute("INSERT INTO session_events(job_id, occurred_at, document) VALUES (?, ?, ?)",
                        (job["job_id"], self.clock(), json.dumps(job, ensure_ascii=False)))

    def submit(self, spec: SessionSpec, *, execution_key: str | None = None,
               managed_by: str | None = None) -> dict:
        # Never trust model_copy/model_construct to bypass contract validation.
        spec = SessionSpec.model_validate(spec.model_dump(mode="json"))
        normalized = spec.model_dump(mode="json")
        normalized["worktree"] = str(Path(spec.worktree).resolve())
        binding = digest(normalized)
        identity = {"task_id": spec.task.task_id, "agent_id": spec.agent_id}
        if execution_key is not None:
            identity["execution_key"] = execution_key
        job_id = digest(identity)
        existing = self.db.execute("SELECT binding FROM session_jobs WHERE job_id=?", (job_id,)).fetchone()
        if existing:
            if existing[0] != binding:
                raise ExecutionBlocked("task/agent already belongs to a different session specification")
            return self.get(job_id)
        snapshot = repository_snapshot(Path(spec.worktree))
        if snapshot["repository"].lower() != spec.task.repository.lower():
            raise ExecutionBlocked("worktree repository differs from the task repository")
        with repository_lock(snapshot):
            if repository_snapshot(Path(spec.worktree)) != snapshot:
                raise ExecutionBlocked("repository changed while submitting the session")
            now = self.clock()
            job = dict(job_id=job_id, task_id=spec.task.task_id, agent_id=spec.agent_id,
                       provider=spec.provider, session_id=spec.session_id, worktree=snapshot["worktree"],
                       head_commit=snapshot["head_commit"], repository_snapshot=snapshot,
                       last_completed_stage=spec.last_completed_stage, checkpoint=spec.checkpoint,
                       specification=normalized, status="READY", reason="session queued",
                       retry_count=0, cycle_retries=0, attempt_count=0, resume_at=now,
                       reset_at=None, lease_owner=None, lease_until=None, process=None,
                       last_category=None, result=None, handoff_count=0, previous_sessions=[],
                       created_at=now, updated_at=now)
            if managed_by is not None:
                job["managed_by"] = managed_by
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO session_jobs VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                                (job_id, binding, "READY", now, json.dumps(job, ensure_ascii=False)))
                current = self.db.execute("SELECT binding FROM session_jobs WHERE job_id=?", (job_id,)).fetchone()
                if current[0] != binding:
                    raise ExecutionBlocked("concurrent submission changed the session specification")
            return self.get(job_id)

    def _recover_interrupted_workers(self) -> None:
        # A live worker retains the repository lock even before on_spawn. Never
        # infer a crash from process=None or an expired lease while it owns it.
        for candidate in self.list_jobs():
            try:
                with repository_lock(candidate["repository_snapshot"]):
                    job = self.get(candidate["job_id"])
                    if execution_alive(job["process"]):
                        continue
                    if job["status"] == "RUNNING":
                        job.update(status="NEEDS_RECONCILIATION",
                                   reason="worker interrupted; inspect execution facts before resuming",
                                   lease_owner=None, lease_until=None, resume_at=None)
                        with self.db:
                            self._save(job)
                    elif job["status"] != "NEEDS_RECONCILIATION":
                        self._clear_guard(job)
            except (ExecutionBlocked, OSError, ValueError):
                pass

    @staticmethod
    def _guard_path(job: dict) -> Path:
        return Path(job["repository_snapshot"]["git_common_dir"]) / "ai-company-session-active.json"

    def _read_guard(self, job: dict) -> dict | None:
        try:
            fd = os.open(self._guard_path(job), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as file:
            value = json.load(file)
        if (not isinstance(value, dict) or not isinstance(value.get("job_id"), str)
                or not isinstance(value.get("queue_root"), str)):
            raise ExecutionBlocked("invalid repository execution guard")
        return value

    def _write_guard(self, job: dict) -> None:
        path = self._guard_path(job)
        marker = {"job_id": job["job_id"], "queue_root": str(self.root),
                  "process": job["process"], "lease_owner": job["lease_owner"]}
        fd, name = tempfile.mkstemp(prefix=".ai-company-active-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(marker, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def _clear_guard(self, job: dict) -> None:
        marker = self._read_guard(job)
        if marker and (marker.get("job_id"), marker.get("queue_root")) == (job["job_id"], str(self.root)):
            self._guard_path(job).unlink()

    def _claim(self, job_id: str) -> dict | None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            job = self.get(job_id)
            if (job["status"] not in ("READY", "WAITING_QUOTA", "WAITING_RETRY")
                    or job["resume_at"] is None or job["resume_at"] > self.clock()):
                self.db.rollback()
                return None
            if job["status"] in ("WAITING_QUOTA", "WAITING_RETRY"):
                job["retry_count"] += 1
                job["cycle_retries"] += 1
            policy = RetryPolicy.model_validate(job["specification"]["retry_policy"])
            job.update(status="RUNNING", lease_owner=uuid4().hex,
                       lease_until=self.clock() + policy.execution_timeout_seconds + 60,
                       attempt_count=job["attempt_count"] + 1, resume_at=None)
            self._save(job)
            self.db.commit()
            return job
        except BaseException:
            self.db.rollback()
            raise

    def _finish(self, job: dict, status: str, reason: str, owner: str):
        job.update(status=status, reason=reason, lease_owner=None, lease_until=None)
        with self.db:
            self._save(job, owner=owner)
        if status != "NEEDS_RECONCILIATION":
            # A normal result records termination before relinquishing repository
            # ownership. A crashed/uncertain invocation retains its durable guard.
            self._clear_guard(job)
        return job

    @staticmethod
    def _prompt(job: dict) -> str:
        checkpoint = {"task": job["specification"]["task"], "last_completed_stage": job["last_completed_stage"],
                      "checkpoint": job["checkpoint"], "head_commit": job["head_commit"],
                      "worktree": job["worktree"], "previous_sessions": job["previous_sessions"]}
        return ("Continue the task from this persisted checkpoint. Inspect the existing work before changing it. "
                "Preserve the task's acceptance criteria and allowed paths. Do not assume tests or review passed.\n"
                + json.dumps(checkpoint, ensure_ascii=False))

    def _run_claimed(self, job: dict, executor) -> dict:
        owner = job["lease_owner"]
        policy = RetryPolicy.model_validate(job["specification"]["retry_policy"])
        if execution_alive(job["process"]):
            return self._finish(job, "NEEDS_RECONCILIATION", "previous execution may still be alive", owner)
        try:
            snapshot = repository_snapshot(Path(job["worktree"]))
            if snapshot != job["repository_snapshot"]:
                return self._finish(job, "NEEDS_RECONCILIATION", "repository changed since checkpoint", owner)
        except (ExecutionBlocked, OSError):
            return self._finish(job, "NEEDS_RECONCILIATION", "repository could not be verified", owner)

        def spawned(identity):
            job["process"] = identity
            with self.db:
                self._save(job, owner=owner)
            self._write_guard(job)

        output_dir = self.root / "session-logs" / job["job_id"]
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Persist ownership BEFORE spawning. Unlike flock, this survives the
        # controller dying while its CLI is still alive (also across queue roots).
        job["process"] = None
        self._write_guard(job)
        try:
            outcome = executor(job["provider"], Path(job["worktree"]), self._prompt(job), job["session_id"],
                               timeout_seconds=policy.execution_timeout_seconds, output_dir=output_dir,
                               on_spawn=spawned)
        except Exception:
            return self._finish(job, "NEEDS_RECONCILIATION", "session executor failed; effects are uncertain", owner)
        # BaseException (e.g. a worker crash) intentionally leaves a RUNNING fact.
        job["last_category"], job["result"] = outcome.category, outcome.result
        if job["session_id"] and outcome.session_id and job["session_id"] != outcome.session_id:
            return self._finish(job, "NEEDS_RECONCILIATION", "CLI returned another session ID", owner)
        job["session_id"] = outcome.session_id or job["session_id"]
        if execution_alive(job["process"]):
            return self._finish(job, "NEEDS_RECONCILIATION", "session process termination is unconfirmed", owner)
        try:
            snapshot = repository_snapshot(Path(job["worktree"]))
            if (snapshot["repository"], snapshot["git_common_dir"]) != (
                    job["repository_snapshot"]["repository"], job["repository_snapshot"]["git_common_dir"]):
                raise ExecutionBlocked("repository identity changed during execution")
            job.update(repository_snapshot=snapshot, head_commit=snapshot["head_commit"])
        except (ExecutionBlocked, OSError):
            return self._finish(job, "NEEDS_RECONCILIATION", "cannot checkpoint repository after execution", owner)
        category = outcome.category
        if category in ("quota", "rate_limit", "transient_network"):
            job["reset_at"] = (outcome.reset_at if outcome.reset_at is not None
                               and math.isfinite(outcome.reset_at) and outcome.reset_at > 0 else None)
            job["resume_at"], job["cycle_retries"] = policy.next_time(
                self.clock(), job["retry_count"], job["cycle_retries"], job["reset_at"])
            # Without a session ID, a fresh execution could duplicate side effects.
            if not job["session_id"]:
                job["resume_at"] = None
            status = "WAITING_RETRY" if category == "transient_network" else "WAITING_QUOTA"
            reason = category if job["session_id"] else category + "; session ID missing, reconciliation required"
            return self._finish(job, status, reason, owner)
        if category == "context_exhausted":
            handoff = {"task_id": job["task_id"], "agent_id": job["agent_id"],
                       "session_id": job["session_id"], "last_completed_stage": job["last_completed_stage"],
                       "repository_snapshot": snapshot, "checkpoint": job["checkpoint"],
                       "task": job["specification"]["task"], "result": outcome.result}
            destination = self.root / "handoffs"
            destination.mkdir(mode=0o700, exist_ok=True)
            path = destination / f"{job['job_id']}-{job['attempt_count']}.json"
            with path.open("x") as file:
                os.chmod(path, 0o600)
                json.dump(handoff, file, ensure_ascii=False, indent=2)
            job["handoff_file"] = str(path)
            return self._finish(job, "NEEDS_CONTEXT_HANDOFF", "context exhausted; checkpoint saved for a new session", owner)
        if category == "success":
            return self._finish(job, "SESSION_COMPLETED", "agent session completed; tests and review are separate gates", owner)
        status = "NEEDS_RECONCILIATION" if category == "reconciliation" else "BLOCKED"
        return self._finish(job, status, "non-retryable session outcome: " + category, owner)

    def run_once(self, *, executor=None, job_id: str | None = None) -> dict:
        if executor is None:
            from ai_company.adapters.session_cli import run_session
            executor = run_session
        try:
            with controller_lock(self.root / "session-worker") as worker_lock:
                self._recover_interrupted_workers()
                due = [job for job in self.list_jobs()
                       if job["status"] in ("READY", "WAITING_QUOTA", "WAITING_RETRY")
                       and (job["job_id"] == job_id if job_id else not job.get("managed_by"))
                       and job["resume_at"] is not None and job["resume_at"] <= self.clock()]
                for candidate in sorted(due, key=lambda item: (item["resume_at"], item["job_id"])):
                    try:
                        with repository_lock(candidate["repository_snapshot"]):
                            if self._read_guard(candidate) is not None:
                                continue
                            job = self._claim(candidate["job_id"])
                            if job is not None:
                                def execute_unlocked(*args, **kwargs):
                                    with suspended_lock(worker_lock):
                                        return executor(*args, **kwargs)
                                return self._run_claimed(job, execute_unlocked)
                    except (ExecutionBlocked, OSError, ValueError) as exc:
                        # A busy repository is not an agent failure; leave it queued.
                        if isinstance(exc, ExecutionBlocked) and "another session owns" in str(exc):
                            continue
                        current = self.get(candidate["job_id"])
                        if current["status"] in ("READY", "WAITING_QUOTA", "WAITING_RETRY"):
                            current.update(status="NEEDS_RECONCILIATION", resume_at=None,
                                           reason="repository lock or execution guard could not be verified")
                            with self.db:
                                self._save(current)
                            continue
                        raise
                return {"status": "IDLE", "reason": "no due, unlocked session job"}
        except ExecutionBlocked as exc:
            if "another controller" in str(exc):
                return {"status": "BUSY", "reason": "another worker owns the queue"}
            raise

    def handoff(self, job_id: str) -> dict:
        """Explicitly schedule a NEW session for context exhaustion, never quota."""
        with controller_lock(self.root / "session-worker"):
            job = self.get(job_id)
            if job["status"] != "NEEDS_CONTEXT_HANDOFF":
                raise ExecutionBlocked("new-session handoff is only allowed for context exhaustion")
            with repository_lock(job["repository_snapshot"]):
                if execution_alive(job["process"]) or repository_snapshot(Path(job["worktree"])) != job["repository_snapshot"]:
                    raise ExecutionBlocked("cannot hand off changed or still-running work")
                job["previous_sessions"].append(job["session_id"])
                job["checkpoint"] = dict(job["checkpoint"], handoff_file=job["handoff_file"])
                job.update(session_id=None, process=None, status="READY", resume_at=self.clock(),
                           reset_at=None, cycle_retries=0, handoff_count=job["handoff_count"] + 1,
                           reason="new session scheduled from context checkpoint")
                with self.db:
                    self._save(job)
                return job
