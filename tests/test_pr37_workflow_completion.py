"""PR #37: replay a settled Workflow review through completion and tick inspection."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ai_company.shared_calls import SharedCallLedger
from tests.test_claude_quota_checkpoint import review, tick


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_workflow_completion(test):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        attempt = root / 'attempt'
        attempt.mkdir()
        head, base = 'a' * 40, 'c' * 40
        patch = 'diff --git a/a.py b/a.py\n+x\n'
        patch_sha = digest(patch)
        prompt = f'--- PATCH {patch_sha} BEGIN ---\n{patch}\n--- PATCH END ---'
        binding = {'pr': 35, 'head': head, 'nonce': 'n', 'diff_sha256': patch_sha,
                   'prompt_sha256': digest(prompt)}
        report = (f'판정: PASS\n대상 HEAD: {head}\n패치 SHA-256: {patch_sha}'
                  '\n검토 범위: a.py\n미검토: 없음')

        def settings(suffix):
            return {'type': 'control_response', 'response': {
                'request_id': 'n-' + suffix,
                'response': {'applied': review.APPLIED, 'has_errors': False}}}

        events = [
            settings('before'),
            {'type': 'system', 'subtype': 'task_started', 'task_id': 'one', 'session_id': 's'},
            {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                {'type': 'tool_use', 'id': 'read', 'name': 'Read',
                 'input': {'file_path': str(root / 'a.py')}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'read', 'content': 'read'}]}},
            {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps({
                    'head': head, 'patch_sha256': patch_sha, 'reviewed_files': ['a.py']})}]}},
            {'type': 'system', 'subtype': 'task_notification', 'task_id': 'one',
             'session_id': 's', 'status': 'completed'},
            {'type': 'result', 'session_id': 's', 'result_index': 0, 'subtype': 'success',
             'is_error': False, 'terminal_reason': 'completed', 'stop_reason': 'end_turn',
             'queued_turn_count': 0, 'permission_denials': [],
             'subagent_stats': {'spawned': 1, 'completed': 1, 'failed': 0,
                               'killed': {}, 'refused': {}},
             'result': report, 'total_cost_usd': 1},
            settings('after'),
        ]
        stream = ''.join(json.dumps(item) + '\n' for item in events)
        facts = [{'state': 'starting', 'binding': binding},
                 {'state': 'prompt_delivery_started'},
                 {'state': 'prompt_delivered', 'prompt_sha256': digest(prompt)},
                 {'state': 'closed', 'exit_code': 0}]
        for name, value in (
            ('pr.diff', patch), ('prompt.txt', prompt), ('report.md', report),
            ('events.jsonl', stream), ('events.live.jsonl', stream),
            ('facts.jsonl', ''.join(json.dumps(item) + '\n' for item in facts)),
        ):
            (attempt / name).write_text(value)

        ledger = SharedCallLedger.initialize(root / 'ledger.sqlite', [
            ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
        try:
            ledger.reserve('r', str(attempt), 'claude', 'review', 'group')
            ledger.started('r', str(attempt), {'kind': 'executor_invocation'})
            ledger.settle('r', str(attempt), 'e', {'category': 'success',
                'duration_seconds': 1, 'total_cost_usd': 1}, terminated=True)
            summary = review.summarize(events, 'n', 0, input_verified=True,
                                       head=head, diff_sha256=patch_sha)
            checkpoint = review.checkpoint_from_events(events, binding=binding, base=base,
                files=['a.py'], reservation_id='r', settlement_event='e',
                events_sha256=digest(stream), repo=root)
            review.replace_private(attempt / 'checkpoint.json', checkpoint)
            receipt = {'pr': 35, 'url': 'https://example.test/pull/35', 'head': head,
                'base': base, 'diff_sha256': patch_sha, 'prompt_sha256': digest(prompt),
                'completion_verified': summary['completion_verified'],
                'review_passed': summary['review_passed'], 'verdict': summary['verdict'],
                'input_delivery_verified': True, 'binding_unchanged_after_review': True,
                'incomplete_reason': None, 'shared_reservation_id': 'r',
                'shared_settlement_event': 'e', 'events_sha256': digest(stream),
                'checkpoint_sha256': hashlib.sha256(
                    (attempt / 'checkpoint.json').read_bytes()).hexdigest()}
            interpretation = {'kind': review.result_interpretation(events)['kind'],
                'events_sha256': digest(stream), 'checkpoint_sha256': receipt['checkpoint_sha256'],
                'reservation_id': 'r', 'settlement_event': 'e', 'completion_verified': True}
            (attempt / 'receipt.json').write_text(json.dumps(receipt))
            (attempt / 'interpretation.json').write_text(json.dumps(interpretation))
            manifest = {'pr': 35, 'pr_url': receipt['url'], 'head': head, 'base': base,
                'diff_sha256': patch_sha, 'attempt_dir': str(attempt), 'checkout': str(root),
                'credential_ref': 'review', 'quota_group': 'group'}
            result = {'kind': interpretation['kind'], 'summary_pass': summary['review_passed'],
                'scope_verified': checkpoint['scope_verified'],
                'terminal_stopped': review.terminal_children_stopped(events),
                'reservation_state': ledger.reservation('r')['state'],
                'completed_review_verified': review.completed_review_verified(
                    attempt, receipt, ledger.reservation('r'), repo=root),
                'tick': tick.inspect(manifest, review, ledger, now=200)}
            test.assertEqual(result['kind'], 'workflow_complete')
            test.assertTrue(result['summary_pass'] and result['scope_verified']
                            and result['terminal_stopped'])
            test.assertEqual(result['reservation_state'], 'SETTLED')
            test.assertTrue(result['completed_review_verified'])
            test.assertEqual(result['tick']['state'], 'DONE')

            # 임시 파생 자료의 종류만 바꿔 거부 조건을 확인합니다. 원문·제품 코드는 그대로입니다.
            interpretation['kind'] = 'single'
            (attempt / 'interpretation.json').write_text(json.dumps(interpretation))
            control = {'same_events_with_incorrect_single_kind_accepted':
                review.completed_review_verified(attempt, receipt, ledger.reservation('r'), repo=root),
                'tick': tick.inspect(manifest, review, ledger, now=200)}
            test.assertFalse(control['same_events_with_incorrect_single_kind_accepted'])
            test.assertEqual(control['tick']['state'], 'NEEDS_RECONCILIATION')
        finally:
            ledger.close()


class WorkflowCompletionRecheckTests(unittest.TestCase):
    def test_completed_workflow_rechecks_to_done_and_wrong_kind_is_rejected(self):
        verify_workflow_completion(self)
