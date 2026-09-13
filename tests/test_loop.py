"""Behavioral gates for task identity, independent review, budgets and crash recovery."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.adapters.fake import FakeAdapter, FakeVerifier
from ai_company.contracts import Policy, Task
from ai_company.harness import Registry
from ai_company.runtime import ExecutionBlocked, Runner
from ai_company.storage import Ledger, controller_lock
from ai_company.workflows import run_task


class Crash(BaseException):
    """A process interruption, not an ordinary adapter error."""


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class HangingAdapter(FakeAdapter):
    confirmed = False

    def status(self, run_id):
        return "running"

    def cancel(self, run_id):
        return self.confirmed


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = Task(task_id="test-001", goal="Implement the acceptance criteria",
                         acceptance=("Independent tests and review pass",), repository="owner/repo",
                         base_sha="a" * 40, allowed_paths=("src/", "tests/"), required_checks=("unit",))
        self.developer = FakeAdapter("fake-developer", "developer")
        self.reviewer = FakeAdapter("fake-reviewer", "reviewer", reject_first=1)
        self.registry = Registry()
        self.registry.register("developer", self.developer)
        self.registry.register("reviewer", self.reviewer)

    def run_loop(self, **kwargs):
        return run_task(self.task, self.root, self.registry, **kwargs)

    def run_count(self):
        with sqlite3.connect(self.root / "executions.sqlite") as db:
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def test_rejection_feedback_and_exact_revision_reach_reviewer(self):
        # A simulation run must not need a network connection or model credentials.
        with patch("socket.create_connection", side_effect=AssertionError("network access")):
            state = self.run_loop()
        self.assertEqual(state["status"], "DEMO_READY")
        self.assertEqual(state["attempt"], 1)
        self.assertEqual(self.run_count(), 4)
        repaired = self.developer.requests[1].context
        reviewed = self.reviewer.requests[1].context
        self.assertEqual(repaired.task, self.task)
        self.assertEqual(repaired.feedback[0].finding_id, "R1")
        self.assertEqual(reviewed.input_sha, state["candidate"])
        self.assertEqual(reviewed.verification.head_sha, state["candidate"])
        self.assertEqual(state["review"]["resolved_findings"], ["R1"])
        self.assertNotEqual(self.developer.requests[0].workspace, self.reviewer.requests[0].workspace)

    def test_duplicate_submission_uses_persisted_result(self):
        first = self.run_loop()
        second = self.run_loop()
        self.assertEqual(first, second)
        self.assertEqual(self.run_count(), 4)
        self.assertEqual(len(self.developer.requests), 2)

    def test_changed_spec_same_id_is_rejected_without_execution(self):
        self.run_loop()
        self.task = self.task.model_copy(update={"acceptance": ("Weakened criterion",)})
        with self.assertRaisesRegex(ExecutionBlocked, "different spec"):
            self.run_loop()
        self.assertEqual(self.run_count(), 4)

    def test_changed_policy_or_adapter_is_rejected(self):
        self.run_loop()
        with self.assertRaisesRegex(ExecutionBlocked, "policy"):
            self.run_loop(policy=Policy(max_repairs=1))
        new = Registry()
        new.register("developer", FakeAdapter("another-developer", "developer"))
        new.register("reviewer", self.reviewer)
        with self.assertRaises(ExecutionBlocked):
            run_task(self.task, self.root, new)

    def test_zero_repairs_stops_before_an_extra_implementation(self):
        self.task = self.task.model_copy(update={"max_repairs": 0})
        state = self.run_loop()
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(self.run_count(), 2)

    def test_policy_caps_the_task_repair_budget(self):
        self.reviewer.reject_first = 100
        state = self.run_loop(policy=Policy(max_repairs=1))
        self.assertEqual(state["status"], "STOPPED")
        self.assertEqual(len(self.developer.requests), 2)

    def test_execution_count_budget_prevents_a_new_agent_call(self):
        state = self.run_loop(policy=Policy(max_runs=1))
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("count budget", state["reason"])
        self.assertEqual(len(self.reviewer.requests), 0)

    def test_expired_task_does_not_continue_after_restart(self):
        initial = self.run_loop(interrupt_after=["implement"])
        self.assertEqual(initial["status"], "RUNNING")
        with patch("ai_company.workflows.time.time", return_value=time.time() + 1000):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(self.run_count(), 1)

    def test_restart_at_checkpoint_resumes_without_implementation_replay(self):
        self.run_loop(interrupt_after=["verify"])
        new_developer = FakeAdapter("fake-developer", "developer")
        new_reviewer = FakeAdapter("fake-reviewer", "reviewer", 1)
        registry = Registry()
        registry.register("developer", new_developer)
        registry.register("reviewer", new_reviewer)
        state = run_task(self.task, self.root, registry)
        self.assertEqual(state["status"], "DEMO_READY")
        self.assertEqual([r.context.attempt for r in new_developer.requests], [1])

    def test_crash_after_effect_record_reuses_completed_result(self):
        original = Ledger.finish

        def finish_then_crash(ledger, run_id, result_json):
            original(ledger, run_id, result_json)
            raise Crash()

        with patch.object(Ledger, "finish", finish_then_crash):
            with self.assertRaises(Crash):
                self.run_loop()
        state = self.run_loop()
        self.assertEqual(state["status"], "DEMO_READY")
        self.assertEqual(len(self.developer.requests), 2)
        self.assertEqual(self.run_count(), 4)

    def test_crash_during_start_blocks_relaunch(self):
        original = self.developer.start

        def start_then_crash(request):
            original(request)
            raise Crash()

        with patch.object(self.developer, "start", start_then_crash):
            with self.assertRaises(Crash):
                self.run_loop()
        state = self.run_loop()
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(len(self.developer.requests), 1)
        self.assertEqual(self.run_count(), 1)
        self.assertEqual(self.run_loop()["status"], "NEEDS_RECONCILIATION")

    def test_timeout_distinguishes_confirmed_and_uncertain_termination(self):
        for confirmed in (True, False):
            with self.subTest(confirmed=confirmed), tempfile.TemporaryDirectory() as root:
                adapter = HangingAdapter("hanging", "developer")
                adapter.confirmed = confirmed
                registry = Registry()
                registry.register("developer", adapter)
                registry.register("reviewer", self.reviewer)
                clock = Clock()
                state = run_task(self.task, Path(root), registry, runner=Runner(clock, clock.sleep))
                self.assertEqual(state["status"], "BLOCKED" if confirmed else "NEEDS_RECONCILIATION")
                run_task(self.task, Path(root), registry)
                self.assertEqual(len(adapter.requests), 1)

    def test_stale_result_is_not_accepted(self):
        original = self.reviewer.collect

        def stale(run_id):
            return original(run_id).model_copy(update={"run_id": "0" * 64})

        with patch.object(self.reviewer, "collect", stale):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("stale", state["reason"])

    def test_review_of_another_revision_is_rejected(self):
        original = self.reviewer.collect
        with patch.object(self.reviewer, "collect", lambda rid: original(rid).model_copy(update={"commit_sha": "b" * 40})):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("different commit", state["reason"])

    def test_pass_cannot_drop_unresolved_feedback(self):
        original = self.reviewer.collect
        with patch.object(self.reviewer, "collect", lambda rid: original(rid).model_copy(update={"resolved_findings": ()})):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("unresolved", state["reason"])

    def test_malformed_success_does_not_count_as_approval(self):
        original = self.reviewer.collect
        with patch.object(self.reviewer, "collect", lambda rid: original(rid).model_copy(update={"evidence": ()})):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertIn("malformed", state["reason"])

    def test_missing_skipped_cancelled_pending_checks_block(self):
        for outcome in (None, "skipped", "cancelled", "pending"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as root:
                verifier = FakeVerifier()
                original = verifier.verify

                def verify(task, policy, candidate):
                    evidence = original(task, policy, candidate)
                    checks = () if outcome is None else (evidence.checks[0].model_copy(update={"outcome": outcome}),)
                    return evidence.model_copy(update={"checks": checks})

                with patch.object(verifier, "verify", verify):
                    state = run_task(self.task, Path(root), self.registry, verifier=verifier)
                self.assertEqual(state["status"], "BLOCKED")
                self.assertEqual(len(self.reviewer.requests), 0)

    def test_failed_check_routes_to_repair_before_review(self):
        verifier = FakeVerifier()
        original = verifier.verify
        calls = 0

        def verify(task, policy, candidate):
            nonlocal calls
            calls += 1
            evidence = original(task, policy, candidate)
            if calls == 1:
                return evidence.model_copy(update={"checks": (evidence.checks[0].model_copy(update={"outcome": "failure"}),)})
            return evidence

        with patch.object(verifier, "verify", verify):
            state = self.run_loop(verifier=verifier)
        self.assertEqual(state["status"], "DEMO_READY")
        self.assertEqual(len(self.reviewer.requests), 1)
        self.assertEqual(self.developer.requests[1].context.feedback[0].finding_id, "CI-0")

    def test_ci_for_different_spec_or_revision_is_rejected(self):
        for field, value in (("head_sha", "b" * 40), ("base_sha", "c" * 40),
                             ("tested_sha", "d" * 40), ("task_digest", "0" * 64),
                             ("policy_digest", "1" * 64)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                verifier = FakeVerifier()
                original = verifier.verify
                with patch.object(verifier, "verify", lambda t, p, c: original(t, p, c).model_copy(update={field: value})):
                    state = run_task(self.task, Path(root), self.registry, verifier=verifier)
                self.assertEqual(state["status"], "BLOCKED")

    def test_head_or_base_change_invalidates_completion_including_cached_result(self):
        for changed_part in ("head", "base"):
            for cached in (True, False):
                with self.subTest(part=changed_part, cached=cached), tempfile.TemporaryDirectory() as root:
                    verifier = FakeVerifier()
                    if cached:
                        run_task(self.task, Path(root), self.registry, verifier=verifier)
                    observed = lambda head, base: ("b" * 40, base) if changed_part == "head" else (head, "c" * 40)
                    with patch.object(verifier, "observe", side_effect=observed):
                        state = run_task(self.task, Path(root), self.registry, verifier=verifier)
                    self.assertEqual(state["status"], "BLOCKED")
                    self.assertIn("head or base changed", state["reason"])

    def test_cli_persists_across_processes_and_returns_a_failure_exit_code(self):
        task_path = self.root / "task.json"
        task_path.write_text(self.task.model_dump_json())
        command = [sys.executable, "-m", "ai_company.cli", "demo", "--task", str(task_path),
                   "--state-dir", str(self.root / "cli-state")]
        first = subprocess.run(command, capture_output=True, text=True, check=True)
        second = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        failed = subprocess.run(command + ["--reject-first", "20"], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout)["status"], "BLOCKED")

    def test_adapter_environment_failure_does_not_trigger_code_repair(self):
        with patch.object(self.developer, "healthcheck", return_value=False):
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(self.run_count(), 0)

    def test_unknown_adapter_failure_requires_reconciliation(self):
        with patch.object(self.developer, "start", side_effect=RuntimeError("provider failure")):
            state = self.run_loop()
        self.assertEqual(state["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(self.run_count(), 1)

    def test_second_controller_cannot_start(self):
        with controller_lock(self.root):
            with self.assertRaisesRegex(ExecutionBlocked, "another controller"):
                self.run_loop()
        self.assertFalse((self.root / "executions.sqlite").exists())

    def test_new_compatible_adapter_requires_only_registry_change(self):
        alternate = FakeAdapter("another-provider", "developer")
        registry = Registry()
        registry.register("developer", alternate)
        registry.register("reviewer", self.reviewer)
        state = run_task(self.task, self.root, registry)
        self.assertEqual(state["status"], "DEMO_READY")
        self.assertEqual(len(alternate.requests), 2)

    def test_role_and_workspace_boundaries(self):
        with self.assertRaises(ExecutionBlocked):
            Registry().register("reviewer", self.developer)
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "workspaces").symlink_to(outside)
            state = self.run_loop()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(self.run_count(), 0)

    def test_invalid_task_specs_are_rejected(self):
        for change in ({"allowed_paths": ("../outside",)}, {"allowed_paths": ("/absolute",)},
                       {"allowed_paths": (".git/config",)}, {"required_checks": ("unit", "unit")},
                       {"max_repairs": True}, {"acceptance": ()}, {"goal": " "}, {"task_id": "../task"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                Task.model_validate(self.task.model_dump() | change)


if __name__ == "__main__":
    unittest.main()
