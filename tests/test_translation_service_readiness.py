"""Translation process liveness and call readiness are independent; no model runs here."""
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from ai_company.adapters.translation_cli import HAIKU, TranslationCLI
from ai_company.service_worker import preflight, runtime_status, serve, translation_call_readiness, translation_tick
from ai_company.shared_calls import SharedCallLedger
from ai_company.translation_worker import run_once
from ai_company.translations import TranslationStore, configuration
from ai_company.management import ManagementStore


class TranslationServiceReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _state(self, account_state='UNKNOWN'):
        (self.root / 'sessions').mkdir()
        db_path = self.root / 'sessions' / 'sessions.sqlite'
        with closing(sqlite3.connect(db_path)) as db:
            db.execute('CREATE TABLE translation_jobs(document TEXT NOT NULL)')
            db.commit()
        ledger_path = self.root / 'calls.sqlite'
        ledger = SharedCallLedger.initialize(ledger_path, [
            ('claude', 'fixture', 'fixture-group', account_state,
             9999999999 if account_state == 'COOLDOWN' else None, None, 0, 0, 0, 0)])
        self.addCleanup(ledger.close)
        config = configuration(dict(provider='claude', model=HAIKU, model_version='2.1.270',
            credential_ref='fixture', quota_group='fixture-group'))
        config_path = self.root / 'translation.json'
        config_path.write_text(json.dumps(config))
        return db_path, ledger, config_path, config

    def test_live_process_can_report_cli_and_account_wait_together(self):
        db_path, ledger, _, config = self._state()
        with closing(sqlite3.connect(db_path)) as db:
            store = TranslationStore(db, shared_calls=ledger, queue_id='fixture')
            class Adapter:
                def readiness(self, _config):
                    return False, 'translation_cli_unverified'
            expected = {'state': 'waiting', 'reasons': [
                'translation_cli_unverified', 'account_confirmation_required']}
            self.assertEqual(translation_call_readiness(store, Adapter(), config), expected)
            stop = Event()
            def tick():
                return {'call_readiness': expected}
            worker = Thread(target=serve, args=(self.root, 'translation', 'fixture-digest', tick),
                            kwargs={'stop': stop, 'poll_seconds': .01})
            worker.start()
            try:
                deadline = time.monotonic() + 2
                while runtime_status(self.root)['translation'].get('passes', 0) < 1 and time.monotonic() < deadline:
                    time.sleep(.01)
                live = runtime_status(self.root)['translation']
                self.assertEqual(live['state'], 'idle')
                self.assertEqual(live['call_readiness'], expected)
            finally:
                stop.set()
                worker.join(timeout=2)
        status = runtime_status(self.root)['translation']
        self.assertEqual(status['state'], 'stopped')
        self.assertEqual(status['call_readiness'],
                         {'state': 'blocked', 'reasons': ['service_not_running']})
        self.assertEqual(json.loads((self.root / 'worker-status' / 'translation.json').read_text())['call_readiness'], expected)
        self.assertGreaterEqual(status['passes'], 1)
        self.assertNotIn('fixture-group', json.dumps(status))

    def test_quota_wait_and_uncertain_child_are_distinct(self):
        db_path, ledger, _, config = self._state('COOLDOWN')
        with ledger.db:
            ledger.db.execute("UPDATE accounts SET resume_at=?,reason=?", (9999999999, 'provider quota'))
        with closing(sqlite3.connect(db_path)) as db:
            store = TranslationStore(db, shared_calls=ledger, queue_id='fixture')
            class Adapter:
                def readiness(self, _config):
                    return True, None
            self.assertEqual(translation_call_readiness(store, Adapter(), config),
                             {'state': 'waiting', 'reasons': ['shared_account_quota']})
            db.execute('INSERT INTO translation_jobs VALUES (?)',
                       (json.dumps({'status': 'running', 'reason': None}),))
            self.assertEqual(translation_call_readiness(store, Adapter(), config)['state'], 'blocked')

    def test_real_queue_tick_survives_unverified_cli_and_unknown_account(self):
        ManagementStore(self.root).close()
        ledger = SharedCallLedger.initialize(self.root / 'calls.sqlite', [
            ('claude', 'fixture', 'fixture-group', 'UNKNOWN', None, None, 0, 0, 0, 0)])
        self.addCleanup(ledger.close)
        config = configuration(dict(provider='claude', model=HAIKU, model_version='2.1.270',
            credential_ref='fixture', quota_group='fixture-group'))
        with patch.object(TranslationCLI, 'readiness', return_value=(False, 'translation_cli_unverified')):
            result = translation_tick(self.root, config, ledger.path)
        self.assertEqual(result['call_readiness'], {'state': 'waiting', 'reasons': [
            'translation_cli_unverified', 'account_confirmation_required']})
        self.assertEqual(ledger.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)

    def test_preflight_reads_copy_without_claiming_or_writing(self):
        db_path, ledger, config_path, _ = self._state()
        before = hashlib.sha256(db_path.read_bytes()).hexdigest()
        ledger_before = hashlib.sha256(ledger.path.read_bytes()).hexdigest()
        with patch.object(TranslationCLI, 'readiness', return_value=(True, None)):
            result = preflight(self.root, 'translation', config_path, None, ledger.path, True)
        self.assertEqual(result, {'component': 'translation', 'status': 'waiting',
                                  'reasons': ['account_confirmation_required']})
        self.assertEqual(hashlib.sha256(db_path.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(ledger.path.read_bytes()).hexdigest(), ledger_before)
        with closing(sqlite3.connect(db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 0)
        self.assertEqual(preflight(self.root, 'translation', config_path, None, None, True)['status'], 'blocked')
        db_path.write_bytes(b'not a database')
        self.assertEqual(preflight(self.root, 'translation', config_path, None, ledger.path, True)['reasons'],
                         ['management_db_invalid'])

    def test_preflight_command_reports_wait_without_model_call(self):
        db_path, ledger, config_path, _ = self._state()
        env = {**os.environ, 'AI_COMPANY_TRANSLATION_CLI_PATH': str(self.root / 'missing-cli'),
               'AI_COMPANY_TRANSLATION_CLI_SHA256': '0' * 64}
        command = [sys.executable, '-m', 'ai_company.service_worker', 'translation', '--preflight',
                   '--state-dir', str(self.root), '--config', str(config_path),
                   '--shared-call-ledger', str(ledger.path), '--execute-translations']
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'waiting')
        self.assertEqual(report['reasons'], ['translation_cli_missing', 'account_confirmation_required'])
        with closing(sqlite3.connect(db_path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 0)

    def test_invalid_start_is_bounded_by_existing_systemd_limit(self):
        unit = (Path(__file__).resolve().parents[1] / 'deploy/systemd/ai-company-translation.service').read_text()
        self.assertIn('StartLimitIntervalSec=600', unit)
        self.assertIn('StartLimitBurst=5', unit)
        self.assertIn('RestartSec=15', unit)
        db_path, ledger, config_path, _ = self._state()
        config_path.write_text('{invalid')
        self.assertEqual(preflight(self.root, 'translation', config_path, None, ledger.path, True),
                         {'component': 'translation', 'status': 'blocked', 'reasons': ['worker_config_invalid']})

    def test_pre_start_cli_change_cancels_without_invoking_child(self):
        job = {'id': 'job', 'lease_token': 'lease', 'config': {'provider': 'claude'}}
        class Store:
            shared_calls = None
            started_calls = 0
            finished = None
            def claim(self, *_args, **_kwargs):
                return job
            def started(self, *_args):
                self.started_calls += 1
                return True
            def finish(self, _id, _token, result):
                self.finished = result
                return True
            def _get(self, _id):
                return {'status': 'blocked', 'reason': 'translation_cli_unverified'}
            def _public(self, value):
                return value
        class Adapter:
            available = True
            execution_calls = 0
            def readiness(self, _config):
                return True, None
            def ready(self, _config):
                return True
            def execute(self, _job, _on_start):
                self.execution_calls += 1
                return {'category': 'blocked', 'reason': 'translation_cli_unverified',
                        'execution_not_started': True}
        store, adapter = Store(), Adapter()
        self.assertEqual(run_once(store, adapter)['status'], 'blocked')
        self.assertEqual(adapter.execution_calls, 1)
        self.assertEqual(store.started_calls, 0)
        self.assertTrue(store.finished['execution_not_started'])

    def test_adapter_exception_remains_uncertain(self):
        class Store:
            shared_calls = None
            started_calls = 0
            def claim(self, *_args, **_kwargs):
                return {'id': 'job', 'lease_token': 'lease', 'config': {}}
            def started(self, _id, _token, identity):
                self.started_calls += 1
                self.assert_identity = identity
                return True
        class Adapter:
            available = True
            def readiness(self, _config):
                return True, None
            def ready(self, _config):
                return True
            def execute(self, _job, _on_start):
                raise RuntimeError('could have spawned')
        store = Store()
        self.assertEqual(run_once(store, Adapter())['reason'], 'adapter_termination_unconfirmed')
        self.assertEqual(store.started_calls, 1)
        self.assertIsNone(store.assert_identity)


if __name__ == '__main__':
    unittest.main()
