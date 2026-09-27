"""Derived E1 persistence tests use isolated stores and never call a model."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from ai_company.management import ManagementStore
from ai_company.pm_evidence_recovery import (
    Diagnosis, RecoveryError, _raw_output, apply_to_isolated_state,
    apply_to_operating_state, verify_saved_recovery,
)
from tests.legacy_pm import use_legacy_requests


class RecoveredPlanTests(unittest.TestCase):
    def setUp(self):
        use_legacy_requests(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.origin = self.base / "original" / "E1"
        self.origin.mkdir(parents=True)
        self.state = self.base / "copy" / "E1"
        self.store = ManagementStore(self.state)
        self.addCleanup(self.store.close)
        project = self.store.create_project({"name": "E1", "goal": "Build safely"})
        message = self.store.post_message(project["id"], {"content": "Make a plan"})
        request = self.store.get_pm_request(message["id"])
        self.store.save_pm_request({**request, "state": "blocked", "reason": "old evidence unavailable",
                                    "configuration_digest": "c" * 64, "mode": "fixture"},
                                   expected_state="pending")
        self.content = {"summary": "Build and check", "roles": [
            {"key": "dev", "name": "Developer", "responsibility": "Build", "goal": "Implement",
             "acceptance": ["Implementation works"], "allowed_paths": ["src/"], "depends_on": []},
            {"key": "test", "name": "Tester", "responsibility": "Check", "goal": "Verify",
             "acceptance": ["Tests pass"], "allowed_paths": ["tests/"], "depends_on": []}],
            "completion_criteria": ["Independent check passes"]}
        self.evidence = {"source": "derived_settled_pm", "recovery_revision_id": "revision",
                         "original_job_id": "original-job"}
        self.binding = {"id": "revision", "validator_commit": "a" * 40}
        self.diagnosis = Diagnosis(self.origin, self.state, self.base / "shared.sqlite",
            self.base / "config.json", self.base / "codex", self.base / "manifest.json",
            message["id"], project["id"], "pm-" + message["id"], "original-job",
            "original-reservation", self.content, self.evidence, self.binding)

    def _apply(self):
        return apply_to_isolated_state(self.diagnosis)

    def test_duplicate_and_concurrent_recovery_save_one_plan_and_revision(self):
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self._apply(), range(4)))
        self.assertEqual([x["state"] for x in results].count("saved"), 1)
        self.assertEqual([x["state"] for x in results].count("already_saved"), 3)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM pm_recovery_revisions").fetchone()[0], 1)
        self.assertEqual(self.store.get_plan(self.diagnosis.project_id, results[0]["plan_id"])["id"],
                         results[0]["plan_id"])

    def test_restart_after_plan_save_completes_missing_revision_once(self):
        saved = self.store.complete_pm_request(self.diagnosis.request_id, self.content,
            expected_state="blocked", evidence=self.evidence)
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            self.assertEqual(self._apply()["plan_id"], saved["id"])
            self.assertEqual(self._apply()["state"], "already_saved")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)

    def test_repeat_refuses_changed_plan_content(self):
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            saved = self._apply()
            plan = self.store.get_plan(self.diagnosis.project_id, saved["plan_id"])
            plan["content"]["summary"] = "Changed after recovery"
            with self.store.db:
                self.store.db.execute("UPDATE management_plans SET document=? WHERE id=?",
                                      (json.dumps(plan), plan["id"]))
            with self.assertRaisesRegex(RecoveryError, "plan or current request changed"):
                self._apply()

    def test_changed_revision_and_operating_scope_are_rejected(self):
        changed = Diagnosis(**{**self.diagnosis.__dict__, "revision": {"id": "changed",
            "validator_commit": "b" * 40}})
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=changed):
            with self.assertRaisesRegex(RecoveryError, "changed after diagnosis"):
                apply_to_isolated_state(self.diagnosis)
        with self.assertRaisesRegex(RecoveryError, "operating scope"):
            apply_to_operating_state(self.diagnosis)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 0)

    def test_original_output_requires_one_complete_matching_message(self):
        path = self.base / "output.jsonl"
        report = {"verdict": "PASS", "plan": {"summary": "fixture"}}
        lines = [
            {"type": "thread.started", "thread_id": "original-session"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}},
            {"type": "turn.completed"},
        ]
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        _raw_output(path, report, "original-session")
        with self.assertRaises(RecoveryError):
            _raw_output(path, {"verdict": "BLOCK"}, "original-session")
        with self.assertRaises(RecoveryError):
            _raw_output(path, report, "different-session")
        path.write_text(path.read_text() + json.dumps(lines[1]) + "\n")
        with self.assertRaises(RecoveryError):
            _raw_output(path, report, "original-session")


if __name__ == "__main__":
    unittest.main()
