"""Read-only diagnosis and explicit, call-free recovery of one settled PM result.

The original session, task, failure and shared settlement remain immutable. A
recovered plan carries a separate validator revision and needs its usual plan
review and master confirmation; this module never runs the coordinator.
"""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import re
import sqlite3
import subprocess

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


def _bound_ledger_facts(db, origin, prior):
    reservations = db.execute("SELECT * FROM reservations WHERE owner=? OR owner LIKE ? "
        "ORDER BY reservation_id", (str(origin), str(prior) + '/%')).fetchall()
    events = db.execute("SELECT * FROM settlement_events WHERE reservation_id IN ("
        "SELECT reservation_id FROM reservations WHERE owner=? OR owner LIKE ?) "
        "ORDER BY event_id", (str(origin), str(prior) + '/%')).fetchall()
    return reservations, events


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
            original_facts = _bound_ledger_facts(original_ledger, origin, prior)
            if (_bound_ledger_facts(ledger, origin, prior) != original_facts or
                    _bound_ledger_facts(frozen_ledger, origin, prior) != original_facts):
                raise RecoveryError("E1 or predecessor settlement differs from original shared ledger")
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
        if ledger.execute("SELECT count(*) FROM reservations WHERE owner=?", (str(origin),)).fetchone()[0] != 1:
            raise RecoveryError("E1 has additional shared reservations")
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


def _apply(diagnosis: Diagnosis, *, operating: bool) -> dict:
    if operating != (diagnosis.state_root == diagnosis.origin_root):
        raise RecoveryError("recovery target does not match the selected operating scope")
    with controller_lock(diagnosis.state_root / "pm-evidence-recovery", blocking=True):
        # Recheck all immutable source facts under the lock before writing.
        current = diagnose(diagnosis.origin_root, diagnosis.state_root,
                           diagnosis.shared_path, diagnosis.config_path,
                           diagnosis.codex_home,
                           validator_commit=diagnosis.revision["validator_commit"],
                           evidence_manifest=diagnosis.evidence_manifest)
        if current.revision != diagnosis.revision:
            raise RecoveryError("E1 evidence changed after diagnosis")
        store = ManagementStore(diagnosis.state_root)
        try:
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
        finally:
            store.close()


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
