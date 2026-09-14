import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
