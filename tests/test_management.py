import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.dispatcher import Dispatcher
from ai_company.management import ManagementError, ManagementStore


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000
        self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.project = self.store.create_project({"name": "Project", "goal": "Build safely", "roles": [{"name": "Backend", "responsibility": "API"}, {"name": "Review", "responsibility": "Independent review"}]})
        self.pid = self.project["id"]
        self.role = self.store.overview(self.pid)["roles"][0]["id"]
        self.subject = {"title": "Preview change", "action": "preview", "environment": "isolated", "artifact_sha": "a" * 40,
                        "cost_usd": 2.0, "expires_at": 2000, "impact": "Preview only", "rollback": "Stop preview", "verification": "CI at artifact a"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def decision(self, approval, **changes):
        return {"decision": "approve", "comment": "Reviewed", "subject_digest": approval["subject_digest"], "idempotency_key": "decision-001", **changes}

    def test_pm_wait_persists_while_reports_approvals_remain_available(self):
        message = self.store.post_message(self.pid, {"content": "Please plan the next work"})
        approval = self.store.request_approval(self.pid, self.subject)
        self.store.close(); self.store = ManagementStore(self.root, clock=lambda: self.now)
        state = self.store.overview(self.pid)
        self.assertEqual(state["messages"], [message])
        self.assertEqual(state["readiness"]["pm"], "awaiting_worker")
        self.assertEqual(message["role"], "user")
        self.assertEqual(state["readiness"]["mode"], "unverified")
        self.assertEqual(self.store.decide(self.pid, approval["id"], self.decision(approval))["status"], "approved")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM session_jobs").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM flow_tasks").fetchone()[0], 0)

    def test_link_reuses_dispatcher_store_and_reflects_wait_and_handoff(self):
        dispatcher = Dispatcher(self.root, clock=lambda: self.now)
        state = {"task_id": "work-a", "specification": {"task": {"goal": "API work"}, "worktree": "/isolated/api", "dependencies": ["work-before"],
                  "agents": [{"agent_id": "astra", "model": "requested-astra"}]}, "status": "WAITING_CAPACITY", "stage": "developer", "reason": "shared quota",
                 "active": {"agent_id": "astra", "session_id": "session-1"}, "executions": [], "resume_at": 5000}
        with dispatcher.db:
            dispatcher._save(state, "waiting_capacity")
        linked = self.store.link_task(self.pid, self.role, "work-a")
        self.assertEqual(linked["harness_version"], 1)
        self.assertEqual(self.store.overview(self.pid)["roles"][0]["session_id"], "session-1")
        state["active"]["session_id"] = "session-2"; state["status"] = "RUNNING"
        with dispatcher.db:
            dispatcher._save(state, "handoff")
        self.assertEqual(self.store.overview(self.pid)["roles"][0]["session_id"], "session-2")
        self.assertEqual(self.store.overview(self.pid)["reports"][0]["source"], "system")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM flow_tasks").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM session_jobs").fetchone()[0], 0)
        other_role = self.store.overview(self.pid)["roles"][1]["id"]
        with self.assertRaises(ManagementError):
            self.store.link_task(self.pid, other_role, "work-a")
        dispatcher.close()

    def test_role_projection_uses_queue_running_fact_and_skips_merge_ready(self):
        dispatcher = Dispatcher(self.root, clock=lambda: self.now)
        completed = {"task_id": "done", "specification": {"task": {"goal": "Done"}, "worktree": "/done"},
                     "status": "MERGE_READY", "stage": "final", "reason": "Verified", "active": None}
        running = {"task_id": "next", "specification": {"task": {"goal": "Next"}, "worktree": "/next"},
                   "status": "READY", "stage": "developer", "reason": "Ready", "active": {"job_id": "job-next", "session_id": None}}
        with dispatcher.db:
            dispatcher._save(completed, "complete")
            dispatcher._save(running, "submitted")
            dispatcher.db.execute("INSERT INTO session_jobs(job_id,binding,state,document) VALUES (?,?,?,?)",
                ("job-next", "binding", "RUNNING", json.dumps({"status": "RUNNING", "session_id": "actual-session"})))
        self.store.link_task(self.pid, self.role, "done")
        self.store.link_task(self.pid, self.role, "next")
        before = self.store.events(self.pid)["cursor"]
        with dispatcher.db:
            dispatcher._save(running, "updated")
        self.assertEqual(self.store.events(self.pid, before)["events"][0]["kind"], "flow_updated")
        overview = self.store.overview(self.pid)
        role = overview["roles"][0]
        self.assertEqual(role["current_task_id"], "next")
        self.assertEqual(role["status"], "RUNNING")
        self.assertEqual(role["session_id"], "actual-session")
        self.assertEqual(dispatcher.get("next")["status"], "READY")
        dispatcher.close()

    def test_overview_uses_one_snapshot_during_concurrent_worker_commit(self):
        dispatcher = Dispatcher(self.root, clock=lambda: self.now)
        state = {"task_id": "snapshot", "specification": {"task": {"goal": "Snapshot"}, "worktree": "/snapshot"},
                 "status": "WAITING_CAPACITY", "stage": "developer", "reason": "quota", "active": None}
        with dispatcher.db:
            dispatcher._save(state, "submitted")
        self.store.link_task(self.pid, self.role, "snapshot")
        original = self.store._project
        def update_after_project_read(project_id):
            result = original(project_id)
            state["status"] = "READY"
            with dispatcher.db:
                dispatcher._save(state, "resumed")
            return result
        with patch.object(self.store, "_project", side_effect=update_after_project_read):
            overview = self.store.overview(self.pid)
        self.assertEqual(overview["tasks"][0]["status"], "WAITING_CAPACITY")
        self.assertEqual(self.store.overview(self.pid)["tasks"][0]["status"], "READY")
        dispatcher.close()

    def test_harness_draft_activation_and_task_revision_are_immutable(self):
        first = self.store.save_harness(self.pid, {"base_version": 1, "content": "First revision"})
        other = self.store.save_harness(self.pid, {"base_version": 1, "content": "Alternative revision"})
        self.assertEqual(self.store.overview(self.pid)["project"]["harness_version"], 1)
        self.store.activate_harness(self.pid, first["version"], first["digest"])
        with self.assertRaises(ManagementError):
            self.store.activate_harness(self.pid, other["version"], other["digest"])
        with self.assertRaises(ManagementError):
            self.store.save_harness(self.pid, {"base_version": 1, "content": "stale"})
        self.assertEqual(self.store.overview(self.pid)["project"]["harness_content"], "First revision")

    def test_approval_idempotency_and_replay_cannot_change_decision(self):
        approval = self.store.request_approval(self.pid, self.subject)
        result = self.store.decide(self.pid, approval["id"], self.decision(approval))
        self.assertEqual(result, self.store.decide(self.pid, approval["id"], self.decision(approval)))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM management_decisions").fetchone()[0], 1)
        with self.assertRaises(ManagementError):
            self.store.decide(self.pid, approval["id"], self.decision(approval, decision="reject"))
        with self.assertRaises(ManagementError):
            self.store.decide(self.pid, approval["id"], self.decision(approval, idempotency_key="different-key"))
        self.store.validate_approval(self.pid, approval["id"], self.subject)
        for key, value in (("artifact_sha", "b" * 40), ("environment", "production"), ("cost_usd", 3), ("expires_at", 3000), ("action", "deploy")):
            with self.subTest(key=key), self.assertRaises(ManagementError):
                self.store.validate_approval(self.pid, approval["id"], {**self.subject, key: value})
        self.now = 2000
        with self.assertRaises(ManagementError):
            self.store.validate_approval(self.pid, approval["id"], self.subject)

    def test_stale_digest_expiry_tampered_subject_and_cross_project_rejected(self):
        approval = self.store.request_approval(self.pid, self.subject)
        with self.assertRaises(ManagementError):
            self.store.decide(self.pid, approval["id"], self.decision(approval, subject_digest="b" * 64))
        other = self.store.create_project({"name": "Other", "goal": "Other scope"})
        with self.assertRaises(ManagementError):
            self.store.decide(other["id"], approval["id"], self.decision(approval))
        changed = copy.deepcopy(approval); changed["cost_usd"] = 200
        with self.store.db:
            self.store.db.execute("UPDATE management_approvals SET document=? WHERE id=?", (json.dumps(changed), approval["id"]))
        with self.assertRaises(ManagementError):
            self.store.decide(self.pid, approval["id"], self.decision(approval))
        self.now = 2000
        fresh = self.store.request_approval(self.pid, {**self.subject, "expires_at": 2001})
        self.now = 2001
        with self.assertRaises(ManagementError):
            self.store.decide(self.pid, fresh["id"], self.decision(fresh))
        self.assertEqual(self.store.overview(self.pid)["approvals"][-1]["status"], "expired")

    def test_fixture_never_seeds_existing_queue_or_authorizes_execution(self):
        with self.assertRaises(ManagementError):
            self.store.seed_demo()
        fixture = ManagementStore(self.root / "fixture", clock=lambda: self.now)
        try:
            project = fixture.seed_demo()
            overview = fixture.overview(project["id"])
            self.assertEqual(overview["readiness"]["mode"], "fixture")
            with self.assertRaises(ManagementError) as blocked:
                fixture.link_task(project["id"], overview["roles"][0]["id"], "real-flow")
            self.assertEqual(blocked.exception.code, "fixture_only")
            self.assertEqual(fixture.db.execute("SELECT COUNT(*) FROM flow_tasks").fetchone()[0], 0)
            approval = overview["approvals"][0]
            fixture.decide(project["id"], approval["id"], self.decision(approval))
            with self.assertRaises(ManagementError):
                fixture.validate_approval(project["id"], approval["id"], {k: approval[k] for k in self.subject})
        finally:
            fixture.close()

    def test_event_cursor_is_persistent_and_project_scoped(self):
        first = self.store.events(self.pid)
        self.store.post_message(self.pid, {"content": "new message"})
        next_page = self.store.events(self.pid, first["cursor"])
        self.assertEqual([e["kind"] for e in next_page["events"]], ["pm_request_saved"])
        self.assertEqual(self.store.events(self.pid, next_page["cursor"])["events"], [])

    def test_project_list_projects_activity_and_actionable_approvals_without_writes(self):
        self.now = 1100
        pending = self.store.request_approval(self.pid, self.subject)
        expired = self.store.request_approval(self.pid, {**self.subject, "expires_at": 1200})
        self.now = 1150
        decided = self.store.request_approval(self.pid, self.subject)
        self.store.decide(self.pid, decided["id"], self.decision(decided))
        self.now = 1250
        other = self.store.create_project({"name": "Other", "goal": "Another project"})
        self.now = 1300
        self.store.post_message(self.pid, {"content": "다음 계획을 설명해주세요"})
        before = list(self.store.db.iterdump())
        summaries = {p["id"]: p for p in self.store.list_projects(summary=True)}
        self.assertEqual(summaries[self.pid]["updated_at"], 1300)
        self.assertEqual(summaries[self.pid]["pending_approval_count"], 1)
        self.assertEqual(summaries[other["id"]]["pending_approval_count"], 0)
        self.assertEqual(summaries[other["id"]]["updated_at"], 1250)
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertEqual(self.store._approval(self.pid, expired["id"])["status"], "pending")
        self.assertEqual(self.store._approval(self.pid, pending["id"]), pending)
        # Existing store callers still get the original project documents.
        self.assertNotIn("updated_at", self.store.list_projects()[0])

    def test_project_list_separates_plan_recent_pm_and_run_facts_without_inventing_execution(self):
        other = self.store.create_project({"name": "Other", "goal": "다른 프로젝트"})
        requests = [
            {"id": "c" * 32, "project_id": self.pid, "created_at": 1100, "state": "waiting_quota", "mode": "fixture"},
            {"id": "a" * 32, "project_id": self.pid, "created_at": 1050, "state": "completed", "mode": "fixture"},
        ]
        runs = [
            {"id": "d" * 32, "project_id": self.pid, "created_at": 1100, "state": "running", "mode": "fixture"},
            {"id": "c" * 32, "project_id": self.pid, "created_at": 1100, "state": "blocked", "mode": "fixture"},
            {"id": "f" * 32, "project_id": self.pid, "created_at": 1050, "state": "completed", "mode": "fixture"},
            {"id": "e" * 32, "project_id": other["id"], "created_at": 5000, "state": "pending", "mode": "fixture"},
        ]
        with self.store.db:
            for item in requests:
                self.store.db.execute("INSERT INTO management_pm_requests VALUES (?,?,?)", (item["id"], item["project_id"], json.dumps(item)))
            for item in runs:
                self.store.db.execute("INSERT INTO management_runs VALUES (?,?,?)", (item["id"], item["project_id"], json.dumps(item)))
        empty = self.store.create_project({"name": "New", "goal": "아직 요청하지 않은 목표"})
        for state in ("running", "blocked", "completed"):
            with self.store.db:
                self.store.db.execute("UPDATE management_runs SET document=? WHERE id=?", (json.dumps({**runs[0], "state": state}), runs[0]["id"]))
            before = list(self.store.db.iterdump())
            summaries = {p["id"]: p for p in self.store.list_projects(summary=True)}
            current = summaries[self.pid]
            self.assertEqual(current["status"], "PLANNING")
            self.assertEqual(current["recent_run"], {"id": runs[0]["id"], "state": state, "created_at": 1100, "mode": "fixture"})
            self.assertEqual(current["recent_pm_request"], {"id": requests[0]["id"], "state": "waiting_quota", "created_at": 1100, "mode": "fixture", "execution": None})
            self.assertEqual(summaries[other["id"]]["recent_run"]["id"], runs[-1]["id"])
            self.assertIsNone(summaries[empty["id"]]["recent_run"])
            self.assertIsNone(summaries[empty["id"]]["recent_pm_request"])
            self.assertEqual(list(self.store.db.iterdump()), before)

    def test_project_list_preserves_running_pm_request_with_actual_execution_wait(self):
        message = self.store.post_message(self.pid, {"content": "역할을 제안해주세요"})
        request = self.store.get_pm_request(message["id"])
        waiting = {"status": "WAITING_CAPACITY", "reason": "적격 PM 계정의 공유 한도를 기다립니다.", "resume_at": 1600}
        request = self.store.save_pm_request({**request, "state": "running", "mode": "fixture",
            "configuration_digest": "a" * 64, "execution": {**waiting, "task_id": "private-task-reference"}})
        before = list(self.store.db.iterdump())
        summary = next(item for item in self.store.list_projects(summary=True) if item["id"] == self.pid)
        self.assertEqual(summary["recent_pm_request"]["state"], "running")
        self.assertEqual(summary["recent_pm_request"]["execution"], waiting)
        self.assertNotIn("private-task-reference", json.dumps(summary))
        self.assertEqual(self.store.get_pm_request(message["id"]), request)
        self.assertEqual(list(self.store.db.iterdump()), before)


class ProjectCreationRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ManagementStore(self.root)
        self.intent = {"name": "새 검증", "goal": "상태를 이해하기 쉽게 정리합니다", "start_pm": True,
                       "idempotency_key": "4e7bf1c7-5940-4ff6-9a24-216a65fa2951"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def count(self, table):
        return self.store.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_restart_after_lost_result_preserves_one_project_and_original_pm_request(self):
        original = self.store.create_project(self.intent)
        request = self.store.pm_requests(original["id"])[0]
        events = self.store.events(original["id"])
        harness = self.store.overview(original["id"])["project"]["harness_content"]
        # The caller lost the result after commit and must recover from durable intent.
        self.store.close()
        self.store = ManagementStore(self.root)
        retry = self.store.create_project(self.intent)
        self.assertEqual(retry["id"], original["id"])
        self.assertEqual(self.store.pm_requests(original["id"]), [request])
        self.assertEqual(self.store.events(original["id"]), events)
        self.assertEqual(self.store.overview(original["id"])["project"]["harness_content"], harness)
        self.assertEqual((self.count("management_projects"), self.count("management_messages")), (1, 1))
        self.assertEqual((self.count("management_runs"), self.count("session_jobs"), self.count("flow_tasks")), (0, 0, 0))

    def test_same_intent_rejects_changed_payload_without_changing_original(self):
        original = self.store.create_project(self.intent)
        for change in ({"name": "다른 이름"}, {"goal": "다른 목표"}, {"start_pm": False},
                       {"roles": [{"name": "추가 역할", "responsibility": "추가 업무"}]}):
            with self.subTest(change=change), self.assertRaises(ManagementError) as conflict:
                self.store.create_project({**self.intent, **change})
            self.assertEqual((conflict.exception.code, conflict.exception.status), ("idempotency_conflict", 409))
        self.assertEqual(self.store.list_projects(), [original])
        self.assertEqual(self.count("management_pm_requests"), 1)
        self.assertEqual(self.count("management_roles"), 0)

    def test_same_name_is_independent_for_new_intents_and_authenticated_principals(self):
        first = self.store.create_project(self.intent, principal="password:edward")
        second = self.store.create_project(self.intent, principal="password:another")
        third = self.store.create_project({**self.intent, "idempotency_key": "different-intent"}, principal="password:edward")
        legacy = {key: value for key, value in self.intent.items() if key != "idempotency_key"}
        fourth = self.store.create_project(legacy)
        fifth = self.store.create_project(legacy)
        self.assertEqual(len({item["id"] for item in (first, second, third, fourth, fifth)}), 5)
        self.assertEqual(self.store.create_project(self.intent, principal="password:edward")["id"], first["id"])
        self.assertEqual(self.count("management_pm_requests"), 5)

    def test_concurrent_connections_commit_one_project_and_one_pm_request(self):
        barrier = threading.Barrier(6)
        def create():
            store = ManagementStore(self.root)
            try:
                barrier.wait(timeout=10)
                return store.create_project(self.intent, principal="password:edward")["id"]
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(lambda _: create(), range(6)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual((self.count("management_projects"), self.count("management_pm_requests")), (1, 1))
        self.assertEqual([event["kind"] for event in self.store.events(ids[0])["events"]],
                         ["project_created", "pm_request_saved"])

    def test_failure_after_pm_insert_rolls_back_entire_intent_then_retry_succeeds(self):
        original_event = self.store._event
        def fail_after_pm_insert(project_id, kind, subject_id):
            original_event(project_id, kind, subject_id)
            if kind == "pm_request_saved":
                raise RuntimeError("interruption before transaction commit")
        with patch.object(self.store, "_event", side_effect=fail_after_pm_insert):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.store.create_project(self.intent)
        for table in ("management_projects", "management_harnesses", "management_roles", "management_messages",
                      "management_pm_requests", "management_events", "management_project_creations"):
            self.assertEqual(self.count(table), 0, table)
        self.store.close()
        self.store = ManagementStore(self.root)
        result = self.store.create_project(self.intent)
        self.assertEqual(len(self.store.pm_requests(result["id"])), 1)

    def test_invalid_keys_and_client_supplied_principal_create_nothing(self):
        for key in ("", "short", "a" * 129, "with/slash", "한글식별자123", 12345678):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.store.create_project({**self.intent, "idempotency_key": key})
        with self.assertRaises(ValidationError):
            self.store.create_project({**self.intent, "principal": "password:another"})
        self.assertEqual(self.count("management_projects"), 0)
