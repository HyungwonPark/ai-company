import copy
import unittest

from ai_company.project_report import project_report


class ProjectReportTests(unittest.TestCase):
    def fixture(self):
        return {'project': {'id': 'p', 'goal': '목표', 'request_revision': 2, 'source': 'master'},
                'plans': [{'id': 'plan', 'digest': 'digest', 'request_revision': 2, 'status': 'confirmed',
                           'confirmed_at': 10, 'content': {'roles': [{'key': 'dev', 'name': '개발'}, {'key': 'test', 'name': '검사'}],
                                                        'completion_criteria': ['같은 후보 검수']}}],
                'runs': [{'id': 'run', 'plan_id': 'plan', 'plan_digest': 'digest', 'mode': 'live', 'state': 'running',
                          'roles': {'dev': {'task_id': 'a', 'status': 'RUNNING'}, 'test': {'task_id': 'b', 'status': 'CONTRIBUTION_READY'}}}],
                'tasks': [{'id': 'a', 'status': 'WAITING_QUOTA', 'wait_reason': '공유 계정 한도', 'resume_at': 50},
                          {'id': 'b', 'status': 'CONTRIBUTION_READY', 'candidate_sha': 'test-sha'},
                          {'id': 'old', 'status': 'MERGE_READY', 'candidate_sha': 'old-sha'}],
                'approvals': [{'id': 'pr10', 'status': 'pending', 'artifact_sha': 'old-sha'}],
                'reports': [{'id': 'system-report', 'source': 'system'}, {'id': 'pm-report', 'source': 'pm'}]}

    def test_current_role_facts_and_sibling_progress_never_imply_goal_completion(self):
        overview = self.fixture(); original = copy.deepcopy(overview)
        report = project_report(overview)
        self.assertEqual([r['task_id'] for r in report['completed']], ['b'])
        self.assertEqual(report['blockers'][0]['reason'], '공유 계정 한도')
        self.assertEqual(report['blockers'][0]['resume_at'], 50)
        self.assertEqual(report['in_progress'][0]['status'], 'WAITING_QUOTA')
        self.assertFalse(report['candidate_verified'])
        self.assertEqual(report['goal_assessment'], 'not_assessed')
        self.assertEqual(report['pm_report_ids'], ['pm-report'])
        self.assertNotIn('pr10', str(report['decisions']))
        self.assertEqual(overview, original)
        self.assertEqual(project_report(overview)['digest'], report['digest'])

    def test_new_goal_or_changed_digest_cannot_borrow_old_success(self):
        overview = self.fixture(); overview['project']['request_revision'] = 3
        report = project_report(overview)
        self.assertIsNone(report['plan_id']); self.assertIsNone(report['run_id'])
        self.assertEqual(report['completed'], [])
        overview['project']['request_revision'] = 2
        overview['runs'][0]['plan_digest'] = 'different'
        self.assertIsNone(project_report(overview)['run_id'])

    def test_integration_sha_and_candidate_approval_are_bound_separately(self):
        overview = self.fixture(); run = overview['runs'][0]
        run.update(approval_id='current', integration={'task_id': 'i', 'candidate_sha': 'sha', 'status': 'MERGE_READY'})
        overview['tasks'].append({'id': 'i', 'status': 'MERGE_READY', 'candidate_sha': 'sha'})
        overview['approvals'].append({'id': 'current', 'artifact_sha': 'sha', 'status': 'pending'})
        report = project_report(overview)
        self.assertTrue(report['candidate_verified'])
        self.assertEqual(report['next_actions'][0]['owner'], 'master')
        self.assertEqual(report['decisions'][-1]['status'], 'pending')
        self.assertEqual(report['goal_assessment'], 'not_assessed')
        overview['tasks'][-1]['candidate_sha'] = 'other'
        self.assertFalse(project_report(overview)['candidate_verified'])
        overview['tasks'][-1]['candidate_sha'] = 'sha'; run['mode'] = 'fixture'
        self.assertFalse(project_report(overview)['candidate_verified'])

    def test_pm_wait_and_delegation_are_not_master_ui_or_pm_report(self):
        overview = self.fixture(); overview['runs'][0]['delegation_id'] = 'receipt'
        self.assertTrue(project_report(overview)['decisions'][0]['delegated'])
        overview['plans'] = []; overview['pm_requests'] = [{'request_revision': 2, 'state': 'waiting_retry', 'reason': '통신', 'resume_at': 90}]
        report = project_report(overview)
        self.assertEqual(report['blockers'][0]['resume_at'], 90)
        self.assertEqual(report['source'], 'system')
        self.assertEqual(report['next_actions'][0]['owner'], 'pm')

    def test_terminal_blocks_require_review_while_scheduled_waits_stay_with_coordinator(self):
        for status in ('BLOCKED','FAILED','STOPPED','NEEDS_RECONCILIATION','NEEDS_CONTEXT_HANDOFF'):
            overview = self.fixture()
            overview['tasks'][0].update(status=status, resume_at=None, wait_reason='실행 비용 기준에 도달했습니다.')
            original = copy.deepcopy(overview)
            report = project_report(overview)
            self.assertEqual(report['blockers'][0]['status'],status)
            self.assertEqual(report['next_actions'][0]['owner'],'operator')
            self.assertEqual([item['task_id'] for item in report['completed']],['b'])
            self.assertEqual(overview,original)
        report = project_report(self.fixture())
        self.assertEqual(report['next_actions'][0]['owner'],'coordinator')
        self.assertEqual(report['blockers'][0]['resume_at'],50)
