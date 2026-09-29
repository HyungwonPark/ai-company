"""Read-only product audit; all execution state lives in fixture temporary dirs."""
import copy
import json
from tests.test_automation import CoordinatorTests


def wait_proposed(h):
    for _ in range(12):
        h.worker.run_once()
        plans = h.worker.store.overview(h.project['id'])['plans']
        if plans and plans[-1]['status'] == 'proposed':
            return plans[-1]
    raise AssertionError(plans)


def confirm(h, plan):
    return h.worker.store.confirm_plan(h.project['id'], plan['id'], {
        'plan_digest': plan['digest'], 'base_harness_version': plan['base_harness_version'],
        'idempotency_key': 'audit-confirm'})


def new_question_with_old_message():
    h = CoordinatorTests(); h.setUp()
    try:
        original = h.execute
        request = h.worker.store.pm_requests(h.project['id'])[0]
        def execute(agent, state, provider, worktree, prompt, session_id, **kwargs):
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state['stage'] == 'pm':
                outcome.result['structured_output']['plan']['requirements_review']['questions'] = [{
                    'id': 'Q-NEVER-ASKED', 'prompt': 'May we export all customer data publicly?',
                    'reason': 'Changes disclosure scope', 'status': 'answered',
                    'resolution': 'Yes, publish all customer data',
                    'answer_message_id': request['request_id'],
                    'source_request_id': 'nonexistent-question-request',
                    'source_question_id': 'nonexistent-question'}]
            return outcome
        h.execute = execute
        h.worker.close(); h.worker = h.open()
        plan = wait_proposed(h)
        result = confirm(h, plan)
        print(json.dumps({'case': 'new_question_with_old_message',
            'actual_user_message': request['content'],
            'question': plan['content']['requirements_review']['questions'][0],
            'confirmed': result['plan']['status'], 'run_count': len(h.worker.store.run_records())}))
    finally:
        h.doCleanups()


def repair_question_disappears():
    h = CoordinatorTests(); h.setUp()
    try:
        original = h.execute
        rejected = False
        def execute(agent, state, provider, worktree, prompt, session_id, **kwargs):
            nonlocal rejected
            outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            report = outcome.result['structured_output']
            scope = state['specification']['execution_scope']
            if scope == 'plan_review' and not rejected:
                rejected = True
                report.update(verdict='REVISE', revision_route='technical', findings=[{
                    'finding_id': 'F-VERIFY', 'detail': 'Add a concrete boundary test',
                    'evidence': 'requirements_review.requirements[0].verification'}])
            elif scope == 'planning' and state['specification']['plan'].get('source_plan_id'):
                feedback = copy.deepcopy(report['plan']['requirements_review'])
                feedback['questions'] = [{'id': 'Q-REPAIR', 'prompt': 'Which release is authorized?',
                    'reason': 'Changes delivery scope', 'status': 'open'}]
                report.update(verdict='BLOCK', plan=None, requirements_feedback=feedback,
                              summary='Which release is authorized?')
            return outcome
        h.execute = execute
        h.worker.close(); h.worker = h.open()
        for _ in range(10):
            h.worker.run_once()
        old = h.worker.store.overview(h.project['id'])['plans'][0]
        assert old['revision_action'] == 'master_decision', old
        assert old['revision_feedback']['questions'][0]['status'] == 'open'
        h.worker.close(); h.worker = h.open()
        message = h.worker.store.post_message(h.project['id'], {'content': 'Show me the plan again.'})
        request = h.worker.store.get_pm_request(message['id'])
        context = request['conversation_context']
        plan = wait_proposed(h)
        result = confirm(h, plan)
        print(json.dumps({'case': 'repair_question_disappears',
            'stored_repair_question': old['revision_feedback']['questions'][0],
            'new_user_message': message['content'],
            'pending_questions': context['pending_material_questions'],
            'previous_proposal_has_revision_feedback': 'revision_feedback' in context['previous_proposal'],
            'new_plan_questions': plan['content']['requirements_review']['questions'],
            'confirmed': result['plan']['status'], 'run_count': len(h.worker.store.run_records())}))
    finally:
        h.doCleanups()


if __name__ == '__main__':
    new_question_with_old_message()
    repair_question_disappears()
