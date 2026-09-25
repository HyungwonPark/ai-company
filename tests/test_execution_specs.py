"""Operator-catalog validation and immutable project versions; no worker calls."""
import copy
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from tests.legacy_pm import use_legacy_requests
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.automation_contracts import AutomationCI, AutomationConfig
from ai_company.contracts import digest
from ai_company.execution_specs import ExecutionCatalog, ExecutionSpecError, ExecutionSpecSelection
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy
from ai_company.management import ManagementError, ManagementStore


def catalog_config():
    profiles = tuple(AgentProfile(agent_id=key, provider=provider, requested_label=key,
        model='gpt-6-astra' if provider == 'codex' else 'claude-opus-5', reasoning_effort=effort,
        ultracode_enabled=provider == 'claude',
        roles=roles, credential_ref='private-' + provider, quota_group='shared-' + provider,
        allowed_paths=('src/', 'tests/')) for key, provider, effort, roles in (
            ('pm', 'codex', 'ultra', ('pm',)), ('dev', 'codex', 'high', ('developer', 'reviewer')),
            ('backup', 'claude', 'xhigh', ('developer', 'reviewer')),
            ('final', 'codex', 'ultra', ('final',))))
    return AutomationConfig(repository='example/project', source_clone='/operator/private-clone', base_sha='a' * 40,
        base_branch='main', allowed_paths=('src/', 'tests/'), checks={'unit': CheckCommand(argv=('python', '-m', 'unittest'))},
        agents=profiles, policy=FlowPolicy(candidates={'pm': ('pm',), 'developer': ('dev', 'backup'),
            'reviewer': ('backup', 'dev'), 'final': ('final',)}, max_cost_usd=10, max_runtime_seconds=100,
            max_executions=20, max_repairs=3), ci=AutomationCI(workflow_path='.github/workflows/checks.yml',
                workflow_digest='b' * 64, required_checks=('required',)), mode='fixture')


def plan_content():
    return {'summary': '명세 범위에서 독립 개발과 검사', 'roles': [
        {'key': key, 'name': key, 'responsibility': '담당 파일 작성', 'goal': '정해진 산출물',
         'acceptance': ['독립 검사'], 'allowed_paths': [path], 'depends_on': []}
        for key, path in [('impl', 'src/a.py'), ('test', 'tests/a.py')]], 'completion_criteria': ['동일 후보 검수']}


class ExecutionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.config = catalog_config()
        self.catalog = ExecutionCatalog({'project-main': self.config})
        self.selection = {'catalog_id': 'project-main', 'catalog_digest': digest(self.config)}

    def test_local_catalog_file_load_and_duplicate_definition_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'catalog.json'
            source.write_text(json.dumps({'project-main': self.config.model_dump(mode='json')}))
            loaded = ExecutionCatalog.load(source)
            self.assertEqual(digest(loaded.resolve(self.selection)), digest(self.config))
            source.write_text('{"duplicate":{},"duplicate":{}}')
            with self.assertRaises(ExecutionSpecError):
                ExecutionCatalog.load(source)

    def test_catalog_reuses_flow_configuration_rules_without_changing_legacy_config(self):
        original = self.config.model_dump(mode='json')
        original_digest = digest(self.config)
        catalog = ExecutionCatalog({'project-main': self.config})
        self.assertEqual(self.config.model_dump(mode='json'), original)
        self.assertEqual(digest(catalog.resolve(self.selection)), original_digest)
        self.assertNotIn('execution_spec', original)
        for index, field, value in (
                (0, 'model', 'other-model'), (0, 'reasoning_effort', 'high'),
                (3, 'reasoning_effort', 'high'), (2, 'ultracode_enabled', False),
                (1, 'quota_group', 'same-account-different-group')):
            with self.subTest(index=index, field=field):
                invalid = copy.deepcopy(original)
                invalid['agents'][index][field] = value
                with self.assertRaises(ValidationError):
                    ExecutionCatalog({'invalid': invalid})

    def test_finite_cost_limit_discloses_actual_codex_stage_block(self):
        limitations = ' '.join(self.catalog.public_entries()[0]['limitations'])
        self.assertIn('Codex PM·최종 검수는 적격 후보 없음으로 차단', limitations)
        unlimited = self.config.model_copy(update={'policy': self.config.policy.model_copy(update={'max_cost_usd': None})})
        entry = ExecutionCatalog({'unlimited': unlimited}).public_entries()[0]
        self.assertNotIn('적격 후보 없음', ' '.join(entry['limitations']))

    def test_catalog_snapshot_and_public_contract(self):
        expected = self.config.model_dump(mode='json')
        self.config.policy.candidates['developer'] = ('backup',)
        resolved = self.catalog.resolve(self.selection)
        self.assertEqual(resolved.model_dump(mode='json'), expected)
        resolved.policy.candidates['developer'] = ('backup',)
        self.assertEqual(self.catalog.resolve(self.selection).model_dump(mode='json'), expected)
        public = self.catalog.public_entries()[0]
        self.assertEqual(public['checks']['unit']['argv'], ['python', '-m', 'unittest'])
        self.assertEqual(public['catalog_digest'], self.selection['catalog_digest'])
        serialized = json.dumps(public)
        for private in ('source_clone', '/operator/private-clone', 'credential_ref', 'private-codex', 'quota_group'):
            self.assertNotIn(private, serialized)
        self.assertIn('비용 미상', ' '.join(public['limitations']))
        self.assertTrue(all(agent['configuration_status'] == 'requested' for agent in public['agents']))

    def test_subset_keeps_checks_ci_credentials_retry_and_runtime_evidence(self):
        selection = {**self.selection, 'allowed_paths': ['src/a.py', 'tests/a.py'],
            'candidates': {'developer': ['backup'], 'reviewer': ['dev']},
            'role_candidates': {'impl': ['backup'], 'test': ['backup']},
            'budget': {'max_cost_usd': 3.0, 'max_runtime_seconds': 40.0, 'max_executions': 8, 'max_repairs': 1}}
        result = self.catalog.resolve(selection)
        self.assertEqual(result.allowed_paths, ('src/a.py', 'tests/a.py'))
        self.assertEqual(result.policy.candidates['developer'], ('backup',))
        self.assertEqual(result.policy.candidates['pm'], self.config.policy.candidates['pm'])
        for field in ('checks', 'ci', 'agents', 'max_parallel', 'source_clone'):
            self.assertEqual(getattr(result, field), getattr(self.config, field))
        self.assertEqual(result.policy.retry, self.config.policy.retry)
        self.assertEqual(result.policy.configuration_evidence, self.config.policy.configuration_evidence)

    def test_rejects_untrusted_commands_accounts_and_policy_fields(self):
        for field, value in {'checks': {'unit': {'argv': ['sh', '-c', 'anything']}},
                'source_clone': '/tmp/foreign', 'agents': [], 'credential_ref': 'other',
                'ci': {}, 'mode': 'live', 'max_parallel': 100, 'repository': 'other/repo',
                'verified_model': 'anything', 'retry': {}}.items():
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.catalog.resolve({**self.selection, field: value})

    def test_rejects_scope_and_candidate_escalation(self):
        invalid = [
            {'allowed_paths': ['../secret']}, {'allowed_paths': ['src/../secret']},
            {'allowed_paths': ['src//a.py']}, {'allowed_paths': ['.github/workflows/x.yml']},
            {'allowed_paths': ['src/.env']}, {'allowed_paths': ['*']}, {'allowed_paths': ['other/a.py']},
            {'candidates': {'pm': ['dev']}}, {'candidates': {'final': []}},
            {'candidates': {'developer': ['backup', 'dev']}}, {'candidates': {'developer': ['dev', 'dev']}},
            {'candidates': {'developer': []}}, {'role_candidates': {'impl': ['final']}},
            {'role_candidates': {'impl': []}}, {'budget': {'max_cost_usd': 11.0}},
            {'budget': {'max_runtime_seconds': 101.0}}, {'budget': {'max_executions': 21}},
            {'budget': {'max_repairs': 4}}, {'budget': {'max_executions': True}},
            {'budget': {'max_cost_usd': float('nan')}}, {'catalog_digest': 'c' * 64},
        ]
        for difference in invalid:
            with self.subTest(difference=difference), self.assertRaises((ExecutionSpecError, ValidationError)):
                self.catalog.resolve({**self.selection, **difference})
        exact = self.config.model_copy(update={'allowed_paths': ('src/a.py',)})
        cat = ExecutionCatalog({'exact': exact})
        with self.assertRaises(ExecutionSpecError):
            cat.resolve({'catalog_id': 'exact', 'catalog_digest': digest(exact), 'allowed_paths': ['src/a.py/']})


class ExecutionSpecPersistenceTests(unittest.TestCase):
    def setUp(self):
        use_legacy_requests(self)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.config = catalog_config(); self.catalog = ExecutionCatalog({'project-main': self.config})
        self.store = ManagementStore(Path(self.tmp.name) / 'state', execution_catalog=self.catalog, clock=lambda: 1000.0)
        self.addCleanup(self.store.close)
        self.project = self.store.create_project({'name': '프로젝트', 'goal': '목표만 입력'})
        self.registration = {'base_version': 0, 'idempotency_key': 'register-v1',
            'selection': {'catalog_id': 'project-main', 'catalog_digest': digest(self.config)}}

    def proposed(self, spec):
        message = self.store.post_message(self.project['id'], {'content': '등록한 범위에서 팀과 계획을 제안해주세요.'})
        request = self.store.get_pm_request(message['id'])
        config = self.store.execution_config_for(self.project['id'], self.store.execution_spec_reference(spec))
        self.store.save_pm_request({**request, 'state': 'running', 'configuration_digest': digest(config), 'mode': config.mode})
        return self.store.complete_pm_request(request['request_id'], plan_content(), evidence={'source': 'fixture'})

    def test_registration_is_append_only_and_does_not_execute(self):
        first = self.store.register_execution_spec(self.project['id'], self.registration)
        self.assertEqual(first, self.store.register_execution_spec(self.project['id'], self.registration))
        self.assertEqual(self.store.pm_requests(), [])
        self.assertEqual(self.store.run_records(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM session_jobs').fetchone()[0], 0)
        with self.assertRaises(ManagementError):
            self.store.register_execution_spec(self.project['id'], {**self.registration, 'selection': {**self.registration['selection'], 'budget': {'max_cost_usd': 2.0}}})
        with self.assertRaises(ManagementError):
            self.store.register_execution_spec(self.project['id'], {**self.registration, 'idempotency_key': 'other-intent'})
        second = self.store.register_execution_spec(self.project['id'], {**self.registration, 'base_version': 1, 'idempotency_key': 'register-v2'})
        self.assertEqual(second['version'], 2)
        self.assertEqual(first, self.store.get_execution_spec(self.project['id'], 1))
        for sql in ('UPDATE management_execution_specs SET document=document', 'DELETE FROM management_execution_specs'):
            with self.assertRaises(sqlite3.IntegrityError), self.store.db:
                self.store.db.execute(sql)
        self.assertEqual(len(self.store.execution_specs(self.project['id'])), 2)

    def test_claim_and_plan_hold_same_specification(self):
        spec = self.store.register_execution_spec(self.project['id'], self.registration)
        plan = self.proposed(spec)
        self.assertEqual(plan['execution_spec'], self.store.execution_spec_reference(spec))
        value = {'plan_digest': plan['digest'], 'base_harness_version': plan['base_harness_version'], 'idempotency_key': 'confirm-v1'}
        with self.assertRaises(ManagementError):
            self.store.confirm_plan(self.project['id'], plan['id'], value)
        value['execution_spec_digest'] = spec['digest']
        confirmed = self.store.confirm_plan(self.project['id'], plan['id'], value)
        self.assertEqual(confirmed['run']['execution_spec'], plan['execution_spec'])
        second = self.store.register_execution_spec(self.project['id'], {**self.registration, 'base_version': 1, 'idempotency_key': 'register-v2'})
        self.assertNotEqual(spec['digest'], second['digest'])
        self.assertEqual(self.store.confirm_plan(self.project['id'], plan['id'], value), confirmed)
        with self.assertRaises(ManagementError):
            self.store.save_run({**confirmed['run'], 'execution_spec': self.store.execution_spec_reference(second)})

    def test_new_spec_stales_old_plan_without_rewriting_original(self):
        first = self.store.register_execution_spec(self.project['id'], self.registration)
        old = self.proposed(first)
        self.store.register_execution_spec(self.project['id'], {**self.registration, 'base_version': 1, 'idempotency_key': 'register-v2'})
        self.assertEqual(self.store.get_plan(self.project['id'], old['id']), old)
        self.assertEqual(self.store.overview(self.project['id'])['plans'][-1]['status'], 'stale')
        with self.assertRaises(ManagementError) as caught:
            self.store.confirm_plan(self.project['id'], old['id'], {'plan_digest': old['digest'],
                'base_harness_version': old['base_harness_version'], 'execution_spec_digest': first['digest'],
                'idempotency_key': 'stale-confirm'})
        self.assertEqual(caught.exception.code, 'stale_plan')

    def test_cross_project_reference_and_catalog_drift_rejected(self):
        first = self.store.register_execution_spec(self.project['id'], self.registration)
        other = self.store.create_project({'name': '다른 프로젝트', 'goal': '분리된 목표'})
        self.store.register_execution_spec(other['id'], self.registration)
        with self.assertRaises(ManagementError):
            self.store.execution_config_for(other['id'], self.store.execution_spec_reference(first))
        self.store.execution_catalog = ExecutionCatalog({'project-main': self.config.model_copy(update={'base_sha': 'f' * 40})})
        with self.assertRaises(ManagementError):
            self.store.execution_config_for(self.project['id'], self.store.execution_spec_reference(first))

    def test_retired_catalog_claim_can_record_block_without_stopping_other_project(self):
        specification = self.store.register_execution_spec(self.project['id'], self.registration)
        message = self.store.post_message(self.project['id'], {'content': '등록 명세로 계획'})
        request = self.store.get_pm_request(message['id'])
        claimed = self.store.save_pm_request({**request, 'state': 'running',
            'configuration_digest': specification['configuration_digest'], 'mode': 'fixture'})
        self.store.execution_catalog = ExecutionCatalog({'other-catalog': self.config})
        blocked = self.store.save_pm_request({**claimed, 'state': 'blocked', 'reason': 'catalog retired'})
        self.assertEqual(blocked['state'], 'blocked')
        self.assertEqual(blocked['execution_spec'], claimed['execution_spec'])
        self.assertEqual(blocked['configuration_digest'], claimed['configuration_digest'])
        with self.assertRaises(ManagementError):
            self.store.save_pm_request({**blocked, 'configuration_digest': 'f' * 64})
        other = self.store.create_project({'name': '다른 프로젝트', 'goal': '계속 진행'})
        other_spec = self.store.register_execution_spec(other['id'], {**self.registration,
            'selection': {'catalog_id': 'other-catalog', 'catalog_digest': digest(self.config)}})
        other_message = self.store.post_message(other['id'], {'content': '다른 명세의 계획'})
        other_request = self.store.get_pm_request(other_message['id'])
        other_claimed = self.store.save_pm_request({**other_request, 'state': 'running',
            'configuration_digest': other_spec['configuration_digest'], 'mode': 'fixture'})
        self.assertEqual(other_claimed['state'], 'running')
        self.assertEqual(self.store.get_pm_request(message['id'])['state'], 'blocked')

    def test_pm_technical_proposal_is_not_registration_or_confirmation(self):
        message = self.store.post_message(self.project['id'], {'content': '프로젝트 범위를 제안해주세요.'})
        request = self.store.get_pm_request(message['id'])
        self.store.save_pm_request({**request, 'state': 'running', 'configuration_digest': digest(self.config), 'mode': 'fixture'})
        content = {**plan_content(), 'execution_spec_proposal': self.registration['selection']}
        plan = self.store.complete_pm_request(message['id'], content, evidence={'source': 'fixture'})
        self.assertNotIn('execution_spec', plan)
        self.assertEqual(self.store.execution_specs(self.project['id']), [])
        self.assertNotIn('execution_spec', self.store._project(self.project['id']))
        with self.assertRaises(ManagementError) as caught:
            self.store.confirm_plan(self.project['id'], plan['id'], {'plan_digest': plan['digest'],
                'base_harness_version': plan['base_harness_version'], 'idempotency_key': 'proposal-is-not-execution'})
        self.assertEqual(caught.exception.code, 'execution_spec_required')
        self.assertEqual(self.store.run_records(), [])

    def test_role_pool_names_and_claim_scope_are_checked(self):
        body = copy.deepcopy(self.registration)
        body['selection']['role_candidates'] = {'missing': ['dev']}
        spec = self.store.register_execution_spec(self.project['id'], body)
        with self.assertRaises(ManagementError) as caught:
            self.proposed(spec)
        self.assertEqual(caught.exception.code, 'execution_spec_mismatch')
        request = self.store.pm_requests()[-1]
        with self.assertRaises(ManagementError):
            self.store.save_pm_request({**request, 'configuration_digest': 'f' * 64})
        with self.assertRaises(ManagementError):
            self.store.save_pm_request({**request, 'execution_spec': None})


class ExecutionCatalogWiringTests(unittest.TestCase):
    def test_manage_serve_and_worker_receive_explicit_local_catalog(self):
        from ai_company import cli, service_worker
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = catalog_config()
            config_path = root / 'automation.json'
            catalog_path = root / 'catalog.json'
            config_path.write_text(config.model_dump_json())
            catalog_path.write_text(json.dumps({'project-main': config.model_dump(mode='json')}))
            state = root / 'state'
            store = ManagementStore(state); store.close()
            with patch('sys.argv', ['ai-company', 'manage', 'serve', '--state-dir', str(state),
                    '--password-login', '--execution-catalog', str(catalog_path)]), \
                    patch('ai_company.management_server.serve') as server:
                self.assertEqual(cli.main(), 0)
            passed = server.call_args.kwargs['execution_catalog']
            self.assertEqual(passed.public_entries()[0]['catalog_digest'], digest(config))
            observed = []
            def inspect_worker(*args, **kwargs):
                worker = args[3].__self__
                observed.append(worker.execution_catalog.public_entries()[0]['catalog_digest'])
                self.assertEqual(worker.store.pm_requests(), [])
                self.assertEqual(worker.store.db.execute('SELECT COUNT(*) FROM session_jobs').fetchone()[0], 0)
            with patch('ai_company.service_worker.serve', side_effect=inspect_worker), patch('signal.signal'):
                service_worker.main(['automation', '--state-dir', str(state), '--config', str(config_path),
                                     '--execution-catalog', str(catalog_path)])
            self.assertEqual(observed, [digest(config)])
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                service_worker.main(['translation', '--state-dir', str(state), '--config', str(config_path),
                                     '--execution-catalog', str(catalog_path)])


if __name__ == '__main__':
    unittest.main()
