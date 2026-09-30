"""Non-operating tests for timeout evidence and operator-approved recovery."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from ai_company.manual_unknown_review import (
    BatchState,
    ReviewTarget,
    advance_batch,
    manual_reservation_id,
    validate_approval,
)
from ai_company.review_protocol import (settled_review, timeout_evidence,
                                         validate_timeout_evidence, workflow_completion)
from ai_company.shared_calls import CapacityUnavailable, SharedCallLedger, SharedCallError


RUNNER = Path(__file__).resolve().parents[1] / "scripts/review_pr_with_claude.py"
spec = importlib.util.spec_from_file_location("review_candidate", RUNNER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class UnknownReviewPathTests(unittest.TestCase):
    def target(self):
        return ReviewTarget(35, "a" * 40, "b" * 40, "c" * 64)

    def approval(self, old="old-unknown"):
        target = self.target()
        return ({"approval_id": hashlib.sha256(b"approval").hexdigest(), "pr": target.pr,
                 "head": target.head, "base": target.base, "patch_sha256": target.patch_sha256,
                 "unknown_reservation_id": old, "reason": "manual review after operator evidence",
                 "approved_by": "operator-edward", "approved_at": "2026-09-30T00:00:00Z"})

    def ledger(self, root):
        ledger = SharedCallLedger.initialize(root / "calls.sqlite", [
            ("claude", "review", "group", "UNKNOWN", None, "timeout", 4, 12, 3.5, 0)])
        ledger.db.execute("INSERT INTO reservations(reservation_id,owner,group_id,state,created_at) "
                          "VALUES (?,?,?,?,?)", ("old-unknown", "old", "group", "UNKNOWN", 1))
        self.addCleanup(ledger.close)
        return ledger

    def test_completion_requires_workflow_and_children(self):
        not_done = workflow_completion(main_process_terminated=True, child_tasks_terminated=False,
                                       workflow_completed=False, result_saved=True, usage_saved=True,
                                       reservation_settled=True, account_protection_confirmed=True)
        self.assertFalse(not_done["complete"])
        self.assertEqual(not_done["reason_code"], "child_tasks_terminated")
        done = workflow_completion(main_process_terminated=True, child_tasks_terminated=True,
                                   workflow_completed=True, result_saved=True, usage_saved=True,
                                   reservation_settled=True, account_protection_confirmed=True)
        self.assertTrue(done["complete"])
        self.assertTrue(settled_review(reservation_state="SETTLED", settlement_event="e",
                                       result_saved=True, usage_saved=True))

    def test_runner_does_not_pass_main_result_without_workflow_event(self):
        nonce = "protocol"
        events = [
            {"type": "control_response", "response": {"request_id": nonce + "-before",
             "response": {"applied": review.APPLIED, "has_errors": False}}},
            {"type": "assistant", "message": {"model": review.MODEL, "content": []}},
            {"type": "control_response", "response": {"request_id": nonce + "-after",
             "response": {"applied": review.APPLIED, "has_errors": False}}},
            {"type": "system", "subtype": "task_started", "task_id": "unfinished", "session_id": "s"},
            {"type": "result", "session_id": "s", "result_index": 0,
             "subtype": "success", "is_error": False, "terminal_reason": "completed",
             "stop_reason": "end_turn", "queued_turn_count": 0, "permission_denials": [],
             "subagent_stats": {"spawned": 0, "completed": 0, "failed": 0,
                                "killed": {}, "refused": {}},
             "modelUsage": {"opus": {"inputTokens": 1, "outputTokens": 1}},
             "result": "판정: PASS\n대상 HEAD: " + "a" * 40 + "\n패치 SHA-256: " + "b" * 64
                       + "\n검토 범위: 전체\n미검토: 없음"},
        ]
        summary = review.summarize(events, nonce, 0, input_verified=True,
                                   head="a" * 40, diff_sha256="b" * 64,
                                   workflow_expected=True)
        self.assertFalse(summary["completion_verified"])
        self.assertFalse(summary["execution_protocol"]["checks"]["child_tasks_terminated"])
        self.assertFalse(summary["execution_protocol"]["checks"]["workflow_completed"])
        self.assertIn(summary["execution_protocol"]["reason_code"],
                      {"child_tasks_terminated", "workflow_completed"})

    def test_timeout_evidence_cannot_authorize_settlement(self):
        record = timeout_evidence(main_process_state="terminated_after_timeout",
                                  child_tasks_terminated=False, workflow_completed=False,
                                  result_saved=False, usage_saved=False,
                                  reservation_state="STARTED",
                                  account_protection_state="UNKNOWN_TIMEOUT_UNRESOLVED")
        self.assertFalse(record["settlement_allowed"])
        self.assertFalse(record["cost_estimate_allowed"])
        with self.assertRaises(ValueError):
            validate_timeout_evidence({**record, "unexpected": True})

    def test_normal_reserve_stays_blocked_without_operator_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = self.ledger(Path(temp))
            with self.assertRaises(CapacityUnavailable) as raised:
                ledger.reserve("new", "new", "claude", "review", "group")
            self.assertEqual(raised.exception.reason_code, "account_unknown_timeout")
            self.assertIsNone(ledger.reservation("new"))

    def test_approved_reservation_is_new_and_old_unknown_is_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = self.ledger(root)
            target, approval = self.target(), self.approval()
            rid = manual_reservation_id(approval, target)
            self.assertEqual(validate_approval(approval, target, unknown_reservation_id="old-unknown"),
                             validate_approval(approval, target, unknown_reservation_id="old-unknown"))
            row = ledger.reserve_approved_unknown(rid, "new-owner", "claude", "review", "group",
                                                  approval, target, "old-unknown")
            self.assertEqual(row["state"], "RESERVED")
            self.assertEqual(ledger.reservation("old-unknown")["state"], "UNKNOWN")
            again = ledger.reserve_approved_unknown(rid, "new-owner", "claude", "review", "group",
                                                    approval, target, "old-unknown")
            self.assertEqual(again, row)
            ledger.started(rid, "new-owner", {"kind": "manual_review"})
            ledger.settle(rid, "new-owner", "new-event",
                          {"category": "success", "duration_seconds": 1, "total_cost_usd": 1},
                          terminated=True)
            self.assertEqual(ledger.reservation("old-unknown")["state"], "UNKNOWN")
            self.assertEqual(ledger.reservation(rid)["state"], "SETTLED")
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM settlement_events").fetchone()[0], 1)
            self.assertEqual(ledger.account("claude", "review", "group")["state"], "UNKNOWN")

    def test_invalid_approval_and_quota_recovery_cannot_bypass_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = self.ledger(Path(temp))
            target, approval = self.target(), self.approval()
            with self.assertRaises(SharedCallError):
                ledger.reserve_approved_unknown("force", "owner", "claude", "review", "group",
                                                {**approval, "reason": ""}, target, "old-unknown")
            with self.assertRaises(SharedCallError):
                ledger.reconcile_settled_quota("old-unknown", "claude", "review", "group", "event")

    def test_timeout_record_is_atomic_unknown_and_has_no_usage_settlement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = SharedCallLedger.initialize(root / "calls.sqlite", [
                ("claude", "review", "group", "AVAILABLE", None, None, 0, 0, 0, 0)])
            ledger.reserve("attempt", "owner", "claude", "review", "group")
            ledger.started("attempt", "owner", {"kind": "executor_invocation"})
            record = timeout_evidence(main_process_state="terminated_after_timeout",
                                      child_tasks_terminated=False, workflow_completed=False,
                                      result_saved=False, usage_saved=False, reservation_state="STARTED",
                                      account_protection_state="UNKNOWN_TIMEOUT_UNRESOLVED")
            ledger.uncertain_timeout("attempt", "owner", record)
            row = ledger.reservation("attempt")
            self.assertEqual(row["state"], "UNKNOWN")
            self.assertEqual(json.loads(row["result"])["category"], "unknown_timeout")
            account = ledger.account("claude", "review", "group")
            self.assertEqual((account["calls"], account["cost_usd"], account["state"]), (0, 0, "UNKNOWN"))
            ledger.close()

    def test_batch_is_sequential_and_stops_on_unknown(self):
        state = BatchState(0)
        state = advance_batch(state, "PASS", total=4)
        self.assertEqual(state.next_index, 1)
        state = advance_batch(state, "UNKNOWN", total=4)
        self.assertEqual((state.next_index, state.stopped_reason), (1, "unknown"))
        self.assertEqual(advance_batch(state, "PASS", total=4), state)

    def test_binary_patch_envelope_is_bound_as_complete_input(self):
        patch = ("diff --git a/assets/icon.png b/assets/icon.png\n"
                 "new file mode 100644\nGIT binary patch\nliteral 4\n"
                 "Jc${Nk\n")
        self.assertEqual(review.patch_files(patch), ["assets/icon.png"])


if __name__ == "__main__":
    unittest.main()
