"""Persistent control-plane records over the existing flow/session SQLite store.

This module records intent and evidence. It never invokes a model or deploys an artifact.
"""

import copy
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from ai_company.contracts import Contract, Digest, Text, digest
from ai_company.sessions import SessionQueue


class ManagementError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


class RoleInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    responsibility: Text


class ProjectInput(Contract):
    name: str = Field(min_length=1, max_length=150)
    goal: Text
    roles: list[RoleInput] = Field(default_factory=list, max_length=32)
    start_pm: StrictBool = False


class MessageInput(Contract):
    content: Text


class HarnessInput(Contract):
    content: Text
    base_version: StrictInt = Field(ge=1)


class ApprovalSubject(Contract):
    title: str = Field(min_length=1, max_length=200)
    action: Text
    environment: str = Field(min_length=1, max_length=200)
    artifact_sha: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    cost_usd: float = Field(ge=0, allow_inf_nan=False)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    impact: Text
    rollback: Text
    verification: Text


class DisplayedTranslation(Contract):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_digest: Digest


class DecisionInput(Contract):
    decision: Literal["approve", "reject", "request_changes"]
    comment: str = Field(default="", max_length=8000)
    subject_digest: Digest
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,128}$")
    displayed_translation: DisplayedTranslation | None = None


class PlanConfirmationInput(Contract):
    plan_digest: Digest
    base_harness_version: StrictInt = Field(ge=1)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,128}$")
    displayed_translation: DisplayedTranslation | None = None


class ValidationDelegationAuthorization(BaseModel):
    """Trusted local receipt; never accepted from an HTTP request or model output."""
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    source: Literal["explicit_user_reply"]
    received_at: float = Field(gt=0, allow_inf_nan=False, strict=True)
    received_at_utc: str | None = Field(default=None, max_length=40)
    receipt_clock: str | None = Field(default=None, min_length=1, max_length=500)
    original_text: str = Field(min_length=1, max_length=64000)
    plan_digest: Digest
    allowed_paths: list[str] = Field(min_length=1, max_length=161)
    max_new_validations: StrictInt = Field(ge=1, le=1)
    applies_to: Literal["new_execution_after_receipt_only"]

    @model_validator(mode="after")
    def check_utc_receipt_time(self):
        if self.received_at_utc is not None:
            parsed = datetime.fromisoformat(self.received_at_utc.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                raise ValueError("received_at_utc must specify UTC")
            if parsed.timestamp() != self.received_at:
                raise ValueError("received_at_utc must equal received_at")
        return self


class ManagementStore:
    def __init__(self, root: Path, *, clock=time.time):
        self.root, self.clock = Path(root).resolve(), clock
        self.queue = SessionQueue(self.root / "sessions", clock=clock)
        self.db = self.queue.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_projects(id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_roles(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_harnesses(project_id TEXT NOT NULL, version INTEGER NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY(project_id,version));
            CREATE TABLE IF NOT EXISTS management_links(flow_task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                role_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_messages(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_approvals(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_decisions(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(project_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS management_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_auth(token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS management_pm_requests(message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_plans(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_delegations(id TEXT PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_confirmations(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(project_id,idempotency_key));
            CREATE TRIGGER IF NOT EXISTS management_flow_insert AFTER INSERT ON flow_tasks BEGIN
                INSERT INTO management_events(project_id,document)
                SELECT project_id,json_object('kind','flow_updated','subject_id',NEW.task_id,
                    'created_at',json_extract(NEW.document,'$.updated_at'))
                FROM management_links WHERE flow_task_id=NEW.task_id;
            END;
            CREATE TRIGGER IF NOT EXISTS management_flow_update AFTER UPDATE ON flow_tasks BEGIN
                INSERT INTO management_events(project_id,document)
                SELECT project_id,json_object('kind','flow_updated','subject_id',NEW.task_id,
                    'created_at',json_extract(NEW.document,'$.updated_at'))
                FROM management_links WHERE flow_task_id=NEW.task_id;
            END;
        """)
        from ai_company.translations import initialize
        initialize(self.db)
        self.db.executescript("""
            CREATE TRIGGER IF NOT EXISTS management_session_update AFTER UPDATE ON session_jobs BEGIN
                INSERT INTO management_events(project_id,document)
                SELECT DISTINCT l.project_id,json_object('kind','session_updated','subject_id',NEW.job_id,
                    'created_at',json_extract(NEW.document,'$.updated_at'))
                FROM management_links l JOIN flow_tasks t ON t.task_id=l.flow_task_id
                WHERE json_extract(t.document,'$.active.job_id')=NEW.job_id;
            END;
            CREATE TRIGGER IF NOT EXISTS management_translation_link_insert AFTER INSERT ON translation_links BEGIN
                INSERT INTO management_events(project_id,document) VALUES (NEW.project_id,
                    json_object('kind','translation_requested','subject_id',NEW.job_id,'document_id',NEW.document_id,
                    'source_digest',NEW.source_digest,'created_at',CAST(strftime('%s','now') AS REAL)));
            END;
            CREATE TRIGGER IF NOT EXISTS management_translation_link_update AFTER UPDATE ON translation_links BEGIN
                INSERT INTO management_events(project_id,document) VALUES (NEW.project_id,
                    json_object('kind','translation_requested','subject_id',NEW.job_id,'document_id',NEW.document_id,
                    'source_digest',NEW.source_digest,'created_at',CAST(strftime('%s','now') AS REAL)));
            END;
            CREATE TRIGGER IF NOT EXISTS management_translation_job_update AFTER UPDATE ON translation_jobs BEGIN
                INSERT INTO management_events(project_id,document)
                SELECT DISTINCT project_id,json_object('kind','translation_updated','subject_id',NEW.id,
                    'created_at',json_extract(NEW.document,'$.updated_at')) FROM translation_links WHERE job_id=NEW.id;
            END;
        """)

    def close(self):
        self.queue.close()

    def _event(self, project_id, kind, subject_id):
        data = {"kind": kind, "subject_id": subject_id, "created_at": self.clock()}
        self.db.execute("INSERT INTO management_events(project_id,document) VALUES (?,?)", (project_id, json.dumps(data)))

    def _project(self, project_id):
        row = self.db.execute("SELECT document FROM management_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise ManagementError("not_found", "Project not found", 404)
        return json.loads(row[0])

    def create_project(self, value):
        value = ProjectInput.model_validate(value)
        project_id = uuid4().hex
        project = {"id": project_id, "name": value.name, "goal": value.goal, "status": "PLANNING",
                   "harness_version": 1, "request_revision": 0, "source": "unverified", "created_at": self.clock()}
        content = json.dumps({"goal": value.goal, "roles": [r.model_dump() for r in value.roles]}, ensure_ascii=False, indent=2)
        harness = {"version": 1, "status": "active", "content": content, "digest": digest(content), "created_at": self.clock()}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO management_projects VALUES (?,?)", (project_id, json.dumps(project)))
            self.db.execute("INSERT INTO management_harnesses VALUES (?,?,?)", (project_id, 1, json.dumps(harness)))
            for role in value.roles:
                item = {"id": uuid4().hex, **role.model_dump()}
                self.db.execute("INSERT INTO management_roles VALUES (?,?,?)", (item["id"], project_id, json.dumps(item)))
            self._event(project_id, "project_created", project_id)
            if value.start_pm:
                self._append_pm_request(project, MessageInput(content=
                    "이 목표를 함께 구체화하고 필요한 역할과 완료 기준을 제안해주세요. "
                    "제가 역할과 범위를 조정할 수 있도록 설명해주세요. 계획 확정 전에는 개발을 시작하지 마세요."))
        return project

    def list_projects(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_projects ORDER BY rowid")]

    def post_message(self, project_id, value):
        value = MessageInput.model_validate(value)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._append_pm_request(self._project(project_id), value)

    def _conversation_context(self, project_id):
        """Project-local reading context; never execution/approval authority."""
        rows = self.db.execute("SELECT document FROM management_messages WHERE project_id=? ORDER BY rowid DESC LIMIT 8",
                               (project_id,)).fetchall()
        messages = []
        remaining = 16000
        for row in rows:
            item = json.loads(row[0])
            text = item['content'][:min(4000, remaining)]
            messages.append({'id': item['id'], 'role': item['role'], 'content': text,
                             'truncated': text != item['content']})
            remaining -= len(text)
            if remaining <= 0:
                break
        row = self.db.execute("SELECT document FROM management_plans WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                              (project_id,)).fetchone()
        previous = None
        if row:
            plan = json.loads(row[0])
            previous = {'id': plan['id'], 'digest': plan['digest'], 'status': plan['status']}
            if len(json.dumps(plan['content'], ensure_ascii=False)) <= 20000:
                previous['content'] = plan['content']
            else:
                previous['content_omitted'] = True
        return {'messages': list(reversed(messages)), 'previous_proposal': previous,
                'purpose': 'discussion_only; a new plan requires a new explicit confirmation'}

    def _append_pm_request(self, project, value):
        # Caller owns one transaction, including project creation when requested.
        project_id = project['id']
        message = {"id": uuid4().hex, "role": "user", "content": value.content,
                   "status": "awaiting_pm", "created_at": self.clock()}
        revision = project.get("request_revision", 0) + 1
        harness_row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?",
                                          (project_id, project["harness_version"])).fetchone()
        harness = json.loads(harness_row[0])
        request = {"request_id": message["id"], "message_id": message["id"], "project_id": project_id,
                       "state": "pending", "request_revision": revision, "base_harness_version": project["harness_version"],
                       "base_harness_digest": digest(harness["content"]), "goal_digest": digest(project["goal"]),
                       "goal": project["goal"], "content": value.content, "source": project["source"],
                       "conversation_context": self._conversation_context(project_id),
                       "created_at": self.clock(), "updated_at": self.clock(), "execution": None, "plan_id": None,
                       "configuration_digest": None, "mode": None}
        project["request_revision"] = revision
        self.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), project_id))
        self.db.execute("INSERT INTO management_messages VALUES (?,?,?)", (message["id"], project_id, json.dumps(message)))
        self.db.execute("INSERT INTO management_pm_requests VALUES (?,?,?)", (message["id"], project_id, json.dumps(request)))
        self._event(project_id, "pm_request_saved", message["id"])
        return message

    def pm_requests(self, project_id=None):
        if project_id is None:
            rows = self.db.execute("SELECT document FROM management_pm_requests ORDER BY rowid")
        else:
            self._project(project_id)
            rows = self.db.execute("SELECT document FROM management_pm_requests WHERE project_id=? ORDER BY rowid", (project_id,))
        return [json.loads(row[0]) for row in rows]

    def get_pm_request(self, request_id):
        row = self.db.execute("SELECT document FROM management_pm_requests WHERE message_id=?", (request_id,)).fetchone()
        if not row:
            raise ManagementError("not_found", "PM request not found", 404)
        return json.loads(row[0])

    def save_pm_request(self, document, *, expected_state=None):
        """Worker-only state persistence; execution ownership is held by its worker lock."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.get_pm_request(document["request_id"])
            immutable = ("request_id", "message_id", "project_id", "request_revision", "base_harness_version",
                         "base_harness_digest", "goal_digest", "goal", "content", "source", "created_at", "conversation_context")
            if any(document.get(key) != previous.get(key) for key in immutable):
                raise ManagementError("request_mismatch", "PM request snapshot is immutable")
            for key in ("configuration_digest", "mode"):
                if previous.get(key) is not None and document.get(key) != previous[key]:
                    raise ManagementError("configuration_mismatch", "PM execution configuration is immutable after claim")
            if document.get("configuration_digest") is not None or document.get("mode") is not None:
                if not re.fullmatch(r"[0-9a-f]{64}", str(document.get("configuration_digest"))) or document.get("mode") not in ("live", "fixture"):
                    raise ManagementError("configuration_missing", "Worker must bind a valid configuration digest and mode")
            if expected_state is not None and previous["state"] != expected_state:
                raise ManagementError("request_conflict", "PM request state changed")
            if previous["state"] in ("completed", "stale"):
                if document == previous:
                    return previous
                raise ManagementError("request_complete", "Completed PM request cannot be replayed")
            document = {**document, "updated_at": self.clock()}
            self.db.execute("UPDATE management_pm_requests SET document=? WHERE message_id=?", (json.dumps(document), document["request_id"]))
            self._event(document["project_id"], "pm_request_updated", document["request_id"])
        return document

    @staticmethod
    def _plan_binding(plan):
        fields = ("id", "request_id", "project_id", "base_harness_version", "base_harness_digest", "request_revision", "goal_digest", "content", "configuration_digest", "mode", "source", "evidence")
        return {key: plan[key] for key in fields}

    def get_plan(self, project_id, plan_id):
        row = self.db.execute("SELECT document FROM management_plans WHERE id=? AND project_id=?", (plan_id, project_id)).fetchone()
        if not row:
            raise ManagementError("not_found", "Plan not found", 404)
        return json.loads(row[0])

    def _request_current(self, request, project):
        row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?",
                              (project["id"], project["harness_version"])).fetchone()
        harness = json.loads(row[0]) if row else {}
        return (project.get("request_revision", 0) == request["request_revision"]
                and digest(project["goal"]) == request["goal_digest"]
                and project["harness_version"] == request["base_harness_version"]
                and digest(harness.get("content")) == request["base_harness_digest"])

    def request_is_current(self, request_id):
        request = self.get_pm_request(request_id)
        return self._request_current(request, self._project(request["project_id"]))

    def complete_pm_request(self, request_id, plan_content, *, expected_state="running", evidence=None):
        from ai_company.automation_contracts import PMPlanContent
        content = PMPlanContent.model_validate(plan_content).model_dump(mode="json")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            request = self.get_pm_request(request_id)
            project = self._project(request["project_id"])
            if request.get("plan_id"):
                old = self.get_plan(project["id"], request["plan_id"])
                if old["content"] != content or old.get("evidence") != evidence:
                    raise ManagementError("result_conflict", "PM request already completed with another result")
                return old
            if request["state"] != expected_state:
                raise ManagementError("request_conflict", "PM request no longer owns this completion")
            if not re.fullmatch(r"[0-9a-f]{64}", str(request.get("configuration_digest"))) or request.get("mode") not in ("live", "fixture"):
                raise ManagementError("configuration_missing", "PM completion requires its claimed server configuration")
            if request["mode"] == "live" and isinstance(evidence, dict) and (evidence.get("source") == "fixture" or evidence.get("scope") == "fixture"):
                raise ManagementError("fixture_only", "Fixture PM evidence cannot produce a live proposal")
            current = self._request_current(request, project)
            plan = {"id": uuid4().hex, "request_id": request_id, "project_id": project["id"],
                    "status": "proposed" if current else "stale", "content": content,
                    **{key: request[key] for key in ("request_revision", "base_harness_version", "base_harness_digest", "goal_digest")},
                    "created_at": self.clock(), "evidence": evidence, "source": (evidence or {}).get("source", request["source"]),
                    "configuration_digest": request["configuration_digest"], "mode": request["mode"]}
            plan["digest"] = digest(self._plan_binding(plan))
            request.update(state="completed" if current else "stale", plan_id=plan["id"], updated_at=self.clock())
            user_row = self.db.execute("SELECT document FROM management_messages WHERE id=?", (request_id,)).fetchone()
            user = json.loads(user_row[0]); user["status"] = request["state"]
            assistant = {"id": uuid4().hex, "role": "assistant", "content": content["summary"], "status": plan["status"],
                         "plan_id": plan["id"], "request_id": request_id, "created_at": self.clock(), "evidence": evidence,
                         "source": plan["source"]}
            self.db.execute("INSERT INTO management_plans VALUES (?,?,?)", (plan["id"], project["id"], json.dumps(plan)))
            self.db.execute("UPDATE management_pm_requests SET document=? WHERE message_id=?", (json.dumps(request), request_id))
            self.db.execute("UPDATE management_messages SET document=? WHERE id=?", (json.dumps(user), request_id))
            self.db.execute("INSERT INTO management_messages VALUES (?,?,?)", (assistant["id"], project["id"], json.dumps(assistant)))
            self._event(project["id"], "pm_plan_proposed" if current else "pm_plan_stale", plan["id"])
        return plan

    def save_pm_feedback(self, request_id, response, *, reason):
        """Publish a Dispatcher-accepted clarification, never a plan or approval."""
        from ai_company.flow_contracts import PMPlanStageReport
        response = PMPlanStageReport.model_validate(response).model_dump(mode='json')
        if response['verdict'] != 'BLOCK':
            raise ManagementError('result_conflict', 'Only a blocked planning response is feedback')
        message_id = 'pm-feedback-' + request_id
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            request = self.get_pm_request(request_id)
            old = self.db.execute('SELECT document FROM management_messages WHERE id=?', (message_id,)).fetchone()
            if old:
                old = json.loads(old[0])
                if old['evidence'] != response:
                    raise ManagementError('result_conflict', 'PM feedback already recorded with another result')
                return old
            if request['state'] != 'running' or not request.get('configuration_digest') or request.get('mode') not in ('fixture', 'live'):
                raise ManagementError('request_conflict', 'PM feedback requires its claimed running request')
            current = self._request_current(request, self._project(request['project_id']))
            request.update(state='blocked' if current else 'stale', reason=reason, updated_at=self.clock())
            message = dict(id=message_id, role='assistant', content=response['summary'], status=request['state'],
                           request_id=request_id, created_at=self.clock(), evidence=response,
                           source='fixture' if request['mode'] == 'fixture' else 'dispatcher')
            user = json.loads(self.db.execute('SELECT document FROM management_messages WHERE id=?', (request_id,)).fetchone()[0])
            user['status'] = request['state']
            self.db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?', (json.dumps(request), request_id))
            self.db.execute('UPDATE management_messages SET document=? WHERE id=?', (json.dumps(user), request_id))
            self.db.execute('INSERT INTO management_messages VALUES (?,?,?)', (message_id, request['project_id'], json.dumps(message)))
            self._event(request['project_id'], 'pm_feedback_saved', message_id)
        return message

    def run_records(self, project_id=None):
        if project_id is None:
            rows = self.db.execute("SELECT document FROM management_runs ORDER BY rowid")
        else:
            self._project(project_id)
            rows = self.db.execute("SELECT document FROM management_runs WHERE project_id=? ORDER BY rowid", (project_id,))
        return [json.loads(row[0]) for row in rows]

    def get_run(self, run_id):
        row = self.db.execute("SELECT document FROM management_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ManagementError("not_found", "Run not found", 404)
        return json.loads(row[0])

    def save_run(self, document, *, expected_state=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.get_run(document["id"])
            immutable = ("id", "project_id", "plan_id", "plan_digest", "role_ids", "harness_version", "created_at", "configuration_digest", "mode",
                         "parent_run_id", "delegation_id", "delegation_digest")
            if any(document.get(key) != previous.get(key) for key in immutable):
                raise ManagementError("run_mismatch", "Confirmed execution identity is immutable")
            if expected_state is not None and previous["state"] != expected_state:
                raise ManagementError("run_conflict", "Run state changed")
            document = {**document, "updated_at": self.clock()}
            self.db.execute("UPDATE management_runs SET document=? WHERE id=?", (json.dumps(document), document["id"]))
            self._event(document["project_id"], "run_updated", document["id"])
        return document

    def get_delegation(self, delegation_id):
        row = self.db.execute("SELECT document FROM management_delegations WHERE id=?", (delegation_id,)).fetchone()
        if not row:
            raise ManagementError("not_found", "Validation delegation not found", 404)
        return json.loads(row[0])

    def delegate_validation(self, plan_digest, authorization):
        """Create one fresh validation from an explicit local receipt, never approve old work.

        Only the trusted local worker calls this method. No HTTP route or model
        tool may assert that a reply is authorized on the master's behalf.
        """
        receipt = ValidationDelegationAuthorization.model_validate(authorization)
        if receipt.plan_digest != plan_digest or not receipt.original_text.strip():
            raise ManagementError("delegation_mismatch", "Receipt must bind the exact requested plan and preserve the user's reply")
        raw_authorization = copy.deepcopy(authorization)
        authorization_digest = digest(raw_authorization)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self.db.execute("SELECT document FROM management_delegations WHERE source_id=?", (receipt.source_id,)).fetchone()
            if prior:
                delegation = json.loads(prior[0])
                if delegation["authorization_digest"] != authorization_digest or delegation["plan_digest"] != plan_digest:
                    raise ManagementError("delegation_replay", "This user reply already authorizes a different immutable receipt")
                if (digest(delegation["authorization"]) != authorization_digest
                        or digest({key: value for key, value in delegation.items() if key != "digest"}) != delegation["digest"]):
                    raise ManagementError("delegation_mismatch", "Stored delegation receipt changed")
                run = self.get_run(delegation["run_id"])
                if (run.get("delegation_id"), run.get("delegation_digest"), run.get("plan_digest"), run.get("parent_run_id")) != (
                        delegation["id"], delegation["digest"], plan_digest, delegation["parent_run_id"]):
                    raise ManagementError("delegation_mismatch", "Delegated validation identity changed")
                return {"delegation": delegation, "run": run}
            created_at = self.clock()
            if receipt.received_at >= created_at:
                raise ManagementError("authorization_not_effective", "A validation must be created after the explicit reply was received")
            rows = self.db.execute("SELECT id,project_id,document FROM management_plans WHERE json_extract(document,'$.digest')=?", (plan_digest,)).fetchall()
            if len(rows) != 1:
                raise ManagementError("plan_mismatch", "Delegation requires one exact persisted plan digest")
            row = rows[0]; plan = json.loads(row[2])
            if (plan.get("id") != row[0] or plan.get("project_id") != row[1]
                    or digest(self._plan_binding(plan)) != plan_digest or plan.get("status") != "confirmed" or not plan.get("run_id")):
                raise ManagementError("plan_mismatch", "The approved plan must remain confirmed and unchanged")
            project = self._project(plan["project_id"])
            parent = self.get_run(plan["run_id"])
            if (project.get("source") == "fixture" or plan.get("source") == "fixture" or plan.get("mode") != "live" or parent.get("mode") != "live"):
                raise ManagementError("fixture_only", "Explicit validation delegation requires a live plan and parent run")
            if (parent.get("project_id"), parent.get("plan_id"), parent.get("plan_digest"), parent.get("configuration_digest")) != (
                    project["id"], plan["id"], plan_digest, plan["configuration_digest"]):
                raise ManagementError("delegation_mismatch", "Parent validation configuration or plan binding changed")
            if (project.get("request_revision", 0) != plan["request_revision"] or digest(project["goal"]) != plan["goal_digest"]
                    or project["harness_version"] != parent["harness_version"]):
                raise ManagementError("stale_plan", "Current project goal, request revision or active harness differs from the confirmed plan")
            harness_row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?",
                                          (project["id"], parent["harness_version"])).fetchone()
            harness = json.loads(harness_row[0]) if harness_row else {}
            expected_content = json.dumps(plan["content"], ensure_ascii=False, indent=2)
            if (harness.get("status") != "active" or harness.get("plan_id") != plan["id"]
                    or harness.get("content") != expected_content or harness.get("digest") != digest(expected_content)):
                raise ManagementError("stale_plan", "Active harness content differs from the exact confirmed plan")
            expected_paths = {path for role in plan["content"]["roles"] for path in role["allowed_paths"]} | {".ai-company-ci/request.json"}
            if len(receipt.allowed_paths) != len(set(receipt.allowed_paths)) or set(receipt.allowed_paths) != expected_paths:
                raise ManagementError("delegation_scope", "Delegated paths must exactly match the plan roles plus the CI request artifact")
            if set(parent["role_ids"]) != {role["key"] for role in plan["content"]["roles"]}:
                raise ManagementError("delegation_mismatch", "Confirmed role ownership changed")
            for role in plan["content"]["roles"]:
                role_row = self.db.execute("SELECT document FROM management_roles WHERE id=? AND project_id=?",
                                           (parent["role_ids"][role["key"]], project["id"])).fetchone()
                saved_role = json.loads(role_row[0]) if role_row else {}
                if (saved_role.get("active") is not True or saved_role.get("plan_id") != plan["id"]
                        or any(saved_role.get(key) != value for key, value in role.items())):
                    raise ManagementError("delegation_mismatch", "Role responsibility or ownership differs from the confirmed plan")
            run_id = uuid4().hex
            delegation = {"id": uuid4().hex, "source_id": receipt.source_id, "project_id": project["id"],
                          "plan_id": plan["id"], "plan_digest": plan_digest, "parent_run_id": parent["id"], "run_id": run_id,
                          "authorization": raw_authorization, "authorization_digest": authorization_digest, "created_at": created_at}
            delegation["digest"] = digest(delegation)
            run = {"id": run_id, "project_id": project["id"], "plan_id": plan["id"], "plan_digest": plan_digest,
                   "state": "pending", "role_ids": copy.deepcopy(parent["role_ids"]), "harness_version": parent["harness_version"],
                   "configuration_digest": parent["configuration_digest"], "mode": "live", "created_at": created_at, "updated_at": created_at,
                   "parent_run_id": parent["id"], "delegation_id": delegation["id"], "delegation_digest": delegation["digest"]}
            self.db.execute("INSERT INTO management_delegations VALUES (?,?,?,?)", (delegation["id"], receipt.source_id, project["id"], json.dumps(delegation)))
            self.db.execute("INSERT INTO management_runs VALUES (?,?,?)", (run_id, project["id"], json.dumps(run)))
            self._event(project["id"], "validation_delegated", delegation["id"])
            self._event(project["id"], "run_pending", run_id)
        return {"delegation": delegation, "run": run}

    def confirm_plan(self, project_id, plan_id, value):
        value = PlanConfirmationInput.model_validate(value)
        request_digest = digest({"plan_id": plan_id, **value.model_dump(exclude_none=True)})
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self._project(project_id)
            if project["source"] == "fixture":
                raise ManagementError("fixture_only", "Fixture projects cannot authorize executable runs")
            plan = self.get_plan(project_id, plan_id)
            prior = self.db.execute("SELECT request_digest,document FROM management_confirmations WHERE project_id=? AND idempotency_key=?",
                                    (project_id, value.idempotency_key)).fetchone()
            if prior:
                if prior[0] != request_digest:
                    raise ManagementError("idempotency_conflict", "Key already used for another confirmation")
                return json.loads(prior[1])
            if value.plan_digest != plan["digest"] or digest(self._plan_binding(plan)) != plan["digest"]:
                raise ManagementError("plan_mismatch", "Plan contents changed; reload before confirming")
            if plan["status"] == "confirmed":
                raise ManagementError("already_confirmed", "Plan already has a run")
            request = self.get_pm_request(plan["request_id"])
            if (plan["status"] != "proposed" or value.base_harness_version != plan["base_harness_version"]
                    or not self._request_current(request, project)):
                raise ManagementError("stale_plan", "Goal, conversation or active harness changed; request a fresh plan")
            if value.displayed_translation is not None:
                from ai_company.collaboration import plan_document
                self._check_displayed_translation(plan_document(project_id, plan), value.displayed_translation)
                plan["displayed_translation"] = value.displayed_translation.model_dump()
            # Keep prior roles for their existing task history. Only newly confirmed
            # roles become the active plan; existing flow policies are never edited.
            for row in self.db.execute("SELECT id,document FROM management_roles WHERE project_id=?", (project_id,)).fetchall():
                old = json.loads(row[1]); old["active"] = False
                self.db.execute("UPDATE management_roles SET document=? WHERE id=?", (json.dumps(old), row[0]))
            role_ids = {}
            for role in plan["content"]["roles"]:
                role_id = uuid4().hex; role_ids[role["key"]] = role_id
                document = {"id": role_id, **role, "active": True, "plan_id": plan_id}
                self.db.execute("INSERT INTO management_roles VALUES (?,?,?)", (role_id, project_id, json.dumps(document)))
            version = self.db.execute("SELECT MAX(version) FROM management_harnesses WHERE project_id=?", (project_id,)).fetchone()[0] + 1
            old_row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?", (project_id, project["harness_version"])).fetchone()
            old_harness = json.loads(old_row[0]); old_harness["status"] = "superseded"
            self.db.execute("UPDATE management_harnesses SET document=? WHERE project_id=? AND version=?", (json.dumps(old_harness), project_id, project["harness_version"]))
            text = json.dumps(plan["content"], ensure_ascii=False, indent=2)
            harness = {"version": version, "base_version": project["harness_version"], "status": "active", "content": text,
                       "digest": digest(text), "plan_id": plan_id, "created_at": self.clock()}
            self.db.execute("INSERT INTO management_harnesses VALUES (?,?,?)", (project_id, version, json.dumps(harness)))
            run = {"id": uuid4().hex, "project_id": project_id, "plan_id": plan_id, "plan_digest": plan["digest"],
                   "state": "pending", "role_ids": role_ids, "harness_version": version, "created_at": self.clock(), "updated_at": self.clock(),
                   "configuration_digest": plan["configuration_digest"], "mode": plan["mode"]}
            plan.update(status="confirmed", run_id=run["id"], confirmed_at=self.clock())
            project.update(harness_version=version, status="PLAN_CONFIRMED")
            result = {"plan": plan, "run": run}
            self.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), project_id))
            self.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(plan), plan_id))
            self.db.execute("INSERT INTO management_runs VALUES (?,?,?)", (run["id"], project_id, json.dumps(run)))
            self.db.execute("INSERT INTO management_confirmations VALUES (?,?,?,?)", (project_id, value.idempotency_key, request_digest, json.dumps(result)))
            self._event(project_id, "plan_confirmed", plan_id)
            self._event(project_id, "run_pending", run["id"])
        return result

    def save_harness(self, project_id, value):
        value = HarnessInput.model_validate(value)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self._project(project_id)
            if value.base_version != project["harness_version"]:
                raise ManagementError("stale_harness", "Active harness changed; reload before drafting")
            version = self.db.execute("SELECT MAX(version) FROM management_harnesses WHERE project_id=?", (project_id,)).fetchone()[0] + 1
            harness = {"version": version, "base_version": value.base_version, "content": value.content,
                       "status": "draft", "digest": digest(value.content), "created_at": self.clock()}
            self.db.execute("INSERT INTO management_harnesses VALUES (?,?,?)", (project_id, version, json.dumps(harness)))
            self._event(project_id, "harness_drafted", str(version))
        return harness

    def activate_harness(self, project_id, version, expected_digest):
        """Explicit trusted-administrator operation, never automatic PM interpretation."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self._project(project_id)
            row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?", (project_id, version)).fetchone()
            if not row:
                raise ManagementError("not_found", "Harness not found", 404)
            harness = json.loads(row[0])
            if harness["status"] != "draft" or harness["digest"] != expected_digest or harness["base_version"] != project["harness_version"]:
                raise ManagementError("stale_harness", "Draft no longer matches the active revision")
            previous = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?", (project_id, project["harness_version"])).fetchone()
            old = json.loads(previous[0]); old["status"] = "superseded"
            self.db.execute("UPDATE management_harnesses SET document=? WHERE project_id=? AND version=?", (json.dumps(old), project_id, project["harness_version"]))
            harness["status"] = "active"; project["harness_version"] = version
            self.db.execute("UPDATE management_harnesses SET document=? WHERE project_id=? AND version=?", (json.dumps(harness), project_id, version))
            self.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), project_id))
            self._event(project_id, "harness_activated", str(version))
        return harness

    def link_task(self, project_id, role_id, flow_task_id, *, title=None):
        """Link a task already submitted to Dispatcher; never enqueue a second copy."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self._project(project_id)
            if project["source"] == "fixture":
                raise ManagementError("fixture_only", "Fixture projects cannot own executable flow tasks")
            if not self.db.execute("SELECT 1 FROM management_roles WHERE id=? AND project_id=?", (role_id, project_id)).fetchone():
                raise ManagementError("not_found", "Role not found", 404)
            row = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (flow_task_id,)).fetchone()
            if not row:
                raise ManagementError("not_found", "Submit the task to Dispatcher before linking", 404)
            state = json.loads(row[0])
            item = {"id": flow_task_id, "flow_task_id": flow_task_id, "role_id": role_id,
                    "title": title or state["specification"]["task"]["goal"], "harness_version": project["harness_version"]}
            existing = self.db.execute("SELECT project_id,role_id,document FROM management_links WHERE flow_task_id=?", (flow_task_id,)).fetchone()
            if existing:
                if existing[0] != project_id or existing[1] != role_id:
                    raise ManagementError("task_owned", "Task is already assigned to another project or role")
                return json.loads(existing[2])
            self.db.execute("INSERT INTO management_links VALUES (?,?,?,?)", (flow_task_id, project_id, role_id, json.dumps(item)))
            self._event(project_id, "task_linked", flow_task_id)
        return item

    def request_approval(self, project_id, value, *, idempotency_key=None):
        project = self._project(project_id)
        subject = ApprovalSubject.model_validate(value).model_dump(mode="json")
        if subject["expires_at"] <= self.clock():
            raise ManagementError("expired", "Approval must have a future expiry")
        approval_id = digest([project_id, idempotency_key])[:32] if idempotency_key else uuid4().hex
        item = {"id": approval_id, "project_id": project_id, **subject, "status": "pending",
                "source": project["source"], "subject_digest": digest({"project_id": project_id, **subject}), "created_at": self.clock()}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT document FROM management_approvals WHERE id=?", (approval_id,)).fetchone()
            if existing:
                previous = json.loads(existing[0])
                if previous["subject_digest"] != item["subject_digest"]:
                    raise ManagementError("idempotency_conflict", "Approval key already binds another candidate")
                return previous
            self.db.execute("INSERT INTO management_approvals VALUES (?,?,?)", (item["id"], project_id, json.dumps(item)))
            self._event(project_id, "approval_requested", item["id"])
        return item

    def _approval(self, project_id, approval_id):
        row = self.db.execute("SELECT document FROM management_approvals WHERE id=? AND project_id=?", (approval_id, project_id)).fetchone()
        if not row:
            raise ManagementError("not_found", "Approval not found", 404)
        return json.loads(row[0])

    @staticmethod
    def _subject(item):
        return {"project_id": item["project_id"], **{key: item[key] for key in ApprovalSubject.model_fields}}

    def _check_displayed_translation(self, original, reference):
        from ai_company.translations import TranslationStore
        try:
            translated = TranslationStore(self.db, clock=self.clock).get_result(reference.id)
        except ValueError as exc:
            raise ManagementError("translation_mismatch", "Displayed translation is not a completed source-bound artifact") from exc
        if (translated["source_digest"] != original["source_digest"]
                or digest(translated["source"]) != original["source_digest"]
                or reference.source_digest != original["source_digest"]
                or translated["source"]["id"] != original["id"]
                or translated["source"]["project_id"] != original["project_id"]):
            raise ManagementError("translation_mismatch", "Displayed translation belongs to another source or version")

    def decide(self, project_id, approval_id, value):
        value = DecisionInput.model_validate(value)
        request_digest = digest({"approval_id": approval_id, **value.model_dump(exclude_none=True)})
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self._approval(project_id, approval_id)
            prior = self.db.execute("SELECT request_digest,document FROM management_decisions WHERE project_id=? AND idempotency_key=?", (project_id, value.idempotency_key)).fetchone()
            if prior:
                if prior[0] != request_digest:
                    raise ManagementError("idempotency_conflict", "Key already used for another decision")
                return json.loads(prior[1])
            if digest(self._subject(item)) != item["subject_digest"] or value.subject_digest != item["subject_digest"]:
                raise ManagementError("subject_mismatch", "Approval target changed; a new request is required")
            if item["expires_at"] <= self.clock():
                raise ManagementError("expired", "Approval request expired")
            if item["status"] != "pending":
                raise ManagementError("already_decided", "Approval already has a decision")
            if value.displayed_translation is not None:
                from ai_company.collaboration import document_sources
                original = next(doc for doc in document_sources({"project": {"id": project_id}, "approvals": [item]})
                                if doc["id"] == "approval:" + approval_id)
                self._check_displayed_translation(original, value.displayed_translation)
                item["displayed_translation"] = value.displayed_translation.model_dump()
            item.update(status={"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}[value.decision],
                        decision=value.decision, comment=value.comment, decided_at=self.clock())
            self.db.execute("UPDATE management_approvals SET document=? WHERE id=?", (json.dumps(item), approval_id))
            self.db.execute("INSERT INTO management_decisions VALUES (?,?,?,?)", (project_id, value.idempotency_key, request_digest, json.dumps(item)))
            self._event(project_id, "approval_decided", approval_id)
        return item

    def validate_approval(self, project_id, approval_id, subject):
        """Read-only preflight for a future executor, not permission to execute here."""
        item = self._approval(project_id, approval_id)
        expected = {"project_id": project_id, **ApprovalSubject.model_validate(subject).model_dump(mode="json")}
        if item["source"] == "fixture" or item["status"] != "approved" or item["expires_at"] <= self.clock():
            raise ManagementError("approval_invalid", "Approval is not current and executable")
        if digest(expected) != item["subject_digest"] or digest(self._subject(item)) != item["subject_digest"]:
            raise ManagementError("subject_mismatch", "Artifact, environment, cost or conditions changed")
        return item

    def events(self, project_id, after=0):
        self._project(project_id)
        rows = self.db.execute("SELECT id,document FROM management_events WHERE project_id=? AND id>? ORDER BY id LIMIT 200", (project_id, after)).fetchall()
        return {"events": [{"id": r[0], **json.loads(r[1])} for r in rows], "cursor": rows[-1][0] if rows else after}

    @staticmethod
    def _evidence(state):
        evidence = []
        spec = state["specification"]
        repo = spec.get("task", {}).get("repository", "")
        # Build only GitHub links from bounded identifiers, never agent-provided URLs.
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return evidence
        verification = state.get("verification") or {}
        head = verification.get("head_sha")
        if isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head):
            evidence.append({"label": "Verified candidate commit", "url": f"https://github.com/{repo}/commit/{head}"})
        remote = verification.get("remote") or verification
        run_id = remote.get("run_id")
        if remote.get("source") == "github" and re.fullmatch(r"[0-9]{1,20}", str(run_id)):
            evidence.append({"label": "Verified workflow run", "url": f"https://github.com/{repo}/actions/runs/{run_id}"})
        return evidence

    def overview(self, project_id):
        with self.db:
            self.db.execute("BEGIN")
            return self._overview(project_id)

    def _overview(self, project_id):
        project = self._project(project_id)
        roles = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_roles WHERE project_id=? ORDER BY rowid", (project_id,))]
        harnesses = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? ORDER BY version", (project_id,))]
        project["harness_content"] = next(h["content"] for h in harnesses if h["version"] == project["harness_version"])
        tasks, reports = [], []
        for row in self.db.execute("SELECT l.document,t.document FROM management_links l JOIN flow_tasks t ON t.task_id=l.flow_task_id WHERE l.project_id=? ORDER BY l.rowid", (project_id,)):
            linked, state = json.loads(row[0]), json.loads(row[1])
            active = state.get("active") or {}
            effective_status = state["status"]
            job = None
            if active.get("job_id"):
                job_row = self.db.execute("SELECT document FROM session_jobs WHERE job_id=?", (active["job_id"],)).fetchone()
                job = json.loads(job_row[0]) if job_row else None
            if job and job.get("status") in ("RUNNING", "WAITING_QUOTA", "WAITING_RETRY"):
                effective_status = job["status"]
                active = {**active, "session_id": job.get("session_id") or active.get("session_id")}
            tasks.append({**linked, "status": effective_status, "stage": state["stage"], "dependencies": state["specification"].get("dependencies", []),
                          "worktree": state["specification"]["worktree"],
                          "execution_scope": state["specification"].get("execution_scope", "full"),
                          "created_at": state.get("created_at", 0), "revision": state["specification"].get("plan", {}).get("revision", 0),
                          "generation": state.get("generation"), "candidate_sha": state.get("snapshot", {}).get("head_commit"),
                          "wait_reason": (job or {}).get("reason") if job and job.get("status", "").startswith("WAITING") else state.get("reason"),
                          "resume_at": (job or {}).get("resume_at") if job and job.get("status", "").startswith("WAITING") else state.get("resume_at"),
                          "active": active or None, "agents": state["specification"].get("agents", []), "handoffs": state.get("executions", []),
                          "repair_reason": state.get("findings", [])})
            reports.append({"id": "flow-" + linked["id"], "title": linked["title"], "summary": state["status"] + ": " + state.get("reason", ""),
                            "source": "system", "created_at": state.get("updated_at", 0), "evidence": self._evidence(state),
                            "verification": state.get("verification"), "review_reports": state.get("reviews", {})})
        if project["source"] == "fixture":
            tasks = project.get("fixture_tasks", [])
            reports = project.get("fixture_reports", [])
        for role in roles:
            own = sorted([t for t in tasks if t["role_id"] == role["id"] and t.get("execution_scope") != "integration"],
                         key=lambda t: (t.get("revision", 0), t.get("created_at", 0)))
            unfinished = [t for t in own if t["status"] not in ("MERGE_READY", "DEMO_READY", "COMPLETE", "FAILED", "BLOCKED")]
            current = own[-1] if own else None
            active = (current or {}).get("active") or {}
            profile = next((a for a in (current or {}).get("agents", []) if a["agent_id"] == active.get("agent_id")), {})
            role.update(status=current["status"] if current else "IDLE", assigned_model=profile.get("model"), session_id=active.get("session_id"),
                        current_task_id=current["id"] if current else None, next_task_id=next((t["id"] for t in unfinished if t is not current), None),
                        wait_reason=(current or {}).get("wait_reason"), resume_at=(current or {}).get("resume_at"), handoffs=(current or {}).get("handoffs", []))
        approvals = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_approvals WHERE project_id=? ORDER BY rowid", (project_id,))]
        for item in approvals:
            if item["expires_at"] <= self.clock() and item["status"] in ("pending", "approved"):
                item["status"] = "expired"
        messages = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_messages WHERE project_id=? ORDER BY rowid", (project_id,))]
        requests = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_pm_requests WHERE project_id=? ORDER BY rowid", (project_id,))]
        plans = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_plans WHERE project_id=? ORDER BY rowid", (project_id,))]
        runs = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_runs WHERE project_id=? ORDER BY rowid", (project_id,))]
        delegations = [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_delegations WHERE project_id=? ORDER BY rowid", (project_id,))]
        requests_by_id = {request["request_id"]: request for request in requests}
        for plan in plans:
            request = requests_by_id.get(plan["request_id"])
            if plan["status"] == "proposed" and (request is None or not self._request_current(request, project)):
                plan["status"] = "stale"
        # A linked real task is execution evidence, not proof of end-to-end readiness.
        readiness = {"mode": "fixture" if project["source"] == "fixture" else "unverified",
                     "pm": "awaiting_worker" if any(m["status"] == "awaiting_pm" for m in messages) else "unverified", "execution": "unverified"}
        if requests:
            latest_state = requests[-1]["state"]
            readiness["pm"] = {"pending": "awaiting_worker", "completed": "plan_proposed"}.get(latest_state, latest_state)
        if runs:
            readiness["execution"] = runs[-1]["state"]
            readiness["execution_mode"] = runs[-1]["mode"]
        project.pop("fixture_tasks", None); project.pop("fixture_reports", None)
        overview = {"project": project, "roles": roles, "tasks": tasks, "reports": reports, "approvals": approvals,
                    "messages": messages, "harnesses": harnesses, "readiness": readiness, "pm_requests": requests, "plans": plans, "runs": runs, "delegations": delegations}
        from ai_company.project_report import project_report
        from ai_company.service_worker import runtime_status
        overview["workers"] = runtime_status(self.root, clock=self.clock)
        overview["project_report"] = project_report(overview)
        from ai_company.collaboration import document_sources, project_collaboration
        from ai_company.translations import TranslationStore
        translations = TranslationStore(self.db, clock=self.clock)
        overview["documents"] = {doc["id"]: {**doc, "translation": translations.read(doc)} for doc in document_sources(overview)}
        overview["translation_summary"] = translations.summary(project_id)
        overview["collaboration"] = project_collaboration(self.db, overview)
        summary = overview["translation_summary"]
        overview["collaboration"]["nodes"].append({"id": "translator:" + project_id, "name": "한글 번역", "kind": "translator",
            "responsibility": "원문을 보존하는 번역 전용 · 실행·승인 권한 없음", "status": summary["status"],
            "current_task_id": None, "task_ids": [], "assignment": {"requested": summary.get("requested_configuration", {}),
            "observed": summary.get("observed_configuration") or {"status": "unavailable"}, "agent_id": "document-translator",
            "provider": summary.get("provider"), "quota_group": summary.get("quota_group"),
            "session_id": (summary.get("observed_configuration") or {}).get("session_id"),
            "quota": summary.get("quota"), "harness_version": project["harness_version"]},
            "wait_reason": summary.get("reason"), "resume_at": summary.get("resume_at"), "handoffs": [], "active": True})
        return overview

    def seed_demo(self):
        """Explicit fixture initialization; refuse any existing execution or project data."""
        if any(self.db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone() for table in ("flow_tasks", "session_jobs", "management_projects")):
            raise ManagementError("not_empty", "Fixtures require an empty isolated state directory")
        project = self.create_project({"name": "AI Company · 예시", "goal": "역할별 병렬 작업과 저장된 승인 화면 확인 — 실제 실행 아님",
            "roles": [{"name": "화면 개발", "responsibility": "매니저·역할별 진행·승인 화면"}, {"name": "서버 개발", "responsibility": "영속 큐·API 연결"},
                      {"name": "독립 검수", "responsibility": "산출물 도착 후 독립 검사"}, {"name": "운영 준비", "responsibility": "격리·도메인·TWA 검증 준비"}]})
        roles = self.overview(project["id"])["roles"]
        fixture_tasks = []
        for index, (role, status) in enumerate(zip(roles, ("WAITING_CAPACITY", "RUNNING", "WAITING_DEPENDENCY", "READY"))):
            fixture_tasks.append({"id": "fixture-" + str(index), "title": role["responsibility"], "role_id": role["id"], "status": status,
                "stage": "developer" if index != 2 else "reviewer", "dependencies": ["fixture-1"] if index == 2 else [], "flow_task_id": None,
                "worktree": None, "wait_reason": "예시: 공유 계정 한도 대기" if index == 0 else "예시 상태 — 실제 실행 기록 아님",
                "resume_at": self.clock() + 3600 if index == 0 else None, "source": "fixture"})
        project.update(source="fixture", fixture_tasks=fixture_tasks, fixture_reports=[{"id": "fixture-report", "title": "예시 보고서",
            "summary": "PM 대기 중에도 저장된 보고·승인은 조회됩니다. 모델·배포·TWA는 미검증입니다.", "source": "fixture", "created_at": self.clock(), "evidence": []}])
        with self.db:
            self.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), project["id"]))
        self.post_message(project["id"], {"content": "예시: 목표와 역할 구성을 검토해 주세요."})
        self.request_approval(project["id"], {"title": "예시 승인 · 실제 실행 없음", "action": "preview_fixture", "environment": "isolated-fixture",
            "artifact_sha": "0" * 40, "cost_usd": 0, "expires_at": self.clock() + 86400, "impact": "예시 승인 기록만 변경", "rollback": "별도 예시 상태 제거", "verification": "fixture; 실제 CI·실행·배포 미검증"})
        return self.overview(project["id"])["project"]
