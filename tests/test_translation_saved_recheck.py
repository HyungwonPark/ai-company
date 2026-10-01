"""Saved-output Korean review: no model calls and no operating-state writes."""
import copy
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from ai_company.adapters.translation_cli import TranslationCLI
from ai_company.contracts import digest
from ai_company.translations import (TranslationStore, configuration, validate_fields,
    REPAIR_PARSER, SAVED_RECHECK_PARSER, SAVED_REVIEW_VERSION)
from tests import test_translation_repair as repair_tests

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/recheck_saved_translations.py'
spec = importlib.util.spec_from_file_location('saved_translation_operator', SCRIPT)
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


class SavedTranslationRecheckTests(unittest.TestCase):
    setUp = repair_tests.TranslationRepairTests.setUp
    failed = repair_tests.TranslationRepairTests.failed

    def repaired_failure(self, *, text='No pending findings.', translated='미결 항목이 없습니다.'):
        original = self.failed(text=text, translated=translated)
        repaired = self.store.repair(original['id'], expected_original_digest=digest(original))
        job = self.store.claim('fixture', adapter_ready=True)
        self.assertEqual(job['id'], repaired['id'])
        directory = self.root / job['id']; directory.mkdir()
        events = [json.loads(line) for line in (Path(original['execution_identity']['evidence_dir']) / 'stdout.log').read_text().splitlines()]
        (directory / 'stdout.log').write_text('\n'.join(json.dumps(event, ensure_ascii=False) for event in events))
        self.store.started(job['id'], job['lease_token'], {'systemd_unit': 'fixture-only', 'evidence_dir': str(directory)})
        result = TranslationCLI.parse(events, 'fixture-init')
        result.update(cgroup_stopped=True, evidence_dir=str(directory))
        self.now += 3
        self.store.finish(job['id'], job['lease_token'], result)
        original = self.store._get(job['id'])
        self.assertEqual((original['status'], original['attempts']), ('failed', 2))
        return original

    def review(self, original, *, verdict='PASS'):
        replay = TranslationCLI.replay(original)
        return dict(version=SAVED_REVIEW_VERSION, job_id=original['id'], source_digest=original['source_digest'],
            raw_sha256=replay['reprocessing']['raw_sha256'], fields_digest=digest(replay['fields']),
            verdict=verdict, reviewer='독립 저장 출력 검토 · 테스트 자료',
            findings=[] if verdict == 'PASS' else ['승인 권한을 인증으로 옮겨 의미가 달라졌습니다.'])

    def test_saved_v4_forms_preserve_negation_conditions_limits_and_legacy_guards(self):
        cases = [
            ('This is not actual master UI confirmation.', '이는 실제 마스터 UI 확인이 아닙니다.', True),
            ('This is not actual master UI confirmation.', '이는 실제 마스터 UI 확인입니다.', False),
            ('This is not actual master UI confirmation.', '이는 실제 마스터 UI 확인이 아닙니까?', False),
            ('No pending findings.', '미결 항목이 없습니다.', True),
            ('No pending findings.', '미결 항목이 있습니다.', False),
            ('Findings remain pending.', '항목은 미결 상태입니다.', True),
            ('Findings remain pending.', '항목은 완료 상태입니다.', False),
            ('Only if verified, allow at most 2 changes.', '검증된 경우에만 최대 2개 변경을 허용합니다.', True),
            ('Only if verified, allow at most 2 changes.', '검증된 경우 최대 2개 변경을 허용합니다.', False),
            ('Only if verified, allow at most 2 changes.', '검증된 경우에만 2개 변경을 허용합니다.', False),
            ('If verified, permit changes.', '변경을 허용합니다.', False),
            ('Permit changes except deployment.', '배포를 허용합니다.', False),
            ('Inspect src/only.py.', 'evil/src/only.py 파일을 검사합니다.', False),
        ]
        for source, target, valid in cases:
            with self.subTest(source=source, target=target):
                job = {'source': {'fields': {'summary': source}}, 'config': configuration({'parser_version': SAVED_RECHECK_PARSER})}
                if valid:
                    self.assertEqual(validate_fields(job, {'summary::0': target}), {'summary': target})
                else:
                    with self.assertRaises(ValueError): validate_fields(job, {'summary::0': target})
        for parser in ('translation-json-v2', REPAIR_PARSER):
            for source, target in [('No pending findings.', '미결 항목이 없습니다.'),
                                  ('This is not confirmation.', '이는 확인이 아닙니다.')]:
                with self.subTest(parser=parser), self.assertRaises(ValueError):
                    validate_fields({'source': {'fields': {'summary': source}}, 'config': configuration({'parser_version': parser})}, {'summary::0': target})

    def test_saved_result_consumes_no_budget_preserves_history_and_survives_sync_restart(self):
        original = self.repaired_failure()
        review = self.review(original)
        old_jobs = dict(self.db.execute('SELECT id,document FROM translation_jobs'))
        quota = list(self.db.execute('SELECT * FROM quota_groups'))
        with patch('subprocess.Popen', side_effect=AssertionError('no new model or service process')):
            record = self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=review)
        self.assertEqual((record['status'], record['new_model_calls']), ('completed', 0))
        result = self.store.get_result(record['result_job_id'])
        self.assertEqual(result['fields'], {'summary': '미결 항목이 없습니다.'})
        self.assertEqual((result['attempts'], result['spent_seconds']), (original['attempts'], original['spent_seconds']))
        self.assertEqual(result['source'], original['source'])
        self.assertEqual(result['recheck_of'], original['id'])
        self.assertEqual(result['execution_result']['total_cost_usd'], original['execution_result']['total_cost_usd'])
        for jid, raw in old_jobs.items():
            self.assertEqual(self.db.execute('SELECT document FROM translation_jobs WHERE id=?', (jid,)).fetchone()[0], raw)
        self.assertEqual(list(self.db.execute('SELECT * FROM quota_groups')), quota)
        self.assertEqual(self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=review), record)
        with closing(sqlite3.connect(self.path)) as connection:
            reopened = TranslationStore(connection)
            reopened.sync('fixture', [self.document], self.config)
            self.assertEqual(reopened.read(self.document)['id'], result['id'])
            self.assertEqual(reopened.summary('fixture')['counts'], {'completed': 1})
            self.assertIsNone(reopened.claim('fixture', adapter_ready=True))
        with self.assertRaisesRegex(ValueError, 'cannot schedule'):
            self.store.sync('fixture', [self.document], {**self.config, 'parser_version': SAVED_RECHECK_PARSER})

    def test_semantic_block_records_review_but_keeps_original_failure_selected(self):
        original = self.repaired_failure(text='Authorization is delegated validation, not actual UI confirmation.',
            translated='인증은 위임된 검증이며, 실제 UI 확인이 아닙니다.')
        before = dict(self.db.execute('SELECT id,document FROM translation_jobs'))
        links = list(self.db.execute('SELECT * FROM translation_links'))
        record = self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=self.review(original, verdict='BLOCK'))
        self.assertEqual((record['status'], record['reason'], record['result_job_id']), ('rejected', 'semantic_review_blocked', None))
        self.assertEqual(dict(self.db.execute('SELECT id,document FROM translation_jobs')), before)
        self.assertEqual(list(self.db.execute('SELECT * FROM translation_links')), links)
        self.assertEqual(self.store.read(self.document)['status'], 'failed')
        self.assertIsNone(self.store.claim('fixture', adapter_ready=True))

    def test_source_output_review_binding_and_missing_guard_cannot_be_approved_away(self):
        original = self.repaired_failure()
        review = self.review(original)
        for key, value in [('job_id', 'another'), ('source_digest', '0'*64), ('raw_sha256', '0'*64),
                           ('fields_digest', '0'*64), ('version', 'other'), ('verdict', 'approve'), ('findings', ['ignored'])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review={**review, key: value})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_saved_rechecks').fetchone()[0], 0)
        path = Path(original['execution_identity']['evidence_dir']) / 'stdout.log'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[-1]['result'] = json.dumps({'summary::0': '미결 항목이 있습니다.'}, ensure_ascii=False)
        path.write_text('\n'.join(json.dumps(event) for event in events))
        with self.assertRaises(ValueError):
            self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=review)
        # Even a newly bound PASS review cannot bypass a missing negation marker.
        record = self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=self.review(original))
        self.assertEqual(record['status'], 'rejected')
        self.assertIn('marker missing', record['reason'])

    def test_concurrent_rechecks_are_one_artifact_and_new_selection_is_not_overwritten(self):
        original = self.repaired_failure()
        review = self.review(original)
        def run(_):
            with closing(sqlite3.connect(self.path)) as connection:
                return TranslationStore(connection).recheck_saved(original['id'], expected_original_digest=digest(original), review=review)
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(run, range(2)))
        self.assertEqual(records[0], records[1])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM translation_saved_rechecks').fetchone()[0], 1)
        self.store.sync('fixture', [self.document], {**self.config, 'glossary_version': 'new-authorized-glossary'})
        selected = self.store.read(self.document)
        changed_review = {**review, 'reviewer': '별도 재검토자'}
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, 'no longer selected'):
            self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=changed_review)
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(self.store.read(self.document), selected)

    def test_copy_preview_apply_is_idempotent_and_source_remains_read_only(self):
        original = self.repaired_failure()
        before = list(self.db.iterdump())
        plan = operator.plan_for(self.path, [self.review(original)])
        copy_path = self.root / 'isolated-copy.sqlite'
        with patch('subprocess.Popen', side_effect=AssertionError('no new process')):
            first = operator.copy_and_apply(plan, copy_path)
        self.assertEqual(copy_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.db.iterdump()), before)
        with closing(sqlite3.connect(copy_path)) as connection:
            self.assertEqual(operator.apply_to_connection(connection, plan), first)
        with self.assertRaises(FileExistsError): operator.copy_and_apply(plan, self.path)
        stale = copy.deepcopy(plan); stale['jobs'][0]['raw_sha256'] = '0'*64
        with closing(sqlite3.connect(copy_path)) as connection, self.assertRaisesRegex(ValueError, '변경됐습니다'):
            operator.apply_to_connection(connection, stale)

    def test_old_worker_blocks_operating_apply_before_any_write(self):
        original = self.repaired_failure()
        plan = operator.plan_for(self.path, [self.review(original)])
        before = list(self.db.iterdump())
        with patch.object(operator, 'require_running_candidate', side_effect=ValueError('old worker source')):
            with self.assertRaisesRegex(ValueError, 'old worker'):
                operator.apply_operating(plan, self.root)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_restore_only_original_display_link_preserves_artifacts_and_cannot_overwrite_newer_selection(self):
        original = self.repaired_failure()
        record = self.store.recheck_saved(original['id'], expected_original_digest=digest(original), review=self.review(original))
        jobs = dict(self.db.execute('SELECT id,document FROM translation_jobs'))
        audits = list(self.db.execute('SELECT * FROM translation_saved_rechecks'))
        restored = self.store.restore_saved_selection(record['id'], expected_result_job_id=record['result_job_id'])
        self.assertEqual(self.store.read(self.document)['id'], original['id'])
        self.assertEqual(self.store.restore_saved_selection(record['id'], expected_result_job_id=record['result_job_id']), restored)
        self.store.sync('fixture', [self.document], self.config)
        self.assertEqual(self.store.read(self.document)['id'], original['id'])
        self.assertEqual(dict(self.db.execute('SELECT id,document FROM translation_jobs')), jobs)
        self.assertEqual(list(self.db.execute('SELECT * FROM translation_saved_rechecks')), audits)
        self.store.sync('fixture', [self.document], {**self.config, 'glossary_version': 'new-authorized-glossary'})
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, 'newer display selection'):
            self.store.restore_saved_selection(record['id'], expected_result_job_id=record['result_job_id'])
        self.assertEqual(list(self.db.iterdump()), before)
