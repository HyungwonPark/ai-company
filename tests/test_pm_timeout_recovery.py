import json
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_company.contracts import digest
from ai_company.adapters.session_cli import run_session
from ai_company.sessions import RetryPolicy, SessionQueue, repository_snapshot
from ai_company.shared_calls import SharedCallLedger
from ai_company.timeout_recovery import diagnose, review_settled_resume, apply_in_stopped_environment


def _simultaneous_recovery(case_root, ledger, baseline, job_id, digest_value, gate, output):
    gate.wait()
    try:
        value = apply_in_stopped_environment(case_root, ledger, baseline, job_id, digest_value)
        output.put(value['state'])
    except Exception as exc:
        output.put(type(exc).__name__)


class TimeoutRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'E2'
        (self.base / 'cases.json').write_text(json.dumps({'cases': [{'id': 'E2',
            'initial_message': 'question', 'followup_message': 'answer'}]}))
        self.clone = self.root / 'automation-git/clones/clone'
        self.clone.mkdir(parents=True)
        for args in (('init', '-q'), ('remote', 'add', 'origin', 'https://github.com/HyungwonPark/ai-company.git'),
                     ('config', 'user.email', 'test@example.invalid'), ('config', 'user.name', 'Test')):
            subprocess.run(('git', *args), cwd=self.clone, check=True, capture_output=True)
        (self.clone / 'README.md').write_text('fixture\n')
        subprocess.run(('git', 'add', 'README.md'), cwd=self.clone, check=True, capture_output=True)
        subprocess.run(('git', 'commit', '-qm', 'fixture'), cwd=self.clone, check=True, capture_output=True)
        self.snapshot = repository_snapshot(self.clone)
        self.job_id = 'job-e2'
        self.task_id = 'pm-request-e2'
        self.request_id = 'request-e2'
        self.process = {'pid': 9999999, 'pgid': 9999999, 'proc_start_ticks': 1,
                        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        'systemd_unit': 'ai-company-run-ffffffffffffffffffffffffffffffff.service'}
        logs = self.root / 'sessions/session-logs/job-e2'
        logs.mkdir(parents=True)
        self.stdout = logs / 'stdout.jsonl'
        self.stdout.write_text(json.dumps({'type': 'thread.started', 'thread_id': 'session-e2'})
                               + '\n' + json.dumps({'type': 'turn.started'}) + '\n'
                               + json.dumps({'type': 'item.completed'}) + '\n')
        self.stderr = logs / 'stderr.log'
        self.stderr.write_text('')
        self.job = {'job_id': self.job_id, 'task_id': self.task_id, 'session_id': 'session-e2',
                    'attempt_count': 1, 'status': 'NEEDS_RECONCILIATION',
                    'reason': 'non-retryable session outcome: reconciliation',
                    'last_category': 'reconciliation', 'worktree': str(self.clone),
                    'retry_count': 0, 'cycle_retries': 0,
                    'specification': {'retry_policy': RetryPolicy(execution_timeout_seconds=240).model_dump(mode='json')},
                    'repository_snapshot': self.snapshot, 'process': self.process,
                    'result': {'stdout_path': str(self.stdout), 'stderr_path': str(self.stderr),
                               'duration_seconds': 240.4, 'exit_code': -15,
                               'cgroup_stopped': True, 'structured_output': None,
                               'termination_cause': 'timeout', 'effective_timeout_seconds': 240,
                               'systemd_unit': self.process['systemd_unit']}}
        self.task = {'task_id': self.task_id, 'status': 'NEEDS_RECONCILIATION',
                     'active': {'job_id': self.job_id, 'session_id': 'session-e2',
                                'accounted_attempts': 1},
                     'usage': {'executions': 1, 'runtime_seconds': 240.4}}
        self.request = {'request_id': self.request_id, 'request_revision': 2,
                        'project_id': 'project-e2', 'content': 'answer',
                        'state': 'blocked', 'execution': {'task_id': self.task_id}}
        self.db_path = self.root / 'sessions/sessions.sqlite'
        with sqlite3.connect(self.db_path) as db:
            db.execute('CREATE TABLE session_jobs(job_id TEXT PRIMARY KEY,binding TEXT NOT NULL,'
                       'state TEXT NOT NULL,resume_at REAL,lease_owner TEXT,lease_until REAL,'
                       'document TEXT NOT NULL)')
            db.execute('CREATE TABLE session_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,'
                       'job_id TEXT NOT NULL,occurred_at REAL NOT NULL,document TEXT NOT NULL)')
            db.execute('CREATE TABLE flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE management_pm_requests(message_id TEXT PRIMARY KEY, project_id TEXT, document TEXT NOT NULL)')
            db.execute('CREATE TABLE flow_events(id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, at REAL, document TEXT)')
            db.execute('INSERT INTO session_jobs VALUES (?,?,?,?,?,?,?)',
                       (self.job_id, 'fixture', 'NEEDS_RECONCILIATION', None, None, None,
                        json.dumps(self.job)))
            db.execute('INSERT INTO flow_tasks VALUES (?,?)', (self.task_id, json.dumps(self.task)))
            db.execute('INSERT INTO management_pm_requests VALUES (?,?,?)',
                       (self.request_id, 'project-e2', json.dumps(self.request)))
            db.execute('INSERT INTO management_pm_requests VALUES (?,?,?)',
                       ('first-e2', 'project-e2', json.dumps({'request_id': 'first-e2',
                        'request_revision': 1, 'project_id': 'project-e2',
                        'content': 'question', 'state': 'answer_needed'})))
        db.close()
        self.guard = Path(self.snapshot['git_common_dir']) / 'ai-company-session-active.json'
        self.guard.write_text(json.dumps({'job_id': self.job_id,
            'queue_root': str(self.root / 'sessions'), 'process': self.process}))
        self.ledger_path = self.base / 'shared.sqlite'
        ledger = SharedCallLedger.initialize(self.ledger_path, [
            ('codex', 'credential', 'group', 'AVAILABLE', None, None, 7, 205.868, 0, 1),
            ('claude', 'separate', 'claude-group', 'UNKNOWN', None, 'account_confirmation_required',
             0, 0, 0, 1)])
        ledger.close()
        self.baseline = self.base / 'baseline.sqlite'
        with sqlite3.connect(self.ledger_path) as source, sqlite3.connect(self.baseline) as target:
            source.backup(target)
        source.close(); target.close()
        self.reservation_id = digest([str(self.root.resolve()), self.job_id, 1])
        ledger = SharedCallLedger(self.ledger_path)
        ledger.reserve(self.reservation_id, str(self.root), 'codex', 'credential', 'group')
        ledger.started(self.reservation_id, str(self.root),
                       {'kind': 'executor_invocation', 'job_id': self.job_id, 'attempt': 1})
        ledger.uncertain(self.reservation_id, str(self.root), 'session termination or effects are uncertain')
        ledger.close()

    def plan(self):
        return diagnose(self.root, self.ledger_path, self.baseline, self.job_id)

    def apply(self, digest_value, **kwargs):
        return apply_in_stopped_environment(self.root, self.ledger_path, self.baseline,
                                             self.job_id, digest_value, **kwargs)

    def test_incomplete_timeout_is_read_only_then_settled_once_for_same_session(self):
        plan = self.plan()
        self.assertEqual(plan['decision'], 'same_session_retry_preparable')
        self.assertFalse(self.db_path.with_name('timeout_recoveries').exists())
        first = self.apply(plan['digest'])
        self.assertEqual(first['state'], 'done')
        self.assertEqual(first['session_id'], 'session-e2')
        self.assertEqual(self.apply(plan['digest']), first)
        self.assertFalse(self.guard.exists())
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'SETTLED')
        account = ledger.account('codex', 'credential', 'group')
        self.assertEqual((account['state'], account['calls']), ('AVAILABLE', 8))
        self.assertAlmostEqual(account['runtime_seconds'], 446.268)
        self.assertEqual(ledger.account('claude', 'separate', 'claude-group')['state'], 'UNKNOWN')
        ledger.close()
        with sqlite3.connect(self.db_path) as db:
            job = json.loads(db.execute('SELECT document FROM session_jobs').fetchone()[0])
            task = json.loads(db.execute('SELECT document FROM flow_tasks').fetchone()[0])
            request = json.loads(db.execute('SELECT document FROM management_pm_requests').fetchone()[0])
            audit = db.execute('SELECT original_job,phase FROM timeout_recoveries').fetchone()
        db.close()
        self.assertEqual((job['status'], job['session_id'], task['status'], request['state']),
                         ('WAITING_RETRY', 'session-e2', 'WAITING_CAPACITY', 'running'))
        self.assertEqual((json.loads(audit[0])['status'], audit[1]), ('NEEDS_RECONCILIATION', 'done'))
        queue = SessionQueue(self.root / 'sessions')
        claimed = queue._claim(self.job_id)
        self.assertEqual((claimed['status'], claimed['session_id'], claimed['attempt_count']),
                         ('RUNNING', 'session-e2', 2))
        queue.close()

    def test_each_crash_boundary_reenters_without_double_usage(self):
        for phase in ('settlement', 'database', 'guard_intent', 'guard_unlink', 'guard', 'account'):
            with self.subTest(phase=phase):
                # A fresh fixture per phase keeps the original evidence immutable.
                if phase != 'settlement':
                    self.tearDown() if hasattr(self, 'tearDown') else None
                    self.setUp()
                plan = self.plan()
                with self.assertRaisesRegex(RuntimeError, 'injected stop'):
                    self.apply(plan['digest'], fail_after=phase)
                self.assertEqual(self.apply(plan['digest'])['state'], 'done')
                ledger = SharedCallLedger(self.ledger_path)
                self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
                ledger.close()

    def test_two_recovery_processes_never_double_settle(self):
        plan = self.plan()
        context = multiprocessing.get_context('fork')
        gate, output = context.Event(), context.Queue()
        arguments = (self.root, self.ledger_path, self.baseline,
                     self.job_id, plan['digest'], gate, output)
        workers = [context.Process(target=_simultaneous_recovery, args=arguments) for _ in range(2)]
        for worker in workers: worker.start()
        gate.set()
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        self.assertIn('done', [output.get(timeout=2) for _ in workers])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_changed_guard_terminal_or_other_reservation_keeps_unknown(self):
        cases = ('guard', 'terminal', 'other')
        for case in cases:
            with self.subTest(case=case):
                self.setUp()
                if case == 'guard':
                    self.guard.write_text(json.dumps({'job_id': 'different',
                        'queue_root': str(self.root / 'sessions'), 'process': self.process}))
                elif case == 'terminal':
                    with self.stdout.open('a') as stream:
                        stream.write(json.dumps({'type': 'turn.completed'}) + '\n')
                else:
                    ledger = SharedCallLedger(self.ledger_path)
                    ledger.db.execute("INSERT INTO reservations(reservation_id,owner,group_id,state,created_at) "
                                      "VALUES ('other','other','group','UNKNOWN',0)")
                    ledger.close()
                plan = self.plan()
                self.assertEqual(plan['decision'], 'saved_terminal_needs_contract_review'
                                 if case == 'terminal' else 'hold_unknown')
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    self.apply(plan['digest'])
                ledger = SharedCallLedger(self.ledger_path)
                self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'UNKNOWN')
                ledger.close()

    def test_wrong_digest_never_settles_or_creates_recovery_record(self):
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.apply('0' * 64)
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'UNKNOWN')
        ledger.close()
        with sqlite3.connect(self.db_path) as db:
            created = db.execute("SELECT 1 FROM sqlite_master WHERE name='timeout_recoveries'").fetchone()
        db.close()
        self.assertIsNone(created)

    def test_preexisting_account_block_cannot_be_reopened(self):
        with sqlite3.connect(self.baseline) as db:
            db.execute("UPDATE accounts SET state='DISABLED',reason='authentication' WHERE group_id='group'")
        db.close()
        plan = self.plan()
        self.assertEqual(plan['decision'], 'hold_unknown')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['state'], 'UNKNOWN')
        ledger.close()

    def test_future_wait_before_diagnosis_blocks_without_settlement(self):
        future = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        plan = self.plan()
        self.assertEqual(plan['decision'], 'hold_unknown')
        self.assertEqual(plan['checks']['account_restriction'], 'future_wait')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'UNKNOWN')
        self.assertEqual(ledger.account('codex', 'credential', 'group')['resume_at'], future)
        ledger.close()

    def test_new_wait_after_settlement_survives_reentry(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='settlement')
        future = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        with self.assertRaisesRegex(ValueError, 'account restriction changed'):
            self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'SETTLED')
        self.assertEqual(ledger.account('codex', 'credential', 'group')['resume_at'], future)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_expired_wait_after_settlement_requires_new_frozen_review(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='settlement')
        with sqlite3.connect(self.db_path) as db:
            original_plan = db.execute('SELECT plan FROM timeout_recoveries').fetchone()[0]
        future = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            original_settlement = db.execute('SELECT * FROM settlement_events').fetchall()
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        with patch('ai_company.timeout_recovery.time.time', return_value=future - 1):
            self.assertEqual(review_settled_resume(self.root, self.ledger_path,
                self.baseline, self.job_id)['decision'], 'hold_wait')
        with patch('ai_company.timeout_recovery.time.time', return_value=future + 1):
            review = review_settled_resume(self.root, self.ledger_path,
                self.baseline, self.job_id)
            self.assertEqual(review['decision'], 'same_session_resume_preparable')
            with self.assertRaisesRegex(ValueError, 'account restriction changed'):
                self.apply(plan['digest'])
            self.assertEqual(self.apply(plan['digest'], expected_resume_digest=review['digest'])['state'], 'done')
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT plan FROM timeout_recoveries').fetchone()[0], original_plan)
            saved = db.execute('SELECT parent_digest,decision FROM timeout_recovery_resume_reviews').fetchone()
            self.assertEqual(saved, (plan['digest'], json.dumps(review, ensure_ascii=False)))
        with sqlite3.connect(self.ledger_path) as db:
            self.assertEqual(db.execute('SELECT * FROM settlement_events').fetchall(), original_settlement)
            self.assertEqual(db.execute("SELECT calls FROM accounts WHERE group_id='group'").fetchone()[0], 8)

    def test_settlement_commit_before_phase_write_can_resume_after_wait(self):
        plan = self.plan()
        original_settle = SharedCallLedger.settle
        def commit_then_stop(ledger, *args, **kwargs):
            original_settle(ledger, *args, **kwargs)
            raise RuntimeError('injected stop between settlement and phase write')
        with patch.object(SharedCallLedger, 'settle', commit_then_stop):
            with self.assertRaisesRegex(RuntimeError, 'between settlement and phase write'):
                self.apply(plan['digest'])
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT phase FROM timeout_recoveries').fetchone()[0], 'pinned')
        future = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        with patch('ai_company.timeout_recovery.time.time', return_value=future + 1):
            review = review_settled_resume(self.root, self.ledger_path, self.baseline, self.job_id)
            self.assertEqual(review['decision'], 'same_session_resume_preparable')
            self.assertEqual(self.apply(plan['digest'], expected_resume_digest=review['digest'])['state'], 'done')
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_second_wait_requires_second_review_and_preserves_auth_blocks(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='guard')
        first_wait = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (first_wait,))
        with patch('ai_company.timeout_recovery.time.time', return_value=first_wait + 1):
            first = review_settled_resume(self.root, self.ledger_path, self.baseline, self.job_id)
            with self.assertRaisesRegex(RuntimeError, 'injected stop'):
                self.apply(plan['digest'], expected_resume_digest=first['digest'], fail_after='resume_review')
        second_wait = first_wait + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (second_wait,))
        with patch('ai_company.timeout_recovery.time.time', return_value=second_wait - 1):
            self.assertEqual(review_settled_resume(self.root, self.ledger_path,
                self.baseline, self.job_id)['decision'], 'hold_wait')
            with self.assertRaisesRegex(ValueError, 'account restriction changed'):
                self.apply(plan['digest'], expected_resume_digest=first['digest'])
        with patch('ai_company.timeout_recovery.time.time', return_value=second_wait + 1):
            second = review_settled_resume(self.root, self.ledger_path, self.baseline, self.job_id)
            self.assertEqual(second['parent_digest'], first['digest'])
            self.assertEqual(self.apply(plan['digest'], expected_resume_digest=second['digest'])['state'], 'done')
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM timeout_recovery_resume_reviews').fetchone()[0], 2)
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_expired_wait_never_overrides_changed_reason_or_alias(self):
        for change in ('authentication', 'alias'):
            with self.subTest(change=change):
                self.setUp()
                plan = self.plan()
                with self.assertRaisesRegex(RuntimeError, 'injected stop'):
                    self.apply(plan['digest'], fail_after='settlement')
                expired = time.time() - 1
                with sqlite3.connect(self.ledger_path) as db:
                    if change == 'authentication':
                        db.execute("UPDATE accounts SET resume_at=?,reason='authentication' WHERE group_id='group'", (expired,))
                    else:
                        db.execute("INSERT INTO accounts SELECT 'codex','alias',group_id,state,?,reason,"
                                   "calls,runtime_seconds,cost_usd,cost_unknown FROM accounts WHERE group_id='group'", (expired,))
                if change == 'alias':
                    with self.assertRaises(ValueError):
                        review_settled_resume(self.root, self.ledger_path, self.baseline, self.job_id)
                else:
                    self.assertNotEqual(review_settled_resume(self.root, self.ledger_path,
                        self.baseline, self.job_id)['decision'], 'same_session_resume_preparable')
                with self.assertRaises(ValueError):
                    self.apply(plan['digest'])

    def test_uninterpretable_wait_after_settlement_is_a_durable_hold(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='settlement')
        for value in ('not-a-time', b'\x01\x02'):
            with self.subTest(value=value):
                with sqlite3.connect(self.ledger_path) as db:
                    db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (value,))
                review = review_settled_resume(self.root, self.ledger_path,
                    self.baseline, self.job_id)
                self.assertEqual((review['decision'], review['checks']['account_restriction']),
                                 ('hold_unknown', 'invalid_wait'))
                self.assertEqual(len(review['digest']), 64)
                with self.assertRaises(ValueError):
                    self.apply(plan['digest'], expected_resume_digest=review['digest'])

    def test_wait_inserted_after_guard_release_is_not_erased(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='guard')
        future = time.time() + 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        with self.assertRaisesRegex(ValueError, 'account restriction changed'):
            self.apply(plan['digest'])
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT phase FROM timeout_recoveries').fetchone()[0], 'guard_cleared')
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['resume_at'], future)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_alias_wait_or_block_mismatch_prevents_recovery(self):
        for restriction in ('future_wait', 'disabled'):
            with self.subTest(restriction=restriction):
                self.setUp()
                with sqlite3.connect(self.ledger_path) as db:
                    db.execute("INSERT INTO accounts SELECT 'codex','alias',group_id,state,resume_at,reason,"
                               "calls,runtime_seconds,cost_usd,cost_unknown FROM accounts WHERE group_id='group'")
                with sqlite3.connect(self.baseline) as db:
                    db.execute("INSERT INTO accounts SELECT 'codex','alias',group_id,state,resume_at,reason,"
                               "calls,runtime_seconds,cost_usd,cost_unknown FROM accounts WHERE group_id='group'")
                with sqlite3.connect(self.ledger_path) as db:
                    if restriction == 'future_wait':
                        db.execute("UPDATE accounts SET resume_at=? WHERE credential_ref='alias'",
                                   (time.time() + 3600,))
                    else:
                        db.execute("UPDATE accounts SET state='DISABLED',reason='authentication' "
                                   "WHERE credential_ref='alias'")
                plan = self.plan()
                self.assertTrue(plan['checks']['historical_ledger_preserved'])
                self.assertEqual(plan['decision'], 'hold_unknown')
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    self.apply(plan['digest'])

    def test_expired_wait_is_reviewed_and_released(self):
        expired = time.time() - 3600
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (expired,))
        plan = self.plan()
        self.assertEqual(plan['decision'], 'same_session_retry_preparable')
        self.assertEqual(self.apply(plan['digest'])['state'], 'done')
        ledger = SharedCallLedger(self.ledger_path)
        self.assertIsNone(ledger.account('codex', 'credential', 'group')['resume_at'])
        ledger.close()

    def test_uninterpretable_wait_is_preserved(self):
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at='not-a-time' WHERE group_id='group'")
        plan = self.plan()
        self.assertEqual(plan['decision'], 'hold_unknown')
        self.assertEqual(plan['checks']['account_restriction'], 'invalid_wait')

    def test_blob_wait_is_rejected_without_serialization_error(self):
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=x'0102' WHERE group_id='group'")
        plan = self.plan()
        self.assertEqual(plan['decision'], 'hold_unknown')
        self.assertEqual(plan['checks']['account_restriction'], 'invalid_wait')

    def test_restriction_added_between_final_check_and_transaction_is_not_erased(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='guard')
        original = SharedCallLedger._transaction
        future = time.time() + 3600
        def inject(ledger, operation):
            if operation.__name__ == 'reopen':
                with sqlite3.connect(ledger.path) as other:
                    other.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
            return original(ledger, operation)
        with patch.object(SharedCallLedger, '_transaction', inject):
            with self.assertRaisesRegex(Exception, 'account restriction changed'):
                self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['resume_at'], future)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 1)
        ledger.close()

    def test_restriction_added_immediately_before_settlement_is_not_overwritten(self):
        plan = self.plan()
        original = SharedCallLedger._transaction
        def inject(ledger, operation):
            if operation.__name__ == 'apply':
                with sqlite3.connect(ledger.path) as other:
                    other.execute("UPDATE accounts SET reason='authentication' WHERE group_id='group'")
            return original(ledger, operation)
        with patch.object(SharedCallLedger, '_transaction', inject):
            with self.assertRaisesRegex(Exception, 'account restriction or usage changed'):
                self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        account = ledger.account('codex', 'credential', 'group')
        self.assertEqual((account['state'], account['reason'], account['calls']),
                         ('UNKNOWN', 'authentication', 7))
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'UNKNOWN')
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 0)
        ledger.close()

    def test_decreased_historical_usage_never_prepares_recovery(self):
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET calls=0,runtime_seconds=0 WHERE group_id='group'")
        plan = self.plan()
        self.assertFalse(plan['checks']['historical_ledger_preserved'])
        self.assertEqual(plan['decision'], 'hold_unknown')

    def test_reentry_rejects_changed_session_before_account_reopens(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='database')
        with sqlite3.connect(self.db_path) as db:
            job = json.loads(db.execute('SELECT document FROM session_jobs').fetchone()[0])
            job['session_id'] = 'another-session'
            db.execute('UPDATE session_jobs SET document=?', (json.dumps(job),))
        with self.assertRaisesRegex(ValueError, 'queue, task or request changed'):
            self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['state'], 'UNKNOWN')
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 8)
        ledger.close()

    def test_reentry_rejects_baseline_wal_change_after_settlement(self):
        plan = self.plan()
        with self.assertRaisesRegex(RuntimeError, 'injected stop'):
            self.apply(plan['digest'], fail_after='settlement')
        with sqlite3.connect(self.baseline) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("UPDATE accounts SET state='DISABLED',reason='authentication' "
                       "WHERE group_id='group'")
        with self.assertRaisesRegex(ValueError, 'recovery evidence changed'):
            self.apply(plan['digest'])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'credential', 'group')['state'], 'UNKNOWN')
        ledger.close()

    def test_missing_or_different_termination_cause_keeps_unknown(self):
        for cause in (None, 'live_descendants', 'supervision_failure', 'cgroup_unconfirmed'):
            with self.subTest(cause=cause):
                self.setUp()
                with sqlite3.connect(self.db_path) as db:
                    job = json.loads(db.execute('SELECT document FROM session_jobs').fetchone()[0])
                    if cause is None:
                        job['result'].pop('termination_cause')
                    else:
                        job['result']['termination_cause'] = cause
                    db.execute('UPDATE session_jobs SET document=?', (json.dumps(job),))
                self.assertEqual(self.plan()['decision'], 'hold_unknown')

    def test_another_session_or_late_output_never_reuses_frozen_evidence(self):
        self.stdout.write_text(json.dumps({'type': 'thread.started',
                                           'thread_id': 'another-session'}) + '\n'
                               + json.dumps({'type': 'turn.started'}) + '\n')
        self.assertEqual(self.plan()['decision'], 'hold_unknown')
        self.stdout.write_text(json.dumps({'type': 'thread.started',
                                           'thread_id': 'session-e2'}) + '\n'
                               + json.dumps({'type': 'turn.started'}) + '\n')
        plan = self.plan()
        with self.stdout.open('a') as stream:
            stream.write(json.dumps({'type': 'item.completed'}) + '\n')
        with self.assertRaisesRegex(ValueError, 'recovery evidence changed'):
            self.apply(plan['digest'])

    def test_unstarted_reservation_only_points_to_existing_cancel_contract(self):
        self.guard.unlink()
        with sqlite3.connect(self.db_path) as db:
            job = json.loads(db.execute('SELECT document FROM session_jobs').fetchone()[0])
            job.update(status='READY', attempt_count=0, process=None, result=None)
            db.execute('UPDATE session_jobs SET state=?,document=?', ('READY', json.dumps(job)))
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE reservations SET state='RESERVED',started_at=NULL,process_identity=NULL "
                       'WHERE reservation_id=?', (self.reservation_id,))
            db.execute("UPDATE accounts SET state='AVAILABLE',reason=NULL WHERE group_id='group'")
        self.assertEqual(self.plan()['decision'], 'existing_cancel_unstarted_contract')
        ledger = SharedCallLedger(self.ledger_path)
        ledger.cancel_unstarted(self.reservation_id, str(self.root),
                                evidence='queue_unclaimed_no_guard_no_process')
        self.assertEqual(ledger.reservation(self.reservation_id)['state'], 'CANCELLED')
        self.assertEqual(ledger.account('codex', 'credential', 'group')['calls'], 7)
        ledger.close()

    @unittest.skipUnless(subprocess.run(['systemctl', '--user', 'show-environment'],
        capture_output=True).returncode == 0, 'user systemd is unavailable')
    def test_fake_cli_timeout_uses_same_cgroup_supervisor_without_model(self):
        fake = self.base / 'fake-codex'
        fake.write_text('#!/usr/bin/env python3\nimport json,time\n'
            'print(json.dumps({"type":"thread.started"}), flush=True)\n'
            'print(json.dumps({"type":"turn.started"}), flush=True)\n'
            'time.sleep(30)\n')
        fake.chmod(0o700)
        seen = []
        outcome = run_session('codex', self.clone, 'fixture', None, timeout_seconds=0.5,
            output_dir=self.base / 'fake-cli-output', executable=str(fake),
            isolate_cgroup=True, on_spawn=seen.append, shared_call_controlled=True)
        self.assertEqual(outcome.category, 'reconciliation')
        self.assertEqual(len(seen), 1)
        self.assertTrue(outcome.result['cgroup_stopped'])
        self.assertEqual(outcome.result['exit_code'], -15)
        self.assertEqual(outcome.result['termination_cause'], 'timeout')
        self.assertEqual(outcome.result['effective_timeout_seconds'], 0.5)

    @unittest.skipUnless(subprocess.run(['systemctl', '--user', 'show-environment'],
        capture_output=True).returncode == 0, 'user systemd is unavailable')
    def test_fake_cli_through_queue_and_shared_ledger_can_resume_same_session(self):
        session_id = '01a0e1e7-8d70-73e3-9940-dc54ab7cb912'
        fake = self.base / 'fake-queue-codex'
        fake.write_text('#!/usr/bin/env python3\nimport json,time\n'
            f'print(json.dumps({{"type":"thread.started","thread_id":"{session_id}"}}), flush=True)\n'
            'print(json.dumps({"type":"turn.started"}), flush=True)\n'
            'time.sleep(30)\n')
        fake.chmod(0o700)
        self.guard.unlink()
        with sqlite3.connect(self.db_path) as db:
            job = json.loads(db.execute('SELECT document FROM session_jobs').fetchone()[0])
            job.update(status='READY', attempt_count=0, process=None, result=None,
                       session_id=session_id, provider='codex', agent_id='pm',
                       last_completed_stage='planning', checkpoint={},
                       previous_sessions=[],
                       head_commit=self.snapshot['head_commit'], lease_owner=None,
                       lease_until=None, resume_at=0)
            job['specification']['retry_policy']['execution_timeout_seconds'] = 1
            job['specification']['task'] = {'task_id': self.task_id}
            db.execute('UPDATE session_jobs SET state=?,resume_at=?,document=?',
                       ('READY', 0, json.dumps(job)))
            task = json.loads(db.execute('SELECT document FROM flow_tasks').fetchone()[0])
            task['active']['session_id'] = session_id
            task['usage'] = {'executions': 0, 'runtime_seconds': 0}
            db.execute('UPDATE flow_tasks SET document=?', (json.dumps(task),))
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE reservations SET state='RESERVED',started_at=NULL,process_identity=NULL "
                       'WHERE reservation_id=?', (self.reservation_id,))
            db.execute("UPDATE accounts SET state='AVAILABLE',reason=NULL WHERE group_id='group'")
        ledger = SharedCallLedger(self.ledger_path)
        queue = SessionQueue(self.root / 'sessions')

        def execute(provider, worktree, prompt, resume_id, **options):
            original_spawn = options['on_spawn']
            def spawned(identity):
                original_spawn(identity)
                ledger.started(self.reservation_id, str(self.root),
                               {'kind': 'executor_invocation', 'job_id': self.job_id, 'attempt': 1})
            options['on_spawn'] = spawned
            return run_session(provider, worktree, prompt, resume_id, executable=str(fake),
                               isolate_cgroup=True, shared_call_controlled=True, **options)

        saved = queue.run_once(executor=execute, job_id=self.job_id)
        self.assertEqual(saved['status'], 'NEEDS_RECONCILIATION')
        self.assertIsNotNone(saved['result'], saved['reason'])
        self.assertEqual(saved['result']['termination_cause'], 'timeout')
        ledger.uncertain(self.reservation_id, str(self.root),
                         'session termination or effects are uncertain')
        with sqlite3.connect(self.db_path) as db:
            task = json.loads(db.execute('SELECT document FROM flow_tasks').fetchone()[0])
            task['active']['accounted_attempts'] = 1
            task['usage'] = {'executions': 1,
                             'runtime_seconds': saved['result']['duration_seconds']}
            db.execute('UPDATE flow_tasks SET document=?', (json.dumps(task),))
        queue.close(); ledger.close()
        plan = self.plan()
        self.assertEqual(plan['decision'], 'same_session_retry_preparable')
        self.assertEqual(self.apply(plan['digest'])['state'], 'done')
        queue = SessionQueue(self.root / 'sessions')
        claimed = queue._claim(self.job_id)
        self.assertEqual((claimed['session_id'], claimed['attempt_count']), (session_id, 2))
        queue.close()


if __name__ == '__main__':
    unittest.main()
