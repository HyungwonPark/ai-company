"""E2 recovery through real Automation, Dispatcher, queue and shared ledger.

Only the provider response and dead process observation are synthetic. Every
database and Git checkout is disposable; no provider or operating worker runs.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.automation import Automation
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.shared_calls import SharedCallLedger
from ai_company.sessions import SessionQueue
from ai_company.timeout_recovery import diagnose, apply_in_stopped_environment
from scripts import evaluate_pm_behavior as evaluation
from tests import test_automation as automation_fixture


class ProductTimeoutRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fixture = automation_fixture.CoordinatorTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.base / 'E2'
        self.initial, self.followup = 'Please clarify the two outputs', 'Build the two approved outputs'
        (self.base / 'cases.json').write_text(json.dumps({'cases': [{
            'id': 'E2', 'initial_message': self.initial, 'followup_message': self.followup}]}))
        self.ledger_path = self.base / 'shared.sqlite'
        SharedCallLedger.initialize(self.ledger_path, [
            ('codex', 'codex', 'codex', 'AVAILABLE', None, None, 7, 205.868, 0, 1),
            ('claude', 'claude', 'claude', 'UNKNOWN', None, 'account_confirmation_required',
             0, 0, 0, 1)]).close()
        self.calls = []
        self.review_calls = []
        self.errors = []
        self.session_id = 'synthetic-e2-session'
        self.timeout_on_first_attempt = True
        self.worker = self.open()
        self.addCleanup(self.worker.close)
        self.project = self.worker.store.create_project({'name': 'E2 isolated', 'goal': 'Two outputs'})
        self.worker.store.post_message(self.project['id'], {'content': self.initial})
        self.observe = patch('ai_company.adapters.session_cli.service_alive', return_value=False)
        self.observe.start(); self.addCleanup(self.observe.stop)
        self.recovery_observe = patch('ai_company.timeout_recovery.service_alive', return_value=False)
        self.recovery_observe.start(); self.addCleanup(self.recovery_observe.stop)

    def open(self):
        def factory(root, **kwargs):
            return Dispatcher(root, executor=self.execute, **kwargs)
        return Automation(self.root, self.fixture.config, dispatcher_factory=factory,
                          shared_calls=self.ledger_path)

    def execute(self, agent, state, provider, worktree, prompt, session_id, **options):
        if state['specification']['execution_scope'] == 'plan_review':
            self.review_calls.append(state['task_id'])
            return self.fixture.execute(agent, state, provider, worktree, prompt, session_id, **options)
        with SessionQueue(self.root / 'sessions') as queue:
            job = queue.get(state['active']['job_id'])
        revision = state['specification']['plan']['request_revision']
        self.calls.append((revision, job['attempt_count'], session_id))
        if revision == 2 and job['attempt_count'] == 1 and self.timeout_on_first_attempt:
            identity = {'pid': 9999999, 'pgid': 9999999, 'proc_start_ticks': 1,
                        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        'systemd_unit': 'ai-company-run-ffffffffffffffffffffffffffffffff.service'}
            options['on_spawn'](identity)
            output = options['output_dir']
            stdout, stderr = output / 'stdout.jsonl', output / 'stderr.log'
            stdout.write_text(json.dumps({'type': 'thread.started', 'thread_id': self.session_id})
                              + '\n' + json.dumps({'type': 'turn.started'}) + '\n')
            stderr.write_text('')
            return SessionOutcome('reconciliation', session_id=self.session_id, result={
                'stdout_path': str(stdout), 'stderr_path': str(stderr),
                'duration_seconds': 1, 'total_cost_usd': None,
                'cgroup_stopped': True, 'exit_code': -15, 'structured_output': None,
                'termination_cause': 'timeout',
                'effective_timeout_seconds': options['timeout_seconds'],
                'systemd_unit': identity['systemd_unit']})
        try:
            outcome = self.fixture.execute(agent, state, provider, worktree, prompt,
                                           session_id or (self.session_id if revision == 2 else None), **options)
        except Exception as exc:
            self.errors.append(repr(exc))
            raise
        if revision == 1:
            outcome.result['structured_output'].update(
                verdict='BLOCK', plan=None, summary='두 결과의 범위를 확인해 주세요.')
            outcome.result['structured_output']['requirements_feedback'] = {
                'version': 2, 'revision': revision,
                'goal_digest': state['specification']['plan']['goal_digest'],
                'problem': 'Two outputs need a scope decision',
                'users_and_flow': 'Owner answers one PM question',
                'scope': ['Create two outputs'], 'exclusions': [], 'assumptions': [],
                'findings': [], 'questions': [{'id': 'Q1', 'prompt': 'Which two outputs?',
                    'reason': 'The initial goal leaves them unspecified', 'status': 'open'}],
                'requirements': [{'id': 'R1', 'source': 'master goal',
                    'acceptance': 'Two outputs are identified',
                    'verification': 'Check the two files', 'role_keys': ['impl', 'test']}]}
        elif revision == 2:
            outcome.result['structured_output']['plan']['requirements_review']['questions'] = [{
                'id': 'Q1', 'prompt': 'Which two outputs?',
                'reason': 'The initial goal leaves them unspecified',
                'status': 'answered', 'resolution': self.followup,
                'answer_message_id': self.followup_id,
                'source_request_id': self.first_request_id,
                'source_question_id': 'Q1'}]
        return outcome

    def advance(self, predicate, *, limit=8):
        for _ in range(limit):
            self.worker.run_once()
            if predicate():
                return
        self.fail('product path did not reach the expected state: '
                  + str(self.errors) + ' ' + str(self.calls)
                  + ' ' + str([(r['request_revision'], r['state'], r.get('reason'))
                              for r in self.worker.store.pm_requests()]))

    def prepare_timeout(self):
        self.advance(lambda: self.worker.store.pm_requests()[0]['state'] == 'answer_needed')
        self.first_request_id = self.worker.store.pm_requests()[0]['request_id']
        with sqlite3.connect(self.ledger_path) as source, sqlite3.connect(self.base / 'baseline.sqlite') as target:
            source.backup(target)
        self.followup_id = self.worker.store.post_message(
            self.project['id'], {'content': self.followup})['id']
        self.advance(lambda: self.worker.store.pm_requests()[-1]['state'] == 'blocked')
        request = self.worker.store.pm_requests()[-1]
        task_id = 'pm-' + request['request_id']
        state = self.worker.dispatcher.get(task_id)
        job = self.worker.dispatcher.queue.get(state['active']['job_id'])
        self.assertEqual(job['status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(state['usage']['executions'], 1)
        self.assertEqual(self.worker.store.overview(self.project['id'])['plans'], [])
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.reservation(digest([str(self.root), job['job_id'], 1]))['state'], 'UNKNOWN')
        self.assertEqual(ledger.account('codex', 'codex', 'codex')['calls'], 8)
        ledger.close()
        return job, state

    def test_e2_timeout_recovery_restarts_same_job_and_stores_plan(self):
        job, state = self.prepare_timeout()
        plan = diagnose(self.root, self.ledger_path, self.base / 'baseline.sqlite', job['job_id'])
        self.assertEqual(plan['decision'], 'same_session_retry_preparable', plan['checks'])
        self.assertEqual(apply_in_stopped_environment(self.root, self.ledger_path,
            self.base / 'baseline.sqlite', job['job_id'], plan['digest'])['state'], 'done')
        self.worker.close()
        self.worker = self.open()
        before = len(self.calls)
        self.advance(lambda: any(p['status'] == 'proposed'
            for p in self.worker.store.overview(self.project['id'])['plans']))
        self.assertEqual(len(self.calls), before + 1)
        self.assertEqual(self.calls[-1], (2, 2, self.session_id))
        new_job = self.worker.dispatcher.queue.get(job['job_id'])
        self.assertEqual((new_job['status'], new_job['attempt_count'], new_job['session_id']),
                         ('SESSION_COMPLETED', 2, self.session_id))
        proposal = self.worker.store.overview(self.project['id'])['plans'][0]
        self.assertEqual(proposal['status'], 'proposed')
        self.assertEqual(proposal['request_revision'], 2)
        self.assertEqual(proposal['content']['summary'], self.fixture.plan['summary'])
        self.assertEqual(len(self.review_calls), 1)
        self.assertTrue(evaluation.case_can_advance('E2',
            evaluation.case_progress(self.worker.store.overview(self.project['id']))))
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'codex', 'codex')['calls'], 11)
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 4)
        self.assertEqual(ledger.account('claude', 'claude', 'claude')['state'], 'UNKNOWN')
        ledger.close()

    def test_product_reentry_after_each_committed_boundary(self):
        for boundary in ('settlement', 'database', 'guard', 'account'):
            with self.subTest(boundary=boundary):
                if boundary != 'settlement':
                    self.worker.close()
                    self.setUp()
                job, _ = self.prepare_timeout()
                plan = diagnose(self.root, self.ledger_path, self.base / 'baseline.sqlite', job['job_id'])
                with self.assertRaisesRegex(RuntimeError, 'injected stop'):
                    apply_in_stopped_environment(self.root, self.ledger_path,
                        self.base / 'baseline.sqlite', job['job_id'], plan['digest'],
                        fail_after=boundary)
                self.assertEqual(apply_in_stopped_environment(self.root, self.ledger_path,
                    self.base / 'baseline.sqlite', job['job_id'], plan['digest'])['state'], 'done')
                self.worker.close(); self.worker = self.open()
                self.advance(lambda: any(p['status'] == 'proposed'
                    for p in self.worker.store.overview(self.project['id'])['plans']))
                self.assertEqual(sum(revision == 2 and attempt == 2
                    for revision, attempt, _ in self.calls), 1)
                ledger = SharedCallLedger(self.ledger_path)
                self.assertEqual(ledger.account('codex', 'codex', 'codex')['calls'], 11)
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0], 4)
                ledger.close()

    def test_future_wait_requires_expiry_before_same_job_recovery(self):
        job, _ = self.prepare_timeout()
        future = time.time() + 0.4
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='codex'", (future,))
        blocked = diagnose(self.root, self.ledger_path, self.base / 'baseline.sqlite', job['job_id'])
        self.assertEqual(blocked['checks']['account_restriction'], 'future_wait')
        before = len(self.calls)
        self.worker.run_once()
        self.assertEqual(len(self.calls), before)
        time.sleep(max(0, future - time.time()) + 0.02)
        plan = diagnose(self.root, self.ledger_path, self.base / 'baseline.sqlite', job['job_id'])
        self.assertEqual(plan['decision'], 'same_session_retry_preparable')
        self.assertEqual(apply_in_stopped_environment(self.root, self.ledger_path,
            self.base / 'baseline.sqlite', job['job_id'], plan['digest'])['state'], 'done')
        self.worker.close(); self.worker = self.open()
        self.advance(lambda: any(p['status'] == 'proposed'
            for p in self.worker.store.overview(self.project['id'])['plans']))
        self.assertEqual(self.calls[-1], (2, 2, self.session_id))

    def test_two_dispatchers_reacquire_only_one_attempt(self):
        job, _ = self.prepare_timeout()
        plan = diagnose(self.root, self.ledger_path, self.base / 'baseline.sqlite', job['job_id'])
        apply_in_stopped_environment(self.root, self.ledger_path,
                                     self.base / 'baseline.sqlite', job['job_id'], plan['digest'])
        def run_worker(_):
            worker = Dispatcher(self.root, executor=self.execute, shared_calls=self.ledger_path)
            try:
                return worker.run_once(task_ids={job['task_id']})['status']
            finally:
                worker.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run_worker, range(2)))
        self.worker.reconcile()
        self.assertEqual(sum(revision == 2 and attempt == 2
            for revision, attempt, _ in self.calls), 1)
        self.assertEqual(self.worker.dispatcher.queue.get(job['job_id'])['attempt_count'], 2)
        overview = self.worker.store.overview(self.project['id'])
        self.assertEqual(len(overview['plans']), 1)
        self.assertFalse(evaluation.case_can_advance('E2', evaluation.case_progress(overview)))
        ledger = SharedCallLedger(self.ledger_path)
        self.assertEqual(ledger.account('codex', 'codex', 'codex')['calls'], 10)
        ledger.close()


if __name__ == '__main__':
    unittest.main()
