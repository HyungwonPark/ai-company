"""Real prompt boundaries with private Git/SQLite and fixture-only executors."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from test_dispatcher import FlowFixture, Crash
from ai_company.adapters.session_cli import SessionOutcome
from ai_company.automation import Automation
from ai_company.automation_contracts import AutomationCI, AutomationConfig
from ai_company.contracts import digest
from ai_company.execution_specs import ExecutionCatalog, ExecutionSpecSelection
from ai_company.harness import guidance
from ai_company.runtime import ExecutionBlocked


class GuidanceTests(FlowFixture):
    def guided(self, **plan):
        return self.spec.model_copy(update={'plan': {'project_id': 'project-a', 'plan_id': 'plan-a',
            'plan_digest': 'a' * 64, 'run_id': 'run-a', 'role': {'key': 'implementation'},
            **plan, 'guidance': guidance.reference().model_dump(mode='json')}})

    def records(self):
        return [json.loads(row[0]) for row in self.dispatcher.db.execute(
            'SELECT document FROM guidance_deliveries ORDER BY rowid')]

    def config(self, **changes):
        return AutomationConfig(repository=self.task.repository, source_clone=str(self.repo),
            base_sha=self.task.base_sha, base_branch='main', allowed_paths=self.task.allowed_paths,
            checks=self.spec.checks, agents=self.spec.agents, policy=self.spec.policy,
            ci=AutomationCI(workflow_path='.github/workflows/evidence.yml', workflow_digest='b' * 64,
                            required_checks=('unit',)), mode='fixture', **changes)

    def test_legacy_config_and_spec_serialization_preserved(self):
        original = self.config().model_dump(mode='json')
        self.assertNotIn('guidance', original)
        explicit_null = AutomationConfig.model_validate({**original, 'guidance': None})
        self.assertEqual(original, explicit_null.model_dump(mode='json'))
        self.assertEqual(digest(original), digest(explicit_null))
        worker = Automation(self.root / 'automation', explicit_null)
        self.addCleanup(worker.close)
        flow = worker._spec(self.task, self.repo, 'full', {'project_id': 'project-a'})
        expected = {'project_id': 'project-a', 'automation_configuration': digest(original)}
        self.assertEqual(flow.plan, expected)
        self.submit()
        self.tick()
        self.assertEqual(self.records(), [])
        self.assertNotIn('guidance_receipts', self.state()['executions'][0])

    def test_bundle_loads_four_roles_and_pinned_sources(self):
        manifest = guidance.load(guidance.reference())
        self.assertEqual(len(manifest['selected_sources']), 6)
        for role in guidance.ROLES:
            document, content_hash = guidance.load(guidance.reference(), role)
            self.assertIn(f'역할 `{role}`', document)
            self.assertEqual(sha256(document.encode()).hexdigest(), content_hash)
            prompt, _ = guidance.augment('EXACT ORIGINAL PROVIDER CONTRACT', guidance.reference(), role)
            self.assertTrue(prompt.endswith('EXACT ORIGINAL PROVIDER CONTRACT'))

    def test_manifest_and_document_tampering_and_symlink_fail_closed(self):
        temp = self.root / 'bundle'
        shutil.copytree(guidance.BUNDLE_ROOT, temp)
        with patch.object(guidance, 'BUNDLE_ROOT', temp):
            manifest_path = temp / 'sources/manifest.json'
            original = manifest_path.read_bytes()
            document_path = temp / 'developer.md'
            document = document_path.read_bytes()
            document_path.write_bytes(document + b'changed')
            with self.assertRaises(ExecutionBlocked): guidance.load(guidance.reference())
            manifest = json.loads(original)
            manifest['roles']['developer']['sha256'] = sha256(document_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ExecutionBlocked): guidance.load(guidance.reference())
            manifest_path.write_bytes(original)
            outside = self.root / 'outside.md'; outside.write_bytes(document)
            document_path.unlink(); document_path.symlink_to(outside)
            with self.assertRaises(ExecutionBlocked): guidance.load(guidance.reference())

    def test_unapproved_reference_rejected_before_queue_submission(self):
        plan = self.guided().plan
        plan['guidance']['source_commit'] = 'b' * 40
        with self.assertRaises(ExecutionBlocked): self.submit(self.spec.model_copy(update={'plan': plan}))
        self.assertEqual(self.dispatcher.tasks(), [])
        self.assertEqual(self.records(), [])

    def test_trusted_catalog_freezes_guidance_and_http_cannot_choose_it(self):
        config = self.config(guidance=guidance.reference())
        catalog = ExecutionCatalog({'pilot': config})
        selection = {'catalog_id': 'pilot', 'catalog_digest': digest(config),
                     'allowed_paths': ['src/code.txt'], 'budget': {'max_executions': 2}}
        resolved = catalog.resolve(selection)
        self.assertEqual(resolved.guidance, guidance.reference())
        self.assertEqual(resolved.policy.max_executions, 2)
        self.assertEqual(resolved.policy.candidates, config.policy.candidates)
        self.assertEqual(resolved.agents, config.agents)
        self.assertEqual(resolved.checks, config.checks)
        self.assertEqual(catalog.public_entries()[0]['guidance'], guidance.reference().model_dump(mode='json'))
        with self.assertRaises(ValueError):
            ExecutionSpecSelection.model_validate({**selection, 'guidance': guidance.reference().model_dump(mode='json')})
        with self.assertRaises(ExecutionBlocked):
            ExecutionCatalog({'bad': config.model_copy(update={'guidance': guidance.reference().model_copy(
                update={'content_hash': '0' * 64})})})
        worker = Automation(self.root / 'automation', config)
        self.addCleanup(worker.close)
        flow = worker._spec(self.task, self.repo, 'full', {'project_id': 'project-a', 'run_id': 'run-a'})
        self.assertEqual(flow.plan['guidance'], guidance.reference().model_dump(mode='json'))
        self.assertEqual(flow.task, self.task)
        self.assertEqual(flow.policy, config.policy)
        config_data = config.model_dump(mode='json'); config_data['guidance']['content_hash'] = '0' * 64
        self.assertEqual(flow.plan['guidance']['content_hash'], guidance.BUNDLE_HASH)

    def test_real_executor_boundary_receipts_bind_role_prompt_and_run(self):
        captured = []
        def execute(agent, state, provider, worktree, prompt, session_id, **kwargs):
            captured.append((state['stage'], provider, prompt))
            phases = [r['phase'] for r in self.records() if r['execution_id'] == state['active']['execution_id']]
            self.assertEqual(phases, ['prepared', 'executor_invocation_started'])
            return self.execute(agent, state, provider, worktree, prompt, session_id, **kwargs)
        self.dispatcher.executor = execute
        self.submit(self.guided())
        state = self.finish()
        self.assertEqual(state['status'], 'DEMO_READY')
        returned = [r for r in self.records() if r['phase'] == 'executor_returned']
        self.assertEqual(len(returned), 3)
        self.assertEqual([r['role'] for r in returned], ['developer', 'reviewer', 'final'])
        for record, (role, provider, prompt) in zip(returned, captured):
            self.assertEqual(record['prompt_hash'], sha256(prompt.encode()).hexdigest())
            self.assertEqual(record['document_hash'], guidance.load(guidance.reference(), role)[1])
            self.assertEqual(record['provider'], provider)
            self.assertEqual(record['project_id'], 'project-a')
            self.assertEqual(record['plan_id'], 'plan-a')
            self.assertEqual(record['plan_digest'], 'a' * 64)
            self.assertEqual(record['run_id'], 'run-a')
            self.assertEqual(record['role_key'], 'implementation')
            self.assertEqual(record['attempt'], 1)
            self.assertEqual(record['model_compliance'], 'unverified')
            self.assertFalse(record['model_execution_verified'])
            job = self.dispatcher.queue.get(record['job_id'])
            self.assertEqual(job['result']['guidance_receipt']['id'], record['id'])
            self.assertIn('Original provider stage contract', prompt)
            if provider == 'claude':
                self.assertIn('Bash and command execution are', prompt)
        self.assertEqual(state['usage']['executions'], 3)
        self.restart()
        self.assertEqual(len(self.records()), 9)

    def test_receipt_identity_cannot_be_reused_with_another_prompt(self):
        self.submit(self.guided()); self.tick()
        record = self.records()[0]
        binding = {key: value for key, value in record.items() if key not in ('id', 'phase', 'at')}
        with self.assertRaises(ExecutionBlocked):
            self.dispatcher._guidance_event({**binding, 'prompt_hash': '0' * 64}, 'prepared')
        self.assertEqual(self.records()[0], record)

    def test_preflight_failure_never_claims_process_or_model_started(self):
        def reject(*args, **kwargs): raise ExecutionBlocked('fixture preflight failure')
        self.dispatcher.executor = reject
        self.submit(self.guided()); self.tick()
        self.assertEqual([r['phase'] for r in self.records()],
                         ['prepared', 'executor_invocation_started', 'executor_failed'])
        self.assertTrue(all(not r['model_execution_verified'] for r in self.records()))
        self.assertEqual(self.calls, [])

    def test_worker_crash_preserves_prepared_receipt_without_return_claim(self):
        self.script = [Crash()]
        self.submit(self.guided())
        with self.assertRaises(Crash): self.tick()
        self.restart()
        self.assertEqual([r['phase'] for r in self.records()], ['prepared', 'executor_invocation_started'])
        self.assertFalse(any(r['model_execution_verified'] for r in self.records()))

    def test_tamper_after_submission_stops_before_executor(self):
        self.submit(self.guided())
        temp = self.root / 'changed-bundle'; shutil.copytree(guidance.BUNDLE_ROOT, temp)
        (temp / 'pm.md').write_text('tampered')
        with patch.object(guidance, 'BUNDLE_ROOT', temp): self.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.records(), [])

    def test_quota_failover_preserves_attempt_receipts_and_handoff(self):
        self.script = [self.quota()]
        self.submit(self.guided()); state = self.tick()
        old = state['executions'][0]
        self.assertEqual(old['guidance_receipts'][-1]['outcome_category'], 'quota')
        self.assertEqual(state['active']['handoff']['previous_guidance_deliveries'], old['guidance_receipts'])
        self.assertEqual(state['active']['handoff']['guidance']['content_hash'], guidance.BUNDLE_HASH)
        self.tick()
        returned = [r for r in self.records() if r['phase'] == 'executor_returned']
        self.assertEqual(len(returned), 2)
        self.assertNotEqual(returned[0]['execution_id'], returned[1]['execution_id'])
        self.assertEqual([r['provider'] for r in returned], ['codex', 'claude'])
        self.assertEqual(self.state()['usage']['executions'], 2)

    def test_saved_retry_result_recovers_receipt_before_same_session_resume(self):
        self.submit(self.guided())
        self.script = [SessionOutcome('transient_network', reset_at=5000)]
        with patch.object(self.dispatcher, '_handle_job', side_effect=Crash()):
            with self.assertRaises(Crash): self.tick()
        returned = self.records()[-1]
        self.assertEqual(returned['phase'], 'executor_returned')
        self.assertEqual(self.state()['usage']['executions'], 0)
        self.restart(); state = self.tick()
        self.assertEqual(state['usage']['executions'], 1)
        self.assertEqual(state['active']['guidance_receipts'][-1], returned)
        self.assertEqual(len(self.calls), 1)
        self.restart(); self.tick()
        self.assertEqual(self.state()['usage']['executions'], 1)
        self.now = state['resume_at']; self.tick()
        returned_records = [r for r in self.records() if r['phase'] == 'executor_returned']
        self.assertEqual([r['attempt'] for r in returned_records], [1, 2])
        self.assertEqual(returned_records[0]['execution_id'], returned_records[1]['execution_id'])
        self.assertEqual(self.calls[-1]['session_id'], 'session-1')
        self.assertEqual(self.state()['usage']['executions'], 2)

    def test_automation_binds_confirmed_run_to_both_roles_and_final_review(self):
        import test_automation
        fixture = test_automation.CoordinatorTests(methodName='runTest')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.worker.close()
        fixture.config = fixture.config.model_copy(update={'guidance': guidance.reference()})
        fixture.worker = fixture.open()
        # This is a private fixture master's confirmation, not a production/UI event.
        run = fixture.confirm(); result = fixture.finish()
        self.assertEqual(result['state'], 'fixture_complete')
        rows = fixture.worker.dispatcher.db.execute(
            "SELECT document FROM guidance_deliveries WHERE phase='executor_returned'").fetchall()
        records = [json.loads(row[0]) for row in rows]
        self.assertEqual(len(records), 5)
        pm = next(r for r in records if r['role'] == 'pm')
        self.assertIsNone(pm['run_id']); self.assertIsNotNone(pm['pm_request_id'])
        self.assertEqual({r['role_key'] for r in records if r['role'] == 'developer'}, {'impl', 'test'})
        for record in records:
            self.assertEqual(record['project_id'], fixture.project['id'])
            if record['role'] == 'pm': continue
            self.assertEqual(record['run_id'], run['id'])
            self.assertEqual(record['plan_id'], run['plan_id'])
            self.assertEqual(record['plan_digest'], run['plan_digest'])
        for task in fixture.worker.dispatcher.tasks():
            self.assertEqual(task['specification']['plan']['guidance'], guidance.reference().model_dump(mode='json'))

    def test_pm_guidance_is_delivered_before_any_confirmed_plan(self):
        roles = [{'key': name, 'name': name, 'responsibility': '구현', 'goal': '구현',
                  'acceptance': ['검사'], 'allowed_paths': [f'src/{name}.py'], 'depends_on': []}
                 for name in ['impl', 'test']]
        def pm(agent, state, provider, worktree, prompt, session_id, **kwargs):
            self.assertIn('역할 `pm`', prompt)
            outcome = self.execute(agent, state, provider, worktree, prompt, session_id, **kwargs)
            outcome.result['structured_output']['plan'] = {'summary': '독립 작업', 'roles': roles,
                                                           'completion_criteria': ['검사']}
            return outcome
        self.dispatcher.executor = pm
        spec = self.guided(pm_request_id='request-a').model_copy(update={'approved_plan': False, 'execution_scope': 'planning'})
        plan = dict(spec.plan); plan.pop('run_id'); plan.pop('plan_id'); plan.pop('plan_digest')
        self.submit(spec.model_copy(update={'plan': plan})); state = self.tick()
        self.assertEqual(state['status'], 'PLAN_READY')
        record = self.records()[-1]
        self.assertEqual(record['role'], 'pm')
        self.assertEqual(record['pm_request_id'], 'request-a')
        self.assertIsNone(record['run_id'])
        self.assertIsNone(record['plan_digest'])
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
