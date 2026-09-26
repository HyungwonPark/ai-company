"""Dispatcher integration at durable queue result and shared settlement boundaries."""

from ai_company.dispatcher import Dispatcher
from ai_company.shared_calls import SharedCallLedger
from ai_company.storage import controller_lock
from tests.test_dispatcher import FlowFixture, Crash


class SharedDispatcherTests(FlowFixture):
    def setUp(self):
        super().setUp()
        self.dispatcher.close()
        self.shared_path = self.root / 'shared-calls.db'
        ledger = SharedCallLedger.initialize(self.shared_path, [
            ('codex', 'codex-account', 'codex-shared', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ('claude', 'claude-account', 'claude-shared', 'AVAILABLE', None, None, 0, 0, 0, 0),
        ], clock=lambda: self.now)
        ledger.close()
        self.dispatcher = self.open()

    def open(self):
        return Dispatcher(self.root / 'state', clock=lambda: self.now,
                          verifier=self.verifier, executor=self.execute,
                          shared_calls=getattr(self, 'shared_path', None))

    def test_quota_result_once_before_handoff_and_restart(self):
        self.submit()
        self.script = [self.quota()]
        state = self.tick()
        self.assertEqual(state['active']['agent_id'], 'claude')
        group = self.dispatcher.shared_calls.account('codex', 'codex-account', 'codex-shared')
        self.assertEqual((group['state'], group['calls']), ('COOLDOWN', 1))
        self.restart()
        self.tick()
        self.assertEqual(self.dispatcher.shared_calls.account('codex', 'codex-account', 'codex-shared')['calls'], 1)

    def test_busy_session_worker_cancellation_retries_same_attempt_once(self):
        self.submit()
        with controller_lock(self.dispatcher.queue.root / 'session-worker'):
            self.tick()
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(self.dispatcher.shared_calls.db.execute(
            "SELECT state FROM reservations").fetchone()[0], 'CANCELLED')
        self.restart()
        self.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.dispatcher.shared_calls.account('codex', 'codex-account', 'codex-shared')['calls'], 1)

    def test_crash_after_shared_reserve_retains_capacity_until_recovery(self):
        self.submit()
        self.script = [Crash()]
        try:
            self.tick()
        except Crash:
            pass
        self.restart()
        self.tick()
        state = self.state()
        self.assertEqual(state['status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(self.dispatcher.shared_calls.account('codex', 'codex-account', 'codex-shared')['state'], 'UNKNOWN')
