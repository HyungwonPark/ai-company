"""Read-only diagnosis and explicit, call-free recovery of one settled PM result.

The original session, task, failure and shared settlement remain immutable. A
recovered plan carries a separate validator revision and needs its usual plan
review and master confirmation; this module never runs the coordinator.
"""

from dataclasses import dataclass
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile

from ai_company.adapters.configuration_evidence import codex_turn_configuration
from ai_company.adapters.session_cli import codex_output_schema_digest
from ai_company.automation import Automation
from ai_company.automation_contracts import AutomationConfig
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import FlowSpec, PMPlanStageReport
from ai_company.management import ManagementError, ManagementStore
from ai_company.sessions import execution_alive
from ai_company.storage import controller_lock


class RecoveryError(ValueError):
    pass


def verify_validator_release(commit: str) -> None:
    """Bind CLI claims to a clean checkout or the pinned installed archive."""
    root = Path(__file__).resolve().parents[2]
    marker = root / "AI-COMPANY-RELEASE-COMMIT"
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=False)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    if head.returncode or top.returncode or Path(top.stdout.strip()).resolve() != root:
        # This label is meaningful only after the archive is verified against
        # its externally pinned SHA-256 at installation.
        if not marker.is_file() or marker.read_text().strip() != commit:
            raise RecoveryError("installed recovery release differs from validator commit")
        return
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                            capture_output=True, text=True, check=False)
    if (status.returncode or head.stdout.strip() != commit or
            status.stdout.strip()):
        raise RecoveryError("validator code is not the clean named release")


@dataclass(frozen=True)
class Diagnosis:
    origin_root: Path
    state_root: Path
    shared_path: Path
    config_path: Path
    codex_home: Path
    evidence_manifest: Path
    request_id: str
    project_id: str
    task_id: str
    job_id: str
    reservation_id: str
    plan_content: dict
    plan_evidence: dict
    revision: dict


def _one(db, query, params=()):
    rows = db.execute(query, params).fetchall()
    if len(rows) != 1:
        raise RecoveryError("expected exactly one bound E1 record")
    return rows[0]


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _bound_ledger_facts(db, reservation_ids):
    """Read only the seven frozen call identities, including their journal rows."""
    if not reservation_ids:
        raise RecoveryError("frozen E1 call identities are absent")
    placeholders = ",".join("?" for _ in reservation_ids)
    reservations = db.execute(f"SELECT * FROM reservations WHERE reservation_id IN ({placeholders}) "
        "ORDER BY reservation_id", reservation_ids).fetchall()
    events = db.execute(f"SELECT * FROM settlement_events WHERE reservation_id IN ({placeholders}) "
        "ORDER BY event_id", reservation_ids).fetchall()
    if len(reservations) != len(reservation_ids) or len(events) != len(reservation_ids):
        raise RecoveryError("a frozen E1 or predecessor call is missing")
    return reservations, events


def _followup_reservations(db, ledger, origin, prior, request, original_plan_id,
                           original_ids, config):
    """Permit only current E1 plan review/repair attempts beyond the frozen calls."""
    rows = ledger.execute("SELECT reservation_id,owner,group_id,state,event_id,result,process_identity FROM reservations "
                          "WHERE owner=? OR owner LIKE ? ORDER BY reservation_id",
                          (str(origin), str(prior) + '/%')).fetchall()
    extra = [row for row in rows if row[0] not in original_ids]
    plans = {}
    for (raw,) in db.execute("SELECT document FROM management_plans"):
        plan = json.loads(raw)
        if plan.get("request_id") == request["request_id"] and plan.get("project_id") == request["project_id"]:
            plans[plan["id"]] = plan
    if extra and original_plan_id not in plans:
        raise RecoveryError("E1 follow-up has no bound original plan")
    allowed = {}
    for plan in plans.values():
        if plan.get("digest") != digest(ManagementStore._plan_binding(plan)):
            raise RecoveryError("E1 follow-up plan digest changed")
        cursor, seen = plan, set()
        while cursor["id"] != original_plan_id:
            if cursor["id"] in seen or cursor.get("revision_of") not in plans:
                raise RecoveryError("E1 follow-up plan lineage is invalid")
            seen.add(cursor["id"])
            parent = plans[cursor["revision_of"]]
            if (cursor.get("auto_revision_attempt") != parent.get("auto_revision_attempt", 0) + 1 or
                    cursor["auto_revision_attempt"] not in (1, 2) or
                    parent.get("revision_result_id") != cursor["id"]):
                raise RecoveryError("E1 follow-up plan revision is stale or skipped")
            cursor = parent
        if (plan.get("request_revision") != request["request_revision"] or
                plan.get("goal_digest") != request["goal_digest"] or
                plan.get("configuration_digest") != request["configuration_digest"]):
            raise RecoveryError("E1 follow-up plan belongs to a different request revision")
        allowed["plan-review-" + plan["digest"][:48]] = ("plan_review", plan)
        attempt = plan.get("auto_revision_attempt", 0) + 1
        if (attempt <= 2 and
                (plan.get("status") == "needs_revision" and plan.get("revision_action") == "automatic" or
                 plan.get("status") == "superseded" and
                 plans.get(plan.get("revision_result_id"), {}).get("auto_revision_attempt") == attempt)):
            allowed["pm-revise-" + digest([plan["id"], attempt])[:48]] = ("planning", plan)
    started_repairs = set()
    elapsed = 0.0
    for reservation_id, owner, group, state, event_id, result, process_identity in extra:
        if owner != str(origin) or group == "@host-only" or state not in ("RESERVED", "STARTED", "UNKNOWN", "SETTLED", "CANCELLED"):
            raise RecoveryError("unrelated shared reservation in E1 lineage")
        archived = ":cancel:" in reservation_id
        bound_id = reservation_id
        if archived:
            cancellation = json.loads(result or "{}")
            bound_id = cancellation.get("original_reservation_id")
            if (state != "CANCELLED" or not isinstance(bound_id, str) or
                    not reservation_id.startswith(bound_id + ":cancel:") or
                    cancellation.get("evidence") != "queue_unclaimed_no_guard_no_process" or
                    not ledger.execute("SELECT 1 FROM reservations WHERE reservation_id=? AND owner=? AND group_id=?",
                                       (bound_id, owner, group)).fetchone()):
                raise RecoveryError("E1 cancelled reservation archive is invalid")
        unclaimed = state in ("RESERVED", "CANCELLED") or state == "UNKNOWN" and not process_identity
        matches = []
        for (job_id, raw) in db.execute("SELECT job_id,document FROM session_jobs"):
            job = json.loads(raw)
            if job.get("task_id") not in allowed:
                continue
            upper = job.get("attempt_count", 0) + unclaimed
            for attempt in range(1, upper + 1):
                if bound_id == digest([str(origin), job_id, attempt]):
                    matches.append((job, attempt))
        if len(matches) != 1:
            raise RecoveryError("E1 follow-up reservation has no unique job attempt")
        job, attempt = matches[0]
        queued = (attempt == job["attempt_count"] + 1 and not job.get("process") and
                  job.get("status") in ("READY", "WAITING_QUOTA", "WAITING_RETRY"))
        claimed_unstarted = (state in ("RESERVED", "UNKNOWN") and
            attempt == job["attempt_count"] and
            job.get("status") in ("RUNNING", "NEEDS_RECONCILIATION") and
            db.execute("SELECT 1 FROM session_events WHERE job_id=? "
                "AND json_extract(document,'$.attempt_count')=? "
                "AND json_extract(document,'$.status')='RUNNING' LIMIT 1",
                (job["job_id"], attempt)).fetchone() is not None)
        if ((unclaimed and not archived and not (queued or claimed_unstarted)) or
                (not unclaimed and attempt > job["attempt_count"])):
            raise RecoveryError("E1 follow-up attempt is not a resumable queue fact")
        task_id = job["task_id"]
        task = json.loads(_one(db, "SELECT document FROM flow_tasks WHERE task_id=?", (task_id,))[0])
        scope, plan = allowed[task_id]
        binding = task.get("specification", {}).get("plan", {})
        executions = [*task.get("executions", []), task.get("active") or {}]
        generation = task.get("generation")
        linked = [item for item in executions if item.get("job_id") == job["job_id"]]
        expected = (job.get("checkpoint") or {}).get("expected_report") or {}
        spec = FlowSpec.model_validate(task["specification"])
        agent = next((candidate for candidate in spec.agents
                      if candidate.agent_id == linked[0].get("agent_id")), None) if len(linked) == 1 else None
        submitted = job.get("specification") or {}
        if (task.get("spec_digest") != digest(spec) or
                spec.task.task_id != task_id or job.get("task_id") != task_id or
                job.get("managed_by") != task_id or
                submitted.get("task", {}).get("task_id") != task_id or
                submitted.get("agent_id") != job.get("agent_id") or
                submitted.get("provider") != job.get("provider") or
                submitted.get("worktree") != spec.worktree or
                agent is None or job.get("agent_id") != agent.agent_id or
                job.get("provider") != agent.provider or
                linked[0].get("provider") != agent.provider or
                linked[0].get("role") != ("reviewer" if scope == "plan_review" else "pm") or
                linked[0].get("role") not in agent.roles or
                group != agent.quota_group or
                task["specification"].get("execution_scope") != scope or
                task["specification"].get("mode") != "live" or
                not linked or len(linked) != 1 or
                not isinstance(generation, int) or
                linked[0].get("generation") not in range(1, generation + 1) or
                any(expected.get(key) != linked[0].get(key) for key in
                    ("execution_id", "generation", "role", "task_digest", "policy_digest")) or
                expected.get("task_digest") != digest(spec.task) or
                expected.get("policy_digest") != digest(spec.policy) or
                (scope == "plan_review" and
                 (binding.get("plan_digest") != plan["digest"] or
                  binding.get("requirements_revision") != request["request_revision"] or
                  binding.get("project_id") != request["project_id"] or
                  {"provider": (plan.get("evidence") or {}).get("provider"),
                   "session_id": (plan.get("evidence") or {}).get("session_id")}
                  not in spec.inherited_pm_sessions)) or
                (scope == "planning" and
                 (binding.get("source_plan_id") != plan["id"] or
                  binding.get("auto_revision_attempt") not in (1, 2) or
                  task_id != "pm-revise-" + digest([plan["id"], binding["auto_revision_attempt"]])[:48] or
                  binding.get("request_revision") != request["request_revision"] or
                  binding.get("goal_digest") != request["goal_digest"] or
                  binding.get("project_id") != request["project_id"]))):
            raise RecoveryError("E1 follow-up task, generation or plan binding changed")
        if state in ("STARTED", "UNKNOWN", "SETTLED") and scope == "planning":
            started_repairs.add(task_id)
        event = ledger.execute("SELECT event_id,result FROM settlement_events WHERE reservation_id=?", (reservation_id,)).fetchall()
        if state == "SETTLED":
            if len(event) != 1 or event[0] != (event_id, result) or not event_id or not process_identity:
                raise RecoveryError("E1 follow-up settlement journal is incomplete")
            saved_attempt = job
            if attempt != job["attempt_count"]:
                previous = db.execute("SELECT document FROM session_events WHERE job_id=? "
                    "AND json_extract(document,'$.attempt_count')=? "
                    "AND json_extract(document,'$.last_category') IS NOT NULL "
                    "ORDER BY sequence DESC LIMIT 1", (job["job_id"], attempt)).fetchone()
                if not previous:
                    raise RecoveryError("E1 follow-up settled attempt has no saved result")
                saved_attempt = json.loads(previous[0])
            if event_id != digest([reservation_id, attempt, saved_attempt.get("last_category"),
                                   saved_attempt.get("result")]):
                raise RecoveryError("E1 follow-up settlement differs from its job result")
            outcome = json.loads(result)
            duration = outcome.get("duration_seconds")
            if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
                    not math.isfinite(duration) or duration < 0):
                raise RecoveryError("E1 follow-up duration is invalid")
            saved_result = saved_attempt.get("result") or {}
            expected_duration = saved_result.get("duration_seconds")
            if (isinstance(expected_duration, bool) or not isinstance(expected_duration, (int, float)) or
                    not math.isfinite(expected_duration) or expected_duration < 0):
                expected_duration = spec.policy.retry.execution_timeout_seconds
            expected = {"category": saved_attempt.get("last_category"),
                        "duration_seconds": expected_duration,
                        "total_cost_usd": saved_result.get("total_cost_usd")}
            schema_sha = saved_result.get("output_schema_sha256")
            if isinstance(schema_sha, str) and re.fullmatch(r"[0-9a-f]{64}", schema_sha):
                expected["output_schema_sha256"] = schema_sha
            if expected["category"] in ("quota", "rate_limit"):
                reset = outcome.get("reset_at")
                if (isinstance(reset, bool) or not isinstance(reset, (int, float)) or
                        not math.isfinite(reset) or reset <= 0 or
                        saved_attempt.get("resume_at") is not None and reset != saved_attempt["resume_at"]):
                    raise RecoveryError("E1 follow-up quota reset differs from its job")
                expected["reset_at"] = reset
            if outcome != expected:
                raise RecoveryError("E1 follow-up settlement content differs from its job")
            elapsed += duration
        elif event or event_id or (state in ("RESERVED", "CANCELLED") and process_identity) or \
                (state == "STARTED" and not process_identity) or \
                (state == "CANCELLED" and result and not archived):
            raise RecoveryError("E1 follow-up reservation state is inconsistent")
        elif state in ("RESERVED", "STARTED", "UNKNOWN"):
            elapsed += config.policy.retry.execution_timeout_seconds
    extra_calls = sum(row[3] != "CANCELLED" for row in extra)
    if (len(started_repairs) > min(2, config.policy.max_repairs) or
            1 + extra_calls > config.policy.max_executions or
            len(original_ids) + extra_calls > 24):
        raise RecoveryError("E1 follow-up exceeds evaluation call or repair cap")
    return extra, elapsed


def _isolated_file_identity(origin, state, snapshot):
    """Reject aliases before a copied SQLite database can be opened for writing."""
    if state == origin:
        raise RecoveryError("isolated recovery must target a separate state")
    target = state / "sessions" / "sessions.sqlite"
    source = origin / "sessions" / "sessions.sqlite"
    protected = [path for base in (source, snapshot)
                 for path in (base, base.with_name(base.name + "-wal"),
                              base.with_name(base.name + "-shm")) if path.exists()]
    for path in (state, state / "sessions", target,
                 target.with_name(target.name + "-wal"),
                 target.with_name(target.name + "-shm"),
                 state / "pm-evidence-recovery",
                 state / "pm-evidence-recovery" / "controller.lock"):
        if path.is_symlink():
            raise RecoveryError("isolated state contains a symbolic link")
    for directory in (state, state / "sessions"):
        metadata = directory.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise RecoveryError("isolated state directory is not privately controlled")
    if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
        raise RecoveryError("isolated state database is absent")
    identity = (target.stat().st_dev, target.stat().st_ino)
    if any(identity == (path.stat().st_dev, path.stat().st_ino) for path in protected):
        raise RecoveryError("isolated state database aliases an original file")
    for suffix in ("-wal", "-shm"):
        sidecar = target.with_name(target.name + suffix)
        if sidecar.exists() and any((sidecar.stat().st_dev, sidecar.stat().st_ino) ==
                                    (path.stat().st_dev, path.stat().st_ino) for path in protected):
            raise RecoveryError("isolated SQLite sidecar aliases an original file")
    return identity


@contextmanager
def _isolated_stage(origin, state, expected_identity):
    """Run SQLite on a private staging copy; publish only into the pinned copy dir."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    state_fd = sessions_fd = None
    try:
        state_fd = os.open(state, directory_flags)
        sessions_fd = os.open("sessions", directory_flags, dir_fd=state_fd)
        source_dir = (origin / "sessions").stat()
        sessions_dir = os.fstat(sessions_fd)
        if (sessions_dir.st_dev, sessions_dir.st_ino) == (source_dir.st_dev, source_dir.st_ino):
            raise RecoveryError("isolated sessions directory aliases the original")
        for suffix in ("-wal", "-shm"):
            try:
                sidecar = os.stat("sessions.sqlite" + suffix, dir_fd=sessions_fd,
                                  follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(sidecar.st_mode) or suffix == "-wal" and sidecar.st_size:
                raise RecoveryError("isolated state has active SQLite sidecars")
        with tempfile.TemporaryDirectory(prefix="ai-company-e1-stage-") as temporary:
            staged_root = Path(temporary)
            staged_sessions = staged_root / "sessions"
            staged_sessions.mkdir(mode=0o700)
            for name in ("sessions.sqlite",):
                try:
                    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                      dir_fd=sessions_fd)
                except FileNotFoundError:
                    if name == "sessions.sqlite":
                        raise RecoveryError("isolated state database is absent")
                    continue
                with os.fdopen(file_fd, "rb") as source_file, (staged_sessions / name).open("xb") as staged_file:
                    if name == "sessions.sqlite":
                        metadata = os.fstat(source_file.fileno())
                        if ((metadata.st_dev, metadata.st_ino) != expected_identity or
                                not stat.S_ISREG(metadata.st_mode)):
                            raise RecoveryError("isolated state database changed before persistence")
                    shutil.copyfileobj(source_file, staged_file)
            yield staged_root, sessions_fd
    except (OSError, sqlite3.Error) as exc:
        raise RecoveryError("isolated state database could not be staged") from exc
    finally:
        if sessions_fd is not None:
            os.close(sessions_fd)
        if state_fd is not None:
            os.close(state_fd)


def _raw_output(path, original, session_id):
    completed = 0
    matches = 0
    started = 0
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            if event.get("type") == "thread.started":
                started += 1
                if event.get("thread_id") != session_id:
                    raise RecoveryError("original CLI output belongs to another session")
            if event.get("type") == "turn.completed":
                completed += 1
            if event.get("type") != "item.completed" or event.get("item", {}).get("type") != "agent_message":
                continue
            try:
                value = json.loads(event["item"]["text"])
            except (KeyError, TypeError, ValueError):
                continue
            matches += 1
            if value != original:
                raise RecoveryError("saved structured output differs from original CLI output")
    if started != 1 or completed != 1 or matches != 1:
        raise RecoveryError("original CLI output lacks one complete structured turn")


def _original_file(path, origin):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(origin):
        raise RecoveryError("original execution file is missing or outside E1")
    return path


def _effective_config(config_path, origin, trial_manifest):
    config_path = config_path.resolve(strict=True)
    config_sha = _hash(config_path)
    pinned = trial_manifest.get("input_sha256", {})
    matching = [name for name, value in pinned.items()
                if value == config_sha and Path(name).name == config_path.name]
    if len(matching) != 1:
        raise RecoveryError("evaluation configuration changed since E1")
    saved = json.loads((origin / "case-budget.json").read_text())
    base = AutomationConfig.model_validate_json(config_path.read_text())
    value = base.model_dump(mode="json")
    value["max_parallel"] = 1
    value["policy"].update(saved["caps"])
    effective = AutomationConfig.model_validate(value)
    encoded = json.dumps(effective.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()
    if saved.get("effective_configuration_sha256") != sha256(encoded).hexdigest():
        raise RecoveryError("E1 effective configuration differs from its pinned budget")
    return effective, config_sha


def diagnose(origin_root: Path, state_root: Path, shared_path: Path, config_path: Path,
             codex_home: Path, *, validator_commit: str,
             evidence_manifest: Path) -> Diagnosis:
    """Validate the exact saved E1 without opening a writable DB or running a worker."""
    origin = Path(origin_root).resolve(strict=True)
    state = Path(state_root).resolve(strict=True)
    shared = Path(shared_path).resolve(strict=True)
    if (origin.name != "E1" or state == shared or
            re.fullmatch(r"[0-9a-f]{40}", validator_commit) is None):
        raise RecoveryError("E1 origin, ledger or validator binding is invalid")
    db = sqlite3.connect(f"file:{state / 'sessions' / 'sessions.sqlite'}?mode=ro", uri=True)
    ledger = sqlite3.connect(f"file:{shared}?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        ledger.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",) or ledger.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RecoveryError("saved database or shared ledger is corrupt")
        request_raw = _one(db, "SELECT document FROM management_pm_requests")[0]
        request = json.loads(request_raw)
        task_id = "pm-" + request["request_id"]
        task_raw = _one(db, "SELECT document FROM flow_tasks WHERE task_id=?", (task_id,))[0]
        task = json.loads(task_raw)
        active = task.get("active") or {}
        job_id = active.get("job_id")
        job_raw = _one(db, "SELECT document FROM session_jobs WHERE job_id=?", (job_id,))[0]
        job = json.loads(job_raw)
        spec = FlowSpec.model_validate(task["specification"])
        if (request.get("state") not in ("blocked", "completed") or
                request.get("reason") != "assigned Codex CLI turn configuration is missing or unbound" or
                task.get("status") != "BLOCKED" or task.get("executions") or
                task.get("spec_digest") != digest(spec) or job.get("task_id") != task_id or
                job.get("status") != "SESSION_COMPLETED" or job.get("last_category") != "success" or
                job.get("attempt_count") != 1 or active.get("job_id") != job_id or
                spec.execution_scope != "planning" or spec.mode != "live" or
                not job.get("result", {}).get("cgroup_stopped") or execution_alive(job.get("process")) or
                (Path(job["repository_snapshot"]["git_common_dir"]) / "ai-company-session-active.json").exists()):
            raise RecoveryError("E1 is not the original stopped, successful call blocked only by CLI evidence")
        if (request["state"] == "blocked" and
                db.execute("SELECT count(*) FROM management_plans").fetchone()[0] != 0):
            raise RecoveryError("blocked E1 already has a saved plan")
        if request["state"] == "completed" and not request.get("plan_id"):
            raise RecoveryError("completed E1 has no saved plan")
        if (job.get("head_commit") != task["snapshot"]["head_commit"] or
                job.get("repository_snapshot") != task["snapshot"]):
            raise RecoveryError("E1 repository or candidate changed")
        manifest_path = origin.parent / "evaluation-bindings.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("case_bindings", {}).get("E1", {}).get("message_sha256") != sha256(request["content"].encode()).hexdigest():
            raise RecoveryError("E1 saved request differs from its pinned case")
        lineage_path = origin.parent / "evaluation-lineage.json"
        lineage = json.loads(lineage_path.read_text())
        prior = Path(lineage["prior_trial_root"]).resolve(strict=True)
        lineage_shared = Path(lineage["shared_call_ledger"]).resolve(strict=True)
        if state == origin and shared != lineage_shared:
            raise RecoveryError("E1 lineage is not tied to its original shared ledger")
        if lineage.get("prior_manifest_sha256") != _hash(prior / "evaluation-bindings.json"):
            raise RecoveryError("E1 predecessor evaluation manifest changed")
        prior_facts = ledger.execute("SELECT state FROM reservations WHERE owner LIKE ?",
                                     (str(prior) + '/%',)).fetchall()
        if len(prior_facts) != 6 or any(row[0] != "SETTLED" for row in prior_facts):
            raise RecoveryError("E1 did not inherit six settled predecessor calls")
        config, config_sha = _effective_config(config_path, origin, manifest)
        if (request.get("configuration_digest") != digest(config) or request.get("mode") != config.mode or
                request.get("goal_digest") != digest(request["goal"]) or
                request.get("request_revision") != spec.plan.get("request_revision") or
                request.get("goal_digest") != spec.plan.get("goal_digest") or
                manifest.get("product_commit") != config.base_sha):
            raise RecoveryError("E1 goal, revision, policy or product binding changed")
        store = object.__new__(ManagementStore)
        store.db = db
        if not store.request_is_current(request["request_id"]):
            raise RecoveryError("E1 request is no longer current")
        if request.get("plan_id") and request["state"] != "completed":
            raise RecoveryError("E1 has a plan without a completed request")
        result = job["result"]
        original_report = result.get("structured_output")
        stdout = _original_file(result.get("stdout_path"), origin)
        stderr = _original_file(result.get("stderr_path"), origin)
        _raw_output(stdout, original_report, job["session_id"])
        schema_sha = codex_output_schema_digest(PMPlanStageReport.model_json_schema())
        if result.get("output_schema_sha256") != schema_sha:
            raise RecoveryError("E1 original wire schema differs from the approved report")
        schema_paths = list((origin / "sessions" / "session-logs").glob("**/schema-*.json"))
        if len(schema_paths) != 1 or _hash(_original_file(schema_paths[0], origin)) != schema_sha:
            raise RecoveryError("E1 original transmitted schema file is missing or different")
        claim = _one(db, "SELECT occurred_at FROM session_events WHERE job_id=? "
                     "AND json_extract(document,'$.attempt_count')=? "
                     "AND json_extract(document,'$.status')='RUNNING' ORDER BY sequence LIMIT 1",
                     (job_id, job["attempt_count"]))[0]
        if not claim <= job["updated_at"] or result.get("exit_code") != 0:
            raise RecoveryError("E1 execution window or exit is invalid")
        rollout_root = (Path(codex_home) / "sessions").resolve(strict=True)
        paths = list(rollout_root.glob("*/*/*/rollout-*-" + job["session_id"] + ".jsonl"))
        if len(paths) != 1 or paths[0].is_symlink() or not paths[0].resolve().is_relative_to(rollout_root):
            raise RecoveryError("E1 original rollout is missing or ambiguous")
        rollout = paths[0]
        frozen = json.loads(Path(evidence_manifest).read_text())
        saved_ledger = frozen["databases"].get("shared-ledger.sqlite", {})
        if saved_ledger.get("source") != str(lineage_shared):
            raise RecoveryError("frozen shared ledger names a different source")
        with sqlite3.connect(f"file:{lineage_shared}?mode=ro", uri=True) as original_ledger, \
                sqlite3.connect(f"file:{Path(evidence_manifest).parent / 'shared-ledger.sqlite'}?mode=ro",
                                uri=True) as frozen_ledger:
            frozen_ids = [row[0] for row in frozen_ledger.execute(
                "SELECT reservation_id FROM reservations WHERE owner=? OR owner LIKE ? ORDER BY reservation_id",
                (str(origin), str(prior) + '/%'))]
            if len(frozen_ids) != 7:
                raise RecoveryError("frozen E1 lineage must contain exactly seven calls")
            original_facts = _bound_ledger_facts(frozen_ledger, frozen_ids)
            if (_bound_ledger_facts(ledger, frozen_ids) != original_facts or
                    _bound_ledger_facts(original_ledger, frozen_ids) != original_facts):
                raise RecoveryError("E1 or predecessor settlement differs from original shared ledger")
            followups, followup_seconds = _followup_reservations(
                db, ledger, origin, prior, request, request.get("plan_id"),
                set(frozen_ids), config)
            usage_delta = {}
            for _, _, group, state_value, _, encoded, _ in followups:
                if state_value != "SETTLED":
                    continue
                outcome = json.loads(encoded)
                calls, seconds, cost, unknown = usage_delta.get(group, (0, 0.0, 0.0, 0))
                usage_delta[group] = (calls + 1, seconds + outcome["duration_seconds"],
                                      cost + (outcome.get("total_cost_usd") or 0),
                                      max(unknown, int(outcome.get("total_cost_usd") is None)))
            account_query = ("SELECT provider,credential_ref,group_id,calls,runtime_seconds,cost_usd,cost_unknown "
                             "FROM accounts")
            frozen_accounts = {(row[0], row[1]): row[2:] for row in frozen_ledger.execute(account_query)}
            for candidate in (ledger, original_ledger):
                accounts = {(row[0], row[1]): row[2:] for row in candidate.execute(account_query)}
                for identity, baseline in frozen_accounts.items():
                    current = accounts.get(identity)
                    if current is None or current[0] != baseline[0]:
                        raise RecoveryError("E1 shared account inventory changed")
                    delta = usage_delta.get(baseline[0], (0, 0.0, 0.0, 0)) if candidate is ledger else (0, 0.0, 0.0, 0)
                    if (current[1] < baseline[1] + delta[0] or
                            current[2] + 1e-6 < baseline[2] + delta[1] or
                            current[3] + 1e-6 < baseline[3] + delta[2] or
                            current[4] < max(baseline[4], delta[3])):
                        raise RecoveryError("E1 shared account usage regressed")
            original_seconds = 0.0
            case_seconds = 0.0
            for row in original_facts[0]:
                outcome = json.loads(row[8])
                duration = outcome.get("duration_seconds")
                if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
                        not math.isfinite(duration) or duration < 0):
                    raise RecoveryError("frozen evaluation runtime is invalid")
                original_seconds += duration
                if row[1] == str(origin):
                    case_seconds += duration
            if (original_seconds + followup_seconds > 1800 or
                    case_seconds + followup_seconds > config.policy.max_runtime_seconds):
                raise RecoveryError("E1 follow-up exceeds evaluation runtime cap")
        originals = {"cli-stdout.jsonl": stdout, "cli-stderr.log": stderr,
                     "output-schema.json": schema_paths[0], "rollout.jsonl": rollout,
                     "case-budget.json": origin / "case-budget.json",
                     "cases.json": origin.parent / "cases.json",
                     "evaluation-bindings.json": manifest_path,
                     "evaluation-lineage.json": lineage_path}
        for name, path in originals.items():
            saved = frozen["files"].get(name, {})
            if (saved.get("source") != str(path) or saved.get("sha256") != _hash(path)
                    or saved.get("bytes") != path.stat().st_size):
                raise RecoveryError("E1 frozen original file changed: " + name)
        original_db = origin / "sessions" / "sessions.sqlite"
        saved_db = frozen["databases"].get("trial-state.sqlite", {})
        if saved_db.get("source") != str(original_db):
            raise RecoveryError("E1 frozen state names a different source")
        # SQLite WAL/checkpoint bytes can change without changing a row. Compare
        # the original records to the private read-only snapshot instead.
        snapshot = Path(evidence_manifest).parent / "trial-state.sqlite"
        with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as frozen_db, \
                sqlite3.connect(f"file:{original_db}?mode=ro", uri=True) as original_db_ro:
            for table, column, value in (("flow_tasks", "task_id", task_id),
                                         ("session_jobs", "job_id", job_id)):
                query = f"SELECT document FROM {table} WHERE {column}=?"
                source = _one(frozen_db, query, (value,))[0]
                if (_one(original_db_ro, query, (value,))[0] != source or
                        _one(db, query, (value,))[0] != source):
                    raise RecoveryError("E1 task or job differs from its frozen original")
            query = "SELECT document FROM session_events WHERE job_id=? ORDER BY sequence"
            original_events = frozen_db.execute(query, (job_id,)).fetchall()
            if (original_db_ro.execute(query, (job_id,)).fetchall() != original_events or
                    db.execute(query, (job_id,)).fetchall() != original_events):
                raise RecoveryError("E1 event history differs from its frozen original")
            query = "SELECT document FROM management_pm_requests WHERE message_id=?"
            frozen_request = json.loads(_one(frozen_db, query, (request["request_id"],))[0])
            for connection in (original_db_ro, db):
                comparison = json.loads(_one(connection, query, (request["request_id"],))[0])
                for key in ("state", "plan_id", "updated_at"):
                    comparison.pop(key, None)
                expected = {key: value for key, value in frozen_request.items()
                            if key not in ("state", "plan_id", "updated_at")}
                if comparison != expected:
                    raise RecoveryError("E1 request differs from its frozen original")
        observed = codex_turn_configuration(job["session_id"], Path(spec.worktree), claim,
                                             job["updated_at"], Path(codex_home))
        if observed.get("status") != "observed":
            raise RecoveryError("E1 rollout is not a supported, bound CLI configuration")
        reservation_id = digest([str(origin), job_id, job["attempt_count"]])
        row = _one(ledger, "SELECT owner,state,event_id,result FROM reservations WHERE reservation_id=?",
                   (reservation_id,))
        settlement = json.loads(row[3])
        event_id = digest([reservation_id, job["attempt_count"], job["last_category"], result])
        journal = _one(ledger, "SELECT result FROM settlement_events WHERE event_id=? AND reservation_id=?",
                       (event_id, reservation_id))[0]
        duration = result.get("duration_seconds")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float)) or
                not 0 <= duration < float("inf")):
            duration = spec.policy.retry.execution_timeout_seconds
        expected_settlement = {"category": job["last_category"],
            "duration_seconds": duration, "total_cost_usd": result.get("total_cost_usd"),
            "output_schema_sha256": schema_sha}
        if (row[0] != str(origin) or row[1] != "SETTLED" or row[2] != event_id or
                journal != row[3] or settlement != expected_settlement):
            raise RecoveryError("E1 original shared reservation is not exactly settled")
        candidate_job = {**job, "result": {**result, "configuration_evidence": observed}}
        dispatcher = object.__new__(Dispatcher)
        dispatcher.db = db
        report = dispatcher.accept_report(task, spec, candidate_job)
        if report.verdict != "PASS":
            raise RecoveryError("E1 original PM response did not propose a ready plan")
        worker = object.__new__(Automation)
        worker.config, worker.execution_catalog, worker.project_context = config, None, None
        plan = worker._select_skills(worker._validate_plan(report.plan),
                                    spec.plan.get("skill_research"))
        content = plan.model_dump(mode="json")
        store._requirements_ready({"content": content, "contract_version": 2,
            "request_id": request["request_id"], "request_revision": request["request_revision"],
            "goal_digest": request["goal_digest"], "project_id": request["project_id"],
            "pm_guidance_version": request.get("pm_guidance_version"), "status": "reviewing"})
        immutable_request = {key: value for key, value in request.items()
                             if key not in ("state", "plan_id", "updated_at")}
        source = {"request": digest(immutable_request),
                  "task": sha256(task_raw.encode()).hexdigest(),
                  "job": sha256(job_raw.encode()).hexdigest(),
                  "result": sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                  "stdout": _hash(stdout), "stderr": _hash(stderr), "rollout": _hash(rollout),
                  "settlement": sha256(row[3].encode()).hexdigest(),
                  "configuration": config_sha, "wire_schema": schema_sha,
                  "case_budget": _hash(origin / "case-budget.json"),
                  "evaluation_manifest": _hash(manifest_path),
                  "evaluation_lineage": _hash(lineage_path),
                  "evidence_manifest": _hash(Path(evidence_manifest)),
                  "original_spec": digest(spec), "original_policy": digest(spec.policy)}
        code_root = Path(__file__).resolve().parents[2]
        validator_files = [*sorted((code_root / "src" / "ai_company").rglob("*.py")),
                           code_root / "scripts" / "evaluate_pm_behavior.py",
                           code_root / "scripts" / "recover_pm_e1.py"]
        validator_code = sha256(json.dumps({str(path.relative_to(code_root)): _hash(path)
            for path in validator_files}, sort_keys=True).encode()).hexdigest()
        revision_id = digest([reservation_id, job_id, source, validator_commit,
                              validator_code, observed.get("profile"), content])
        evidence = {"source": "derived_settled_pm", "task_id": task_id,
                    "session_id": job["session_id"], "provider": job["provider"],
                    "candidate_sha": task["snapshot"]["head_commit"],
                    "configuration": observed,
                    "configuration_policy": config.policy.configuration_evidence,
                    "recovery_revision_id": revision_id,
                    "original_reservation_id": reservation_id,
                    "original_job_id": job_id,
                    "original_result_sha256": source["result"]}
        revision = {"id": revision_id, "request_id": request["request_id"],
                    "job_id": job_id, "reservation_id": reservation_id,
                    "validator_commit": validator_commit, "profile": observed.get("profile"),
                    "validator_code": validator_code,
                    "original": source, "content_sha256": digest(content),
                    "evidence_sha256": digest(evidence)}
        return Diagnosis(origin, state, shared, Path(config_path).resolve(), Path(codex_home).resolve(),
                         Path(evidence_manifest).resolve(),
                         request["request_id"], request["project_id"],
                         task_id, job_id, reservation_id, content, evidence, revision)
    except (KeyError, TypeError, IndexError, sqlite3.Error, OSError) as exc:
        raise RecoveryError("E1 original evidence is incomplete or invalid") from exc
    finally:
        db.close()
        ledger.close()


def _save_recovery(store, diagnosis):
    with store.db:
        store.db.execute("""CREATE TABLE IF NOT EXISTS pm_recovery_revisions(
            id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            plan_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL)""")
    existing = store.db.execute("SELECT document FROM pm_recovery_revisions WHERE request_id=?",
                                (diagnosis.request_id,)).fetchone()
    if existing:
        saved = json.loads(existing[0])
        if saved.get("binding") != diagnosis.revision:
            raise RecoveryError("a different recovery revision already owns E1")
        plan = store.get_plan(diagnosis.project_id, saved["plan_id"])
        request = store.get_pm_request(diagnosis.request_id)
        if (plan.get("evidence") != diagnosis.plan_evidence or
                plan.get("content") != diagnosis.plan_content or
                plan.get("status") not in ("reviewing", "proposed", "needs_revision", "superseded") or
                request.get("state") != "completed" or request.get("plan_id") != plan["id"] or
                not store.request_is_current(diagnosis.request_id)):
            raise RecoveryError("recovered plan or current request changed")
        return {"state": "already_saved", "revision_id": saved["binding"]["id"],
                "plan_id": plan["id"]}
    plan = store.complete_pm_request(diagnosis.request_id, diagnosis.plan_content,
                                     expected_state="blocked", evidence=diagnosis.plan_evidence)
    record = {"binding": diagnosis.revision, "plan_id": plan["id"],
              "original_failure": "assigned Codex CLI turn configuration is missing or unbound"}
    with store.db:
        store.db.execute("INSERT INTO pm_recovery_revisions VALUES (?,?,?,?)",
                         (diagnosis.revision["id"], diagnosis.request_id, plan["id"],
                          json.dumps(record, ensure_ascii=False, sort_keys=True)))
    return {"state": "saved", "revision_id": diagnosis.revision["id"],
            "plan_id": plan["id"]}


def _apply(diagnosis: Diagnosis, *, operating: bool) -> dict:
    if operating != (diagnosis.state_root == diagnosis.origin_root):
        raise RecoveryError("recovery target does not match the selected operating scope")
    if not operating:
        _isolated_file_identity(diagnosis.origin_root, diagnosis.state_root,
                                diagnosis.evidence_manifest.parent / "trial-state.sqlite")
    with controller_lock(diagnosis.state_root / "pm-evidence-recovery", blocking=True):
        # Recheck all immutable source facts under the lock before writing.
        current = diagnose(diagnosis.origin_root, diagnosis.state_root,
                           diagnosis.shared_path, diagnosis.config_path,
                           diagnosis.codex_home,
                           validator_commit=diagnosis.revision["validator_commit"],
                           evidence_manifest=diagnosis.evidence_manifest)
        if current.revision != diagnosis.revision:
            raise RecoveryError("E1 evidence changed after diagnosis")
        identity = None
        if not operating:
            identity = _isolated_file_identity(diagnosis.origin_root, diagnosis.state_root,
                                                diagnosis.evidence_manifest.parent / "trial-state.sqlite")
        if operating:
            store = ManagementStore(diagnosis.state_root)
            try:
                return _save_recovery(store, diagnosis)
            finally:
                store.close()
        with _isolated_stage(diagnosis.origin_root, diagnosis.state_root, identity) as (stage, sessions_fd):
            store = ManagementStore(stage)
            try:
                result = _save_recovery(store, diagnosis)
                if result["state"] == "saved":
                    store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    store.db.execute("PRAGMA journal_mode=DELETE")
            finally:
                store.close()
            if identity != _isolated_file_identity(
                    diagnosis.origin_root, diagnosis.state_root,
                    diagnosis.evidence_manifest.parent / "trial-state.sqlite"):
                raise RecoveryError("isolated state database changed before persistence")
            if result["state"] == "saved":
                if os.stat(stage).st_dev != os.fstat(sessions_fd).st_dev:
                    raise RecoveryError("isolated stage and copy are on different filesystems")
                for suffix in ("-wal", "-shm"):
                    try:
                        sidecar = os.stat("sessions.sqlite" + suffix, dir_fd=sessions_fd,
                                          follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(sidecar.st_mode) or suffix == "-wal" and sidecar.st_size:
                        raise RecoveryError("isolated state gained active SQLite sidecars")
                    os.unlink("sessions.sqlite" + suffix, dir_fd=sessions_fd)
                os.replace(stage / "sessions" / "sessions.sqlite", "sessions.sqlite",
                           dst_dir_fd=sessions_fd)
            return result


def apply_to_isolated_state(diagnosis: Diagnosis) -> dict:
    """Persist one derived plan on an explicit isolated copy."""
    return _apply(diagnosis, operating=False)


def apply_to_operating_state(diagnosis: Diagnosis) -> dict:
    """Future separately approved operating transition; never called by a worker."""
    return _apply(diagnosis, operating=True)


def verify_saved_recovery(diagnosis: Diagnosis, *, allow_review_progress: bool = False) -> dict:
    """Recheck a saved derived plan and its unchanged, settled source."""
    current = diagnose(diagnosis.origin_root, diagnosis.state_root,
                       diagnosis.shared_path, diagnosis.config_path,
                       diagnosis.codex_home,
                       validator_commit=diagnosis.revision["validator_commit"],
                       evidence_manifest=diagnosis.evidence_manifest)
    if current.revision != diagnosis.revision:
        raise RecoveryError("saved E1 recovery revision no longer matches its source")
    db = sqlite3.connect(f"file:{diagnosis.state_root / 'sessions' / 'sessions.sqlite'}?mode=ro", uri=True)
    try:
        record = json.loads(_one(db,
            "SELECT document FROM pm_recovery_revisions WHERE request_id=?",
            (diagnosis.request_id,))[0])
        if record.get("binding") != diagnosis.revision:
            raise RecoveryError("E1 recovery record is not the validated revision")
        store = object.__new__(ManagementStore)
        store.db = db
        plan = store.get_plan(diagnosis.project_id, record["plan_id"])
        request = store.get_pm_request(diagnosis.request_id)
        allowed_statuses = ({"reviewing", "proposed", "needs_revision", "superseded"}
                            if allow_review_progress else {"reviewing"})
        if (request.get("state") != "completed" or request.get("plan_id") != plan["id"] or
                plan.get("status") not in allowed_statuses or
                plan.get("content") != diagnosis.plan_content or
                plan.get("evidence") != diagnosis.plan_evidence or
                not store.request_is_current(diagnosis.request_id)):
            raise RecoveryError("E1 derived plan is stale, changed or already actioned")
        return {"state": "verified", "revision_id": diagnosis.revision["id"],
                "plan_id": plan["id"], "reservation_id": diagnosis.reservation_id}
    except (KeyError, TypeError, sqlite3.Error, ManagementError) as exc:
        raise RecoveryError("E1 derived plan is missing or invalid") from exc
    finally:
        db.close()
