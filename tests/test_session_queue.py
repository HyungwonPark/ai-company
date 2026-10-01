"""Persisted quota scheduling, crash safety and repository/session identity gates."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.contracts import Task
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import RetryPolicy, SessionQueue, SessionSpec, repository_lock
from ai_company.storage import controller_lock


class Crash(BaseException):
    pass


class SessionQueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.worktree = self.root / "repo"
        self.worktree.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Queue tests")
        self.git("config", "user.email", "queue-tests@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/owner/repo.git")
        (self.worktree / "src").mkdir()
        (self.worktree / "src/example.txt").write_text("initial\n")
        self.commit()
        self.task = Task(task_id="quota-001", goal="Complete the isolated task", acceptance=("Checks pass",),
                         repository="owner/repo", base_sha=self.git("rev-parse", "HEAD"),
                         allowed_paths=("src/",), required_checks=("unit",))
        self.spec = SessionSpec(task=self.task, agent_id="codex-developer", provider="codex",
                                worktree=str(self.worktree), session_id="saved-session-1",
                                last_completed_stage="verify", checkpoint={"completed": ["implement", "verify"]})
        self.now = 1000.0
        self.calls = []
        self.queue = self.open_queue()
        self.addCleanup(lambda: self.queue.close())

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.worktree), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("add", "src")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")

    def open_queue(self):
        return SessionQueue(self.root / "state", clock=lambda: self.now)

    def restart(self):
        self.queue.close()
        self.queue = self.open_queue()

    def executor(self, *outcomes):
        sequence = iter(outcomes)

        def execute(provider, worktree, prompt, session_id, **kwargs):
            self.calls.append(dict(provider=provider, worktree=worktree, prompt=prompt,
                                   session_id=session_id, timeout=kwargs["timeout_seconds"]))
            result = next(sequence)
            if isinstance(result, BaseException):
                raise result
            return result
        return execute

    def quota(self, *, reset_at=None):
        return SessionOutcome("quota", session_id="saved-session-1", reset_at=reset_at)

    def test_reset_at_persists_required_resume_metadata(self):
        initial = self.queue.submit(self.spec)
        waiting = self.queue.run_once(executor=self.executor(self.quota(reset_at=9000)))
        self.assertEqual(waiting["status"], "WAITING_QUOTA")
        self.assertEqual(waiting["resume_at"], 9030)
        self.assertEqual(waiting["head_commit"], self.task.base_sha)
        self.assertEqual(waiting["task_id"], self.task.task_id)
        self.assertEqual(waiting["agent_id"], "codex-developer")
        self.assertEqual(waiting["session_id"], "saved-session-1")
        self.assertEqual(waiting["worktree"], str(self.worktree))
        self.assertEqual(waiting["last_completed_stage"], "verify")
        self.assertEqual(waiting["retry_count"], 0)
        self.restart()
        self.assertEqual(self.queue.get(initial["job_id"]), waiting)

    def test_no_reset_uses_bounded_exponential_backoff(self):
        policy = RetryPolicy(backoff_initial_seconds=10, backoff_max_seconds=25, retries_per_cycle=10)
        self.queue.submit(self.spec.model_copy(update={"retry_policy": policy}))
        execute = self.executor(*[self.quota() for _ in range(5)])
        for delay in (10, 20, 25, 25, 25):
            result = self.queue.run_once(executor=execute)
            self.assertEqual(result["status"], "WAITING_QUOTA")
            self.assertEqual(result["resume_at"], self.now + delay)
            self.now = result["resume_at"]

    def test_waiting_time_does_not_consume_300_second_execution_budget(self):
        self.queue.submit(self.spec)
        execute = self.executor(self.quota(reset_at=self.now + 7200),
                                SessionOutcome("success", "saved-session-1"))
        waiting = self.queue.run_once(executor=execute)
        self.restart()
        self.now = waiting["resume_at"] - 1
        self.assertEqual(self.queue.run_once(executor=execute)["status"], "IDLE")
        self.assertEqual(len(self.calls), 1)
        self.now += 1
        done = self.queue.run_once(executor=execute)
        self.assertEqual(done["status"], "SESSION_COMPLETED")
        self.assertEqual(done["retry_count"], 1)
        self.assertEqual([call["timeout"] for call in self.calls], [300, 300])
        self.assertEqual([call["session_id"] for call in self.calls], ["saved-session-1"] * 2)

    def test_resume_success_is_not_repeated_after_worker_restart(self):
        self.queue.submit(self.spec)
        execute = self.executor(self.quota(), SessionOutcome("success", "saved-session-1"))
        waiting = self.queue.run_once(executor=execute)
        self.restart()
        self.now = waiting["resume_at"]
        self.assertEqual(self.queue.run_once(executor=execute)["status"], "SESSION_COMPLETED")
        self.restart()
        self.now += 10000
        self.assertEqual(self.queue.run_once(executor=execute)["status"], "IDLE")
        self.assertEqual(len(self.calls), 2)

    def test_repeated_quota_waits_again_and_cools_down_after_retry_cycle(self):
        policy = RetryPolicy(backoff_initial_seconds=5, backoff_max_seconds=10,
                             retries_per_cycle=2, cycle_cooldown_seconds=100)
        self.queue.submit(self.spec.model_copy(update={"retry_policy": policy}))
        execute = self.executor(self.quota(), self.quota(), self.quota(), self.quota())
        first = self.queue.run_once(executor=execute)
        self.now = first["resume_at"]
        second = self.queue.run_once(executor=execute)
        self.now = second["resume_at"]
        third = self.queue.run_once(executor=execute)
        self.assertEqual(third["status"], "WAITING_QUOTA")
        self.assertEqual(third["retry_count"], 2)
        self.assertEqual(third["cycle_retries"], 0)
        self.assertEqual(third["resume_at"], self.now + 100)
        self.now = third["resume_at"]
        fourth = self.queue.run_once(executor=execute)
        self.assertEqual(fourth["status"], "WAITING_QUOTA")
        self.assertEqual(fourth["retry_count"], 3)

    def test_auth_permission_approval_test_code_and_unknown_errors_never_retry(self):
        for index, category in enumerate(("authentication", "permission", "approval_required", "test_failure",
                                          "code_error", "unknown")):
            with self.subTest(category=category):
                task = self.task.model_copy(update={"task_id": f"nonretry-{index}"})
                spec = self.spec.model_copy(update={"task": task})
                job = self.queue.submit(spec)
                execute = self.executor(SessionOutcome(category, "saved-session-1"))
                blocked = self.queue.run_once(executor=execute)
                self.assertEqual(blocked["status"], "BLOCKED")
                self.assertIsNone(blocked["resume_at"])
                self.now += 100000
                self.restart()
                self.assertEqual(self.queue.run_once(executor=execute)["status"], "IDLE")
                self.assertEqual(self.queue.get(job["job_id"])["attempt_count"], 1)

    def test_transient_network_wait_is_separate_from_quota(self):
        self.queue.submit(self.spec)
        result = self.queue.run_once(executor=self.executor(SessionOutcome("transient_network", "saved-session-1")))
        self.assertEqual(result["status"], "WAITING_RETRY")
        self.assertEqual(result["resume_at"], self.now + 60)

    def test_native_budget_block_has_readable_reason_cost_and_no_automatic_retry(self):
        spec = self.spec.model_copy(update={"provider":"claude"})
        job = self.queue.submit(spec)
        native = {"type":"result","subtype":"error_max_budget_usd","session_id":"saved-session-1","is_error":True}
        result = {"total_cost_usd":.3924525,"native_terminal":native}
        execute = self.executor(SessionOutcome("code_error","saved-session-1",result=result))
        blocked = self.queue.run_once(executor=execute)
        self.assertEqual(blocked["status"],"BLOCKED")
        self.assertIn("실행 비용",blocked["reason"])
        self.assertEqual(blocked["result"],result)
        self.assertIsNone(blocked["resume_at"])
        self.now += 10000; self.restart()
        self.assertEqual(self.queue.run_once(executor=execute)["status"],"IDLE")
        self.assertEqual(self.queue.get(job["job_id"])["attempt_count"],1)

    def test_other_session_or_model_text_cannot_label_a_failure_as_native_budget(self):
        for index,terminal in enumerate(({"type":"result","subtype":"error_max_budget_usd","session_id":"other"},
                {"type":"assistant","subtype":"error_max_budget_usd","session_id":"saved-session-1"},
                {"type":"result","subtype":"error_max_budget_usd","session_id":"saved-session-1","is_error":False},
                {"type":"result","subtype":[],"session_id":"saved-session-1"})):
            task = self.task.model_copy(update={"task_id":f"budget-label-{index}"})
            self.queue.submit(self.spec.model_copy(update={"task":task,"provider":"claude"}))
            result = self.queue.run_once(executor=self.executor(SessionOutcome("code_error","saved-session-1",result={"native_terminal":terminal})))
            self.assertEqual(result["reason"],"non-retryable session outcome: code_error")
    def test_context_handoff_requires_explicit_new_session_and_keeps_checkpoint(self):
        self.queue.submit(self.spec)
        execute = self.executor(SessionOutcome("context_exhausted", "saved-session-1"),
                                SessionOutcome("success", "new-session-2"))
        result = self.queue.run_once(executor=execute)
        self.assertEqual(result["status"], "NEEDS_CONTEXT_HANDOFF")
        self.assertEqual(json.loads(Path(result["handoff_file"]).read_text())["last_completed_stage"], "verify")
        self.restart()
        self.assertEqual(self.queue.run_once(executor=execute)["status"], "IDLE")
        ready = self.queue.handoff(result["job_id"])
        self.assertIsNone(ready["session_id"])
        done = self.queue.run_once(executor=execute)
        self.assertEqual(done["session_id"], "new-session-2")
        self.assertIsNone(self.calls[-1]["session_id"])
        self.assertIn("saved-session-1", self.calls[-1]["prompt"])
        self.assertEqual(done["retry_count"], 0)

    def test_quota_cannot_be_resumed_as_a_new_session_handoff(self):
        self.queue.submit(self.spec)
        job = self.queue.run_once(executor=self.executor(self.quota()))
        with self.assertRaisesRegex(ExecutionBlocked, "only allowed for context"):
            self.queue.handoff(job["job_id"])

    def test_repository_head_change_blocks_resume(self):
        self.queue.submit(self.spec)
        execute = self.executor(self.quota())
        waiting = self.queue.run_once(executor=execute)
        (self.worktree / "src/example.txt").write_text("external commit\n")
        self.commit()
        self.now = waiting["resume_at"]
        result = self.queue.run_once(executor=execute)
        self.assertEqual(result["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(len(self.calls), 1)

    def test_dirty_and_untracked_content_changes_block_resume(self):
        for filename in ("src/example.txt", "untracked.txt"):
            with self.subTest(filename=filename):
                path = self.worktree / filename
                path.write_text("interrupted agent edit\n")
                spec = self.spec.model_copy(update={"agent_id": "file-" + path.stem})
                self.queue.submit(spec)
                execute = self.executor(self.quota())
                waiting = self.queue.run_once(executor=execute)
                # Git status remains the same, only the actual content changes.
                path.write_text("external replacement\n")
                self.now = waiting["resume_at"]
                result = self.queue.run_once(executor=execute)
                self.assertEqual(result["status"], "NEEDS_RECONCILIATION")

    def test_agent_edits_before_quota_are_checkpointed_and_preserved(self):
        self.queue.submit(self.spec)

        def interrupted(*args, **kwargs):
            (self.worktree / "src/example.txt").write_text("completed agent edit\n")
            self.commit()
            return self.quota()

        waiting = self.queue.run_once(executor=interrupted)
        self.assertNotEqual(waiting["head_commit"], self.task.base_sha)
        self.assertEqual(waiting["head_commit"], self.git("rev-parse", "HEAD"))
        self.restart()
        self.now = waiting["resume_at"]
        result = self.queue.run_once(executor=self.executor(SessionOutcome("success", "saved-session-1")))
        self.assertEqual(result["status"], "SESSION_COMPLETED")

    def test_changed_session_id_requires_reconciliation(self):
        self.queue.submit(self.spec)
        result = self.queue.run_once(executor=self.executor(SessionOutcome("success", "another-session")))
        self.assertEqual(result["status"], "NEEDS_RECONCILIATION")

    def test_missing_session_id_stays_waiting_without_starting_fresh(self):
        self.queue.submit(self.spec.model_copy(update={"session_id": None}))
        result = self.queue.run_once(executor=self.executor(SessionOutcome("quota")))
        self.assertEqual(result["status"], "WAITING_QUOTA")
        self.assertIsNone(result["resume_at"])
        self.restart()
        self.now += 10000
        self.assertEqual(self.queue.run_once()["status"], "IDLE")

    def test_process_crash_does_not_replay_an_expired_running_lease(self):
        job = self.queue.submit(self.spec)
        execute = self.executor(Crash())
        with self.assertRaises(Crash):
            self.queue.run_once(executor=execute)
        self.assertEqual(self.queue.get(job["job_id"])["status"], "RUNNING")
        self.now += 10000
        self.restart()
        self.assertEqual(self.queue.run_once(executor=execute)["status"], "IDLE")
        self.assertEqual(self.queue.get(job["job_id"])["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(len(self.calls), 1)

    def test_still_running_previous_execution_blocks_resume(self):
        self.queue.submit(self.spec)
        result = self.queue.run_once(executor=self.executor(self.quota()))
        self.now = result["resume_at"]
        with patch("ai_company.sessions.execution_alive", return_value=True):
            blocked = self.queue.run_once(executor=self.executor())
        self.assertEqual(blocked["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(len(self.calls), 1)

    def test_worker_and_repository_locks_prevent_concurrent_calls(self):
        job = self.queue.submit(self.spec)
        with controller_lock(self.queue.root / "session-worker"):
            self.assertEqual(self.queue.run_once()["status"], "BUSY")
        with repository_lock(job["repository_snapshot"]):
            self.assertEqual(self.queue.run_once()["status"], "IDLE")
        self.assertEqual(self.queue.get(job["job_id"])["attempt_count"], 0)

    def test_lease_owner_fences_stale_result_writes(self):
        job = self.queue.submit(self.spec)
        claimed = self.queue._claim(job["job_id"])
        old_owner = claimed["lease_owner"]
        changed = dict(claimed, lease_owner="replacement-owner")
        with self.queue.db:
            self.queue._save(changed)
        with self.assertRaisesRegex(ExecutionBlocked, "lease lost"):
            self.queue._finish(claimed, "SESSION_COMPLETED", "stale completion", old_owner)
        self.assertEqual(self.queue.get(job["job_id"])["lease_owner"], "replacement-owner")

    def test_crashed_worker_guard_blocks_other_jobs_and_other_queue_roots(self):
        first = self.queue.submit(self.spec)
        with self.assertRaises(Crash):
            self.queue.run_once(executor=self.executor(Crash()))
        self.assertTrue(self.queue._guard_path(first).exists())
        second_spec = self.spec.model_copy(update={"agent_id": "second-agent"})
        second = self.queue.submit(second_spec)
        self.restart()
        self.assertEqual(self.queue.run_once(executor=self.executor())["status"], "IDLE")
        self.assertEqual(self.queue.get(second["job_id"])["attempt_count"], 0)
        with SessionQueue(self.root / "other-state", clock=lambda: self.now) as other:
            third = other.submit(self.spec.model_copy(update={"agent_id": "third-agent"}))
            self.assertEqual(other.run_once(executor=self.executor())["status"], "IDLE")
            self.assertEqual(other.get(third["job_id"])["attempt_count"], 0)
        self.assertEqual(len(self.calls), 1)

    def test_crash_after_wait_commit_clears_own_guard_without_replaying(self):
        job = self.queue.submit(self.spec)
        clear = self.queue._clear_guard

        def crash_after_wait(saved):
            if saved["status"] == "WAITING_QUOTA":
                raise Crash()
            clear(saved)

        with patch.object(self.queue, "_clear_guard", side_effect=crash_after_wait):
            with self.assertRaises(Crash):
                self.queue.run_once(executor=self.executor(self.quota()))
        self.assertEqual(self.queue.get(job["job_id"])["status"], "WAITING_QUOTA")
        self.assertTrue(self.queue._guard_path(job).exists())
        self.restart()
        self.assertEqual(self.queue.run_once(executor=self.executor())["status"], "IDLE")
        self.assertFalse(self.queue._guard_path(job).exists())
        self.assertEqual(len(self.calls), 1)

    def test_missing_repository_does_not_starve_another_due_job(self):
        first = self.queue.submit(self.spec)
        original = self.worktree
        second_tree = self.root / "repo-two"
        subprocess.run(["git", "clone", "-q", str(original), str(second_tree)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(second_tree), "remote", "set-url", "origin",
                        "https://github.com/owner/repo.git"], check=True, capture_output=True)
        self.now += 1
        second = self.queue.submit(self.spec.model_copy(update={"agent_id": "second-agent", "worktree": str(second_tree)}))
        original.rename(self.root / "moved-repo")
        result = self.queue.run_once(executor=self.executor(SessionOutcome("success", "saved-session-1")))
        self.assertEqual(result["job_id"], second["job_id"])
        self.assertEqual(result["status"], "SESSION_COMPLETED")
        self.assertEqual(self.queue.get(first["job_id"])["status"], "NEEDS_RECONCILIATION")

    def test_duplicate_submission_is_idempotent_and_changed_spec_is_rejected(self):
        job = self.queue.submit(self.spec)
        self.assertEqual(job, self.queue.submit(self.spec))
        done = self.queue.run_once(executor=self.executor(SessionOutcome("success", "saved-session-1")))
        self.restart()
        self.assertEqual(done, self.queue.submit(self.spec))
        changed = self.spec.model_copy(update={"last_completed_stage": "review"})
        with self.assertRaisesRegex(ExecutionBlocked, "different session specification"):
            self.queue.submit(changed)
        self.assertEqual(len(self.calls), 1)

    def test_cli_submission_and_status_persist_across_processes(self):
        task_file = self.root / "task.json"
        task_file.write_text(self.task.model_dump_json())
        state = self.root / "cli-state"
        submit = [sys.executable, "-m", "ai_company.cli", "session", "submit", "--task", str(task_file),
                  "--agent", "codex", "--agent-id", "cli-codex", "--worktree", str(self.worktree),
                  "--session-id", "saved-session-1", "--state-dir", str(state)]
        first = json.loads(subprocess.run(submit, check=True, capture_output=True, text=True).stdout)
        duplicate = json.loads(subprocess.run(submit, check=True, capture_output=True, text=True).stdout)
        status = subprocess.run([sys.executable, "-m", "ai_company.cli", "session", "status",
                                 "--state-dir", str(state)], check=True, capture_output=True, text=True)
        self.assertEqual(first, duplicate)
        self.assertEqual(first["status"], "READY")
        self.assertEqual(first["attempt_count"], 0)
        self.assertEqual(json.loads(status.stdout), [first])

    def test_nonfinite_reset_uses_backoff_and_past_reset_gets_grace(self):
        policy = RetryPolicy()
        self.assertEqual(policy.next_time(self.now, 0, 0, float("nan"))[0], self.now + 60)
        self.assertEqual(policy.next_time(self.now, 0, 0, self.now - 10)[0], self.now + 30)

    def test_malformed_execution_guard_blocks_its_job_without_crashing_worker(self):
        job = self.queue.submit(self.spec)
        self.queue._guard_path(job).write_text("{invalid-json")
        self.assertEqual(self.queue.run_once(executor=self.executor())["status"], "IDLE")
        result = self.queue.get(job["job_id"])
        self.assertEqual(result["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(result["attempt_count"], 0)


if __name__ == "__main__":
    unittest.main()
