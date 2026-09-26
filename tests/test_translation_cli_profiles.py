"""The private CLI profile is independent of durable translation job policy."""
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters.translation_cli import CLI_FLAGS, HAIKU, TranslationCLI
from ai_company.contracts import digest
from ai_company.shared_calls import SharedCallLedger
from ai_company.translation_worker import run_once
from ai_company.translations import EXECUTION_FACT_KEYS, TranslationStore, configuration, initialize


class TranslationCLIProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = configuration(dict(provider='claude', model=HAIKU, model_version='2.1.270',
            quota_group='shared', credential_ref='fixture'))

    def cli(self, *, version='2.1.270 (Claude Code)', flags=CLI_FLAGS):
        path = self.root / 'claude'
        path.write_text('#!' + sys.executable + '\nimport sys\n'
            'if "--version" in sys.argv: print(' + repr(version) + ')\n'
            'elif "--help" in sys.argv: print(' + repr(' '.join(flags)) + ')\n')
        path.chmod(0o700)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_pinned_executable_matches_review_and_invocation(self):
        path, fingerprint = self.cli()
        adapter = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        self.assertEqual(adapter.readiness(self.config), (True, None))
        spec = adapter.command_spec(self.config)
        self.assertTrue(spec['executable'])
        self.assertEqual(spec['argv'][0], str(path.resolve()))
        self.assertEqual(spec['cli_sha256'], fingerprint)
        self.assertEqual(spec['cli_version'], '2.1.270')
        self.assertEqual(self.config['model_version'], '2.1.270')
        self.assertNotIn('cli_executable', self.config)
        with patch.dict(os.environ, {'AI_COMPANY_TRANSLATION_CLI_PATH': str(path),
                                  'AI_COMPANY_TRANSLATION_CLI_SHA256': fingerprint}):
            from_private_env = TranslationCLI(self.root / 'runtime')
            self.assertEqual(from_private_env.readiness(self.config), (True, None))
            self.assertEqual(from_private_env.command_spec(self.config)['argv'][0], str(path.resolve()))

    def test_missing_tampered_other_version_and_missing_options_block_before_spawn(self):
        path, fingerprint = self.cli()
        adapter = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        path.unlink()
        self.assertEqual(adapter.readiness(self.config), (False, 'translation_cli_missing'))
        path, fingerprint = self.cli(version='2.1.283 (Claude Code)')
        wrong = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        self.assertEqual(wrong.readiness(self.config), (False, 'translation_cli_unverified'))
        self.assertEqual(wrong.execute({'config': self.config}, lambda _: self.fail('unexpected start')),
            {'category': 'blocked', 'reason': 'translation_cli_unverified', 'tool_calls': [],
             'observed_configuration': None, 'execution_not_started': True})
        path, fingerprint = self.cli(flags=CLI_FLAGS[:-1])
        no_options = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        self.assertEqual(no_options.readiness(self.config), (False, 'translation_cli_options_unverified'))
        path, fingerprint = self.cli()
        pinned = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        self.assertTrue(pinned.ready(self.config))
        path.write_text(path.read_text() + '# changed\n')
        self.assertEqual(pinned.readiness(self.config), (False, 'translation_cli_unverified'))
        self.assertFalse(pinned.command_spec(self.config)['executable'])
        self.assertFalse(TranslationCLI(self.root / 'runtime', cli_executable=str(path)).ready(self.config))
        with patch.dict(os.environ, {'AI_COMPANY_TRANSLATION_CLI_PATH': str(path),
                                  'AI_COMPANY_TRANSLATION_CLI_SHA256': ''}):
            self.assertEqual(TranslationCLI(self.root / 'runtime').readiness(self.config),
                             (False, 'translation_cli_unverified'))

    def test_binary_changed_after_command_construction_does_not_spawn(self):
        path, fingerprint = self.cli()
        adapter = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=fingerprint)
        original_spec = adapter.command_spec
        def mutate(config):
            spec = original_spec(config)
            path.write_text(path.read_text() + '# changed after spec\n')
            return spec
        with patch.object(adapter, 'command_spec', side_effect=mutate):
            outcome = adapter.execute({'config': self.config}, lambda _: self.fail('unexpected start'))
        self.assertEqual(outcome['reason'], 'translation_cli_unverified')
        self.assertTrue(outcome['execution_not_started'])

    def test_waiting_job_keeps_identity_digest_and_budget_when_profile_is_fixed(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        database.executescript("""CREATE TABLE quota_groups(group_id TEXT PRIMARY KEY,state TEXT,resume_at REAL,reason TEXT);
          CREATE TABLE credential_groups(provider TEXT PRIMARY KEY,credential_ref TEXT,group_id TEXT);
          INSERT INTO quota_groups VALUES('shared','AVAILABLE',NULL,NULL);
          INSERT INTO credential_groups VALUES('claude','fixture','shared');""")
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: 1000)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: 1000, shared_calls=shared, queue_id='fixture-queue')
        document = dict(id='approval:1', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '1'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        first = store.sync('test', [document], self.config)[0]
        path, wrong_sha = self.cli(version='2.1.283 (Claude Code)')
        wrong = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=wrong_sha)
        self.assertIsNone(store.claim('worker', adapter_ready=wrong.readiness))
        waiting = store.read(document)
        self.assertEqual((waiting['id'], waiting['reason']), (first['id'], 'translation_cli_unverified'))
        original = store._get(first['id'])
        original_digest = digest(original['config'])
        self.assertEqual(original['attempts'], 0)
        path, correct_sha = self.cli()
        verified = TranslationCLI(self.root / 'runtime', cli_executable=str(path), cli_sha256=correct_sha)
        claim = store.claim('worker', adapter_ready=verified.readiness)
        self.assertEqual(claim['id'], first['id'])
        self.assertEqual(claim['attempts'], 1)
        self.assertEqual(claim['config']['model_version'], '2.1.270')
        self.assertEqual(digest(claim['config']), original_digest)
        self.assertEqual(store.sync('test', [document], self.config)[0]['id'], first['id'])
        self.assertEqual(database.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 1)

    def test_no_call_cli_block_does_not_spend_single_attempt(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        database.executescript("""CREATE TABLE quota_groups(group_id TEXT PRIMARY KEY,state TEXT,resume_at REAL,reason TEXT);
          CREATE TABLE credential_groups(provider TEXT PRIMARY KEY,credential_ref TEXT,group_id TEXT);
          INSERT INTO quota_groups VALUES('shared','AVAILABLE',NULL,NULL);
          INSERT INTO credential_groups VALUES('claude','fixture','shared');""")
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: 1000)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: 1000, shared_calls=shared, queue_id='fixture-queue')
        config = configuration({**self.config, 'max_attempts': 1})
        document = dict(id='approval:2', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '2'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        store.sync('test', [document], config)
        first = store.claim('worker', adapter_ready=True)
        self.assertTrue(store.finish(first['id'], first['lease_token'], dict(category='blocked',
            reason='translation_cli_unverified', execution_not_started=True)))
        blocked = store._get(first['id'])
        self.assertEqual((blocked['status'], blocked['attempts'], blocked['spent_seconds']), ('blocked', 0, 0))
        self.assertEqual(shared.reservation(first['shared_reservation_id'])['state'], 'CANCELLED')
        resumed = store.claim('worker', adapter_ready=True)
        self.assertEqual(resumed['id'], first['id'])
        self.assertEqual(resumed['attempts'], 1)
        self.assertEqual(resumed['shared_reservation_id'], first['shared_reservation_id'])
        self.assertEqual(shared.reservation(first['shared_reservation_id'])['state'], 'RESERVED')

    def test_quota_evidence_survives_later_unstarted_cli_block(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        database.executescript("""CREATE TABLE quota_groups(group_id TEXT PRIMARY KEY,state TEXT,resume_at REAL,reason TEXT);
          INSERT INTO quota_groups VALUES('shared','AVAILABLE',NULL,NULL);""")
        self.now = 1000
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: self.now)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: self.now, shared_calls=shared, queue_id='fixture-queue')
        document = dict(id='approval:3', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '3'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        store.sync('test', [document], self.config)
        first = store.claim('worker', adapter_ready=True)
        self.assertTrue(store.started(first['id'], first['lease_token'], {'unit': 'fixture-1'}))
        self.now = 1001
        result = dict(category='quota', reason='provider_quota', reset_at=1040,
            cgroup_stopped=True, total_cost_usd=0.01,
            observed_configuration={'provider': 'claude', 'model': HAIKU, 'status': 'observed'})
        self.assertTrue(store.finish(first['id'], first['lease_token'], result))
        before = store._get(first['id'])
        self.assertEqual(before['status'], 'waiting_quota')
        self.now = 1041
        second = store.claim('worker', adapter_ready=True)
        self.assertEqual(second['id'], first['id'])
        self.assertTrue(store.finish(second['id'], second['lease_token'], dict(category='blocked',
            reason='translation_cli_unverified', execution_not_started=True)))
        blocked = store._get(first['id'])
        self.assertEqual((blocked['attempts'], blocked['spent_seconds']),
                         (before['attempts'], before['spent_seconds']))
        self.assertEqual({key: blocked.get(key) for key in EXECUTION_FACT_KEYS},
                         {key: before.get(key) for key in EXECUTION_FACT_KEYS})
        self.assertEqual(blocked['reason'], 'translation_cli_unverified')
        resumed = store.claim('second-worker', adapter_ready=True)
        self.assertEqual((resumed['id'], resumed['attempts']), (first['id'], 2))

    def test_unknown_account_wait_rechecks_only_after_external_confirmation(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'UNKNOWN', None, 'account_binding_unverified', 0, 0, 0, 0)],
            clock=lambda: 1000)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: 1000, shared_calls=shared, queue_id='fixture-queue')
        document = dict(id='approval:4', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '4'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        original = store.sync('test', [document], self.config)[0]
        calls = []
        class DummyAdapter:
            available = True
            def readiness(self, _config):
                return True, None
            def ready(self, _config):
                return True
            def execute(self, job, on_start):
                calls.append(job['id'])
                on_start({'unit': 'fixture-call'})
                return dict(category='success', fields={'title::0': '승인하지 마세요.'},
                            cgroup_stopped=True, tool_calls=[])
        adapter = DummyAdapter()
        self.assertIsNone(run_once(store, adapter))
        self.assertIsNone(run_once(store, adapter))
        waiting = store._get(original['id'])
        self.assertEqual((waiting['status'], waiting['reason'], waiting['attempts']),
                         ('blocked', 'shared_credential_unavailable', 0))
        self.assertEqual(shared.account('claude', 'fixture', 'shared')['state'], 'UNKNOWN')
        self.assertEqual(shared.db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0], 0)
        # Simulates the separate trusted account-reconciliation procedure.
        shared.db.execute("UPDATE accounts SET state='AVAILABLE',reason=NULL WHERE group_id='shared'")
        completed = run_once(store, adapter)
        self.assertEqual((completed['id'], completed['status']), (original['id'], 'completed'))
        self.assertIsNone(run_once(store, adapter))
        self.assertEqual(calls, [original['id']])
        self.assertEqual(shared.db.execute('SELECT COUNT(*) FROM settlement_events').fetchone()[0], 1)
        self.assertEqual(shared.account('claude', 'fixture', 'shared')['calls'], 1)

    def test_crash_after_shared_cancellation_restores_same_job_without_usage(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        self.now = 1000
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: self.now)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: self.now, shared_calls=shared, queue_id='fixture-queue')
        config = configuration({**self.config, 'max_attempts': 1})
        document = dict(id='approval:5', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '5'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        initial = store.sync('test', [document], config)[0]
        claim = store.claim('first-worker', adapter_ready=True)
        before = store._get(claim['id'])
        with patch.object(store, '_save', side_effect=RuntimeError('crash before local commit')):
            with self.assertRaisesRegex(RuntimeError, 'crash before local commit'):
                store.finish(claim['id'], claim['lease_token'], dict(category='blocked',
                    reason='translation_cli_unverified', execution_not_started=True))
        self.assertEqual(shared.reservation(claim['shared_reservation_id'])['state'], 'CANCELLED')
        self.assertEqual(store._get(claim['id']), before)
        self.now = claim['lease_expires_at'] + 1
        self.assertEqual(store.recover(lambda _: None), 1)
        recovered = store._get(initial['id'])
        self.assertEqual((recovered['status'], recovered['attempts'], recovered['spent_seconds']),
                         ('waiting_retry', 0, 0))
        self.assertEqual(shared.account('claude', 'fixture', 'shared')['state'], 'AVAILABLE')
        self.assertEqual(shared.db.execute('SELECT COUNT(*) FROM settlement_events').fetchone()[0], 0)
        next_claim = store.claim('second-worker', adapter_ready=True)
        self.assertEqual((next_claim['id'], next_claim['attempts']), (initial['id'], 1))
        self.assertIsNone(store.claim('third-worker', adapter_ready=True))

    def test_legacy_unstarted_claim_without_snapshot_fails_closed_without_erasing_facts(self):
        database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(database.close)
        initialize(database)
        self.now = 1000
        shared = SharedCallLedger.initialize(self.root / 'calls.sqlite',
            [('claude', 'fixture', 'shared', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: self.now)
        self.addCleanup(shared.close)
        store = TranslationStore(database, clock=lambda: self.now, shared_calls=shared, queue_id='fixture-queue')
        document = dict(id='approval:6', kind='approval', project_id='test', source_version=1,
            author_role='pm', source_ref={'id': '6'}, fields={'title': 'Do not approve.'},
            protected={'approval': 'pending'})
        store.sync('test', [document], self.config)
        claim = store.claim('old-worker', adapter_ready=True)
        legacy = store._get(claim['id'])
        legacy.pop('_preclaim_execution')
        legacy['execution_result'] = {'category': 'quota', 'reason': 'old_quota_evidence'}
        legacy['execution_identity'] = {'unit': 'old-unit'}
        store._save(legacy)
        database.commit()
        before = store._get(claim['id'])
        self.now = claim['lease_expires_at'] + 1
        self.assertEqual(store.recover(lambda _: None), 0)
        blocked = store._get(claim['id'])
        self.assertEqual((blocked['status'], blocked['reason']),
            ('blocked', 'unstarted_snapshot_missing_requires_reconciliation'))
        self.assertEqual({key: blocked.get(key) for key in EXECUTION_FACT_KEYS},
                         {key: before.get(key) for key in EXECUTION_FACT_KEYS})
        self.assertIsNone(store.claim('another-worker', adapter_ready=True))


if __name__ == '__main__':
    unittest.main()
