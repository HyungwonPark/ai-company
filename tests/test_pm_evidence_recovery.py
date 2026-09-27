"""Derived E1 persistence tests use isolated stores and never call a model."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
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
        self.state.chmod(0o700)
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
        source = self.origin / "sessions" / "sessions.sqlite"
        source.parent.mkdir()
        with sqlite3.connect(source) as original:
            self.store.db.backup(original)
        with sqlite3.connect(self.base / "trial-state.sqlite") as frozen:
            self.store.db.backup(frozen)
        self.store.close()

    def _apply(self):
        return apply_to_isolated_state(self.diagnosis)

    def test_duplicate_and_concurrent_recovery_save_one_plan_and_revision(self):
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: self._apply(), range(4)))
        self.assertEqual([x["state"] for x in results].count("saved"), 1)
        self.assertEqual([x["state"] for x in results].count("already_saved"), 3)
        store = ManagementStore(self.state)
        try:
            self.assertEqual(store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)
            self.assertEqual(store.db.execute("SELECT count(*) FROM pm_recovery_revisions").fetchone()[0], 1)
            self.assertEqual(store.get_plan(self.diagnosis.project_id, results[0]["plan_id"])["id"],
                             results[0]["plan_id"])
        finally:
            store.close()

    def test_restart_after_plan_save_completes_missing_revision_once(self):
        store = ManagementStore(self.state)
        try:
            saved = store.complete_pm_request(self.diagnosis.request_id, self.content,
                expected_state="blocked", evidence=self.evidence)
        finally:
            store.close()
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            self.assertEqual(self._apply()["plan_id"], saved["id"])
            self.assertEqual(self._apply()["state"], "already_saved")
        store = ManagementStore(self.state)
        try:
            self.assertEqual(store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)
        finally:
            store.close()

    def test_repeat_refuses_changed_plan_content(self):
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            saved = self._apply()
            store = ManagementStore(self.state)
            try:
                plan = store.get_plan(self.diagnosis.project_id, saved["plan_id"])
                plan["content"]["summary"] = "Changed after recovery"
                with store.db:
                    store.db.execute("UPDATE management_plans SET document=? WHERE id=?",
                                     (json.dumps(plan), plan["id"]))
            finally:
                store.close()
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
        store = ManagementStore(self.state)
        try:
            self.assertEqual(store.db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 0)
        finally:
            store.close()

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

    def test_isolated_apply_rejects_database_symlink_and_hardlink_before_write(self):
        source = self.origin / "sessions" / "sessions.sqlite"
        target = self.state / "sessions" / "sessions.sqlite"
        self.store.close()
        for link in (os.symlink, os.link):
            target.unlink()
            link(source, target)
            with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
                with self.assertRaisesRegex(RecoveryError, "symbolic link|aliases"):
                    self._apply()
            with sqlite3.connect(source) as db:
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='pm_recovery_revisions'").fetchone())

    def test_isolated_apply_rejects_intermediate_directory_symlink(self):
        self.store.close()
        sessions = self.state / "sessions"
        (sessions / "sessions.sqlite").unlink()
        sessions.rmdir()
        sessions.symlink_to(self.origin / "sessions", target_is_directory=True)
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            with self.assertRaisesRegex(RecoveryError, "symbolic link|changed before persistence"):
                self._apply()

    def test_directory_swap_after_pinning_never_writes_original(self):
        sessions = self.state / "sessions"
        original = self.origin / "sessions" / "sessions.sqlite"
        with sqlite3.connect(original) as db:
            db.execute("DROP TABLE management_links")
        original_files = [original, original.with_name(original.name + "-wal"),
                          original.with_name(original.name + "-shm")]
        before = [path.read_bytes() if path.exists() else None for path in original_files[:2]]
        shm_existed = original_files[2].exists()
        constructor = ManagementStore.__init__

        def swap_after_pin(store, root, **kwargs):
            sessions.rename(self.state / "detached-sessions")
            sessions.symlink_to(self.origin / "sessions", target_is_directory=True)
            constructor(store, root, **kwargs)

        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis), \
                patch.object(ManagementStore, "__init__", swap_after_pin):
            with self.assertRaisesRegex(RecoveryError, "symbolic link|changed before persistence"):
                self._apply()
        with sqlite3.connect(original) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='management_links'").fetchone())
        self.assertEqual(before, [path.read_bytes() if path.exists() else None
                                  for path in original_files[:2]])
        self.assertEqual(shm_existed, original_files[2].exists())

    def test_directory_swap_before_pinning_is_rejected(self):
        from ai_company import pm_evidence_recovery as recovery
        sessions = self.state / "sessions"
        original = self.origin / "sessions" / "sessions.sqlite"
        opener = recovery._isolated_stage

        @contextmanager
        def swap_before_pin(*args):
            sessions.rename(self.state / "detached-sessions")
            sessions.symlink_to(self.origin / "sessions", target_is_directory=True)
            with opener(*args) as staged:
                yield staged

        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis), \
                patch.object(recovery, "_isolated_stage", swap_before_pin):
            with self.assertRaisesRegex(RecoveryError, "could not be staged"):
                self._apply()
        with sqlite3.connect(original) as db:
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='pm_recovery_revisions'").fetchone())

    def test_idle_sidecars_are_removed_only_when_copy_is_published(self):
        sessions = self.state / "sessions"
        wal = sessions / "sessions.sqlite-wal"
        shm = sessions / "sessions.sqlite-shm"
        wal.write_bytes(b"")
        shm.write_bytes(b"idle")
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            self.assertEqual(self._apply()["state"], "saved")
        self.assertFalse(wal.exists())
        self.assertFalse(shm.exists())

    def test_nonempty_copy_wal_is_rejected_without_unlink(self):
        wal = self.state / "sessions" / "sessions.sqlite-wal"
        wal.write_bytes(b"pending")
        with patch("ai_company.pm_evidence_recovery.diagnose", return_value=self.diagnosis):
            with self.assertRaisesRegex(RecoveryError, "active SQLite sidecars"):
                self._apply()
        self.assertEqual(wal.read_bytes(), b"pending")


if __name__ == "__main__":
    unittest.main()
