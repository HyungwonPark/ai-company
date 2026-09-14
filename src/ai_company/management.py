"""Persistent control-plane records over the existing flow/session SQLite store.

This module records intent and evidence. It never invokes a model or deploys an artifact.
"""

import json
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictInt

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


class DecisionInput(Contract):
    decision: Literal["approve", "reject", "request_changes"]
    comment: str = Field(default="", max_length=8000)
    subject_digest: Digest
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{8,128}$")


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
                   "harness_version": 1, "source": "unverified", "created_at": self.clock()}
        content = json.dumps({"goal": value.goal, "roles": [r.model_dump() for r in value.roles]}, ensure_ascii=False, indent=2)
        harness = {"version": 1, "status": "active", "content": content, "digest": digest(content), "created_at": self.clock()}
        with self.db:
            self.db.execute("INSERT INTO management_projects VALUES (?,?)", (project_id, json.dumps(project)))
            self.db.execute("INSERT INTO management_harnesses VALUES (?,?,?)", (project_id, 1, json.dumps(harness)))
            for role in value.roles:
                item = {"id": uuid4().hex, **role.model_dump()}
                self.db.execute("INSERT INTO management_roles VALUES (?,?,?)", (item["id"], project_id, json.dumps(item)))
            self._event(project_id, "project_created", project_id)
        return project

    def list_projects(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM management_projects ORDER BY rowid")]

    def post_message(self, project_id, value):
        self._project(project_id)
        value = MessageInput.model_validate(value)
        message = {"id": uuid4().hex, "role": "user", "content": value.content,
                   "status": "awaiting_pm", "created_at": self.clock()}
        with self.db:
            self.db.execute("INSERT INTO management_messages VALUES (?,?,?)", (message["id"], project_id, json.dumps(message)))
            self._event(project_id, "pm_request_saved", message["id"])
        return message

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

    def request_approval(self, project_id, value):
        project = self._project(project_id)
        subject = ApprovalSubject.model_validate(value).model_dump(mode="json")
        if subject["expires_at"] <= self.clock():
            raise ManagementError("expired", "Approval must have a future expiry")
        item = {"id": uuid4().hex, "project_id": project_id, **subject, "status": "pending",
                "source": project["source"], "subject_digest": digest({"project_id": project_id, **subject}), "created_at": self.clock()}
        with self.db:
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

    def decide(self, project_id, approval_id, value):
        value = DecisionInput.model_validate(value)
        request_digest = digest({"approval_id": approval_id, **value.model_dump()})
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
                          "worktree": state["specification"]["worktree"], "wait_reason": state.get("reason"), "resume_at": state.get("resume_at"),
                          "active": active or None, "agents": state["specification"].get("agents", []), "handoffs": state.get("executions", []),
                          "repair_reason": state.get("findings", [])})
            reports.append({"id": "flow-" + linked["id"], "title": linked["title"], "summary": state["status"] + ": " + state.get("reason", ""),
                            "source": "system", "created_at": state.get("updated_at", 0), "evidence": self._evidence(state), "verification": state.get("verification")})
        if project["source"] == "fixture":
            tasks = project.get("fixture_tasks", [])
            reports = project.get("fixture_reports", [])
        for role in roles:
            own = [t for t in tasks if t["role_id"] == role["id"]]
            unfinished = [t for t in own if t["status"] not in ("MERGE_READY", "DEMO_READY", "COMPLETE", "FAILED", "BLOCKED")]
            current = next((t for t in unfinished if t.get("active")), next(iter(unfinished), own[-1] if own else None))
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
        # A linked real task is execution evidence, not proof of end-to-end readiness.
        readiness = {"mode": "fixture" if project["source"] == "fixture" else "unverified",
                     "pm": "awaiting_worker" if any(m["status"] == "awaiting_pm" for m in messages) else "unverified", "execution": "unverified"}
        project.pop("fixture_tasks", None); project.pop("fixture_reports", None)
        return {"project": project, "roles": roles, "tasks": tasks, "reports": reports, "approvals": approvals,
                "messages": messages, "harnesses": harnesses, "readiness": readiness}

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
