from copy import deepcopy
from unittest.mock import patch
from uuid import UUID

from ai_company.adapters.claude_observation import persisted_attempt
from ai_company.contracts import digest
from ai_company.flow_contracts import FlowSpec
from ai_company.harness.prompts import stage_prompt
from test_claude_observation import observed
from test_dispatcher import FlowFixture, Crash


class ClaudeDispatcherTests(FlowFixture):
    def setUp(self):
        super().setUp()
        self.mutation = None
        self.spec = self.spec.model_copy(update={
            'task': self.task.model_copy(update={'allowed_paths': ('src/code.txt',)}),
            'execution_scope':'contribution',
            'agents': tuple(a.model_copy(update={'verified_model':None, 'verified_effort':None,
                'verified_ultracode':None, 'verification_evidence':None}) for a in self.spec.agents),
            'policy':self.spec.policy.model_copy(update={'configuration_evidence':'cli_configuration_v2'})})

    def execute(self, agent, state, provider, worktree, prompt, session_id, **kwargs):
        with patch.object(self, 'commit'):
            outcome = super().execute(agent, state, provider, worktree, prompt, session_id, **kwargs)
        outcome.session_id = session_id or str(UUID(int=len(self.calls)))
        if outcome.category != 'success':
            return outcome
        r = outcome.result
        r['structured_output']['commit_requested'] = True
        r['observed_models'] = []; r['observed_efforts'] = []; r['observed_ultracode'] = []
        if provider == 'claude':
            job = self.dispatcher.queue.get(state['active']['job_id'])
            binding, start = persisted_attempt(self.dispatcher.db, job)
            r['configuration_evidence'] = observed(binding, outcome.session_id, start, self.now, ('src/code.txt',))
        else:
            r['configuration_evidence'] = {'source':'codex_rollout', 'scope':'cli_turn_configuration', 'status':'observed',
                'cli_version':'0.154.0', 'session_id':outcome.session_id, 'backend_model_verified':False,
                'contexts':[{'turn_id':'turn-1', 'model':agent.model, 'reasoning_effort':agent.reasoning_effort, 'recorded_at':self.now}]}
        if self.mutation: self.mutation(r)
        return outcome

    def only_claude(self):
        return self.spec.model_copy(update={'policy':self.spec.policy.model_copy(update={
            'candidates':{**self.spec.policy.candidates, 'developer':('claude',)}})})

    def test_new_policy_commits_claude_file_contribution_and_preserves_original_report(self):
        self.submit(self.only_claude()); result = self.tick()
        self.assertEqual(result['status'], 'CONTRIBUTION_READY')
        job = self.dispatcher.queue.get(result['executions'][-1]['job_id'])
        self.assertEqual(job['result']['structured_output']['candidate_sha'], self.task.base_sha)
        self.assertNotEqual(job['result']['runner_commit']['candidate_sha'], self.task.base_sha)
        self.assertEqual(result['authors'], [{'provider':'claude','session_id':str(UUID(int=1))}])
        self.assertEqual(job['result']['observed_efforts'], [])
        from ai_company.collaboration import observed_configuration
        ui = observed_configuration(self.dispatcher.db, job)
        self.assertEqual((ui['model'], ui['reasoning_effort'], ui['ultracode_enabled']), ('claude-opus-5','xhigh',True))
        self.assertNotIn('worktree', ui['evidence'])
        invalid_job = deepcopy(job)
        invalid_job['result']['configuration_evidence']['binding']['attempt'] = 99
        self.assertEqual(observed_configuration(self.dispatcher.db, invalid_job)['status'], 'unavailable')
        self.restart(); self.tick(); self.assertEqual(len(self.calls), 1)

    def test_legacy_mode_and_full_developer_do_not_gain_claude_eligibility(self):
        for changes in ({'policy':self.spec.policy.model_copy(update={'configuration_evidence':'cli_configuration'})},
                        {'execution_scope':'full'}, {'task':self.task}):
            spec = self.spec.model_copy(update=changes)
            profiles = self.dispatcher._candidates(spec, {'stage':'developer','authors':[]})[0]
            self.assertNotIn('claude', [p.agent_id for p in profiles])
        self.assertNotEqual(digest(self.spec.policy), digest(self.spec.policy.model_copy(update={'configuration_evidence':'cli_configuration'})))

    def test_codex_keeps_existing_turn_validation_in_new_policy(self):
        self.submit(); self.assertEqual(self.tick()['status'], 'CONTRIBUTION_READY')
        self.assertEqual(self.calls[0]['agent'], 'astra')

    def test_quota_crash_accounts_once_then_hands_off_to_new_policy_claude(self):
        self.script = [self.quota()]; self.submit()
        with patch.object(self.dispatcher, '_handle_job', side_effect=Crash), self.assertRaises(Crash): self.tick()
        self.restart(); self.tick(); result = self.tick()
        self.assertEqual(result['status'], 'CONTRIBUTION_READY')
        self.assertEqual([c['agent'] for c in self.calls], ['astra','claude'])
        self.assertEqual(result['usage']['executions'], 2)
        self.assertEqual(result['usage']['cost_usd'], .2)
        self.restart(); self.tick(); self.assertEqual(self.state()['usage']['executions'], 2)

    def test_retry_requires_same_session_but_fresh_attempt_binding(self):
        self.script = [self.quota()]; self.submit(self.only_claude()); self.tick()
        first = deepcopy(self.state()['active'])
        self.now = self.state()['resume_at'] + 1; self.restart(); result = self.tick()
        self.assertEqual(result['status'], 'CONTRIBUTION_READY')
        self.assertEqual(self.calls[1]['session_id'], str(UUID(int=1)))
        job = self.dispatcher.queue.get(result['executions'][-1]['job_id'])
        binding = job['result']['configuration_evidence']['binding']
        self.assertEqual(binding['attempt'], 2)
        self.assertEqual(binding['previous_session_id'], str(UUID(int=1)))
        self.assertEqual(binding['execution_id'], first['execution_id'])

    def test_claimed_model_text_cannot_replace_attempt_evidence(self):
        self.submit(self.only_claude())
        self.mutation = lambda r: r['configuration_evidence']['binding'].update(attempt=999)
        result = self.tick()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('observation identity', result['reason'])

    def test_file_instructions_use_actual_tools_and_runner_commit(self):
        prompt = stage_prompt('checkpoint', provider='claude', role='developer', contribution=True, file_tools=True)
        self.assertIn('mcp__company_files__write_file', prompt)
        self.assertNotIn('edit and run authorized checks', prompt)
        with self.assertRaises(ValueError): stage_prompt('checkpoint', provider='claude', role='developer', file_tools=True)
