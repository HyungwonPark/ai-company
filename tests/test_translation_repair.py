"""Versioned native-result repair; no model calls or operating-state writes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from ai_company.adapters.translation_cli import HAIKU, TranslationCLI
from ai_company.contracts import digest
from ai_company.translations import (TranslationStore, initialize, configuration,
    source_digest, validate_fields, segments, REPAIR_PARSER, REPAIR_PROMPT)

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/repair_recorded_translations.py'
spec = importlib.util.spec_from_file_location('translation_repair_operator', SCRIPT)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


class TranslationRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.path = self.root / 'state.sqlite'
        self.db = sqlite3.connect(self.path); self.addCleanup(self.db.close)
        initialize(self.db)
        self.db.executescript("""CREATE TABLE quota_groups(group_id TEXT PRIMARY KEY,state TEXT,resume_at REAL,reason TEXT);
          CREATE TABLE credential_groups(provider TEXT PRIMARY KEY,credential_ref TEXT,group_id TEXT);
          INSERT INTO quota_groups VALUES('shared','AVAILABLE',NULL,NULL);
          INSERT INTO credential_groups VALUES('claude','fixture','shared');""")
        self.now = 1000.
        self.store = TranslationStore(self.db, clock=lambda: self.now)
        self.config = configuration(dict(provider='claude', model=HAIKU, model_version='2.1.270',
            credential_ref='fixture', quota_group='shared', max_attempts=2))
        self.document = dict(id='plan:fixture', kind='plan', project_id='fixture', source_version='v1',
            author_role='pm', source_ref={'plan_id': 'fixture'}, fields={'summary': 'Inspect src/only.py.'},
            protected={'plan_digest': 'a'*64, 'approval': 'pending'})

    def failed(self, *, text='Inspect src/only.py.', translated='src/only.py 파일을 검사합니다.', stopped=True):
        self.document['fields'] = {'summary': text}
        self.store.sync('fixture', [self.document], self.config)
        job = self.store.claim('fixture', adapter_ready=True)
        directory = self.root / job['id']; directory.mkdir()
        self.store.started(job['id'], job['lease_token'], {'systemd_unit': 'fixture-only', 'evidence_dir': str(directory)})
        events = [
            {'type': 'control_response', 'response': {'request_id': 'fixture-init', 'subtype': 'success',
                'response': {'models': [{'resolvedModel': HAIKU}]}}},
            {'type': 'system', 'subtype': 'init', 'session_id': 'fixture-session', 'model': HAIKU, 'tools': [], 'mcp_servers': []},
            {'type': 'result', 'subtype': 'success', 'is_error': False, 'permission_denials': [],
                'total_cost_usd': .001, 'result': json.dumps({'summary::0': translated}, ensure_ascii=False)}]
        (directory / 'stdout.log').write_text('\n'.join(json.dumps(event) for event in events))
        result = TranslationCLI.parse(events, 'fixture-init')
        result.update(cgroup_stopped=stopped, evidence_dir=str(directory))
        self.now += 3
        self.store.finish(job['id'], job['lease_token'], result)
        failed = self.store._get(job['id'])
        self.assertEqual(failed['status'], 'failed')
        return failed

    def test_path_sentence_period_replay_keeps_original_failure_and_source(self):
        original = self.failed(); before = copy.deepcopy(original)
        with patch('subprocess.Popen', side_effect=AssertionError('no model call')):
            replay = TranslationCLI.replay(original)
            repaired = self.store.repair(original['id'], expected_original_digest=digest(original), replay=replay)
        self.assertEqual(repaired['status'], 'completed')
        self.assertNotEqual(repaired['id'], original['id'])
        self.assertEqual(repaired['fields'], {'summary': 'src/only.py 파일을 검사합니다.'})
        self.assertEqual(repaired['reprocessing']['new_model_calls'], 0)
        self.assertEqual(self.store._get(original['id']), before)
        self.assertEqual(self.store.get_result(repaired['id'])['source'], original['source'])
        self.assertEqual(self.store.get_result(repaired['id'])['attempts'], original['attempts'])
        self.assertEqual(self.store.repair(original['id'], expected_original_digest=digest(original), replay=replay), repaired)
        self.store.sync('fixture', [self.document], self.config)
        self.assertEqual(self.store.read(self.document)['id'], repaired['id'])
        self.assertEqual(self.store.summary('fixture')['counts'], {'completed': 1})
        observed = self.store.summary('fixture')['observed_configuration']
        self.assertEqual(observed['model'], HAIKU)
        self.assertEqual(observed['session_id'], 'fixture-session')
        self.assertEqual(observed['evidence']['reprocessing']['original_failed_job_id'], original['id'])
        with sqlite3.connect(self.path) as reopened:
            store = TranslationStore(reopened)
            store.sync('fixture', [self.document], self.config)
            self.assertEqual(store.read(self.document)['id'], repaired['id'])

    def test_full_paths_code_literals_conditions_and_duplicates_remain_protected(self):
        cases = [
            ('Inspect src/only.py.', 'src/only.py 파일을 검사합니다.', True),
            ('Inspect src/only.py.', '`src/only.py` 파일을 검사합니다.', True),
            ('Inspect src/only.py.', 'evil/src/only.py 파일을 검사합니다.', False),
            ('Inspect src/only.py.', 'src/only.py.bak 파일을 검사합니다.', False),
            ('Inspect tests/test_only.py.', 'tests/test_only.py 파일을 검사합니다.', True),
            ('Inspect /tmp/only.py.', '/tmp/only.py 파일을 검사합니다.', True),
            ('Inspect `src/only.` exactly.', '`src/only` 파일을 검사합니다.', False),
            ('Inspect src/only.py.', 'src/only.py 와 src/only.py 파일을 검사합니다.', False),
            ('Only if verified, keep BLOCKED.', '검증된 경우에만 BLOCKED 를 유지합니다.', True),
            ('Only if verified, keep BLOCKED.', '검증된 경우에만 유지합니다.', False),
            ('Do not approve.', '승인합니다.', False),
        ]
        for source, target, valid in cases:
            with self.subTest(source=source, target=target):
                job={'source': {'fields': {'summary':source}}, 'config': configuration({'parser_version':REPAIR_PARSER})}
                if valid:
                    self.assertEqual(validate_fields(job, {'summary::0':target}), {'summary':target})
                else:
                    with self.assertRaises(ValueError):validate_fields(job, {'summary::0':target})
        legacy={'source': {'fields': {'summary':'Inspect src/only.py.'}}, 'config':configuration()}
        with self.assertRaises(ValueError):validate_fields(legacy, {'summary::0':'src/only.py 파일을 검사합니다.'})

    def test_missing_literals_and_non_json_are_not_reinterpreted_as_success(self):
        original = self.failed(text='Keep BLOCKED status.', translated='차단 상태를 유지합니다.')
        replay = TranslationCLI.replay(original)
        with self.assertRaises(ValueError):
            self.store.repair(original['id'], expected_original_digest=digest(original), replay=replay)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 1)
        self.assertEqual(self.store.read(self.document)['id'], original['id'])
        directory = Path(original['execution_identity']['evidence_dir'])
        events=[json.loads(line) for line in (directory/'stdout.log').read_text().splitlines()]
        events[-1]['result']='Here is the translation: {"summary::0":"BLOCKED 상태 유지"}'
        (directory/'stdout.log').write_text('\n'.join(json.dumps(event) for event in events))
        with self.assertRaisesRegex(ValueError, 'translation_output_not_json'):TranslationCLI.replay(original)
        events[-1]['result']='{"summary::0":"BLOCKED 상태 유지"}';events[1]['tools']=['Bash']
        (directory/'stdout.log').write_text('\n'.join(json.dumps(event) for event in events))
        with self.assertRaisesRegex(ValueError, 'effective_tool_catalog_not_empty'):TranslationCLI.replay(original)

    def test_remaining_budget_and_shared_quota_apply_to_one_new_attempt(self):
        original = self.failed(text='Keep BLOCKED status.', translated='차단 상태를 유지합니다.')
        repaired = self.store.repair(original['id'], expected_original_digest=digest(original))
        child = self.store._get(repaired['id'])
        self.assertEqual((child['attempts'], child['spent_seconds']), (1, 3))
        self.assertEqual(child['config']['quota_group'], 'shared')
        self.assertEqual(child['config']['max_attempts'], original['config']['max_attempts'])
        prompt = TranslationCLI.prompt(child)
        self.assertIn('"BLOCKED"', prompt)
        self.assertIn('protected_literals', prompt)
        with self.db:self.db.execute("UPDATE quota_groups SET state='COOLDOWN',resume_at=?", (self.now+100,))
        self.assertIsNone(self.store.claim('fixture', adapter_ready=True))
        self.assertEqual(self.store.read(self.document)['status'], 'waiting_quota')
        self.now += 101
        job = self.store.claim('fixture', adapter_ready=True)
        self.assertEqual(job['attempts'], 2)
        self.store.finish(job['id'], job['lease_token'], {'category':'code_error','cgroup_stopped':True})
        with self.assertRaises(ValueError):
            current=self.store._get(job['id']);self.store.repair(current['id'], expected_original_digest=digest(current))
        self.assertIsNone(self.store.claim('fixture', adapter_ready=True))
        self.assertEqual(self.store.repair(original['id'], expected_original_digest=digest(original))['id'], job['id'])

    def test_unknown_termination_tampering_and_exhausted_budget_block_repairs(self):
        original = self.failed(stopped=False)
        with self.assertRaises(ValueError):TranslationCLI.replay(original)
        with self.assertRaises(ValueError):self.store.repair(original['id'], expected_original_digest=digest(original))
        with self.assertRaises(ValueError):self.store.repair(original['id'], expected_original_digest='0'*64)
        original['execution_result']['cgroup_stopped']=True;original['attempts']=original['config']['max_attempts']
        with self.db:self.store._save(original)
        original=self.store._get(original['id'])
        with self.assertRaises(ValueError):self.store.repair(original['id'], expected_original_digest=digest(original))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 1)

    def test_concurrent_repair_is_one_artifact_and_other_config_does_not_adopt_it(self):
        original = self.failed()
        replay=TranslationCLI.replay(original)
        def repair(_):
            with sqlite3.connect(self.path, timeout=5) as connection:
                store=TranslationStore(connection, clock=lambda:self.now)
                return store.repair(original['id'], expected_original_digest=digest(original), replay=replay)['id']
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(repair,range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 2)
        self.store.sync('fixture',[self.document],{**self.config,'prompt_version':'different-authorized-prompt'})
        self.assertNotEqual(self.store.read(self.document)['id'],ids[0])
        self.assertIsNone(self.store.summary('fixture')['observed_configuration'])

    def test_new_source_keeps_its_own_link(self):
        original=self.failed();replay=TranslationCLI.replay(original)
        changed=copy.deepcopy(self.document);changed['source_version']='v2';changed['fields']['summary']='Inspect different behavior.'
        self.store.sync('fixture',[changed],self.config)
        pending=self.store.read(changed)
        self.store.repair(original['id'],expected_original_digest=digest(original),replay=replay)
        self.assertEqual(self.store.read(changed),pending)
        self.assertEqual(self.store._get(original['id']),original)

    def test_lost_selection_rolls_back_repair_insertion(self):
        original = self.failed(); replay = TranslationCLI.replay(original)
        self.store.sync('fixture', [self.document], {**self.config, 'prompt_version': 'new-authorized-prompt'})
        selected = self.store.read(self.document)
        before = self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'no longer the selected'):
            self.store.repair(original['id'], expected_original_digest=digest(original), replay=replay)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], before)
        self.assertEqual(self.store.read(self.document), selected)

    def test_operator_preview_idempotent_apply_and_stale_proposal(self):
        original = self.failed()
        proposal = self.root / 'proposal.json'
        before = list(self.db.iterdump())
        operator.main(['--database', str(self.path), '--job', original['id'], '--output', str(proposal)])
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(proposal.stat().st_mode & 0o777, 0o600)
        plan = json.loads(proposal.read_text())
        self.assertEqual(plan['jobs'][0]['maximum_new_model_calls'], 0)
        with patch.object(operator, 'require_running_candidate', side_effect=ValueError('old worker')):
            with self.assertRaisesRegex(ValueError, 'old worker'):
                operator.apply_plan(plan, candidate_release=self.root)
        self.assertEqual(list(self.db.iterdump()), before)
        stale = copy.deepcopy(plan); stale['jobs'][0]['raw_sha256'] = '0'*64
        with patch.object(operator, 'require_running_candidate', return_value=123):
            with self.assertRaisesRegex(ValueError, '변경됐습니다'):
                operator.apply_plan(stale, candidate_release=self.root)
            self.assertEqual(list(self.db.iterdump()), before)
            first = operator.apply_plan(plan, candidate_release=self.root)
            second = operator.apply_plan(plan, candidate_release=self.root)
        self.assertEqual(first, second)
        self.assertEqual(first[0]['status'], 'completed')
        self.assertEqual(self.store._get(original['id']), original)

    def test_operator_rejects_wrong_hash_running_release_and_unsafe_native_evidence(self):
        original = self.failed(text='Keep BLOCKED status.', translated='차단 상태를 유지합니다.')
        action, replay = operator.action_for(self.store, original['id'])
        self.assertIsNone(replay)
        self.assertEqual(action['maximum_new_model_calls'], 1)
        proposal = self.root / 'wrong.json'; proposal.write_text('{}')
        with patch.object(operator, 'apply_plan', side_effect=AssertionError('must not apply')):
            with self.assertRaises(SystemExit):
                operator.main(['--apply', '--proposal', str(proposal), '--expected-proposal-sha256', '0'*64,
                               '--candidate-release', str(self.root)])
        with patch.object(operator.subprocess, 'check_output', return_value=f'ActiveState=active\nMainPID={os.getpid()}\n'):
            with self.assertRaisesRegex(ValueError, '후보 release'):
                operator.require_running_candidate(self.path, self.root)
        raw = Path(original['execution_identity']['evidence_dir']) / 'stdout.log'
        events = [json.loads(line) for line in raw.read_text().splitlines()]
        for altered in [events + [events[-1]], [events[0], {**events[1], 'session_id': 'another'}, events[-1]],
                        [events[0], {**events[1], 'tools': ['Bash']}, events[-1]]]:
            raw.write_text('\n'.join(json.dumps(event) for event in altered))
            with self.assertRaises(ValueError):operator.action_for(self.store, original['id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_jobs').fetchone()[0], 1)
