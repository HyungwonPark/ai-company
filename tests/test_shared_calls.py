"""Two queue roots sharing one credential and two host slots."""

from concurrent.futures import ThreadPoolExecutor
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from ai_company.shared_calls import CapacityUnavailable, SharedCallError, SharedCallLedger
from ai_company.dispatcher import Dispatcher
from ai_company.runtime import ExecutionBlocked


class SharedCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "calls.db"
        ledger = SharedCallLedger.initialize(self.path, [
            ("codex", "primary", "account-a", "AVAILABLE", None, None, 0, 0, 0, 0),
            ("claude", "primary", "account-b", "AVAILABLE", None, None, 0, 0, 0, 0),
            ("codex", "third", "account-c", "AVAILABLE", None, None, 0, 0, 0, 0),
            ("codex", "alias", "account-a", "AVAILABLE", None, None, 0, 0, 0, 0),
        ], clock=lambda: 100)
        ledger.close()

    def open(self):
        ledger = SharedCallLedger(self.path, clock=lambda: 100)
        self.addCleanup(ledger.close)
        return ledger

    def test_two_queues_reserve_atomically_and_alias_cannot_evade(self):
        def reserve(owner, group, credential, provider="codex"):
            ledger = SharedCallLedger(self.path, clock=lambda: 100)
            try:
                return ledger.reserve(owner, owner, provider, credential, group)["state"]
            except CapacityUnavailable as exc:
                return exc.reason
            finally:
                ledger.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda args: reserve(*args), [
                ("queue-a", "account-a", "primary"), ("queue-b", "account-a", "alias")]))
        self.assertEqual(outcomes.count("RESERVED"), 1)
        self.assertEqual(outcomes.count("shared account is reserved"), 1)
        holder = "queue-a" if outcomes[0] == "RESERVED" else "queue-b"
        other = "queue-b" if holder == "queue-a" else "queue-a"
        ledger = self.open()
        self.assertEqual(ledger.reserve("second-account", other, "claude", "primary", "account-b")["state"], "RESERVED")
        with self.assertRaisesRegex(CapacityUnavailable, "host slots"):
            ledger.reserve("third-account", "queue-c", "codex", "third", "account-c")

    def test_restart_unknown_process_retains_slot_and_duplicate_settlement_is_once(self):
        ledger = self.open()
        ledger.reserve("attempt", "queue-a", "codex", "primary", "account-a")
        ledger.started("attempt", "queue-a", {"pid": 47})
        ledger.close()
        recovered = self.open()
        self.assertEqual(recovered.reservation("attempt")["state"], "STARTED")
        with self.assertRaises(CapacityUnavailable):
            recovered.reserve("other", "queue-b", "codex", "alias", "account-a")
        recovered.uncertain("attempt", "queue-a", "termination unconfirmed")
        with self.assertRaises(CapacityUnavailable):
            recovered.reserve("other", "queue-b", "codex", "primary", "account-a")
        result = {"category": "quota", "duration_seconds": 10, "total_cost_usd": 0.25, "reset_at": 200}
        self.assertTrue(recovered.settle("attempt", "queue-a", "event", result, terminated=True))
        self.assertFalse(recovered.settle("attempt", "queue-a", "event", result, terminated=True))
        account = recovered.account("codex", "alias", "account-a")
        self.assertEqual((account["calls"], account["runtime_seconds"], account["cost_usd"]), (1, 10, 0.25))
        self.assertEqual(account["state"], "UNKNOWN")  # explicitly uncertain requires separate reconciliation
        with self.assertRaises(SharedCallError):
            recovered.settle("attempt", "queue-a", "different-event", result, terminated=True)

    def test_unstarted_cancellation_requires_proof_and_never_clears_started(self):
        ledger = self.open()
        ledger.reserve("attempt", "queue-a", "codex", "primary", "account-a")
        with self.assertRaises(SharedCallError):
            ledger.cancel_unstarted("attempt", "queue-a", evidence="lease_expired")
        ledger.cancel_unstarted("attempt", "queue-a", evidence="queue_unclaimed_no_guard_no_process")
        ledger.reserve("second", "queue-b", "codex", "primary", "account-a")
        ledger.started("second", "queue-b", {"pid": 1})
        with self.assertRaises(SharedCallError):
            ledger.cancel_unstarted("second", "queue-b", evidence="queue_unclaimed_no_guard_no_process")

    def test_local_checks_consume_same_two_host_slots_as_model_calls(self):
        ledger = self.open()
        with self.assertRaises(SharedCallError):
            SharedCallLedger.initialize(Path(self.temp.name) / 'reserved-group.db', [
                ('codex', 'host', '@host-only', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ])
        ledger.reserve_host('check-a', 'queue-a')
        ledger.started('check-a', 'queue-a', {'kind': 'local_check_invocation'})
        ledger.reserve('model-b', 'queue-b', 'codex', 'primary', 'account-a')
        with self.assertRaisesRegex(CapacityUnavailable, 'host slots'):
            ledger.reserve_host('check-c', 'queue-c')
        ledger.settle('check-a', 'queue-a', 'check-event',
                      {'category': 'success', 'duration_seconds': 1, 'total_cost_usd': 0}, terminated=True)
        self.assertEqual(ledger.account('codex', 'primary', 'account-a')['calls'], 0)
        self.assertEqual(ledger.reserve_host('check-c', 'queue-c')['state'], 'RESERVED')

    def test_unregistered_or_corrupt_ledger_fails_closed(self):
        ledger = self.open()
        with self.assertRaises(SharedCallError):
            ledger.reserve("id", "queue", "codex", "unregistered", "account-a")
        ledger.db.execute("UPDATE accounts SET state='DISABLED' WHERE provider='codex' AND credential_ref='alias'")
        with self.assertRaisesRegex(SharedCallError, 'aliases disagree'):
            ledger.reserve("id", "queue", "codex", "primary", "account-a")
        ledger.close()
        with self.assertRaises(SharedCallError):
            SharedCallLedger(self.path.with_name("absent.db"))

    def test_adoption_fence_blocks_legacy_dispatcher_before_scheduling(self):
        dispatcher = Dispatcher(Path(self.temp.name) / 'legacy-queue')
        try:
            with patch.dict('os.environ', {'AI_COMPANY_REQUIRE_SHARED_CALLS': '1'}):
                with self.assertRaises(ExecutionBlocked):
                    dispatcher.run_once()
            self.assertEqual(dispatcher.tasks(), [])
        finally:
            dispatcher.close()


if __name__ == "__main__":
    unittest.main()
