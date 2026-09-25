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
    idempotency_key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{8,128}$")


class MessageInput(Contract):
    content: Text
    idempotency_key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:-]{8,128}$")


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
    execution_spec_digest: Digest | None = None


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
    PM_GUIDANCE_VERSION = "pm-requirements-v3"
    PLAN_REVIEW_VERSION = "plan-content-review-v2"

    def __init__(self, root: Path, *, clock=time.time, execution_catalog=None):
        self.root, self.clock = Path(root).resolve(), clock
        self.execution_catalog = execution_catalog
        self.queue = SessionQueue(self.root / "sessions", clock=clock)
        self.db = self.queue.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_projects(id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_execution_specs(project_id TEXT NOT NULL, version INTEGER NOT NULL,
                document TEXT NOT NULL, PRIMARY KEY(project_id,version));
            CREATE TABLE IF NOT EXISTS management_execution_spec_registrations(project_id TEXT NOT NULL, principal TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request_digest TEXT NOT NULL, version INTEGER NOT NULL,
                PRIMARY KEY(project_id,principal,idempotency_key));
            CREATE TRIGGER IF NOT EXISTS immutable_execution_spec_update BEFORE UPDATE ON management_execution_specs BEGIN
                SELECT RAISE(ABORT,'Execution specification versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_execution_spec_delete BEFORE DELETE ON management_execution_specs BEGIN
                SELECT RAISE(ABORT,'Execution specification versions are immutable'); END;
            CREATE TABLE IF NOT EXISTS management_project_creations(principal TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL, project_id TEXT NOT NULL, PRIMARY KEY(principal,idempotency_key));
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
            CREATE TABLE IF NOT EXISTS management_message_submissions(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                content_digest TEXT NOT NULL, message_id TEXT NOT NULL, PRIMARY KEY(project_id,idempotency_key));
            CREATE TABLE IF NOT EXISTS management_plans(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_skill_catalogs(configuration_digest TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS management_skill_catalog_active(project_id TEXT PRIMARY KEY, configuration_digest TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS management_plan_revision_once ON management_plans(
                json_extract(document,'$.revision_of'),json_extract(document,'$.auto_revision_attempt'))
                WHERE json_type(document,'$.revision_of')='text';
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

    def register_skill_catalog(self, configuration_digest, entries, *, project_id=None):
        """Worker-owned local catalog; the HTTP API cannot register or alter it."""
        if not re.fullmatch(r"[0-9a-f]{64}", configuration_digest):
            raise ManagementError("catalog_mismatch", "Invalid automation configuration identity")
        from ai_company.skill_selection import load_trusted_catalog
        load_trusted_catalog(entries)
        document = json.dumps(list(entries), sort_keys=True)
        scope = project_id or "*"
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT document FROM management_skill_catalogs WHERE configuration_digest=?",
                                  (configuration_digest,)).fetchone()
            if old and old[0] != document:
                raise ManagementError("catalog_mismatch", "Immutable catalog configuration differs")
            self.db.execute("INSERT OR IGNORE INTO management_skill_catalogs VALUES (?,?)",
                            (configuration_digest, document))
            self.db.execute("INSERT INTO management_skill_catalog_active VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET configuration_digest=excluded.configuration_digest",
                            (scope, configuration_digest))

    def _skill_selection_ready(self, plan):
        selection = plan["content"].get("skill_selection")
        if plan.get("pm_guidance_version") != "pm-requirements-v3":
            return
        if not isinstance(selection, dict):
            raise ManagementError("skill_selection_required", "Current plans need a server-owned skill selection")
        row = self.db.execute("SELECT document FROM management_skill_catalogs WHERE configuration_digest=?",
                              (plan["configuration_digest"],)).fetchone()
        if not row:
            raise ManagementError("skill_catalog_missing", "Trusted skill catalog is unavailable")
        from ai_company.skill_selection import load_trusted_catalog, validate_selection
        from ai_company.skill_catalog import SkillCatalogError
        try:
            trusted = load_trusted_catalog(json.loads(row[0]))
            validate_selection(selection, trusted)
        except SkillCatalogError as error:
            raise ManagementError("skill_selection_changed", str(error)) from error
        if set(selection["roles"]) != {role["key"] for role in plan["content"]["roles"]}:
            raise ManagementError("skill_selection_mismatch", "Skill choices do not match this plan's roles")
        if not selection.get("can_continue"):
            raise ManagementError("skill_required", "A required role skill is not approved or compatible")
        if plan.get("status") != "confirmed":
            active = self.db.execute("SELECT configuration_digest FROM management_skill_catalog_active WHERE project_id IN (?, '*') ORDER BY project_id DESC LIMIT 1",
                                     (plan["project_id"],)).fetchone()
            if not active or active[0] != plan["configuration_digest"]:
                raise ManagementError("skill_catalog_changed", "Current skill catalog differs from the reviewed plan")

    def _project(self, project_id):
        row = self.db.execute("SELECT document FROM management_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise ManagementError("not_found", "Project not found", 404)
        return json.loads(row[0])

    @staticmethod
    def execution_spec_reference(document):
        from ai_company.execution_specs import execution_spec_reference
        return execution_spec_reference(document)

    def execution_specs(self, project_id):
        self._project(project_id)
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT document FROM management_execution_specs WHERE project_id=? ORDER BY version', (project_id,))]

    def get_execution_spec(self, project_id, version):
        from ai_company.execution_specs import execution_spec_binding
        self._project(project_id)
        row = self.db.execute('SELECT document FROM management_execution_specs WHERE project_id=? AND version=?',
                              (project_id, version)).fetchone()
        if not row:
            raise ManagementError('execution_spec_not_found', 'Registered execution specification not found', 404)
        document = json.loads(row[0])
        if document['project_id'] != project_id or document['version'] != version or digest(execution_spec_binding(document)) != document['digest']:
            raise ManagementError('execution_spec_mismatch', 'Registered execution specification identity changed')
        return document

    def execution_config_for(self, project_id, reference):
        from ai_company.execution_specs import ExecutionSpecError, ExecutionSpecReference
        reference = ExecutionSpecReference.model_validate(reference).model_dump()
        document = self.get_execution_spec(project_id, reference['version'])
        if self.execution_spec_reference(document) != reference:
            raise ManagementError('execution_spec_mismatch', 'Execution specification belongs to another project or version')
        if self.execution_catalog is None:
            raise ManagementError('execution_catalog_unavailable', 'Trusted execution catalog is not configured')
        try:
            config = self.execution_catalog.resolve(document['selection'])
        except ExecutionSpecError as error:
            raise ManagementError('execution_catalog_mismatch', str(error)) from error
        if digest(config) != document['configuration_digest']:
            raise ManagementError('execution_spec_mismatch', 'Resolved execution configuration changed')
        return config

    def register_execution_spec(self, project_id, value, *, principal='local-master'):
        from ai_company.execution_specs import ExecutionSpecError, ExecutionSpecRegistration, execution_spec_binding
        registration = ExecutionSpecRegistration.model_validate(value)
        if not isinstance(principal, str) or not principal or len(principal) > 200:
            raise ManagementError('invalid_principal', 'A stable authenticated principal is required', 400)
        request_digest = digest(registration)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            project = self._project(project_id)
            prior = self.db.execute('SELECT request_digest,version FROM management_execution_spec_registrations '
                                    'WHERE project_id=? AND principal=? AND idempotency_key=?',
                                    (project_id, principal, registration.idempotency_key)).fetchone()
            if prior:
                if prior[0] != request_digest:
                    raise ManagementError('idempotency_conflict', 'This key belongs to another specification registration')
                return self.get_execution_spec(project_id, prior[1])
            current = self.db.execute('SELECT COALESCE(MAX(version),0) FROM management_execution_specs WHERE project_id=?',
                                      (project_id,)).fetchone()[0]
            if registration.base_version != current:
                raise ManagementError('stale_execution_spec', 'Execution specification changed; review the current version')
            if self.execution_catalog is None:
                raise ManagementError('execution_catalog_unavailable', 'Trusted execution catalog is not configured')
            try:
                config = self.execution_catalog.resolve(registration.selection)
            except ExecutionSpecError as error:
                raise ManagementError('execution_spec_invalid', str(error), 400) from error
            selection = registration.selection.model_dump(mode='json', exclude_none=True)
            document = dict(id=uuid4().hex, project_id=project_id, version=current + 1, selection=selection,
                            configuration_digest=digest(config),
                            resolved=self.execution_catalog.summary(config, role_candidates=registration.selection.role_candidates),
                            catalog_id=registration.selection.catalog_id, catalog_digest=registration.selection.catalog_digest,
                            created_at=self.clock(), created_by=principal)
            document['digest'] = digest(execution_spec_binding(document))
            self.db.execute('INSERT INTO management_execution_specs VALUES (?,?,?)', (project_id, document['version'], json.dumps(document)))
            self.db.execute('INSERT INTO management_execution_spec_registrations VALUES (?,?,?,?,?)',
                            (project_id, principal, registration.idempotency_key, request_digest, document['version']))
            project['execution_spec'] = self.execution_spec_reference(document)
            self.db.execute('UPDATE management_projects SET document=? WHERE id=?', (json.dumps(project), project_id))
            self._event(project_id, 'execution_spec_registered', document['id'])
        return document

    def _validate_plan_execution_spec(self, project_id, reference, content, configuration_digest):
        from ai_company.execution_specs import path_subset
        config = self.execution_config_for(project_id, reference)
        if digest(config) != configuration_digest:
            raise ManagementError('configuration_mismatch', 'Plan configuration differs from its registered execution specification')
        if any(not path_subset(path, config.allowed_paths) for role in content['roles'] for path in role['allowed_paths']):
            raise ManagementError('execution_spec_mismatch', 'Proposed role paths exceed the registered execution specification')
        document = self.get_execution_spec(project_id, reference['version'])
        pools = document['selection'].get('role_candidates', {})
        if pools and set(pools) != {role['key'] for role in content['roles']}:
            raise ManagementError('execution_spec_mismatch', 'Logical role pools must match the exact proposed team')
        return config

    def create_project(self, value, *, principal="local-master"):
        # principal is supplied by the authenticated HTTP adapter, never the JSON body.
        value = ProjectInput.model_validate(value)
        request_digest = digest(value.model_dump(mode="json", exclude={"idempotency_key"}))
        project_id = uuid4().hex
        project = {"id": project_id, "name": value.name, "goal": value.goal, "status": "PLANNING",
                   "harness_version": 1, "request_revision": 0, "source": "unverified", "created_at": self.clock()}
        content = json.dumps({"goal": value.goal, "roles": [r.model_dump() for r in value.roles]}, ensure_ascii=False, indent=2)
        harness = {"version": 1, "status": "active", "content": content, "digest": digest(content), "created_at": self.clock()}
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if value.idempotency_key is not None:
                prior = self.db.execute("SELECT request_digest,project_id FROM management_project_creations "
                                        "WHERE principal=? AND idempotency_key=?",
                                        (principal, value.idempotency_key)).fetchone()
                if prior:
                    if prior[0] != request_digest:
                        raise ManagementError("idempotency_conflict", "이 생성 요청은 다른 내용으로 이미 사용됐습니다. 원래 입력으로 결과를 확인하세요.")
                    return self._project(prior[1])
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
            if value.idempotency_key is not None:
                self.db.execute("INSERT INTO management_project_creations VALUES (?,?,?,?)",
                                (principal, value.idempotency_key, request_digest, project_id))
        return project

    def list_projects(self, *, summary=False):
        if not summary:
            return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_projects ORDER BY rowid")]
        # One read snapshot, using recorded activity and currently actionable approvals.
        # Expiry is projected; viewing a list never writes a decision or advances work.
        rows = self.db.execute("""WITH activity AS (
                SELECT project_id,MAX(json_extract(document,'$.created_at')) AS updated_at
                FROM management_events GROUP BY project_id
            ), pending AS (
                SELECT project_id,COUNT(*) AS count FROM management_approvals
                WHERE json_extract(document,'$.status')='pending' AND json_extract(document,'$.expires_at')>?
                GROUP BY project_id
            ) SELECT p.document,a.updated_at,COALESCE(pending.count,0),
                (SELECT json_object('id',r.id,'state',json_extract(r.document,'$.state'),
                    'created_at',json_extract(r.document,'$.created_at'),'mode',json_extract(r.document,'$.mode'))
                 FROM management_runs r WHERE r.project_id=p.id
                 ORDER BY json_extract(r.document,'$.created_at') DESC,r.id DESC LIMIT 1),
                (SELECT json_object('id',m.message_id,'state',json_extract(m.document,'$.state'),
                    'created_at',json_extract(m.document,'$.created_at'),'mode',json_extract(m.document,'$.mode'),
                    'execution',CASE WHEN json_type(m.document,'$.execution')='object' THEN
                        json_object('status',json_extract(m.document,'$.execution.status'),
                            'reason',json_extract(m.document,'$.execution.reason'),
                            'resume_at',json_extract(m.document,'$.execution.resume_at')) ELSE NULL END)
                 FROM management_pm_requests m WHERE m.project_id=p.id
                 ORDER BY json_extract(m.document,'$.created_at') DESC,m.message_id DESC LIMIT 1)
              FROM management_projects p LEFT JOIN activity a ON a.project_id=p.id
              LEFT JOIN pending ON pending.project_id=p.id ORDER BY p.rowid""", (self.clock(),))
        result = []
        for row in rows:
            project = json.loads(row[0])
            result.append({**project, "updated_at": max(project["created_at"], row[1] or project["created_at"]),
                           "pending_approval_count": row[2],
                           "recent_run": json.loads(row[3]) if row[3] else None,
                           "recent_pm_request": json.loads(row[4]) if row[4] else None})
        return result

    def post_message(self, project_id, value):
        value = MessageInput.model_validate(value)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            project = self._project(project_id)
            if value.idempotency_key:
                prior = self.db.execute("SELECT content_digest,message_id FROM management_message_submissions "
                                        "WHERE project_id=? AND idempotency_key=?",
                                        (project_id, value.idempotency_key)).fetchone()
                if prior:
                    if prior[0] != digest(value.content):
                        raise ManagementError("idempotency_conflict", "이 전송 번호는 다른 답변에 이미 사용됐습니다")
                    row = self.db.execute("SELECT document FROM management_messages WHERE id=?", (prior[1],)).fetchone()
                    return json.loads(row[0])
            message = self._append_pm_request(project, value)
            if value.idempotency_key:
                self.db.execute("INSERT INTO management_message_submissions VALUES (?,?,?,?)",
                                (project_id, value.idempotency_key, digest(value.content), message["id"]))
            return message

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
            if plan.get('review'):
                previous['review'] = plan['review']
            if len(json.dumps(plan['content'], ensure_ascii=False)) <= 20000:
                previous['content'] = plan['content']
            else:
                previous['content_omitted'] = True
        feedback_row = self.db.execute("SELECT document FROM management_pm_requests WHERE project_id=? "
                                       "AND json_type(document,'$.requirements_feedback')='object' "
                                       "ORDER BY rowid DESC LIMIT 1", (project_id,)).fetchone()
        prior_feedback = None
        if feedback_row:
            prior = json.loads(feedback_row[0])
            candidate = {'request_id': prior['request_id'], 'requirements': prior['requirements_feedback']}
            if len(json.dumps(candidate, ensure_ascii=False)) <= 12000:
                prior_feedback = candidate
        return {'messages': list(reversed(messages)), 'previous_proposal': previous,
                'previous_requirements_feedback': prior_feedback,
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
                       "requirements_contract_version": 2,
                       "pm_guidance_version": self.PM_GUIDANCE_VERSION,
                       "plan_review_version": self.PLAN_REVIEW_VERSION,
                       "state": "pending", "request_revision": revision, "base_harness_version": project["harness_version"],
                       "base_harness_digest": digest(harness["content"]), "goal_digest": digest(project["goal"]),
                       "goal": project["goal"], "content": value.content, "source": project["source"],
                       "conversation_context": self._conversation_context(project_id),
                       "created_at": self.clock(), "updated_at": self.clock(), "execution": None, "plan_id": None,
                       "configuration_digest": None, "mode": None}
        if project.get("execution_spec") is not None:
            request["execution_spec"] = copy.deepcopy(project["execution_spec"])
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
                         "base_harness_digest", "goal_digest", "goal", "content", "source", "created_at", "conversation_context", "execution_spec", "requirements_contract_version", "pm_guidance_version", "plan_review_version")
            if any(document.get(key) != previous.get(key) for key in immutable):
                raise ManagementError("request_mismatch", "PM request snapshot is immutable")
            for key in ("configuration_digest", "mode"):
                if previous.get(key) is not None and document.get(key) != previous[key]:
                    raise ManagementError("configuration_mismatch", "PM execution configuration is immutable after claim")
            if document.get("configuration_digest") is not None or document.get("mode") is not None:
                if not re.fullmatch(r"[0-9a-f]{64}", str(document.get("configuration_digest"))) or document.get("mode") not in ("live", "fixture"):
                    raise ManagementError("configuration_missing", "Worker must bind a valid configuration digest and mode")
            if (previous.get('execution_spec') is not None and previous.get('configuration_digest') is None
                    and document.get('configuration_digest') is not None):
                config = self.execution_config_for(previous['project_id'], previous['execution_spec'])
                if digest(config) != document['configuration_digest'] or config.mode != document['mode']:
                    raise ManagementError('configuration_mismatch', 'PM claim must use its registered execution configuration')
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
        result = {key: plan[key] for key in fields}
        if plan.get("execution_spec") is not None:
            result["execution_spec"] = plan["execution_spec"]
        if plan.get("contract_version") == 2:
            result["contract_version"] = 2
            result["pm_guidance_version"] = plan["pm_guidance_version"]
            result["plan_review_version"] = plan["plan_review_version"]
        if plan.get("revision_of") is not None:
            result["revision_of"] = plan["revision_of"]
            result["auto_revision_attempt"] = plan["auto_revision_attempt"]
        return result

    def _requirements_ready(self, plan):
        if plan.get("contract_version") != 2:
            return
        from ai_company.automation_contracts import PMPlanContent
        content = PMPlanContent.model_validate(plan["content"])
        spec = content.requirements_review
        if (spec is None or spec.version != 2 or spec.revision != plan["request_revision"]
                or spec.goal_digest != plan["goal_digest"]):
            raise ManagementError("requirements_mismatch", "Requirements do not bind this goal and revision")
        roles = {role.key for role in content.roles}
        if any(not set(item.role_keys) <= roles for item in spec.requirements):
            raise ManagementError("requirements_unmapped", "Every requirement needs a valid responsible role")
        if any(item.status == "open" for item in spec.questions):
            raise ManagementError("answer_required", "A material decision still needs an answer")
        if plan.get("pm_guidance_version") == "pm-requirements-v3":
            request = self.get_pm_request(plan["request_id"])
            prior = (request.get("conversation_context") or {}).get("previous_requirements_feedback") or {}
            previous_questions = (prior.get("requirements") or {}).get("questions", [])
            prior_row = self.db.execute("SELECT rowid FROM management_messages WHERE id=? AND project_id=?",
                                        (prior.get("request_id"), plan["project_id"])).fetchone()
            for previous in previous_questions:
                if previous.get("status") != "open":
                    continue
                resolved = next((item for item in spec.questions if item.id == previous.get("id")), None)
                if resolved is None or resolved.status != "answered" or not resolved.answer_message_id:
                    raise ManagementError("answer_required", "A previous material question needs an explicit master answer")
                answer_row = self.db.execute("SELECT rowid FROM management_messages WHERE id=? AND project_id=?",
                                             (resolved.answer_message_id, plan["project_id"])).fetchone()
                if prior_row is None or answer_row is None or answer_row[0] <= prior_row[0]:
                    raise ManagementError("answer_required", "The answer must follow the recorded material question")
            for item in spec.questions:
                if item.status == "assumed" or not item.answer_message_id:
                    raise ManagementError("answer_required", "A material decision needs a recorded master answer")
                row = self.db.execute("SELECT document FROM management_messages WHERE id=? AND project_id=?",
                                      (item.answer_message_id, plan["project_id"])).fetchone()
                answer = json.loads(row[0]) if row else {}
                if answer.get("role") != "user":
                    raise ManagementError("answer_required", "A material decision needs a project-local master answer")
        if any(item.blocking and item.status != "resolved" for item in spec.findings):
            raise ManagementError("blocking_finding", "A blocking plan finding is unresolved")

    def assert_plan_ready(self, plan):
        if digest(self._plan_binding(plan)) != plan["digest"]:
            raise ManagementError("plan_mismatch", "Plan content or version differs from its digest")
        if plan.get("contract_version") == 2 and plan.get("status") != "confirmed":
            request = self.get_pm_request(plan["request_id"])
            if not self._request_current(request, self._project(plan["project_id"])):
                raise ManagementError("review_policy_changed", "This unconfirmed plan needs review under the current policy")
        self._requirements_ready(plan)
        self._skill_selection_ready(plan)
        if plan.get("contract_version") == 2:
            review = plan.get("review")
            if (not isinstance(review, dict) or review.get("verdict") != "PASS"
                    or review.get("plan_digest") != plan["digest"]
                    or review.get("requirements_revision") != plan["request_revision"]
                    or review.get("requirements_digest") != digest(plan["content"]["requirements_review"])
                    or review.get("review_version") != plan["plan_review_version"]
                    or review.get("pm_session_id") != (plan.get("evidence") or {}).get("session_id")
                    or review.get("pm_session_id") == review.get("session_id")
                    or not review.get("session_id") or review.get("findings")):
                raise ManagementError("plan_review_required", "This exact plan needs a passing independent content review")
            self._review_fact(plan, review)
            readiness = plan.get("readiness") or {}
            if readiness != {"state": "eligible_for_master_confirmation", "source": "server",
                    "plan_digest": plan["digest"], "requirements_revision": plan["request_revision"],
                    "requirements_digest": digest(plan["content"]["requirements_review"]),
                    "review_task_id": review["task_id"], "review_session_id": review["session_id"]}:
                raise ManagementError("plan_readiness_required", "Server readiness does not bind this reviewed plan")

    def _review_fact(self, plan, review):
        """Match the review receipt to the durable Dispatcher and session facts."""
        row = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (review.get("task_id"),)).fetchone()
        state = json.loads(row[0]) if row else {}
        specification = state.get("specification") or {}
        from ai_company.flow_contracts import FlowSpec
        try:
            spec_valid = digest(FlowSpec.model_validate(specification)) == state.get("spec_digest")
        except ValueError:
            spec_valid = False
        context = specification.get("plan") or {}
        request = self.get_pm_request(plan["request_id"])
        executions = state.get("executions") or []
        job_row = self.db.execute("SELECT document FROM session_jobs WHERE job_id=?",
                                  (executions[-1]["job_id"],)).fetchone() if executions and executions[-1].get("job_id") else None
        job = json.loads(job_row[0]) if job_row else {}
        report = (state.get("reviews") or {}).get("reviewer") or {}
        expected_pm = {"provider": (plan.get("evidence") or {}).get("provider") or "codex",
                       "session_id": review["pm_session_id"]}
        expected_status = "PLAN_REVIEWED" if review.get("verdict") == "PASS" else "NEEDS_PLAN_REVISION"
        if (not spec_valid or state.get("task_id") != review.get("task_id") or state.get("status") != expected_status
                or specification.get("execution_scope") != "plan_review"
                or specification.get("approved_plan") is not False
                or expected_pm not in specification.get("inherited_pm_sessions", [])
                or context.get("plan_digest") != plan["digest"]
                or context.get("requirements_revision") != plan["request_revision"]
                or context.get("review_version") != plan["plan_review_version"]
                or context.get("plan") != plan["content"]
                or context.get("goal") != request["goal"]
                or context.get("master_message") != request["content"]
                or context.get("conversation_context") != request["conversation_context"]
                or job.get("status") != "SESSION_COMPLETED"
                or job.get("session_id") != review["session_id"]
                or (job.get("provider"), job.get("session_id")) == (expected_pm["provider"], expected_pm["session_id"])
                or report.get("verdict") != review["verdict"]
                or report.get("findings") != review["findings"]
                or report.get("summary") != review["summary"]
                or report.get("revision_route") != review.get("revision_route")):
            raise ManagementError("plan_review_mismatch", "Stored review is not the result of this exact independent run")

    def get_plan(self, project_id, plan_id):
        row = self.db.execute("SELECT document FROM management_plans WHERE id=? AND project_id=?", (plan_id, project_id)).fetchone()
        if not row:
            raise ManagementError("not_found", "Plan not found", 404)
        return json.loads(row[0])

    def _request_current(self, request, project):
        row = self.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?",
                              (project["id"], project["harness_version"])).fetchone()
        harness = json.loads(row[0]) if row else {}
        return (project.get("execution_spec") == request.get("execution_spec")
                and project.get("request_revision", 0) == request["request_revision"]
                and digest(project["goal"]) == request["goal_digest"]
                and project["harness_version"] == request["base_harness_version"]
                and digest(harness.get("content")) == request["base_harness_digest"]
                and (request.get("requirements_contract_version") != 2 or
                     (request.get("pm_guidance_version") == self.PM_GUIDANCE_VERSION and
                      request.get("plan_review_version") == self.PLAN_REVIEW_VERSION)))

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
            if request.get("requirements_contract_version") == 2:
                check = {"content": content, "contract_version": 2, "request_id": request_id,
                         "request_revision": request["request_revision"], "goal_digest": request["goal_digest"],
                         "project_id": request["project_id"], "pm_guidance_version": request.get("pm_guidance_version")}
                self._requirements_ready(check)
                if not isinstance(evidence, dict) or not evidence.get("session_id"):
                    raise ManagementError("pm_evidence_required", "New plans require the PM session identity")
            current = self._request_current(request, project)
            plan = {"id": uuid4().hex, "request_id": request_id, "project_id": project["id"],
                    "status": ("reviewing" if request.get("requirements_contract_version") == 2 else "proposed") if current else "stale", "content": content,
                    **{key: request[key] for key in ("request_revision", "base_harness_version", "base_harness_digest", "goal_digest")},
                    "created_at": self.clock(), "evidence": evidence, "source": (evidence or {}).get("source", request["source"]),
                    "configuration_digest": request["configuration_digest"], "mode": request["mode"]}
            if request.get("requirements_contract_version") == 2:
                plan.update(contract_version=2, pm_guidance_version=request["pm_guidance_version"],
                            plan_review_version=request["plan_review_version"])
            if request.get('execution_spec') is not None:
                self._validate_plan_execution_spec(project['id'], request['execution_spec'], content, request['configuration_digest'])
                plan['execution_spec'] = copy.deepcopy(request['execution_spec'])
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

    def complete_pm_revision(self, source_plan_id, plan_content, *, evidence):
        """Keep one technical repair under the original master request and budget."""
        from ai_company.automation_contracts import PMPlanContent
        content = PMPlanContent.model_validate(plan_content).model_dump(mode="json")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            source_row = self.db.execute("SELECT document FROM management_plans WHERE id=?", (source_plan_id,)).fetchone()
            if not source_row:
                raise ManagementError("not_found", "Source plan not found", 404)
            source = json.loads(source_row[0])
            attempt = source.get("auto_revision_attempt", 0) + 1
            prior = self.db.execute("SELECT document FROM management_plans WHERE json_extract(document,'$.revision_of')=? "
                                    "AND json_extract(document,'$.auto_revision_attempt')=?",
                                    (source_plan_id, attempt)).fetchone()
            if prior:
                result = json.loads(prior[0])
                if result["content"] != content or result["evidence"] != evidence:
                    raise ManagementError("result_conflict", "Technical repair already has a different result")
                return result
            if (source["status"] != "needs_revision" or source.get("revision_action") != "automatic"
                    or attempt > 2 or source.get("contract_version") != 2):
                raise ManagementError("revision_not_allowed", "This plan cannot be repaired automatically")
            request = self.get_pm_request(source["request_id"])
            if not self._request_current(request, self._project(source["project_id"])):
                raise ManagementError("stale_plan", "A changed goal or review policy needs a fresh plan")
            previous = PMPlanContent.model_validate(source["content"])
            revised = PMPlanContent.model_validate(content)
            old_requirements, new_requirements = previous.requirements_review, revised.requirements_review
            if (previous.execution_spec_proposal != revised.execution_spec_proposal
                    or previous.skill_selection != revised.skill_selection
                    or [(r.key, r.goal, r.responsibility, r.allowed_paths, r.depends_on,
                         r.required_capabilities, r.skill_required) for r in previous.roles]
                    != [(r.key, r.goal, r.responsibility, r.allowed_paths, r.depends_on,
                         r.required_capabilities, r.skill_required) for r in revised.roles]
                    or old_requirements is None or new_requirements is None
                    or (old_requirements.goal_digest, old_requirements.scope, old_requirements.exclusions,
                        old_requirements.assumptions, old_requirements.questions)
                    != (new_requirements.goal_digest, new_requirements.scope, new_requirements.exclusions,
                        new_requirements.assumptions, new_requirements.questions)
                    or [(r.id, r.source, r.role_keys) for r in old_requirements.requirements]
                    != [(r.id, r.source, r.role_keys) for r in new_requirements.requirements]):
                raise ManagementError("revision_scope_changed", "Automatic repair changed a master decision or role ownership")
            self._requirements_ready({**source, "content": content})
            task_id = "pm-revise-" + digest([source_plan_id, attempt])[:48]
            if not isinstance(evidence, dict) or evidence.get("task_id") != task_id or not evidence.get("session_id"):
                raise ManagementError("pm_evidence_required", "Technical repair needs its exact PM task and session")
            task_row = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (task_id,)).fetchone()
            state = json.loads(task_row[0]) if task_row else {}
            specification = state.get("specification") or {}
            from ai_company.flow_contracts import FlowSpec
            try:
                spec_valid = digest(FlowSpec.model_validate(specification)) == state.get("spec_digest")
            except ValueError:
                spec_valid = False
            facts = specification.get("plan") or {}
            result = PMPlanContent.model_validate(state.get("plan") or {}).model_dump(mode="json") if state.get("plan") else {}
            if previous.skill_selection is not None:
                if result.get("skill_selection") not in (None, source["content"]["skill_selection"]):
                    raise ManagementError("revision_scope_changed", "PM result changed the selected skill bundle")
                result["skill_selection"] = source["content"]["skill_selection"]
            executions = state.get("executions") or []
            job_row = self.db.execute("SELECT document FROM session_jobs WHERE job_id=?",
                (executions[-1]["job_id"],)).fetchone() if executions and executions[-1].get("job_id") else None
            job = json.loads(job_row[0]) if job_row else {}
            if (not spec_valid or state.get("status") != "PLAN_READY" or state.get("task_id") != task_id
                    or specification.get("execution_scope") != "planning"
                    or facts.get("source_plan_id") != source_plan_id or facts.get("auto_revision_attempt") != attempt
                    or facts.get("goal_digest") != source["goal_digest"] or facts.get("request_revision") != source["request_revision"]
                    or result != content or job.get("status") != "SESSION_COMPLETED"
                    or job.get("session_id") != evidence["session_id"] or job.get("provider") != evidence.get("provider")
                    or state.get("snapshot", {}).get("head_commit") != evidence.get("candidate_sha")):
                raise ManagementError("pm_evidence_mismatch", "Technical repair is not the stored PM execution result")
            plan = {key: copy.deepcopy(source[key]) for key in (
                "request_id", "project_id", "request_revision", "base_harness_version", "base_harness_digest",
                "goal_digest", "configuration_digest", "mode", "source", "contract_version",
                "pm_guidance_version", "plan_review_version")}
            plan.update(id=uuid4().hex, status="reviewing", content=content, created_at=self.clock(),
                        evidence=evidence, revision_of=source_plan_id, auto_revision_attempt=attempt)
            if source.get("execution_spec") is not None:
                self._validate_plan_execution_spec(source["project_id"], source["execution_spec"], content,
                                                   source["configuration_digest"])
                plan["execution_spec"] = copy.deepcopy(source["execution_spec"])
            plan["digest"] = digest(self._plan_binding(plan))
            source["status"] = "superseded"
            source["revision_result_id"] = plan["id"]
            assistant = {"id": uuid4().hex, "role": "assistant", "content": content["summary"],
                         "status": "reviewing", "plan_id": plan["id"], "request_id": source["request_id"],
                         "created_at": self.clock(), "evidence": evidence, "source": plan["source"]}
            self.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(source), source_plan_id))
            self.db.execute("INSERT INTO management_plans VALUES (?,?,?)", (plan["id"], plan["project_id"], json.dumps(plan)))
            self.db.execute("INSERT INTO management_messages VALUES (?,?,?)", (assistant["id"], plan["project_id"], json.dumps(assistant)))
            self._event(plan["project_id"], "pm_plan_revised", plan["id"])
        return plan

    def complete_plan_review(self, project_id, plan_id, review):
        """Worker-only receipt for one separately executed reviewer session."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            plan = self.get_plan(project_id, plan_id)
            if plan.get("contract_version") != 2:
                raise ManagementError("plan_review_mismatch", "Legacy plans do not use content review")
            if plan.get("review"):
                if plan["review"] != review:
                    raise ManagementError("plan_review_conflict", "A different review is already stored")
                return plan
            if plan["status"] != "reviewing" or not self._request_current(self.get_pm_request(plan["request_id"]), self._project(project_id)):
                raise ManagementError("stale_plan", "Only the current exact plan can finish review")
            if (review.get("plan_digest") != plan["digest"]
                    or review.get("requirements_revision") != plan["request_revision"]
                    or review.get("requirements_digest") != digest(plan["content"]["requirements_review"])
                    or review.get("review_version") != plan["plan_review_version"]
                    or review.get("pm_session_id") != (plan.get("evidence") or {}).get("session_id")
                    or not review.get("session_id") or review["session_id"] == review["pm_session_id"]
                    or review.get("verdict") not in ("PASS", "REVISE", "BLOCK")
                    or review.get("verdict") == "REVISE" and not review.get("findings")
                    or review.get("verdict") == "PASS" and review.get("findings")
                    or review.get("revision_route") not in (None, "technical", "master_decision")
                    or review.get("revision_route") is not None and review.get("verdict") != "REVISE"):
                raise ManagementError("plan_review_mismatch", "Review does not bind this plan and separate session")
            self._review_fact(plan, review)
            plan["review"] = copy.deepcopy(review)
            plan["status"] = "proposed" if review["verdict"] == "PASS" else "needs_revision"
            if review["verdict"] == "REVISE":
                attempt = plan.get("auto_revision_attempt", 0) + 1
                plan["revision_action"] = ("automatic" if review.get("revision_route") == "technical" and attempt <= 2
                                           else "limit_reached" if review.get("revision_route") == "technical"
                                           else "master_decision")
            plan.pop("review_problem", None)
            if review["verdict"] == "PASS":
                plan["readiness"] = {"state": "eligible_for_master_confirmation", "source": "server",
                    "plan_digest": plan["digest"], "requirements_revision": plan["request_revision"],
                    "requirements_digest": digest(plan["content"]["requirements_review"]),
                    "review_task_id": review["task_id"], "review_session_id": review["session_id"]}
            self.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(plan), plan_id))
            self._event(project_id, "plan_review_completed", plan_id)
        return plan

    def note_plan_review_problem(self, project_id, plan_id, reason):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            plan = self.get_plan(project_id, plan_id)
            if plan["status"] == "reviewing" and plan.get("review_problem") != reason:
                plan["review_problem"] = reason
                self.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(plan), plan_id))
                self._event(project_id, "plan_review_waiting", plan_id)
        return plan

    def note_pm_revision_problem(self, project_id, plan_id, reason, *, decision_required=False, feedback=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            plan = self.get_plan(project_id, plan_id)
            if plan["status"] != "needs_revision":
                return plan
            action = "master_decision" if decision_required else "blocked"
            if (plan.get("revision_action"), plan.get("revision_problem"), plan.get("revision_feedback")) != (action, reason, feedback):
                plan.update(revision_action=action, revision_problem=reason)
                if feedback is not None:
                    plan["revision_feedback"] = copy.deepcopy(feedback)
                self.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(plan), plan_id))
                self._event(project_id, "pm_revision_waiting", plan_id)
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
            questions = (response.get('requirements_feedback') or {}).get('questions', [])
            feedback = response.get('requirements_feedback')
            if feedback and (feedback['version'] != request.get('requirements_contract_version')
                             or feedback['revision'] != request['request_revision']
                             or feedback['goal_digest'] != request['goal_digest']):
                raise ManagementError('requirements_mismatch', 'PM questions do not bind this goal and revision')
            awaiting_answer = any(item.get('status') == 'open' for item in questions)
            request.update(state=('answer_needed' if awaiting_answer else 'blocked') if current else 'stale',
                           reason=reason, updated_at=self.clock())
            if response.get('requirements_feedback') is not None:
                request['requirements_feedback'] = response['requirements_feedback']
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
                         "parent_run_id", "delegation_id", "delegation_digest", "execution_spec")
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
            self.assert_plan_ready(plan)
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
            if parent.get('execution_spec') is not None:
                run['execution_spec'] = copy.deepcopy(parent['execution_spec'])
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
            self.assert_plan_ready(plan)
            if plan["status"] == "confirmed":
                raise ManagementError("already_confirmed", "Plan already has a run")
            request = self.get_pm_request(plan["request_id"])
            if (plan["status"] != "proposed" or value.base_harness_version != plan["base_harness_version"]
                    or not self._request_current(request, project)):
                raise ManagementError("stale_plan", "Goal, conversation or active harness changed; request a fresh plan")
            if plan['content'].get('execution_spec_proposal') is not None:
                raise ManagementError('execution_spec_required', 'Register the proposed execution specification and request a fresh plan before confirming')
            if plan.get('execution_spec') is not None:
                if value.execution_spec_digest != plan['execution_spec']['digest']:
                    raise ManagementError('execution_spec_mismatch', 'Confirm the exact displayed execution specification digest')
                if request.get('execution_spec') != plan['execution_spec']:
                    raise ManagementError('execution_spec_mismatch', 'Plan and PM request execution versions differ')
                self._validate_plan_execution_spec(project_id, plan['execution_spec'], plan['content'], plan['configuration_digest'])
            elif value.execution_spec_digest is not None:
                raise ManagementError('execution_spec_mismatch', 'This legacy plan has no execution specification')
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
            if plan.get("execution_spec") is not None:
                run["execution_spec"] = copy.deepcopy(plan["execution_spec"])
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
            role_row = self.db.execute("SELECT document FROM management_roles WHERE id=? AND project_id=?", (role_id, project_id)).fetchone()
            if not role_row:
                raise ManagementError("not_found", "Role not found", 404)
            row = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (flow_task_id,)).fetchone()
            if not row:
                raise ManagementError("not_found", "Submit the task to Dispatcher before linking", 404)
            state = json.loads(row[0])
            item = {"id": flow_task_id, "flow_task_id": flow_task_id, "role_id": role_id,
                    "title": title or state["specification"]["task"]["goal"], "harness_version": project["harness_version"]}
            role = json.loads(role_row[0])
            if role.get('plan_id'):
                plan = self.get_plan(project_id, role['plan_id'])
                if plan.get('run_id'):
                    run = self.get_run(plan['run_id'])
                    if run['project_id'] != project_id or role_id not in run['role_ids'].values():
                        raise ManagementError('task_owned', 'Role and confirmed run ownership differ')
                    item['harness_version'] = run['harness_version']
                    if run.get('execution_spec') is not None:
                        context = state['specification'].get('plan', {})
                        if context.get('execution_spec') != run['execution_spec'] or context.get('plan_digest') != run['plan_digest']:
                            raise ManagementError('execution_spec_mismatch', 'Flow task differs from the role execution specification')
                        item['execution_spec'] = copy.deepcopy(run['execution_spec'])
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
            skill = state["specification"].get("plan", {}).get("skill_delivery")
            skill_display = None
            if isinstance(skill, dict) and skill.get("task_id") == state["task_id"]:
                receipts = [json.loads(item[0]) for item in self.db.execute(
                    "SELECT document FROM guidance_deliveries WHERE json_extract(document,'$.delivery_id')=? ORDER BY rowid",
                    (skill.get("delivery_id"),))]
                phases = {item.get("phase") for item in receipts}
                phase = ("process_started" if "skill_process_started" in phases else
                         "executor_returned" if "skill_executor_returned" in phases else
                         "prompt_prepared" if "skill_prompt_prepared" in phases else "submitted")
                skill_display = {"delivery_id": skill["delivery_id"], "selection_digest": skill["selection_digest"],
                                 "role_key": skill["role_key"], "documents": skill["documents"],
                                 "phase": phase, "mode": state["specification"].get("mode")}
            tasks.append({**linked, "status": effective_status, "stage": state["stage"], "dependencies": state["specification"].get("dependencies", []),
                          "worktree": state["specification"]["worktree"],
                          "execution_scope": state["specification"].get("execution_scope", "full"),
                          "created_at": state.get("created_at", 0), "revision": state["specification"].get("plan", {}).get("revision", 0),
                          "generation": state.get("generation"), "candidate_sha": state.get("snapshot", {}).get("head_commit"),
                          "wait_reason": (job or {}).get("reason") if job and job.get("status", "").startswith("WAITING") else state.get("reason"),
                          "resume_at": (job or {}).get("resume_at") if job and job.get("status", "").startswith("WAITING") else state.get("resume_at"),
                          "active": active or None, "agents": state["specification"].get("agents", []), "handoffs": state.get("executions", []),
                          "repair_reason": state.get("findings", []), **({"skill_delivery": skill_display} if skill_display else {})})
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
                plan["stale_reason"] = "검수 기준이나 목표가 변경되었습니다. 새 계획을 요청해 주세요."
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
        if project.get("execution_spec") is not None:
            overview["execution_specs"] = self.execution_specs(project_id)
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
        from ai_company.workspace_graph import workspace_graph
        overview["workspace_graph"] = workspace_graph(self.db, overview, observed_at=self.clock())
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
