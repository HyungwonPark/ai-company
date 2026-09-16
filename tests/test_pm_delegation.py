"""Explicit local delegation creates one new validation, never rewrites old BLOCKs."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.contracts import digest
from ai_company.management import ManagementError, ManagementStore


class ValidationDelegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000.0
        self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.project = self.store.create_project({"name": "Delegation fixture", "goal": "Validate two contributions"})
        self.pid = self.project["id"]
        content = {"summary": "Implement two contributions", "roles": [
            {"key": "api", "name": "API", "responsibility": "Implementation", "goal": "Implement API",
             "acceptance": ["API verified"], "allowed_paths": ["src/api/"], "depends_on": []},
            {"key": "test", "name": "Test", "responsibility": "Tests", "goal": "Create tests",
             "acceptance": ["Tests pass"], "allowed_paths": ["tests/"], "depends_on": []}], "completion_criteria": ["Independent review passes"]}
        message = self.store.post_message(self.pid, {"content": "Propose the plan"})
        request = self.store.get_pm_request(message["id"])
        self.store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "live"})
        self.plan = self.store.complete_pm_request(message["id"], content, evidence={"source": "unit_test", "scope": "stub"})
        self.parent = self.store.confirm_plan(self.pid, self.plan["id"], {
            "plan_digest": self.plan["digest"], "base_harness_version": 1, "idempotency_key": "original-confirmation"})["run"]
        self.parent = self.store.save_run({**self.parent, "state": "blocked", "reason": "No delegated validation authority",
                                           "roles": {"api": {"task_id": "old-blocked-task", "status": "BLOCKED"}}})
        self.plan = self.store.get_plan(self.pid, self.plan["id"])
        with self.store.db:
            self.store.db.execute("INSERT INTO flow_tasks VALUES (?,?)", ("old-blocked-task", json.dumps({"status": "BLOCKED", "reason": "Before authorization"})))
        self.now = 1002.0
        self.authorization = {"source_id": "explicit-reply-01", "source": "explicit_user_reply", "received_at": 1001.0,
            "original_text": "\n  I approve this exact plan and delegate one new validation after this reply.\nKeep the previous BLOCK unchanged.  \n",
            "plan_digest": self.plan["digest"], "allowed_paths": ["src/api/", "tests/", ".ai-company-ci/request.json"],
            "max_new_validations": 1, "applies_to": "new_execution_after_receipt_only"}

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def delegate(self, authorization=None, plan_digest=None):
        return self.store.delegate_validation(plan_digest or self.plan["digest"], authorization or self.authorization)

    def assert_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(ManagementError) as error:
            callable_(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_creates_one_fresh_run_preserving_old_plan_run_and_task(self):
        old_plan = self.store.get_plan(self.pid, self.plan["id"])
        old_parent = self.store.get_run(self.parent["id"])
        old_task = self.store.db.execute("SELECT document FROM flow_tasks WHERE task_id='old-blocked-task'").fetchone()[0]
        result = self.delegate(); run, delegation = result["run"], result["delegation"]
        self.assertNotEqual(run["id"], self.parent["id"])
        self.assertEqual(run["state"], "pending")
        self.assertNotIn("roles", run)
        self.assertGreater(run["created_at"], self.authorization["received_at"])
        self.assertEqual(run["parent_run_id"], self.parent["id"])
        self.assertEqual(run["role_ids"], self.parent["role_ids"])
        self.assertEqual(run["configuration_digest"], self.parent["configuration_digest"])
        self.assertEqual(run["harness_version"], self.parent["harness_version"])
        self.assertEqual(delegation["authorization"], self.authorization)
        self.assertEqual(delegation["authorization_digest"], digest(self.authorization))
        self.assertEqual(self.store.get_delegation(delegation["id"]), delegation)
        self.assertEqual(self.store.get_plan(self.pid, self.plan["id"]), old_plan)
        self.assertEqual(self.store.get_run(self.parent["id"]), old_parent)
        self.assertEqual(self.store.db.execute("SELECT document FROM flow_tasks WHERE task_id='old-blocked-task'").fetchone()[0], old_task)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM session_jobs").fetchone()[0], 0)
        overview = self.store.overview(self.pid)
        self.assertEqual(overview["delegations"], [delegation])
        self.assertEqual(len(overview["runs"]), 2)

    def test_same_reply_is_one_use_across_restart_and_changed_receipt_is_rejected(self):
        first = self.delegate()
        self.store.close(); self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.assertEqual(self.delegate(), first)
        self.assertEqual(len(self.store.run_records()), 2)
        changed = {**self.authorization, "original_text": self.authorization["original_text"] + "extra"}
        self.assert_error("delegation_replay", self.delegate, changed)
        changed = {**self.authorization, "allowed_paths": ["src/", "tests/", ".ai-company-ci/request.json"]}
        self.assert_error("delegation_replay", self.delegate, changed)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM management_delegations").fetchone()[0], 1)

    def test_concurrent_replays_create_exactly_one_new_validation(self):
        def issue():
            store = ManagementStore(self.root, clock=lambda: self.now)
            try:
                return store.delegate_validation(self.plan["digest"], self.authorization)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: issue(), range(2)))
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.run_records()), 2)

    def test_receipt_requires_exact_plan_single_new_validation_and_effective_time(self):
        self.assert_error("delegation_mismatch", self.delegate, plan_digest="a" * 64)
        self.assert_error("plan_mismatch", self.delegate, {**self.authorization, "plan_digest": "a" * 64}, "a" * 64)
        for value in (1002.0, 1003.0):
            self.assert_error("authorization_not_effective", self.delegate, {**self.authorization, "received_at": value})
        for change in ({"max_new_validations": 2}, {"max_new_validations": True}, {"source": "model_reply"},
                       {"applies_to": "all_existing_runs"}, {"received_at": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.delegate({**self.authorization, **change})
        self.assertEqual(len(self.store.run_records()), 1)

    def test_paths_cannot_be_widened_omitted_or_duplicated(self):
        for paths in (["src/", "tests/", ".ai-company-ci/request.json"], ["src/api/", "tests/"],
                      self.authorization["allowed_paths"] + [".github/"], self.authorization["allowed_paths"] + ["tests/"]):
            with self.subTest(paths=paths):
                self.assert_error("delegation_scope", self.delegate, {**self.authorization, "allowed_paths": paths})

    def test_receipt_clock_metadata_is_preserved_and_utc_epoch_must_match(self):
        authorization = {**self.authorization, "received_at_utc": "1970-01-01T00:16:41Z",
                         "receipt_clock": "First recorded UTC clock after processing this user reply"}
        for invalid in ("1970-01-01T00:16:40Z", "1970-01-01T00:16:41", "1970-01-01T09:16:41+09:00", "invalid"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                self.delegate({**authorization, "received_at_utc": invalid})
        result = self.delegate(authorization)
        self.assertEqual(result["delegation"]["authorization"], authorization)
        self.assertEqual(result["delegation"]["authorization_digest"], digest(authorization))
        self.assertEqual(self.delegate(authorization), result)
        self.assertEqual(len(self.store.run_records()), 2)

    def test_changed_plan_goal_revision_harness_and_role_are_not_reauthorized(self):
        original = self.store._project(self.pid)
        for field, value in (("goal", "Different goal"), ("request_revision", 99), ("harness_version", 99)):
            changed = {**original, field: value}
            with self.store.db:
                self.store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(changed), self.pid))
            self.assert_error("stale_plan", self.delegate)
        with self.store.db:
            self.store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(original), self.pid))
        row = self.store.db.execute("SELECT document FROM management_harnesses WHERE project_id=? AND version=?", (self.pid, self.parent["harness_version"])).fetchone()
        harness = json.loads(row[0]); changed_harness = {**harness, "content": "Changed active constraints", "digest": digest("Changed active constraints")}
        with self.store.db:
            self.store.db.execute("UPDATE management_harnesses SET document=? WHERE project_id=? AND version=?", (json.dumps(changed_harness), self.pid, self.parent["harness_version"]))
        self.assert_error("stale_plan", self.delegate)
        with self.store.db:
            self.store.db.execute("UPDATE management_harnesses SET document=? WHERE project_id=? AND version=?", (json.dumps(harness), self.pid, self.parent["harness_version"]))
        changed_plan = copy.deepcopy(self.plan); changed_plan["content"]["summary"] = "Unapproved plan"
        with self.store.db:
            self.store.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(changed_plan), self.plan["id"]))
        self.assert_error("plan_mismatch", self.delegate)
        with self.store.db:
            self.store.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(self.plan), self.plan["id"]))
        role_id = self.parent["role_ids"]["api"]
        role = json.loads(self.store.db.execute("SELECT document FROM management_roles WHERE id=?", (role_id,)).fetchone()[0]); role["allowed_paths"] = ["src/"]
        with self.store.db:
            self.store.db.execute("UPDATE management_roles SET document=? WHERE id=?", (json.dumps(role), role_id))
        self.assert_error("delegation_mismatch", self.delegate)
        self.assertEqual(len(self.store.run_records()), 1)

    def test_fixture_mode_and_changed_parent_configuration_are_rejected(self):
        parent = self.store.get_run(self.parent["id"])
        for field, value, code in (("mode", "fixture", "fixture_only"), ("configuration_digest", "d" * 64, "delegation_mismatch")):
            with self.store.db:
                self.store.db.execute("UPDATE management_runs SET document=? WHERE id=?", (json.dumps({**parent, field: value}), parent["id"]))
            self.assert_error(code, self.delegate)
        with self.store.db:
            self.store.db.execute("UPDATE management_runs SET document=? WHERE id=?", (json.dumps(parent), parent["id"]))
        project = self.store._project(self.pid)
        with self.store.db:
            self.store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps({**project, "source": "fixture"}), self.pid))
        self.assert_error("fixture_only", self.delegate)

    def test_delegation_and_new_run_commit_atomically(self):
        class Crash(BaseException):
            pass
        with patch.object(self.store, "_event", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.delegate()
        self.store.close(); self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.assertEqual(len(self.store.run_records()), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM management_delegations").fetchone()[0], 0)
        self.assertEqual(self.delegate()["run"]["state"], "pending")
        self.assertEqual(self.store.get_run(self.parent["id"])["state"], "blocked")

    def test_delegation_identity_cannot_be_removed_changed_or_attached_retroactively(self):
        result = self.delegate(); run = result["run"]
        for field in ("parent_run_id", "delegation_id", "delegation_digest"):
            for changed in ({**run, field: "changed"}, {key: value for key, value in run.items() if key != field}):
                self.assert_error("run_mismatch", self.store.save_run, changed)
        self.assert_error("run_mismatch", self.store.save_run, {**self.parent, "delegation_id": result["delegation"]["id"]})
        self.store.save_run({**run, "state": "preparing"}, expected_state="pending")
        replay = self.delegate()
        self.assertEqual(replay["run"]["id"], run["id"])
        self.assertEqual(replay["run"]["state"], "preparing")
        self.assertEqual(len(self.store.run_records()), 2)
