"""Versioned PM readiness and independent content review, using fixture sessions."""
import copy
import json
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.contracts import digest
from ai_company.management import ManagementError
from tests import test_automation as fixture


class PMRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.CoordinatorTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)

    def plan(self):
        for _ in range(5):
            self.h.worker.run_once()
            plans = self.h.worker.store.overview(self.h.project['id'])['plans']
            if plans and plans[-1]['status'] == 'proposed':
                return plans[-1]
        self.fail(str(plans))

    def confirm(self, plan):
        return self.h.worker.store.confirm_plan(self.h.project['id'], plan['id'], {
            'plan_digest': plan['digest'], 'base_harness_version': plan['base_harness_version'],
            'idempotency_key': 'confirm-reviewed-plan'})

    def test_review_precedes_confirmation_and_execution(self):
        self.h.worker.run_once()
        plan = self.h.worker.store.overview(self.h.project['id'])['plans'][0]
        self.assertEqual(plan['status'], 'reviewing')
        with self.assertRaises(ManagementError) as error:
            self.confirm(plan)
        self.assertEqual(error.exception.code, 'plan_review_required')
        self.assertEqual(self.h.worker.store.run_records(), [])
        reviewed = self.plan()
        self.assertEqual(reviewed['content']['requirements_review']['questions'], [])
        self.assertEqual(reviewed['content']['requirements_review']['findings'], [])
        self.assertEqual(reviewed['readiness']['plan_digest'], reviewed['digest'])
        self.assertEqual(reviewed['review']['plan_digest'], reviewed['digest'])
        self.assertNotEqual(reviewed['review']['session_id'], reviewed['review']['pm_session_id'])
        self.assertEqual(reviewed['review']['mode'], 'fixture')
        run = self.confirm(reviewed)['run']
        self.h.worker.run_once()
        self.assertEqual(set(self.h.worker.store.get_run(run['id'])['roles']), {'impl', 'test'})

    def test_changed_server_review_policy_blocks_new_confirmation(self):
        plan = self.plan()
        store = self.h.worker.store
        self.assertTrue(store.request_is_current(plan['request_id']))
        with patch.object(type(store), 'PM_GUIDANCE_VERSION', 'pm-requirements-v4'), \
             patch.object(type(store), 'PLAN_REVIEW_VERSION', 'plan-content-review-v3'):
            self.assertFalse(store.request_is_current(plan['request_id']))
            projected = store.overview(self.h.project['id'])['plans'][-1]
            self.assertEqual(projected['status'], 'stale')
            self.assertIn('새 계획', projected['stale_reason'])
            with self.assertRaises(ManagementError):
                self.confirm(plan)
            self.assertEqual(store.run_records(), [])

    def test_review_wait_reports_changed_policy_without_starting_new_review(self):
        self.h.worker.run_once()
        store = self.h.worker.store
        plan = store.overview(self.h.project['id'])['plans'][-1]
        self.assertEqual(plan['status'], 'reviewing')
        with patch.object(type(store), 'PM_GUIDANCE_VERSION', 'pm-requirements-v4'), \
             patch.object(type(store), 'PLAN_REVIEW_VERSION', 'plan-content-review-v3'):
            self.h.worker.run_once()
            waiting = store.overview(self.h.project['id'])['plans'][-1]
            self.assertEqual(waiting['status'], 'reviewing')
            self.assertIn('새 계획', waiting['review_problem'])
            self.assertEqual(store.run_records(), [])

    def test_confirmed_run_recovers_with_its_original_review_policy(self):
        plan = self.plan()
        run = self.confirm(plan)['run']
        with patch.object(type(self.h.worker.store), 'PM_GUIDANCE_VERSION', 'pm-requirements-v4'), \
             patch.object(type(self.h.worker.store), 'PLAN_REVIEW_VERSION', 'plan-content-review-v3'):
            self.h.worker.close(); self.h.worker = self.h.open()
            self.h.worker.run_once()
            self.assertEqual(set(self.h.worker.store.get_run(run['id'])['roles']), {'impl', 'test'})

    def test_confirmed_plan_is_not_rechecked_against_new_question_history_rule(self):
        store = self.h.worker.store
        old = store.pm_requests(self.h.project['id'])[0]
        store.post_message(self.h.project['id'], {'content': '같은 범위로 새 계획을 검토합니다.'})
        plan = self.plan()
        self.confirm(plan)
        legacy = store.get_pm_request(old['request_id'])
        legacy['requirements_feedback'] = {'questions': [{
            'id': 'Q1', 'prompt': '이전 필수 결정은?', 'status': 'open'}]}
        feedback_id = 'pm-feedback-' + old['request_id']
        with store.db:
            store.db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?',
                             (json.dumps(legacy), old['request_id']))
            store.db.execute('INSERT INTO management_messages VALUES (?,?,?)',
                             (feedback_id, self.h.project['id'], json.dumps({
                                 'id': feedback_id, 'role': 'assistant', 'content': '이전 필수 결정은?',
                                 'status': 'blocked', 'created_at': self.h.now})))
        self.assertEqual(len(store._pending_material_questions(self.h.project['id'], plan['request_revision'])), 1)
        store.assert_plan_ready(store.get_plan(self.h.project['id'], plan['id']))
        self.h.worker.run_once()
        self.assertEqual(len(store.run_records()), 1)

    def test_new_question_cannot_claim_nonexistent_source_and_old_goal_message(self):
        original = self.h.execute
        old_request = self.h.worker.store.pm_requests(self.h.project['id'])[0]
        def forged(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['stage'] == 'pm':
                outcome.result['structured_output']['plan']['requirements_review']['questions'] = [{
                    'id': 'Q-NEVER-ASKED', 'prompt': '공개 배포를 허용합니까?',
                    'reason': '공개 범위가 달라집니다.', 'status': 'answered', 'resolution': '허용',
                    'answer_message_id': old_request['request_id'],
                    'source_request_id': 'nonexistent-request',
                    'source_question_id': 'nonexistent-question'}]
            return outcome
        self.h.execute = forged
        self.h.worker.close(); self.h.worker = self.h.open()
        for _ in range(5):
            self.h.worker.run_once()
        self.assertEqual(self.h.worker.store.overview(self.h.project['id'])['plans'], [])
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_original_goal_decision_needs_verbatim_saved_master_evidence(self):
        plan = self.plan()
        store = self.h.worker.store
        request = store.get_pm_request(plan['request_id'])
        content = copy.deepcopy(plan['content'])
        question = {'id': 'Q-DEPLOY', 'prompt': '공개 배포를 허용합니까?',
                    'reason': '배포 범위가 달라집니다.', 'status': 'answered',
                    'answer_message_id': request['request_id'], 'evidence_kind': 'master_goal',
                    'resolution': '공개 배포를 허용합니다.', 'goal_quote': '공개 배포를 허용합니다.'}
        content['requirements_review']['questions'] = [question]
        with self.assertRaises(ManagementError) as error:
            store._requirements_ready({**plan, 'content': content})
        self.assertEqual(error.exception.code, 'answer_required')
        quoted = request['content'][:20]
        content['requirements_review']['questions'][0].update(
            resolution=quoted, goal_quote=quoted)
        store._requirements_ready({**plan, 'content': content})

    def test_repair_question_survives_restart_and_new_unanswered_request(self):
        original = self.h.execute
        rejected = {'value': False}
        answer_id = {'value': None}
        repair_source = {'plan': None}
        def repair_question(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            report = outcome.result['structured_output']
            scope = state['specification']['execution_scope']
            if scope == 'plan_review' and not rejected['value']:
                rejected['value'] = True
                report.update(verdict='REVISE', revision_route='technical', findings=[{
                    'finding_id': 'F-VERIFY', 'detail': '검사를 구체화합니다.',
                    'evidence': '요구사항 검증 방법'}])
            elif scope == 'planning' and state['specification']['plan'].get('source_plan_id'):
                feedback = copy.deepcopy(report['plan']['requirements_review'])
                feedback['questions'] = [{'id': 'Q-REPAIR', 'prompt': '어떤 배포를 허용합니까?',
                    'reason': '배포 범위가 달라집니다.', 'status': 'open'}]
                report.update(verdict='BLOCK', plan=None, requirements_feedback=feedback,
                              summary='배포 범위를 결정해주세요.')
            elif scope == 'planning' and answer_id['value'] and state['task_id'] == 'pm-' + answer_id['value']:
                report['plan']['requirements_review']['questions'] = [{
                    'id': 'Q-REPAIR', 'prompt': '어떤 배포를 허용합니까?',
                    'reason': '배포 범위가 달라집니다.', 'status': 'answered',
                    'resolution': '이번 단계에서는 배포하지 않습니다.',
                    'answer_message_id': answer_id['value'],
                    'source_request_id': repair_source['plan']['request_id'],
                    'source_question_id': 'Q-REPAIR',
                    'source_plan_id': repair_source['plan']['id'],
                    'source_repair_attempt': 1}]
            return outcome
        self.h.execute = repair_question
        self.h.worker.close(); self.h.worker = self.h.open()
        for _ in range(10):
            self.h.worker.run_once()
        old = self.h.worker.store.overview(self.h.project['id'])['plans'][0]
        repair_source['plan'] = old
        self.assertEqual(old['revision_action'], 'master_decision')
        self.h.worker.close(); self.h.worker = self.h.open()
        self.h.worker.store.post_message(self.h.project['id'], {'content': '계획을 다시 보여주세요.'})
        pending = self.h.worker.store.pm_requests(self.h.project['id'])[-1]['conversation_context']['pending_material_questions']
        self.assertEqual(pending[0]['plan_id'], old['id'])
        for _ in range(6):
            self.h.worker.run_once()
        self.assertEqual(len(self.h.worker.store.overview(self.h.project['id'])['plans']), 1)
        self.assertEqual(self.h.worker.store.run_records(), [])
        answer = self.h.worker.store.post_message(self.h.project['id'], {
            'content': '이번 단계에서는 배포하지 않습니다.', 'idempotency_key': 'repair-answer-once'})
        answer_id['value'] = answer['id']
        reviewed = self.plan()
        self.assertEqual(reviewed['content']['requirements_review']['questions'][0]['source_plan_id'], old['id'])
        run = self.confirm(reviewed)['run']
        self.assertEqual(run['plan_id'], reviewed['id'])
        self.assertEqual(len(self.h.worker.store.run_records()), 1)

    def test_missing_question_and_verification_prevent_readiness(self):
        plan = self.plan()
        content = copy.deepcopy(plan['content'])
        spec = content['requirements_review']
        spec['questions'] = [{'id': 'Q1', 'prompt': 'What is the first task?',
                              'reason': 'The scope changes with this answer', 'status': 'open'}]
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store._requirements_ready({**plan, 'content': content})
        self.assertEqual(error.exception.code, 'answer_required')
        spec['questions'] = []
        spec['findings'] = [{'id': 'F1', 'evidence': 'Goal conflicts with method', 'impact': 'Cannot meet goal',
            'alternatives': ['Change the method'], 'recommendation': 'Use the alternative',
            'blocking': True, 'status': 'open'}]
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store._requirements_ready({**plan, 'content': content})
        self.assertEqual(error.exception.code, 'blocking_finding')
        spec['findings'] = []
        spec['requirements'][0]['verification'] = ''
        with self.assertRaises(ValueError):
            self.h.worker.store._requirements_ready({**plan, 'content': content})

    def test_stale_and_forged_review_cannot_be_reused(self):
        first = self.plan()
        store = self.h.worker.store
        second_message = store.post_message(self.h.project['id'], {'content': 'Keep the scope but clarify completion'})
        second = self.plan()
        self.assertNotEqual(first['digest'], second['digest'])
        forged = copy.deepcopy(second)
        forged['review'] = copy.deepcopy(first['review'])
        with self.assertRaises(ManagementError):
            store.assert_plan_ready(forged)
        missing_readiness = copy.deepcopy(second); missing_readiness.pop('readiness')
        with self.assertRaises(ManagementError) as readiness_error:
            store.assert_plan_ready(missing_readiness)
        self.assertEqual(readiness_error.exception.code, 'plan_readiness_required')
        row = store.db.execute('SELECT document FROM flow_tasks WHERE task_id=?',
                               (second['review']['task_id'],)).fetchone()
        task = json.loads(row[0]); task['status'] = 'NEEDS_PLAN_REVISION'
        with store.db:
            store.db.execute('UPDATE flow_tasks SET document=? WHERE task_id=?',
                             (json.dumps(task), second['review']['task_id']))
        with self.assertRaises(ManagementError) as error:
            self.confirm(second)
        self.assertEqual(error.exception.code, 'plan_review_mismatch')
        self.assertEqual(store.run_records(), [])
        self.assertEqual(store.get_pm_request(second_message['id'])['plan_id'], second['id'])

    def test_duplicate_answer_key_survives_store_reopen(self):
        store = self.h.worker.store
        body = {'content': '대표 업무는 웹사이트 제작입니다.', 'idempotency_key': 'answer-once-001'}
        first = store.post_message(self.h.project['id'], body)
        before = len(store.pm_requests(self.h.project['id']))
        self.h.worker.close(); self.h.worker = self.h.open()
        second = self.h.worker.store.post_message(self.h.project['id'], body)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(before, len(self.h.worker.store.pm_requests(self.h.project['id'])))
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store.post_message(self.h.project['id'], {**body, 'content': 'Different decision'})
        self.assertEqual(error.exception.code, 'idempotency_conflict')

    def test_material_question_waits_for_answer_then_pm_writes_spec(self):
        original = self.h.execute
        asked = {'value': False}
        def question_once(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['stage'] == 'pm' and not asked['value']:
                asked['value'] = True
                report = outcome.result['structured_output']
                report.update(verdict='BLOCK', plan=None, summary='첫 버전의 대표 업무를 정해주세요.')
                feedback = {'version': 2, 'revision': state['specification']['plan']['request_revision'],
                    'goal_digest': state['specification']['plan']['goal_digest'],
                    'problem': 'Representative work is unknown', 'users_and_flow': 'A novice starts with a goal',
                    'scope': ['First project'], 'exclusions': [], 'assumptions': [],
                    'questions': [{'id': 'Q1', 'prompt': '첫 버전에서 맡길 대표 업무가 무엇인가요?',
                        'reason': 'The first workflow depends on representative work',
                        'options': ['웹사이트 제작', '여러 종류의 프로젝트'],
                        'recommendation': '첫 버전은 웹사이트 제작부터 시작하는 안을 권합니다.',
                        'status': 'open'}], 'findings': [],
                    'requirements': [{'id': 'R1', 'source': 'master goal', 'acceptance': 'First project can start',
                        'verification': 'Run the first project flow', 'role_keys': ['impl']}]}
                report['requirements_feedback'] = feedback
            elif state['stage'] == 'pm':
                outcome.result['structured_output']['plan']['requirements_review']['questions'] = [{
                    'id': 'Q1', 'prompt': '첫 버전에서 맡길 대표 업무가 무엇인가요?',
                    'reason': 'The first workflow depends on representative work',
                    'status': 'answered', 'resolution': '웹사이트 제작',
                    'answer_message_id': answer['id']}]
            return outcome
        self.h.execute = question_once
        with self.h.worker.store.db:
            self.h.worker.store.db.execute(
                "INSERT INTO management_messages VALUES (?,?,?)",
                ('early-answer', self.h.project['id'], json.dumps({
                    'id': 'early-answer', 'role': 'user', 'content': '질문 전에 쓴 다른 메시지',
                    'status': 'stored', 'created_at': self.h.now})))
        self.h.worker.run_once()
        request = self.h.worker.store.pm_requests(self.h.project['id'])[-1]
        self.assertEqual(request['state'], 'answer_needed')
        self.assertEqual(self.h.worker.store.run_records(), [])

        self.assertEqual(self.h.worker.store.overview(self.h.project['id'])['plans'], [])
        answer = self.h.worker.store.post_message(self.h.project['id'], {
            'content': '대표 업무는 웹사이트 제작입니다.', 'idempotency_key': 'material-answer-001'})
        next_request = self.h.worker.store.get_pm_request(answer['id'])
        self.assertEqual(next_request['conversation_context']['previous_requirements_feedback']['requirements']['questions'][0]['id'], 'Q1')
        self.assertEqual(self.h.worker.store.post_message(self.h.project['id'], {
            'content': '대표 업무는 웹사이트 제작입니다.', 'idempotency_key': 'material-answer-001'})['id'], answer['id'])
        plan = self.plan()
        self.assertEqual(plan['content']['requirements_review']['questions'][0]['answer_message_id'], answer['id'])
        incomplete = copy.deepcopy(plan)
        incomplete['content']['requirements_review']['questions'] = []
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store._requirements_ready(incomplete)
        self.assertEqual(error.exception.code, 'answer_required')
        early = copy.deepcopy(plan)
        early['content']['requirements_review']['questions'][0]['answer_message_id'] = 'early-answer'
        with self.assertRaises(ManagementError) as error:
            self.h.worker.store._requirements_ready(early)
        self.assertEqual(error.exception.code, 'answer_required')
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_two_open_questions_with_reused_id_need_separate_answer_bindings(self):
        store = self.h.worker.store
        first = store.pm_requests(self.h.project['id'])[0]

        def synthetic_feedback(request, prompt):
            # Isolate the readiness rule; the normal worker separately validates PM facts.
            document = store.get_pm_request(request['request_id'])
            document['requirements_feedback'] = {'questions': [{
                'id': 'Q1', 'prompt': prompt, 'status': 'open'}]}
            with store.db:
                store.db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?',
                                 (json.dumps(document), request['request_id']))
                message_id = 'pm-feedback-' + request['request_id']
                store.db.execute('INSERT INTO management_messages VALUES (?,?,?)',
                                 (message_id, self.h.project['id'], json.dumps({
                                     'id': message_id, 'role': 'assistant', 'content': prompt,
                                     'status': 'blocked', 'created_at': self.h.now})))

        synthetic_feedback(first, '배포 대상은 무엇인가요?')
        answer_one = store.post_message(self.h.project['id'], {'content': '시험 환경만 대상으로 합니다.'})
        second = store.get_pm_request(answer_one['id'])
        synthetic_feedback(second, '예산 상한은 얼마인가요?')
        answer_two = store.post_message(self.h.project['id'], {'content': '기존 예산 안에서만 합니다.'})
        request = store.get_pm_request(answer_two['id'])
        self.assertEqual(len(request['conversation_context']['pending_material_questions']), 2)
        self.assertEqual([item['request_id'] for item in request['conversation_context']['pending_material_questions']],
                         [first['request_id'], second['request_id']])
        content = copy.deepcopy(self.h.plan)
        questions = [
            {'id': 'Q1-first', 'prompt': '배포 대상은 무엇인가요?', 'reason': '범위 결정',
             'status': 'answered', 'resolution': '시험 환경', 'answer_message_id': answer_one['id'],
             'source_request_id': first['request_id'], 'source_question_id': 'Q1'},
            {'id': 'Q1-second', 'prompt': '예산 상한은 얼마인가요?', 'reason': '비용 결정',
             'status': 'answered', 'resolution': '기존 예산', 'answer_message_id': answer_two['id'],
             'source_request_id': second['request_id'], 'source_question_id': 'Q1'},
        ]
        content['requirements_review'] = {'version': 2, 'revision': request['request_revision'],
            'goal_digest': request['goal_digest'], 'problem': 'Two decisions',
            'users_and_flow': 'Master answers both questions', 'scope': ['Small task'],
            'exclusions': [], 'assumptions': [], 'questions': questions[1:], 'findings': [],
            'requirements': [{'id': 'R1', 'source': 'master goal', 'acceptance': 'Task is checked',
                              'verification': 'Run isolated checks', 'role_keys': ['impl']}]}
        check = {'content': content, 'contract_version': 2, 'request_id': request['request_id'],
                 'request_revision': request['request_revision'], 'goal_digest': request['goal_digest'],
                 'project_id': self.h.project['id'], 'pm_guidance_version': 'pm-requirements-v3'}
        with self.assertRaises(ManagementError) as error:
            store._requirements_ready(check)
        self.assertEqual(error.exception.code, 'answer_required')
        content['requirements_review']['questions'] = questions
        store._requirements_ready(check)
        content['requirements_review']['questions'][0]['prompt'] = '다른 질문으로 바꿈'
        with self.assertRaises(ManagementError) as error:
            store._requirements_ready(check)
        self.assertEqual(error.exception.code, 'answer_required')

    def test_review_quota_wait_recovers_without_duplicate_task(self):
        original = self.h.execute
        attempts = {'count': 0}
        def once(agent, state, provider, worktree, prompt, session_id, **kwargs):
            if state['specification']['execution_scope'] == 'plan_review' and attempts['count'] < 2:
                attempts['count'] += 1
                return SessionOutcome('quota', session_id=session_id or 'quota-plan-review-' + str(attempts['count']), reset_at=self.h.now + 30,
                                      result={'duration_seconds': 1, 'total_cost_usd': 0.01})
            return original(agent, state, provider, worktree, prompt, session_id, **kwargs)
        self.h.execute = once
        self.h.worker.run_once(); self.h.worker.run_once(); self.h.worker.run_once()
        plan = self.h.worker.store.overview(self.h.project['id'])['plans'][0]
        self.assertEqual(plan['status'], 'reviewing')
        task_id = 'plan-review-' + plan['digest'][:48]
        self.assertEqual(self.h.worker.dispatcher.get(task_id)['status'], 'WAITING_CAPACITY')
        self.h.worker.close(); self.h.worker = self.h.open()
        self.h.now += 60
        reviewed = self.plan()
        self.assertEqual(reviewed['review']['task_id'], task_id)
        self.assertEqual(attempts['count'], 2)
        self.assertEqual(len([t for t in self.h.worker.dispatcher.tasks() if t['task_id'] == task_id]), 1)
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_review_finding_requires_a_new_plan_and_surfaces_to_pm(self):
        original = self.h.execute
        def reject(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['specification']['execution_scope'] == 'plan_review':
                outcome.result['structured_output'].update(verdict='REVISE', findings=[{
                    'finding_id': 'F-VERIFY', 'detail': 'Complete criterion lacks an observable case',
                    'evidence': 'requirements_review.requirements[0].verification'}])
            return outcome
        self.h.execute = reject
        for _ in range(4):
            self.h.worker.run_once()
        store = self.h.worker.store
        plan = store.overview(self.h.project['id'])['plans'][0]
        self.assertEqual(plan['status'], 'needs_revision')
        self.assertEqual(plan['review']['verdict'], 'REVISE')
        with self.assertRaises(ManagementError):
            self.confirm(plan)
        self.assertEqual(store.run_records(), [])
        next_message = store.post_message(self.h.project['id'], {'content': '검수 지적을 반영해 계획을 수정해 주세요.'})
        context = store.get_pm_request(next_message['id'])['conversation_context']
        self.assertEqual(context['previous_proposal']['review']['findings'][0]['finding_id'], 'F-VERIFY')

    def test_technical_review_repairs_and_rechecks_once_without_new_master_request(self):
        original = self.h.execute
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
        self.h.execute = revise_once
        for _ in range(10):
            self.h.worker.run_once()
            plans = self.h.worker.store.overview(self.h.project['id'])['plans']
            if len(plans) == 2 and plans[-1]['status'] == 'proposed':
                break
        self.assertEqual(len(plans), 2, plans)
        self.assertEqual(plans[0]['status'], 'superseded')
        self.assertEqual(plans[1]['revision_of'], plans[0]['id'])
        self.assertEqual(plans[1]['auto_revision_attempt'], 1)
        self.assertEqual(plans[1]['review']['verdict'], 'PASS')
        self.assertEqual(reviews['count'], 2)
        self.assertEqual(len(self.h.worker.store.pm_requests(self.h.project['id'])), 1)
        self.h.worker.close(); self.h.worker = self.h.open()
        for _ in range(2):
            self.h.worker.run_once()
        self.assertEqual(len(self.h.worker.store.overview(self.h.project['id'])['plans']), 2)
        self.assertEqual(len(self.h.worker.store.run_records()), 0)
        run = self.confirm(plans[1])['run']
        self.h.worker.run_once()
        self.assertEqual(set(self.h.worker.store.get_run(run['id'])['roles']), {'impl', 'test'})

    def test_automatic_repair_stops_at_two_and_master_decision_never_autoruns(self):
        original = self.h.execute
        def always_revise(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            scope = state['specification']['execution_scope']
            report = outcome.result['structured_output']
            if scope == 'plan_review':
                report.update(verdict='REVISE', revision_route='technical', findings=[{
                    'finding_id': 'F-VERIFY', 'detail': '검증 방법을 더 구체화하세요.',
                    'evidence': 'requirements_review.requirements[0].verification'}])
            elif scope == 'planning' and state['specification']['plan'].get('source_plan_id'):
                attempt = state['specification']['plan']['auto_revision_attempt']
                report['plan']['requirements_review']['requirements'][0]['verification'] = f'{attempt}차 빈 입력 검사'
            return outcome
        self.h.execute = always_revise
        for _ in range(16):
            self.h.worker.run_once()
        plans = self.h.worker.store.overview(self.h.project['id'])['plans']
        self.assertEqual(len(plans), 3, plans)
        self.assertEqual([p.get('auto_revision_attempt', 0) for p in plans], [0, 1, 2])
        self.assertEqual(plans[-1]['revision_action'], 'limit_reached')
        self.assertEqual(self.h.worker.store.run_records(), [])
        self.assertFalse(any(t['task_id'].startswith('pm-revise-') and t['task_id'] ==
            'pm-revise-' + digest([plans[-1]['id'], 3])[:48] for t in self.h.worker.dispatcher.tasks()))

    def test_master_decision_review_waits_without_pm_repair(self):
        original = self.h.execute
        def needs_decision(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['specification']['execution_scope'] == 'plan_review':
                outcome.result['structured_output'].update(verdict='REVISE', revision_route='master_decision', findings=[{
                    'finding_id': 'F-DECISION', 'detail': '범위 결정이 필요합니다.', 'evidence': '목표의 범위가 모호합니다.'}])
            return outcome
        self.h.execute = needs_decision
        for _ in range(7):
            self.h.worker.run_once()
        plans = self.h.worker.store.overview(self.h.project['id'])['plans']
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['revision_action'], 'master_decision')
        self.assertFalse(any(t['task_id'].startswith('pm-revise-') for t in self.h.worker.dispatcher.tasks()))

    def test_revise_route_must_match_the_stored_independent_reviewer(self):
        original = self.h.execute
        def master_route(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['specification']['execution_scope'] == 'plan_review':
                outcome.result['structured_output'].update(verdict='REVISE', revision_route='master_decision', findings=[{
                    'finding_id': 'F-SCOPE', 'detail': '범위를 마스터가 정해야 합니다.', 'evidence': '두 범위가 충돌합니다.'}])
            return outcome
        self.h.execute = master_route
        self.h.worker.close(); self.h.worker = self.h.open()
        self.h.worker.run_once()
        store = self.h.worker.store
        plan = store.overview(self.h.project['id'])['plans'][0]
        task_id = 'plan-review-' + plan['digest'][:48]
        for _ in range(5):
            self.h.worker.dispatcher.run_once(task_ids={task_id})
            state = self.h.worker.dispatcher.get(task_id)
            if state['status'] == 'NEEDS_PLAN_REVISION':
                break
        self.assertEqual(state['status'], 'NEEDS_PLAN_REVISION')
        report = state['reviews']['reviewer']
        job = self.h.worker.dispatcher.queue.get(state['executions'][-1]['job_id'])
        receipt = {'plan_digest': plan['digest'], 'requirements_revision': plan['request_revision'],
            'requirements_digest': digest(plan['content']['requirements_review']),
            'review_version': plan['plan_review_version'], 'pm_session_id': plan['evidence']['session_id'],
            'session_id': job['session_id'], 'task_id': task_id, 'verdict': report['verdict'],
            'findings': report['findings'], 'summary': report['summary'], 'mode': plan['mode'],
            'revision_route': 'technical'}
        with self.assertRaises(ManagementError) as error:
            store.complete_plan_review(self.h.project['id'], plan['id'], receipt)
        self.assertEqual(error.exception.code, 'plan_review_mismatch')
        self.assertEqual(store.get_plan(self.h.project['id'], plan['id'])['status'], 'reviewing')
        self.assertEqual(store.run_records(), [])

    def test_reviewer_receives_original_goal_request_and_conversation(self):
        self.h.worker.run_once()
        store = self.h.worker.store
        plan = store.overview(self.h.project['id'])['plans'][0]
        request = store.get_pm_request(plan['request_id'])
        task = self.h.worker.dispatcher.get('plan-review-' + plan['digest'][:48])
        context = task['specification']['plan']
        self.assertEqual(context['goal'], request['goal'])
        self.assertEqual(context['master_message'], request['content'])
        self.assertEqual(context['conversation_context'], request['conversation_context'])

    def test_terminal_reviewer_failure_is_a_visible_environment_problem(self):
        original = self.h.execute
        def fail_review(agent, state, provider, worktree, prompt, session_id, **kwargs):
            if state['specification']['execution_scope'] == 'plan_review':
                return SessionOutcome('fatal', session_id=session_id or 'review-failed',
                                      result={'duration_seconds': 1, 'total_cost_usd': 0.01})
            return original(agent, state, provider, worktree, prompt, session_id, **kwargs)
        self.h.execute = fail_review
        for _ in range(4):
            self.h.worker.run_once()
        plan = self.h.worker.store.overview(self.h.project['id'])['plans'][0]
        self.assertEqual(plan['status'], 'reviewing')
        self.assertIn('fatal', plan['review_problem'])
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_stale_question_feedback_rejected(self):
        store = self.h.worker.store
        request = store.pm_requests(self.h.project['id'])[0]
        store.save_pm_request({**request, 'state': 'running', 'configuration_digest': 'c' * 64,
                               'mode': 'fixture'}, expected_state='pending')
        feedback = {'version': 2, 'revision': request['request_revision'] + 1,
            'goal_digest': request['goal_digest'], 'problem': 'Need a decision',
            'users_and_flow': 'User starts a project', 'scope': ['First release'],
            'exclusions': [], 'assumptions': [], 'findings': [],
            'questions': [{'id': 'Q1', 'prompt': 'Which release?', 'reason': 'Scope differs', 'status': 'open'}],
            'requirements': [{'id': 'R1', 'source': 'master goal', 'acceptance': 'Scope defined',
                'verification': 'Check release behavior', 'role_keys': ['impl']}]}
        report = {'execution_id': 'a' * 64, 'generation': 1, 'role': 'pm', 'task_digest': 'b' * 64,
            'policy_digest': 'c' * 64, 'candidate_sha': self.h.base, 'verification_digest': None,
            'verdict': 'BLOCK', 'findings': [], 'resolved_findings': [], 'summary': 'Need an answer',
            'plan': None, 'requirements_feedback': feedback}
        with self.assertRaises(ManagementError) as error:
            store.save_pm_feedback(request['request_id'], report, reason='question')
        self.assertEqual(error.exception.code, 'requirements_mismatch')
        self.assertEqual(store.get_pm_request(request['request_id'])['state'], 'running')
        self.assertEqual(store.overview(self.h.project['id'])['plans'], [])

    def test_pm_cannot_resolve_a_new_question_without_a_later_master_answer(self):
        store = self.h.worker.store
        first = store.pm_requests(self.h.project['id'])[0]
        store.save_pm_request({**first, 'state': 'running', 'configuration_digest': 'c' * 64,
                               'mode': 'fixture'}, expected_state='pending')
        feedback = {'version': 2, 'revision': first['request_revision'],
            'goal_digest': first['goal_digest'], 'problem': 'Need a decision',
            'users_and_flow': 'Master chooses the first release', 'scope': ['First release'],
            'exclusions': [], 'assumptions': [], 'findings': [],
            'questions': [{'id': 'Q1', 'prompt': 'Which release?', 'reason': 'Scope differs',
                           'status': 'answered', 'resolution': 'First release',
                           'answer_message_id': first['request_id']}],
            'requirements': [{'id': 'R1', 'source': 'master goal', 'acceptance': 'Scope defined',
                'verification': 'Check release behavior', 'role_keys': ['impl']}]}
        report = {'execution_id': 'a' * 64, 'generation': 1, 'role': 'pm', 'task_digest': 'b' * 64,
            'policy_digest': 'c' * 64, 'candidate_sha': self.h.base, 'verification_digest': None,
            'verdict': 'BLOCK', 'findings': [], 'resolved_findings': [], 'summary': 'Need an answer',
            'plan': None, 'requirements_feedback': feedback}
        with self.assertRaises(ManagementError) as error:
            store.save_pm_feedback(first['request_id'], report, reason='question')
        self.assertEqual(error.exception.code, 'answer_required')
        self.assertEqual(store.get_pm_request(first['request_id'])['state'], 'running')
        feedback['questions'][0].update(status='open', resolution=None, answer_message_id=None)
        store.save_pm_feedback(first['request_id'], report, reason='question')
        answer = store.post_message(self.h.project['id'], {'content': '첫 버전만 합니다.'})
        second = store.get_pm_request(answer['id'])
        store.save_pm_request({**second, 'state': 'running', 'configuration_digest': 'd' * 64,
                               'mode': 'fixture'}, expected_state='pending')
        feedback.update(revision=second['request_revision'])
        feedback['questions'][0].update(status='answered', resolution='첫 버전',
            answer_message_id=answer['id'], source_request_id=first['request_id'], source_question_id='Q1')
        store.save_pm_feedback(second['request_id'], report, reason='question')
        self.assertEqual(store.get_pm_request(second['request_id'])['state'], 'blocked')

    def test_review_revision_requires_a_concrete_finding(self):
        self.h.worker.run_once()
        store = self.h.worker.store
        plan = store.overview(self.h.project['id'])['plans'][0]
        review = {'plan_digest': plan['digest'], 'requirements_revision': plan['request_revision'],
            'requirements_digest': digest(plan['content']['requirements_review']),
            'review_version': plan['plan_review_version'],
            'pm_session_id': plan['evidence']['session_id'], 'session_id': 'separate-reviewer',
            'task_id': 'plan-review-' + plan['digest'][:48], 'verdict': 'REVISE',
            'findings': [], 'summary': 'Please revise', 'mode': 'fixture'}
        with self.assertRaises(ManagementError) as error:
            store.complete_plan_review(self.h.project['id'], plan['id'], review)
        self.assertEqual(error.exception.code, 'plan_review_mismatch')


if __name__ == '__main__':
    unittest.main()
