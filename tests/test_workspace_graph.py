"""Run/project isolation and read-only graph contracts on temporary SQLite facts."""
import json
from pathlib import Path
import tempfile
import unittest

from ai_company.dispatcher import Dispatcher
from ai_company.management import ManagementStore
from ai_company.workspace_graph import workspace_graph


class WorkspaceGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ManagementStore(Path(self.temp.name), clock=lambda: 1000)
        self.addCleanup(self.store.close)
        self.dispatcher = Dispatcher(Path(self.temp.name), clock=lambda: 1000)
        self.addCleanup(self.dispatcher.close)
        self.project = self.store.create_project({"name": "역할 확인", "goal": "같은 실행의 전달을 읽습니다.",
            "roles": [{"name": "개발", "responsibility": "함수 구현"}, {"name": "검사", "responsibility": "독립 검사"}]})
        self.pid = self.project["id"]
        self.roles = self.store.overview(self.pid)["roles"]
        self.plan = {"id": "plan-1", "project_id": self.pid, "digest": "b" * 64, "request_id": "pm-1",
            "content": {"roles": [{"key": "dev", "name": "개발", "responsibility": "함수 구현", "depends_on": []},
                                  {"key": "test", "name": "검사", "responsibility": "독립 검사", "depends_on": ["dev"]}]}}

    def task(self, identity, index=0, status="RUNNING", revision=0):
        state = {"task_id": identity, "created_at": 1000, "status": status, "stage": "developer", "reason": "저장된 작업 상태",
            "specification": {"task": {"goal": "집계 확인"}, "worktree": "/private/clone/secret", "execution_scope": "contribution",
                              "plan": {"plan_digest": self.plan["digest"], "revision": revision},
                              "agents": [{"agent_id": "agent", "provider": "codex", "model": "requested-model", "reasoning_effort": "high"}]},
            "active": {"agent_id": "agent", "execution_id": identity + ":1", "generation": 1, "role": "developer"}, "executions": []}
        with self.dispatcher.db:
            self.dispatcher._save(state, "role_assigned")
        self.store.link_task(self.pid, self.roles[index]["id"], identity, title="집계 작업")
        return state

    def run_record(self, identity, task, **kwargs):
        return {"id": identity, "project_id": self.pid, "plan_id": self.plan["id"], "plan_digest": self.plan["digest"],
            "harness_version": 1, "role_ids": {"dev": self.roles[0]["id"], "test": self.roles[1]["id"]},
            "roles": {"dev": {"task_id": task}}, "mode": "live", **kwargs}

    def graph(self, runs=(), plans=None, mutate=None):
        overview = self.store.overview(self.pid)
        overview.update(plans=plans if plans is not None else [self.plan], runs=list(runs))
        if mutate:
            mutate(overview)
        return workspace_graph(self.store.db, overview, observed_at=1001)

    def snapshot(self, graph, run_id=None):
        return next(s for s in graph["snapshots"] if s["run_id"] == run_id)

    def test_plan_registration_is_not_execution_and_dependencies_are_planned(self):
        result = self.snapshot(self.graph())
        self.assertEqual(result["mode"], "planned")
        self.assertTrue(all(n["phase"] == "planned" for n in result["nodes"]))
        edge = next(e for e in result["edges"] if e["kind"] == "planned_dependency")
        self.assertEqual((edge["kind"], edge["source"], edge["phase"]), ("planned_dependency", "planned", "planned"))
        self.assertEqual(edge["reference"]["plan_digest"], self.plan["digest"])
        self.assertEqual(result["approval_refs"], [])

    def test_independent_planned_roles_have_only_pm_proposal_links_without_run_duplicates(self):
        for role in self.plan["content"]["roles"]:
            role["depends_on"] = []
        planned = self.snapshot(self.graph())
        pm_id = next(node["id"] for node in planned["nodes"] if node["kind"] == "pm")
        role_ids = {node["id"] for node in planned["nodes"] if node["kind"] == "role"}
        self.assertEqual(len(planned["edges"]), 2)
        self.assertEqual({edge["from"] for edge in planned["edges"]}, {pm_id})
        self.assertEqual({edge["to"] for edge in planned["edges"]}, role_ids)
        self.assertTrue(all(edge["kind"] == "planned_specification" and edge["phase"] == "planned" for edge in planned["edges"]))
        self.assertEqual(planned["history"]["planned"], 2)
        self.assertEqual(planned["history"]["recorded"], 0)
        self.assertEqual(planned["edges"], self.snapshot(self.graph())["edges"])
        self.task("task")
        execution = self.snapshot(self.graph([self.run_record("run", "task")]), "run")
        self.assertFalse(any(edge["kind"] == "planned_specification" for edge in execution["edges"]))
        self.assertTrue(any(edge["kind"] == "specification" and edge["phase"] == "recorded" for edge in execution["edges"]))

    def test_two_runs_with_same_role_do_not_borrow_current_state_or_documents(self):
        self.task("old", status="STOPPED")
        self.task("new", status="RUNNING")
        old = self.run_record("run-old", "old", approval_id="approval-old")
        new = self.run_record("run-new", "new", approval_id="approval-new")
        def documents(value):
            value["approvals"] = [{"id": "approval-old", "subject_digest": "c" * 64, "artifact_sha": "a" * 40},
                                  {"id": "approval-new", "subject_digest": "d" * 64, "artifact_sha": "b" * 40}]
        graph = self.graph([old, new], mutate=documents)
        for run_id, task, status in (("run-old", "old", "STOPPED"), ("run-new", "new", "RUNNING")):
            result = self.snapshot(graph, run_id)
            node = next(n for n in result["nodes"] if n["current_task_id"] == task)
            self.assertEqual(node["status"], status)
            self.assertEqual([r["id"] for r in result["report_refs"]], ["flow-" + task])
            self.assertEqual([a["id"] for a in result["approval_refs"]], ["approval-" + task])
            self.assertTrue(all(e["run_id"] == run_id for e in result["edges"]))
        one, two = self.snapshot(graph, "run-old"), self.snapshot(graph, "run-new")
        self.assertFalse({n["id"] for n in one["nodes"]} & {n["id"] for n in two["nodes"]})

    def test_explicit_current_task_wins_over_late_historical_revision(self):
        self.task("current", status="WAITING_CAPACITY")
        self.task("history", status="STOPPED", revision=999)
        result = self.snapshot(self.graph([self.run_record("run", "current", role_history=[{"task_id": "history"}])]), "run")
        role = next(n for n in result["nodes"] if n["kind"] == "role" and n["name"] == "개발")
        self.assertEqual(role["current_task_id"], "current")
        self.assertEqual(role["status"], "WAITING_CAPACITY")
        self.assertIn("history", role["task_ids"])

    def test_cross_project_run_and_forged_task_reference_are_excluded(self):
        self.task("owned")
        other = self.store.create_project({"name": "다른 프로젝트", "goal": "분리"})
        forged = self.run_record("foreign-run", "owned", project_id=other["id"])
        own = self.run_record("own-run", "foreign-task")
        result = self.graph([forged, own])
        self.assertNotIn("foreign-run", [s["run_id"] for s in result["snapshots"]])
        snapshot = self.snapshot(result, "own-run")
        self.assertFalse(any(n["current_task_id"] for n in snapshot["nodes"]))
        self.assertEqual(snapshot["report_refs"], [])

    def test_repeated_cursor_is_read_only_and_fingerprint_tracks_session_changes(self):
        state = self.task("task")
        state["active"]["job_id"] = "session-job"
        job = {"job_id": "session-job", "status": "RUNNING", "reason": "실행 중"}
        with self.store.db:
            self.store.db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(state), "task"))
            self.store.db.execute("INSERT INTO session_jobs(job_id,binding,state,document) VALUES (?,?,?,?)",
                                  ("session-job", "binding", "RUNNING", json.dumps(job)))
        runs = [self.run_record("run", "task")]
        before = self.store.db.total_changes
        first, second = self.graph(runs), self.graph(runs)
        self.assertEqual(first, second)
        self.assertEqual(self.store.db.total_changes, before)
        job.update(status="WAITING_QUOTA", reason="공유 한도 대기", resume_at=2000)
        # Inject a replayed cursor while session facts advance; fingerprint must still change.
        with self.store.db:
            self.store.db.execute("UPDATE session_jobs SET document=? WHERE job_id=?", (json.dumps(job), "session-job"))
        changed = self.graph(runs, mutate=lambda value: value["collaboration"].update(cursor=first["cursor"]))
        self.assertEqual(first["cursor"], changed["cursor"])
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])
        self.assertEqual([n["id"] for n in self.snapshot(first, "run")["nodes"]],
                         [n["id"] for n in self.snapshot(changed, "run")["nodes"]])
        self.assertNotIn("/private/clone", json.dumps(changed))
        node = next(n for n in self.snapshot(changed, "run")["nodes"] if n["current_task_id"] == "task")
        self.assertEqual(node["assignment"]["requested"]["model"], "requested-model")
        self.assertEqual(node["assignment"]["observed"]["status"], "unavailable")

    def test_fixture_relation_phase_does_not_claim_observed_execution(self):
        graph = self.graph(mutate=lambda value: value["project"].update(source="fixture"))
        result = self.snapshot(graph)
        self.assertEqual(result["source"], "fixture")
        self.assertEqual(result["edges"][0]["phase"], "planned")
        self.assertEqual(result["edges"][0]["source"], "fixture")

    def test_mismatched_plan_digest_does_not_supply_dependencies_to_run(self):
        self.task("task")
        result = self.snapshot(self.graph([self.run_record("run", "task", plan_digest="e" * 64)]), "run")
        self.assertTrue(result["warnings"])
        self.assertFalse(any(e["phase"] == "planned" for e in result["edges"]))

    def test_mismatched_plan_does_not_borrow_pm_execution_or_current_role_labels(self):
        self.task("task")
        self.task("unrelated-pm", 1)
        def poison(value):
            value["plans"][0]["mode"] = "fixture"
            for role in value["roles"]:
                role.update(name="다른 계획의 역할", responsibility="가져오면 안 되는 설명")
            value["pm_requests"] = [{"request_id": self.plan["request_id"], "state": "completed",
                                     "execution": {"task_id": "unrelated-pm"}}]
        result = self.snapshot(self.graph([self.run_record("run", "task", plan_digest="e" * 64)], mutate=poison), "run")
        pm = next(node for node in result["nodes"] if node["kind"] == "pm")
        self.assertIsNone(pm["current_task_id"])
        self.assertIsNone(pm["reference"]["id"])
        self.assertIsNone(pm["assignment"]["execution_id"])
        self.assertEqual(pm["phase"], "planned")
        self.assertEqual(result["source"], "execution")
        self.assertNotIn("unrelated-pm", json.dumps(result))
        self.assertNotIn("다른 계획의 역할", json.dumps(result, ensure_ascii=False))
        self.assertTrue(all(node["name"] == "역할 확인 불가" for node in result["nodes"] if node["kind"] == "role"))

    def test_mismatched_plan_recovers_role_only_from_exact_fixed_flow_binding(self):
        state = self.task("task")
        state["specification"]["plan"].update(plan_digest="e" * 64, project_id=self.pid,
            role={"key": "dev", "name": "고정 실행 역할", "responsibility": "고정된 역할 설명"})
        with self.store.db:
            self.store.db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(state), "task"))
        run = self.run_record("run", "task", plan_digest="e" * 64)
        result = self.snapshot(self.graph([run]), "run")
        role = next(node for node in result["nodes"] if node["current_task_id"] == "task")
        self.assertEqual(role["name"], "고정 실행 역할")
        self.assertEqual(role["responsibility"], "고정된 역할 설명")
        state["specification"]["plan"]["project_id"] = "different-project"
        with self.store.db:
            self.store.db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(state), "task"))
        result = self.snapshot(self.graph([run]), "run")
        role = next(node for node in result["nodes"] if node["current_task_id"] == "task")
        self.assertEqual(role["name"], "역할 확인 불가")

    def test_actual_overview_is_additive_and_empty_project_has_next_action(self):
        overview = self.store.overview(self.pid)
        self.assertIn("collaboration", overview)
        graph = overview["workspace_graph"]
        self.assertEqual(graph["project_id"], self.pid)
        self.assertTrue(graph["snapshots"][0]["empty_reason"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM management_runs").fetchone()[0], 0)
