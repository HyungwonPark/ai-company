"""Project-bound orchestration using real temporary clones and fixture executors."""
import copy
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.automation import Automation
from ai_company.automation_contracts import AutomationConfig
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.execution_specs import ExecutionCatalog
from ai_company.management import ManagementError
from tests import test_automation as fixture


class ProjectAutomationTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.CoordinatorTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.h.worker.close()
        second = self.h.root / "second-source"
        self.h.git(self.h.root, "clone", "--no-hardlinks", str(self.h.repo), str(second))
        self.h.git(second, "remote", "set-url", "origin", "https://github.com/owner/other.git")
        data = self.h.config.model_dump(mode="json")
        data.update(repository="owner/other", source_clone=str(second))
        self.catalog = ExecutionCatalog({"first": self.h.config, "second": AutomationConfig.model_validate(data)})
        self.h.worker = self.open()

    def open(self):
        return Automation(self.h.root / "state", self.h.config, clock=lambda: self.h.now,
            execution_catalog=self.catalog, dispatcher_factory=lambda root, **kwargs:
                Dispatcher(root, verifier=self.h, executor=self.h.execute, **kwargs))

    def register(self, project, key, **selection):
        entry = next(e for e in self.catalog.public_entries() if e["catalog_id"] == key)
        return self.h.worker.store.register_execution_spec(project["id"], {
            "base_version": 0, "idempotency_key": "register-" + project["id"],
            "selection": {"catalog_id": key, "catalog_digest": entry["catalog_digest"], **selection}})

    def plan(self, project):
        self.h.now += 1
        self.h.worker.store.post_message(project["id"], {"content": "등록한 명세로 두 역할을 제안해 주세요."})
        for _ in range(6):
            self.h.worker.run_once()
            plans = self.h.worker.store.overview(project["id"])["plans"]
            current = [p for p in plans if p["status"] == "proposed" and self.h.worker.store.request_is_current(p["request_id"])]
            if current:
                return current[0]
            self.h.now += 60
        self.fail(str(self.h.worker.store.pm_requests()))

    def confirm(self, project, plan):
        return self.h.worker.store.confirm_plan(project["id"], plan["id"], {
            "plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
            "execution_spec_digest": plan["execution_spec"]["digest"],
            "idempotency_key": "confirm-" + project["id"]})["run"]

    def test_two_repositories_complete_using_fixed_specs_and_common_account_ledger(self):
        first = self.h.project
        second = self.h.worker.store.create_project({"name": "두 번째", "goal": "독립 산출물"})
        one = self.register(first, "first", role_candidates={"impl": ["backup"], "test": ["dev"]})
        two = self.register(second, "second")
        p1, p2 = self.plan(first), self.plan(second)
        self.assertEqual(self.h.worker.store.run_records(), [])
        r1, r2 = self.confirm(first, p1), self.confirm(second, p2)
        for _ in range(16):
            self.h.worker.run_once()
            runs = self.h.worker.store.run_records()
            if all(r["state"] == "fixture_complete" for r in runs):
                break
            self.h.now += 60
        self.assertTrue(all(r["state"] == "fixture_complete" for r in runs), runs)
        for project, reference, run_id, repository in (
            (first, one, r1["id"], "owner/repo"), (second, two, r2["id"], "owner/other")):
            run = self.h.worker.store.get_run(run_id)
            self.assertEqual(run["execution_spec"]["digest"], reference["digest"])
            for item in [*run["roles"].values(), run["integration"]]:
                spec = self.h.worker.dispatcher.get(item["task_id"])["specification"]
                self.assertEqual(spec["task"]["repository"], repository)
                self.assertEqual(spec["project_budget"]["scope_id"], project["id"])
                self.assertEqual(spec["plan"]["execution_spec"], run["execution_spec"])
                self.assertIn(project["id"], str(Path(spec["worktree"])))
        state = self.h.worker.dispatcher.get(self.h.worker.store.get_run(r1["id"])["roles"]["impl"]["task_id"])
        self.assertEqual(state["specification"]["policy"]["candidates"]["developer"], ["backup"])
        self.assertEqual(len(self.h.worker.dispatcher.db.execute("SELECT * FROM quota_groups").fetchall()), 2)
        before = copy.deepcopy(self.h.worker.dispatcher.tasks())
        self.h.worker.close(); self.h.worker = self.open()
        self.h.worker.run_once()
        self.assertEqual(before, self.h.worker.dispatcher.tasks())

    def test_pm_technical_proposal_requires_separate_registration_and_new_plan(self):
        entry = self.catalog.public_entries()[1]
        self.h.plan["execution_spec_proposal"] = {"catalog_id": entry["catalog_id"], "catalog_digest": entry["catalog_digest"]}
        self.h.worker.run_once()
        self.h.worker.run_once()
        plan = self.h.worker.store.overview(self.h.project["id"])["plans"][0]
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store.confirm_plan(self.h.project["id"], plan["id"], {
                "plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
                "idempotency_key": "not-an-execution-approval"})
        self.assertEqual(error.exception.code, "execution_spec_required")
        self.assertEqual(self.h.worker.store.run_records(), [])
        self.register(self.h.project, "second")
        self.assertEqual(self.h.worker.store.run_records(), [])
        self.h.plan.pop("execution_spec_proposal")
        new = self.plan(self.h.project)
        self.assertNotEqual(plan["digest"], new["digest"])
        self.confirm(self.h.project, new)
        self.assertEqual(len(self.h.worker.store.run_records()), 1)

    def test_invalid_recorded_usage_is_conservative_and_not_a_new_allowance(self):
        self.register(self.h.project, "first")
        run = self.confirm(self.h.project, self.plan(self.h.project))
        self.h.worker.reconcile()
        task_id = self.h.worker.store.get_run(run["id"])["roles"]["impl"]["task_id"]
        dispatcher = self.h.worker.dispatcher
        dispatcher.executor = lambda *a, **k: SessionOutcome("code_error", session_id="invalid-usage",
            result={"duration_seconds": float("nan"), "total_cost_usd": float("inf")})
        dispatcher.run_once(task_ids={task_id})
        state = dispatcher.get(task_id)
        self.assertEqual(state["usage"]["executions"], 1)
        self.assertTrue(state["usage"]["cost_unknown"])
        self.assertEqual(state["usage"]["runtime_seconds"], self.h.config.policy.retry.execution_timeout_seconds)
        self.assertNotIn("project_reservation", state)

    def test_interrupted_reservation_before_claim_can_transfer_after_account_cooldown(self):
        class InterruptedBeforeClaim(BaseException):
            pass
        self.register(self.h.project, "first")
        run = self.confirm(self.h.project, self.plan(self.h.project))
        self.h.worker.reconcile()
        task_id = self.h.worker.store.get_run(run["id"])["roles"]["impl"]["task_id"]
        dispatcher = self.h.worker.dispatcher
        with patch.object(dispatcher.queue, "run_once", side_effect=InterruptedBeforeClaim):
            with self.assertRaises(InterruptedBeforeClaim):
                dispatcher.run_once(task_ids={task_id})
        reserved = dispatcher.get(task_id)
        self.assertIn("project_reservation", reserved)
        self.assertEqual(dispatcher.queue.get(reserved["active"]["job_id"])["attempt_count"], 0)
        with dispatcher.db:
            dispatcher.db.execute("UPDATE quota_groups SET state='COOLDOWN',resume_at=5000 WHERE group_id='codex'")
        dispatcher.run_once(task_ids={task_id})
        transferred = dispatcher.get(task_id)
        self.assertEqual(transferred["active"]["agent_id"], "backup")
        self.assertNotIn("project_reservation", transferred)
        dispatcher.run_once(task_ids={task_id})
        done = dispatcher.get(task_id)
        self.assertEqual(done["status"], "CONTRIBUTION_READY")
        self.assertEqual(done["usage"]["executions"], 1)
