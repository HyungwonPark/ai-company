import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_company.shared_calls import SharedCallError, SharedCallLedger
from ai_company.contracts import digest


PATH = Path(__file__).resolve().parents[1] / 'scripts/evaluate_pm_behavior.py'
SPEC = importlib.util.spec_from_file_location('evaluate_pm_behavior', PATH)
EVALUATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATION)
CASES = Path(__file__).resolve().parents[1] / 'docs/work-reviews/pm-behavior-cases-2026-09-26.json'


def fixture_budget(_root, _case, config, _shared):
    if not hasattr(config.policy, 'max_runtime_seconds'):
        config.policy.max_runtime_seconds = 1800
    return config, {'effective_configuration_sha256': 'fixture', 'caps': {
        'max_executions': 24, 'max_runtime_seconds': 1800, 'max_repairs': 2}}


class PMEValRunnerTests(unittest.TestCase):
    def test_technical_revision_reaches_second_review_in_real_automation(self):
        from tests import test_automation as fixture

        harness = fixture.CoordinatorTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        original = harness.execute
        reviews = {'count': 0}

        def revise_once(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            scope = state['specification']['execution_scope']
            report = outcome.result['structured_output']
            if scope == 'plan_review':
                reviews['count'] += 1
                if reviews['count'] == 1:
                    report.update(verdict='REVISE', revision_route='technical', findings=[{
                        'finding_id': 'F-VERIFY', 'detail': '검증 방법을 구체화하세요.',
                        'evidence': 'requirements_review.requirements[0].verification'}])
            elif scope == 'planning' and state['specification']['plan'].get('source_plan_id'):
                report['plan']['requirements_review']['requirements'][0]['verification'] = '빈 값과 모든 분류를 검사합니다.'
            return outcome

        harness.execute = revise_once
        root = harness.root / 'evaluation'
        root.mkdir()
        (root / 'cases.json').write_text(json.dumps({'common_project_goal': '두 결과를 검증합니다.'}))
        def factory(case_root, _config, **_kwargs):
            from ai_company.automation import Automation
            from ai_company.dispatcher import Dispatcher
            return Automation(case_root, harness.config, clock=lambda: harness.now,
                dispatcher_factory=lambda path, **kwargs: Dispatcher(path, verifier=harness,
                    executor=harness.execute, **kwargs))

        with patch.object(EVALUATION, 'Automation', side_effect=factory), \
             patch.object(EVALUATION, 'case_config', return_value=harness.config), \
             patch.object(EVALUATION, 'global_usage', return_value=(0, 0)), \
             patch.object(EVALUATION, 'execution_usage', return_value=(0, 0)), \
             patch.object(EVALUATION.time, 'sleep'):
            result = EVALUATION.run_case(root, {'id': 'E4', 'initial_message': '계획을 검토합니다.',
                'injected_review_material': []}, harness.config, root / 'unused.db', float('inf'))
        self.assertEqual(result['state'], 'plan_ready')
        self.assertEqual(result['plan_states'], ['superseded', 'proposed'])
        self.assertEqual(reviews['count'], 2)
        self.assertEqual(result['development_runs'], 0)

    def test_idle_quota_wait_does_not_consume_runtime_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
            store = Mock()
            store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
            store.overview.side_effect = [
                {'pm_requests': [{'request_id': 'request', 'state': 'pending'}], 'plans': [], 'runs': []},
                {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                 'plans': [{'request_id': 'request', 'status': 'proposed'}], 'runs': []}]
            worker = Mock(store=store)
            worker.dispatcher.tasks.return_value = []
            config = SimpleNamespace(policy=SimpleNamespace(retry=SimpleNamespace(execution_timeout_seconds=30)),
                                     poll_seconds=1)
            with patch.object(EVALUATION, 'Automation', return_value=worker), \
                 patch.object(EVALUATION, 'case_budget_config', side_effect=fixture_budget), \
                 patch.object(EVALUATION, 'global_usage', return_value=(0, 0)), \
                 patch.object(EVALUATION, 'execution_usage', return_value=(0, 0)), \
                 patch.object(EVALUATION.time, 'sleep'):
                result = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'}, config,
                                             root / 'unused.db', time.time() - 7200)
            self.assertEqual(result['state'], 'plan_ready')
            worker.run_once.assert_called_once()

    def test_only_current_plan_controls_revision_and_decision_state(self):
        request = {'request_id': 'new', 'state': 'completed'}
        old = {'request_id': 'old', 'status': 'needs_revision', 'revision_action': 'automatic'}
        def progress(plan):
            return EVALUATION.case_progress({'pm_requests': [request], 'plans': [old, plan]})
        self.assertEqual(progress({'request_id': 'new', 'status': 'proposed'}), 'plan_ready')
        self.assertEqual(progress({'request_id': 'new', 'status': 'needs_revision',
                                   'revision_action': 'automatic'}), 'running')
        self.assertEqual(progress({'request_id': 'new', 'status': 'needs_revision',
                                   'revision_action': 'master_decision'}), 'master_decision_wait')
        self.assertEqual(progress({'request_id': 'new', 'status': 'needs_revision',
                                   'revision_action': 'limit_reached'}), 'revision_limit')
        self.assertEqual(EVALUATION.case_progress({'pm_requests': [{'request_id': 'new',
            'state': 'answer_needed'}], 'plans': [old]}), 'answer_needed')
        self.assertEqual(EVALUATION.case_progress({'pm_requests': [{'request_id': 'new',
            'state': 'blocked'}], 'plans': [old]}), 'environment_problem')

    def test_scheduled_wait_is_reported_and_expired_wait_can_continue(self):
        overview = {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                    'plans': [{'request_id': 'request', 'status': 'reviewing',
                               'digest': 'a' * 64}], 'runs': []}
        worker = Mock()
        worker.dispatcher.tasks.return_value = [{'task_id': 'plan-review-' + 'a' * 48,
            'status': 'WAITING_QUOTA', 'resume_at': time.time() + 7200, 'reason': 'quota'}]
        self.assertEqual(EVALUATION.case_wait(worker, overview)['state'], 'provider_wait')
        worker.dispatcher.tasks.return_value[0]['resume_at'] = time.time() - 1
        self.assertIsNone(EVALUATION.case_wait(worker, overview))

    def test_settled_runtime_excludes_two_hour_wait_and_unsettled_call_is_reserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = {'at': 100.0}
            ledger_path = root / 'shared.db'
            ledger = SharedCallLedger.initialize(ledger_path, [
                ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ], clock=lambda: clock['at'])
            owner = str(root / 'E1')
            ledger.reserve('first', owner, 'codex', 'fixture', 'group')
            ledger.started('first', owner, {'kind': 'test', 'pid': 1})
            clock['at'] = 100 + 7200
            ledger.settle('first', owner, 'event-first', {'category': 'success',
                'duration_seconds': 40, 'total_cost_usd': 0}, terminated=True)
            self.assertEqual(EVALUATION.execution_usage(root, ledger_path, 60), (40, 0))
            self.assertFalse(ledger.settle('first', owner, 'event-first', {'category': 'success',
                'duration_seconds': 40, 'total_cost_usd': 0}, terminated=True))
            ledger.reserve('second', owner, 'codex', 'fixture', 'group')
            ledger.started('second', owner, {'kind': 'test', 'pid': 2})
            self.assertEqual(EVALUATION.execution_usage(root, ledger_path, 60), (100, 1))
            clock['at'] += 7200
            self.assertEqual(EVALUATION.execution_usage(root, ledger_path, 60), (100, 1))
            ledger.settle('second', owner, 'event-second', {'category': 'success',
                'total_cost_usd': 0}, terminated=True)
            self.assertEqual(EVALUATION.execution_usage(root, ledger_path, 60), (100, 0))
            ledger.close()

    def test_trial_root_owned_reservation_counts_for_calls_and_runtime_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger_path = root / 'shared.db'
            ledger = SharedCallLedger.initialize(ledger_path, [
                ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
            ledger.reserve('root-call', str(root), 'codex', 'fixture', 'group')
            ledger.started('root-call', str(root), {'kind': 'test', 'pid': 1})
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (1, 0))
            self.assertEqual(EVALUATION.execution_usage(root, ledger_path, 60), (60, 1))
            ledger.close()

    def test_unreconciled_start_does_not_launch_another_call_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
            ledger_path = root / 'shared.db'
            ledger = SharedCallLedger.initialize(ledger_path, [
                ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ])
            owner = str(root / 'E1')
            ledger.reserve('started', owner, 'codex', 'fixture', 'group')
            ledger.started('started', owner, {'kind': 'test', 'pid': 1})
            pending = {'pm_requests': [{'request_id': 'request', 'state': 'pending'}],
                       'plans': [], 'runs': []}
            proposed = {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                        'plans': [{'request_id': 'request', 'status': 'proposed'}], 'runs': []}
            store = Mock()
            store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
            store.overview.side_effect = [pending, pending, proposed]
            worker = Mock(store=store)
            worker.dispatcher.tasks.return_value = []
            config = SimpleNamespace(policy=SimpleNamespace(retry=SimpleNamespace(execution_timeout_seconds=60)),
                                     poll_seconds=1)
            with patch.object(EVALUATION, 'Automation', return_value=worker), \
                 patch.object(EVALUATION, 'case_budget_config', side_effect=fixture_budget), \
                 patch.object(EVALUATION, 'global_usage', return_value=(1, 0)), \
                 patch.object(EVALUATION.time, 'sleep'):
                first = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'}, config,
                                            ledger_path, None)
                self.assertEqual(first['state'], 'reconciliation_wait')
                worker.run_once.assert_not_called()
                ledger.settle('started', owner, 'event', {'category': 'success',
                    'duration_seconds': 20, 'total_cost_usd': 0}, terminated=True)
                second = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'}, config,
                                             ledger_path, None)
            self.assertEqual(second['state'], 'plan_ready')
            worker.run_once.assert_called_once()
            ledger.close()

    def test_runtime_limit_and_repair_cap_only_block_new_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
            store = Mock()
            store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
            worker = Mock(store=store)
            worker.dispatcher.tasks.return_value = []
            config = SimpleNamespace(policy=SimpleNamespace(retry=SimpleNamespace(execution_timeout_seconds=60)),
                                     poll_seconds=1)
            request = {'request_id': 'request', 'state': 'completed'}
            reviewing = {'request_id': 'request', 'status': 'reviewing', 'digest': 'a' * 64}
            proposed = {'request_id': 'request', 'status': 'proposed'}
            automatic = {'request_id': 'request', 'status': 'needs_revision',
                         'revision_action': 'automatic', 'id': 'plan', 'auto_revision_attempt': 0}
            with patch.object(EVALUATION, 'Automation', return_value=worker), \
                 patch.object(EVALUATION, 'case_budget_config', side_effect=fixture_budget), \
                 patch.object(EVALUATION, 'global_usage', return_value=(2, 2)), \
                 patch.object(EVALUATION.time, 'sleep'):
                store.overview.return_value = {'pm_requests': [request], 'plans': [automatic], 'runs': []}
                with patch.object(EVALUATION, 'execution_usage', return_value=(10, 0)):
                    limited = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'},
                                                   config, root / 'unused.db')
                self.assertEqual(limited['state'], 'budget_wait')
                worker.run_once.assert_not_called()

                store.overview.side_effect = [
                    {'pm_requests': [request], 'plans': [reviewing], 'runs': []},
                    {'pm_requests': [request], 'plans': [reviewing, proposed], 'runs': []}]
                with patch.object(EVALUATION, 'execution_usage', return_value=(30, 0)):
                    reviewed = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'},
                                                    config, root / 'unused.db')
                self.assertEqual(reviewed['state'], 'plan_ready')
                worker.run_once.assert_called_once()

                worker.run_once.reset_mock()
                store.overview.side_effect = None
                store.overview.return_value = {'pm_requests': [request], 'plans': [reviewing], 'runs': []}
                with patch.object(EVALUATION, 'execution_usage', return_value=(1800, 0)):
                    exhausted = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'},
                                                     config, root / 'unused.db')
                self.assertEqual(exhausted['state'], 'budget_wait')
                worker.run_once.assert_not_called()

    def test_planning_uses_actual_shorter_timeout_before_global_budget_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
            store = Mock()
            store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
            store.overview.side_effect = [
                {'pm_requests': [{'request_id': 'request', 'state': 'pending'}], 'plans': [], 'runs': []},
                {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                 'plans': [{'request_id': 'request', 'status': 'proposed'}], 'runs': []}]
            worker = Mock(store=store)
            worker.dispatcher.tasks.return_value = []
            config = SimpleNamespace(pm_timeout_seconds=30, policy=SimpleNamespace(
                retry=SimpleNamespace(execution_timeout_seconds=600)), poll_seconds=1)
            with patch.object(EVALUATION, 'Automation', return_value=worker), \
                 patch.object(EVALUATION, 'case_budget_config', side_effect=fixture_budget), \
                 patch.object(EVALUATION, 'global_usage', return_value=(2, 0)), \
                 patch.object(EVALUATION, 'execution_usage', return_value=(1750, 0)), \
                 patch.object(EVALUATION.time, 'sleep'):
                result = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'},
                                             config, root / 'unused.db')
            self.assertEqual(result['state'], 'plan_ready')
            worker.run_once.assert_called_once()

    def test_submitted_repair_is_not_charged_until_invocation_starts(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger_path = root / 'shared.db'
            ledger = SharedCallLedger.initialize(ledger_path, [
                ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
            case_root = root / 'E1'
            path = case_root / 'sessions' / 'sessions.sqlite'
            path.parent.mkdir(parents=True)
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE session_jobs(job_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            task = {'task_id': 'pm-revise-fixture', 'usage': {'repairs': 0, 'executions': 0},
                    'active': {'job_id': 'job'}, 'executions': []}
            db.execute('INSERT INTO flow_tasks VALUES (?,?)', (task['task_id'], json.dumps(task)))
            db.execute('INSERT INTO session_jobs VALUES (?,?)', ('job', json.dumps({'job_id': 'job',
                'attempt_count': 0})))
            db.commit()
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (0, 0))
            db.execute('UPDATE session_jobs SET document=? WHERE job_id=?',
                       (json.dumps({'job_id': 'job', 'attempt_count': 1}), 'job'))
            db.commit()
            with self.assertRaisesRegex(SharedCallError, 'lacks a live shared fact'):
                EVALUATION.global_usage(root, ledger_path)
            db.execute('UPDATE session_jobs SET document=? WHERE job_id=?',
                       (json.dumps({'job_id': 'job', 'attempt_count': 0}), 'job'))
            db.commit()
            reservation_id = digest([str(case_root), 'job', 1])
            ledger.reserve(reservation_id, str(case_root), 'codex', 'fixture', 'group')
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (1, 0))
            ledger.started(reservation_id, str(case_root), {'kind': 'test', 'job_id': 'job'})
            db.execute('UPDATE session_jobs SET document=? WHERE job_id=?',
                       (json.dumps({'job_id': 'job', 'attempt_count': 1}), 'job'))
            db.commit()
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (1, 1))
            ledger.settle(reservation_id, str(case_root), 'repair-result', {'category': 'success',
                'duration_seconds': 20, 'total_cost_usd': 0}, terminated=True)
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (1, 1))
            db.close(); ledger.close()

    def test_derived_case_budget_is_pinned_and_product_executor_uses_its_remaining_time(self):
        from ai_company.automation import Automation
        from ai_company.dispatcher import Dispatcher
        from tests import test_automation as fixture

        harness = fixture.CoordinatorTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        root = harness.root / 'evaluation'
        case_root = root / 'E1'
        case_root.mkdir(parents=True)
        ledger_path = harness.root / 'shared.db'
        ledger = SharedCallLedger.initialize(ledger_path, [
            ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
        prior = str(root / 'E0')
        ledger.reserve('prior', prior, 'codex', 'fixture', 'group')
        ledger.started('prior', prior, {'kind': 'test', 'pid': 1})
        ledger.settle('prior', prior, 'prior-result', {'category': 'success',
            'duration_seconds': 1700, 'total_cost_usd': 0}, terminated=True)
        effective, budget = EVALUATION.case_budget_config(root, 'E1', harness.config, ledger_path)
        self.assertEqual(budget['caps']['max_runtime_seconds'], 40)
        self.assertEqual(budget['caps']['max_executions'], 23)
        self.assertEqual(budget['caps']['max_repairs'], 2)
        self.assertEqual(effective.policy.max_runtime_seconds, 40)
        self.assertEqual(EVALUATION.case_budget_config(root, 'E1', harness.config, ledger_path)[1], budget)

        timeouts = []
        original = harness.execute
        def observed(agent, state, provider, worktree, prompt, session_id, **kwargs):
            timeouts.append((state['specification']['execution_scope'], kwargs['timeout_seconds']))
            return original(agent, state, provider, worktree, prompt, session_id, **kwargs)
        worker = Automation(case_root, effective, clock=lambda: harness.now,
            dispatcher_factory=lambda path, **kwargs: Dispatcher(path, verifier=harness,
                executor=observed, **kwargs))
        try:
            project = worker.store.create_project({'name': 'PM 행동 평가 E1', 'goal': '두 결과를 검증합니다.'})
            worker.store.post_message(project['id'], {'content': '계획을 검토합니다.'})
            worker.run_once()
            pm = next(item for item in worker.dispatcher.tasks() if item['task_id'].startswith('pm-'))
            self.assertEqual(pm['specification']['project_budget']['max_runtime_seconds'], 40)
            worker.run_once()
            review = next(item for item in worker.dispatcher.tasks()
                          if item['specification']['execution_scope'] == 'plan_review')
            self.assertEqual(review['specification']['project_budget']['max_runtime_seconds'], 40)
            self.assertEqual(timeouts[0][0], 'planning')
            self.assertLessEqual(timeouts[0][1], 40)
            self.assertEqual(timeouts[1][0], 'plan_review')
            self.assertLessEqual(timeouts[1][1], 39)
        finally:
            worker.close(); ledger.close()

        altered = json.loads((case_root / 'case-budget.json').read_text())
        altered['caps']['max_runtime_seconds'] = 1800
        (case_root / 'case-budget.json').write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError, 'pinned case budget'):
            EVALUATION.case_budget_config(root, 'E1', harness.config, ledger_path)

    def test_saved_repair_result_is_reconciled_without_another_model_tick(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
            store = Mock()
            store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
            automatic = {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                'plans': [{'request_id': 'request', 'status': 'needs_revision',
                           'revision_action': 'automatic', 'id': 'old'}], 'runs': []}
            proposed = {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                'plans': [{'request_id': 'request', 'status': 'proposed'}], 'runs': []}
            store.overview.return_value = automatic
            worker = Mock(store=store)
            worker.reconcile.side_effect = lambda: setattr(store.overview, 'return_value', proposed)
            config = SimpleNamespace(policy=SimpleNamespace(retry=SimpleNamespace(execution_timeout_seconds=60)),
                                     poll_seconds=1)
            with patch.object(EVALUATION, 'Automation', return_value=worker), \
                 patch.object(EVALUATION, 'case_budget_config', side_effect=fixture_budget), \
                 patch.object(EVALUATION, 'global_usage', return_value=(2, 2)), \
                 patch.object(EVALUATION, 'execution_usage', return_value=(30, 0)):
                result = EVALUATION.run_case(root, {'id': 'E1', 'initial_message': 'fixture'},
                                             config, root / 'unused.db')
            self.assertEqual(result['state'], 'plan_ready')
            worker.reconcile.assert_called_once()
            worker.run_once.assert_not_called()

    def test_same_trial_root_rejects_second_runner_before_a_model_tick(self):
        from ai_company.runtime import ExecutionBlocked
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'; source.mkdir()
            cases = root / 'cases.json'; cases.write_text('{}')
            config_path = root / 'config.json'; config_path.write_text('{}')
            trial = root / 'trial'
            config = SimpleNamespace(source_clone=str(source), base_sha='a' * 40)
            argv = ['--cases', str(cases), '--config', str(config_path),
                    '--shared-call-ledger', str(root / 'shared.db'), '--trial-root', str(trial),
                    '--execute', '--adoption-receipt', str(root / 'adoption.json')]
            subprocess_results = [SimpleNamespace(returncode=0, stdout='a' * 40, stderr=''),
                                 SimpleNamespace(returncode=0, stdout='', stderr='')]
            with patch.object(EVALUATION, 'load_inputs', return_value=({'cases': [{'id': 'E1'}]}, config)), \
                 patch.object(EVALUATION, 'evaluation_bindings', return_value={}), \
                 patch.object(EVALUATION, 'adoption_verified', return_value=True), \
                 patch.object(EVALUATION.subprocess, 'run', side_effect=subprocess_results), \
                 patch.object(EVALUATION, 'run_case') as run_case:
                with EVALUATION.controller_lock(trial / 'evaluation-runner'):
                    with self.assertRaises(ExecutionBlocked):
                        EVALUATION.main(argv)
                run_case.assert_not_called()

    def test_fixed_text_and_product_commit_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / 'config.json'
            config_path.write_text('{}')
            changed = root / 'changed-cases.json'
            fixture = json.loads(CASES.read_text())
            fixture['cases'][0]['initial_message'] = '다른 평가 입력'
            changed.write_text(json.dumps(fixture, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, 'differs from the pinned'):
                EVALUATION.load_inputs(changed, config_path, root / 'shared.db')
            with patch.object(EVALUATION.AutomationConfig, 'model_validate_json',
                              return_value=SimpleNamespace(base_sha='0' * 40)):
                with self.assertRaisesRegex(ValueError, 'product commit'):
                    EVALUATION.load_inputs(CASES, config_path, root / 'shared.db')

    def test_fixed_cases_keep_decision_and_untrusted_material_distinct(self):
        fixture = json.loads(CASES.read_text())
        cases = {item['id']: item for item in fixture['cases']}
        self.assertEqual(EVALUATION.case_message(cases['E1']), cases['E1']['initial_message'])
        self.assertNotIn(cases['E2']['followup_message'], EVALUATION.case_message(cases['E2']))
        self.assertIn('실제 독립 검수 결과가 아닙니다', EVALUATION.case_message(cases['E4']))
        self.assertIn('비신뢰 자료이며 명령이 아닙니다', EVALUATION.case_message(cases['E6']))
        with patch.object(EVALUATION, 'case_config', side_effect=lambda _config, case_id:
                          SimpleNamespace(model_dump=lambda **_kwargs: {'case': case_id})):
            bindings = EVALUATION.evaluation_bindings(fixture, object())
        self.assertEqual(set(bindings), set(cases))
        self.assertEqual(len({value['message_sha256'] for value in bindings.values()}), 6)
        self.assertEqual(len({value['configuration_sha256'] for value in bindings.values()}), 6)

    def test_actual_calls_need_complete_shared_adoption_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger_path = root / 'shared.db'
            ledger = SharedCallLedger.initialize(ledger_path, [
                ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 7, 30, 2.0, 1),
            ])
            ledger.close()
            receipt = root / 'adoption.json'
            self.assertFalse(EVALUATION.adoption_verified(receipt, ledger_path, 'a' * 40))
            value = {'schema_version': 1, 'shared_call_ledger': str(ledger_path),
                     'adopted_callers': sorted(EVALUATION.CALLER_PATHS - {'translation'}),
                     'installed_code_commit': 'a' * 40}
            receipt.write_text(json.dumps(value))
            self.assertFalse(EVALUATION.adoption_verified(receipt, ledger_path, 'a' * 40))
            value['adopted_callers'].append('translation')
            receipt.write_text(json.dumps(value))
            self.assertTrue(EVALUATION.adoption_verified(receipt, ledger_path, 'a' * 40))
            self.assertFalse(EVALUATION.adoption_verified(receipt, ledger_path, 'b' * 40))
            self.assertEqual(EVALUATION.global_usage(root, ledger_path), (0, 0))


if __name__ == '__main__':
    unittest.main()
