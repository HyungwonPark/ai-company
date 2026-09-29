"""The provider's Workflow notification is a cumulative result, not a new call."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ai_company.shared_calls import SharedCallError, SharedCallLedger


RUNNER = Path(__file__).resolve().parents[1] / 'scripts/review_pr_with_claude.py'
spec = importlib.util.spec_from_file_location('review_quota_candidate', RUNNER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
TICK_FILE = RUNNER.with_name('review_claude_quota_tick.py')
tick_spec = importlib.util.spec_from_file_location('review_quota_tick', TICK_FILE)
tick = importlib.util.module_from_spec(tick_spec)
tick_spec.loader.exec_module(tick)


def workflow_events():
    def result(index, cost):
        value = {'type': 'result', 'uuid': f'result-{index}', 'session_id': 'same-session',
                 'result_index': index, 'is_error': True, 'terminal_reason': 'api_error',
                 'api_error_status': 429, 'queued_turn_count': 0,
                 'subagent_stats': {'spawned': 0, 'completed': 0, 'failed': 0,
                                    'killed': {}, 'refused': {}},
                 'modelUsage': {'opus': {'inputTokens': index + 1, 'outputTokens': index + 2,
                                        'cacheReadInputTokens': index + 3,
                                        'cacheCreationInputTokens': index + 4,
                                        'thinkingTokens': index + 5, 'costUSD': cost}},
                 'total_cost_usd': cost}
        if index:
            value['origin'] = {'kind': 'task-notification'}
        return value
    return [{'type': 'system', 'subtype': 'task_started', 'task_id': 'task-1'},
            {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 200}},
            result(0, 10),
            {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': []},
            {'type': 'system', 'subtype': 'task_notification', 'task_id': 'task-1', 'status': 'completed'},
            result(1, 11)]


def settings_events(binding):
    safe = {'applied': review.APPLIED, 'has_errors': False}
    return [
        {'type': 'control_response', 'response': {
            'request_id': binding['nonce'] + '-before', 'response': safe}},
        {'type': 'assistant', 'message': {'model': review.MODEL, 'content': []}},
        {'type': 'control_response', 'response': {
            'request_id': binding['nonce'] + '-after', 'response': safe}},
    ]


class QuotaCheckpointTests(unittest.TestCase):
    def test_timeout_unknown_cost_settlement_stays_blocked_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = SharedCallLedger.initialize(Path(temporary) / 'ledger.sqlite', [
                ('claude', 'credential', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
            self.addCleanup(ledger.close)
            owner = str(Path(temporary) / 'attempt')
            ledger.reserve('reservation', owner, 'claude', 'credential', 'group')
            ledger.started('reservation', owner, {'kind': 'executor_invocation'})
            ledger.uncertain('reservation', owner, 'timeout')
            fact = {'category': 'reconciliation', 'cause': 'timeout', 'duration_seconds': 1800,
                    'total_cost_usd': None}
            self.assertTrue(ledger.settle('reservation', owner, 'event', fact,
                                          terminated=True))
            self.assertFalse(ledger.settle('reservation', owner, 'event', fact,
                                           terminated=True))
            account = ledger.account('claude', 'credential', 'group')
            self.assertEqual((account['state'], account['calls'], account['cost_unknown']),
                             ('UNKNOWN', 1, 1))
            with self.assertRaises(SharedCallError):
                ledger.settle('reservation', owner, 'event',
                              {**fact, 'total_cost_usd': 4.46}, terminated=True)

    def test_completed_workflow_before_single_result_is_one_finished_review(self):
        # Shape projected from the saved PR #29 CLI stream: two task starts,
        # two completed notifications, then one final result and after settings.
        terminal = {'type': 'result', 'session_id': 'same-session', 'result_index': 0,
                    'subtype': 'success', 'is_error': False, 'terminal_reason': 'completed',
                    'stop_reason': 'end_turn', 'queued_turn_count': 0,
                    'permission_denials': [], 'subagent_stats': {
                        'spawned': 0, 'completed': 0, 'failed': 0, 'killed': {}, 'refused': {}},
                    'result': ('판정: PASS\n대상 HEAD: ' + 'a' * 40 + '\n패치 SHA-256: '
                               + 'b' * 64 + '\n검토 범위: 패치 전체\n미검토: 없음')}
        tasks = [{'type': 'system', 'subtype': 'task_started',
                  'task_id': task, 'session_id': 'same-session'} for task in ('one', 'two')]
        done = [{'type': 'system', 'subtype': 'task_notification', 'task_id': task,
                 'session_id': 'same-session', 'status': 'completed'} for task in ('one', 'two')]
        events = [*tasks, *done, terminal]
        self.assertEqual(review.result_interpretation(events)['kind'], 'workflow_complete')
        self.assertTrue(review.terminal_children_stopped(events))
        summary = review.summarize([*settings_events({'nonce': 'n'}), *events], 'n', 0,
                                   input_verified=True, head='a' * 40, diff_sha256='b' * 64)
        self.assertTrue(summary['completion_verified'])
        self.assertTrue(summary['review_passed'])
        for changed in ([*tasks, done[0], terminal],
                        [*tasks, done[0], {**done[1], 'status': 'stopped'}, terminal],
                        [*tasks, *done, dict(done[1]), terminal],
                        [*tasks, done[0], terminal, done[1]],
                        [*tasks, *done, terminal, {'type': 'system',
                            'subtype': 'task_progress', 'task_id': 'one'}],
                        [*tasks, done[0], {**done[1], 'session_id': 'other'}, terminal]):
            with self.subTest(changed=changed[-2:]):
                self.assertIsNone(review.result_interpretation(changed))
        for malformed in ([*tasks, {**done[0], 'task_id': []}, done[1], terminal],
                          [{**tasks[0], 'session_id': None}, tasks[1], *done,
                           {**terminal, 'session_id': None}],
                          [*tasks, *done, {**terminal, 'session_id': None}]):
            with self.subTest(malformed=malformed[-2:]):
                self.assertIsNone(review.result_interpretation(malformed))

    def test_progress_needs_bound_midrun_settings_before_partial_credit(self):
        binding = {'pr': 35, 'head': 'a' * 40, 'diff_sha256': 'b' * 64, 'nonce': 'n'}
        claim = {'head': binding['head'], 'patch_sha256': binding['diff_sha256'],
                 'reviewed_files': ['a.py'], 'requirements': ['reviewed a.py'], 'findings': []}
        rows = [settings_events(binding)[0],
                {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                    {'type': 'tool_use', 'id': 'read-1', 'name': 'Read',
                     'input': {'file_path': '/repo/a.py'}}]}},
                {'type': 'user', 'message': {'content': [
                    {'type': 'tool_result', 'tool_use_id': 'read-1', 'is_error': False}]}},
                {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                    {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(claim)}]}},
                {'type': 'control_response', 'response': {
                    'request_id': None, 'response': {
                        'applied': review.APPLIED, 'has_errors': False}}}]
        rows[-1]['response']['request_id'] = review.progress_request_id('n', rows[3], 1)
        args = dict(binding=binding, base='c' * 40, files=['a.py'],
                    reservation_id='reservation', repo='/repo')
        self.assertEqual(review.checkpoint_from_events(rows, **args)['reviewed_files'], ['a.py'])
        for changed in (rows[:-1],
                        [*rows[:-1], {**rows[-1], 'response': {
                            **rows[-1]['response'], 'response': {
                                'applied': {**review.APPLIED, 'effort': 'medium'},
                                'has_errors': False}}}],
                        [*rows[:-1], {**rows[-1], 'response': {
                            **rows[-1]['response'], 'request_id': 'n-progress-wrong'}}],
                        [rows[0], rows[1], rows[3], rows[4]],
                        [rows[0], {**rows[1], 'message': {
                            **rows[1]['message'], 'model': 'other-model'}}, *rows[2:]],
                        [*rows, {**rows[4], 'response': {**rows[4]['response'],
                            'response': {'applied': {**review.APPLIED, 'effort': 'medium'},
                                         'has_errors': False}}}],
                        [*rows[:3], {**rows[3], 'message': {
                            **rows[3]['message'], 'content': [{'type': 'text',
                                'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps({
                                    **claim, 'head': 'd' * 40})}]}}, rows[4]]):
            with self.subTest(changed=changed[-1:]):
                self.assertEqual(review.checkpoint_from_events(changed, **args)['reviewed_files'], [])

    def test_pr37_progress_repros_reject_bad_snapshot_and_misaligned_response(self):
        binding = {'pr': 35, 'head': 'a' * 40, 'diff_sha256': 'b' * 64, 'nonce': 'n'}
        claim = {'head': binding['head'], 'patch_sha256': binding['diff_sha256'],
                 'reviewed_files': ['a.py']}
        def progress(value, *, child=False):
            return {'type': 'assistant', 'parent_tool_use_id': 'child' if child else None,
                    'session_id': 'main', 'message': {'model': review.MODEL,
                    'content': [{'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(value)}]}}
        def setting(suffix, effort='xhigh'):
            return {'type': 'control_response', 'response': {
                'request_id': suffix, 'response': {'applied': {**review.APPLIED, 'effort': effort},
                                                  'has_errors': False}}}
        before, after = setting('n-before'), setting('n-after')
        read = [{'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                    {'type': 'tool_use', 'name': 'Read', 'id': 'r',
                     'input': {'file_path': '/repo/a.py'}}]}},
                {'type': 'user', 'message': {'content': [
                    {'type': 'tool_result', 'tool_use_id': 'r', 'is_error': False}]}}]
        args = dict(binding=binding, base='c' * 40, files=['a.py'],
                    reservation_id='reservation', repo='/repo')
        valid = progress(claim)
        request = review.progress_request_id('n', valid, 1)
        self.assertEqual(review.checkpoint_from_events(
            [before, *read, valid, after], **args)['reviewed_files'], ['a.py'])
        for events in ([before, *read, valid, setting(request, 'medium'), after],
                       [before, *read, valid, setting('n-progress-1', 'medium'), after],
                       [before, *read, setting(request), valid, after],
                       [before, *read, valid, setting(request), setting(request, 'medium'), after]):
            with self.subTest(case='bad or conflicting snapshot', count=len(events)):
                self.assertEqual(review.checkpoint_from_events(events, **args)['reviewed_files'], [])

        long_marker = progress({**claim, 'requirements': ['x' * 10000]})
        malformed = {'type': 'assistant', 'parent_tool_use_id': None,
                     'message': {'model': review.MODEL, 'content': [
                         {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: {invalid'}]}}
        self.assertIsNone(review.progress_request_id('n', long_marker, 1))
        self.assertIsNone(review.progress_request_id('n', malformed, 1))
        self.assertIsNone(review.progress_request_id('n', progress(claim, child=True), 1))
        shifted = [before, *read, long_marker, malformed, progress(claim, child=True),
                   valid, setting('n-progress-1'), setting(request, 'medium')]
        self.assertEqual(review.checkpoint_from_events(shifted, **args)['reviewed_files'], [])

        second = progress({**claim, 'reviewed_files': ['b.py']})
        second_request = review.progress_request_id('n', second, 2)
        read_b = [{'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                      {'type': 'tool_use', 'name': 'Read', 'id': 'r2',
                       'input': {'file_path': '/repo/b.py'}}]}},
                  {'type': 'user', 'message': {'content': [
                      {'type': 'tool_result', 'tool_use_id': 'r2', 'is_error': False}]}}]
        delayed = [before, *read, valid, *read_b, second,
                   setting(second_request, 'medium'), setting(request)]
        self.assertEqual(review.checkpoint_from_events(delayed, **{**args,
                         'files': ['a.py', 'b.py']})['reviewed_files'], ['a.py'])

    def test_completed_primary_then_workflow_429_uses_cumulative_terminal_result(self):
        # Compact projection of the saved PR #35 stream: the primary result
        # completed while Workflow was still running, then its notification
        # ended at the provider's limit with a synthetic error message.
        events = workflow_events()
        events.pop(1)
        primary = events[1]
        primary.update(is_error=False, terminal_reason='completed',
                       stop_reason='end_turn', subtype='success')
        primary.pop('api_error_status')
        events.insert(-1, {'type': 'rate_limit_event', 'rate_limit_info': {
            'status': 'rejected', 'resetsAt': 200}})
        events.insert(-1, {'type': 'assistant', 'is_api_error_message': True,
                           'error': 'rate_limit', 'message': {'model': '<synthetic>',
                           'content': [{'type': 'text', 'text': 'session limit'}]}})
        events = [*settings_events({'nonce': 'n'}), *events]
        self.assertEqual(review.result_interpretation(events)['kind'], 'workflow_quota')
        self.assertTrue(review.terminal_children_stopped(events))
        self.assertEqual(review.rejected_quota_reset(events), 200)
        summary = review.summarize(events, 'n', 1)
        self.assertTrue(summary['configuration_verified'])
        self.assertEqual(summary['cost_usd_estimate'], 11)
        self.assertFalse(summary['completion_verified'])
        self.assertEqual(summary['response_models'], ['<synthetic>', review.MODEL])
        wrong_request = list(events)
        wrong_request[0] = {**events[0], 'response': {
            **events[0]['response'], 'request_id': 'other-before'}}
        self.assertFalse(review.summarize(wrong_request, 'n', 1)['configuration_verified'])
        wrong_applied = list(events)
        wrong_applied[2] = {**events[2], 'response': {
            **events[2]['response'], 'response': {
                'applied': {**review.APPLIED, 'effort': 'medium'}, 'has_errors': False}}}
        self.assertFalse(review.summarize(wrong_applied, 'n', 1)['configuration_verified'])
        errors = list(events)
        errors[2] = {**events[2], 'response': {
            **events[2]['response'], 'response': {
                'applied': review.APPLIED, 'has_errors': True}}}
        self.assertFalse(review.summarize(errors, 'n', 1)['configuration_verified'])
        self.assertFalse(review.summarize([*events, {'type': 'assistant', 'message': {
            'model': 'other-model'}}], 'n', 1)['configuration_verified'])
        self.assertFalse(review.summarize([*events, {'type': 'assistant', 'message': {
            'model': '<synthetic>'}}], 'n', 1)['configuration_verified'])
        final_index = next(i for i, event in enumerate(events)
                           if event.get('type') == 'result' and event.get('result_index') == 1)
        wrong_session = list(events)
        wrong_session[final_index] = {**events[final_index], 'session_id': 'other-session'}
        self.assertIsNone(review.result_interpretation(wrong_session))
        missing_notification = [event for event in events
                                if event.get('subtype') != 'task_notification']
        self.assertFalse(review.terminal_children_stopped(missing_notification))
        wrong_final = list(events)
        wrong_final[final_index] = {**events[final_index], 'api_error_status': 500}
        self.assertIsNone(review.rejected_quota_reset(wrong_final))

    def test_old_synthetic_model_checkpoint_replays_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            head, base = 'a' * 40, 'b' * 40
            patch_text = 'diff --git a/a.py b/a.py\n+x\n'
            diff_sha = hashlib.sha256(patch_text.encode()).hexdigest()
            old = root / ('pr-35-' + head + '-quota-old')
            new = root / ('pr-35-' + head)
            old.mkdir()
            new.mkdir()
            rows = workflow_events()
            rows[2].update(is_error=False, terminal_reason='completed', stop_reason='end_turn')
            rows[2].pop('api_error_status')
            rows.insert(-1, {'type': 'assistant', 'is_api_error_message': True,
                             'error': 'rate_limit', 'message': {'model': '<synthetic>'}})
            for directory, nonce in ((old, 'old'), (new, 'new')):
                events = [*settings_events({'nonce': nonce}), *rows]
                (directory / 'events.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in events))
                (directory / 'pr.diff').write_text(patch_text)
                (directory / 'facts.jsonl').write_text(json.dumps({'binding': {
                    'pr': 35, 'head': head, 'diff_sha256': diff_sha,
                    'nonce': nonce}, 'exit_code': 1}) + '\n')
            old_receipt = {'head': head, 'base': base, 'diff_sha256': diff_sha,
                           'events_sha256': None, 'shared_reservation_id': 'old-r'}
            (old / 'receipt.json').write_text(json.dumps(old_receipt))
            (old / 'reconciliation.json').write_text('{}')
            (old / 'prompt.txt').write_text('')
            with patch.object(review, 'input_delivery_verified', return_value=True):
                inherited = review.previous_checkpoint(old, head=head, base=base,
                    diff_sha256=diff_sha, files=['a.py'])
                self.assertFalse(inherited['configuration_observed']['verified'])
                record = {'source': str(old), 'receipt_sha256': hashlib.sha256(
                    (old / 'receipt.json').read_bytes()).hexdigest(),
                    'events_sha256': hashlib.sha256((old / 'events.jsonl').read_bytes()).hexdigest(),
                    'checkpoint': inherited}
                (new / 'prompt.txt').write_text('PREVIOUS_CHECKPOINT_JSON: ' + json.dumps(record) + '\n')
                new_events = [json.loads(x) for x in (new / 'events.jsonl').read_text().splitlines()]
                old_style = review.checkpoint_from_events(new_events, binding={
                    'pr': 35, 'head': head, 'diff_sha256': diff_sha, 'nonce': 'new'},
                    base=base, files=['a.py'], reservation_id='new-r', previous=inherited,
                    events_sha256=hashlib.sha256((new / 'events.jsonl').read_bytes()).hexdigest(),
                    legacy_model_check=True)
                (new / 'checkpoint.json').write_text(json.dumps(old_style))
                (new / 'receipt.json').write_text(json.dumps({
                    'head': head, 'base': base, 'diff_sha256': diff_sha,
                    'events_sha256': hashlib.sha256((new / 'events.jsonl').read_bytes()).hexdigest(),
                    'checkpoint_sha256': hashlib.sha256((new / 'checkpoint.json').read_bytes()).hexdigest(),
                    'shared_reservation_id': 'new-r'}))
                self.assertEqual(review.previous_checkpoint(new, head=head, base=base,
                    diff_sha256=diff_sha, files=['a.py']), old_style)

    def test_original_classifier_failed_but_workflow_quota_is_one_cumulative_result(self):
        events = workflow_events()
        self.assertEqual(review.result_interpretation(events)['kind'], 'workflow_quota')
        self.assertTrue(review.terminal_children_stopped(events))
        self.assertEqual(review.rejected_quota_reset(events), 200)
        summary = review.summarize(events, 'nonce', 1)
        self.assertEqual(summary['cost_usd_estimate'], 11)
        self.assertFalse(summary['completion_verified'])

    def test_duplicate_requires_same_stable_identity_and_same_content(self):
        events = workflow_events()
        self.assertTrue(review.terminal_children_stopped([*events, dict(events[-1])]))
        conflicting = {**events[-1], 'total_cost_usd': 12}
        self.assertFalse(review.terminal_children_stopped([*events, conflicting]))
        unrelated = {**events[-1], 'uuid': 'different'}
        self.assertFalse(review.terminal_children_stopped([*events, unrelated]))

    def test_notification_only_or_api_500_does_not_become_quota_resume(self):
        events = workflow_events()
        self.assertFalse(review.terminal_children_stopped([events[1], events[-1]]))
        single = {**events[2], 'api_error_status': 500}
        self.assertTrue(review.terminal_children_stopped([events[1], single]))
        self.assertIsNone(review.rejected_quota_reset([events[1], single]))
        # A reset observed during a long Workflow can be past at closure.
        self.assertEqual(review.rejected_quota_reset(events, min_reset=100), 200)

    def test_unfinished_task_wrong_session_and_nonmonotonic_usage_are_unknown(self):
        events = workflow_events()
        mutations = [
            [*events[:4], {**events[4], 'status': 'running'}, events[5]],
            [*events[:5], {**events[5], 'session_id': 'other'}],
            [*events[:5], {**events[5], 'total_cost_usd': 9}],
            [*events[:5], {**events[5], 'modelUsage': {'opus': {
                **events[5]['modelUsage']['opus'], 'outputTokens': 1}}}],
            [{**events[0], 'task_id': ['task-1']}, *events[1:4],
             {**events[4], 'task_id': ['task-1']}, events[5]],
            [*events[:5], {**events[5], 'subagent_stats': {
                **events[5]['subagent_stats'], 'spawned': 1}}],
        ]
        for rows in mutations:
            with self.subTest(rows=rows[-1]):
                self.assertFalse(review.terminal_children_stopped(rows))
                self.assertIsNone(review.rejected_quota_reset(rows))

    def test_checkpoint_only_accepts_matching_claim_after_successful_read(self):
        binding = {'pr': 35, 'head': 'a' * 40, 'diff_sha256': 'b' * 64, 'nonce': 'n'}
        claim = {'head': binding['head'], 'patch_sha256': binding['diff_sha256'],
                 'reviewed_files': ['a.py'], 'requirements': ['quota'],
                 'findings': [{'file': 'a.py', 'evidence': 'line 1'}]}
        message = {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 'read-1', 'name': 'Read', 'input': {'file_path': '/repo/a.py'}}]}}
        claim_event = {'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(claim)}]}}
        rows = [*settings_events(binding), message, {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'read-1', 'content': 'read'}]}}, claim_event]
        result = review.checkpoint_from_events(rows, binding=binding, base='c' * 40,
                                               files=['a.py', 'b.py'], reservation_id='r')
        self.assertEqual(result['reviewed_files'], ['a.py'])
        self.assertEqual(result['remaining_files'], ['b.py'])
        self.assertFalse(result['scope_verified'])
        failed = [*settings_events(binding), message, {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'read-1', 'is_error': True}]}}, claim_event]
        self.assertEqual(review.checkpoint_from_events(failed, binding=binding,
                         base='c' * 40, files=['a.py', 'b.py'],
                         reservation_id='r')['reviewed_files'], [])
        next_binding = {**binding, 'nonce': 'next'}
        next_claim = {**claim, 'reviewed_files': ['b.py'], 'findings': []}
        next_rows = [*settings_events(next_binding), {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 'read-2', 'name': 'Read', 'input': {'file_path': '/repo/b.py'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'read-2', 'content': 'read'}]}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(next_claim)}]}}]
        continued = review.checkpoint_from_events(next_rows, binding=next_binding,
            base='c' * 40, files=['a.py', 'b.py'], reservation_id='r2', previous=result)
        self.assertEqual(continued['reviewed_files'], ['a.py', 'b.py'])
        self.assertEqual(len(continued['lineage']), 2)
        self.assertEqual(continued['findings'][0]['status'], 'open')
        resolution = {'head': binding['head'], 'patch_sha256': binding['diff_sha256'],
                      'reviewed_files': ['a.py'], 'resolved_findings': [
                          {'id': result['findings'][0]['id'], 'reason': 'line 1 is covered'}]}
        resolution_rows = [*settings_events({**binding, 'nonce': 'third'}),
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'read-3', 'name': 'Read', 'input': {'file_path': '/repo/a.py'}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'read-3', 'content': 'read'}]}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(resolution)}]}}]
        resolved = review.checkpoint_from_events(resolution_rows,
            binding={**binding, 'nonce': 'third'}, base='c' * 40,
            files=['a.py', 'b.py'], reservation_id='r3', previous=continued)
        self.assertEqual(resolved['findings'][0]['status'], 'resolved')
        self.assertTrue(resolved['scope_verified'])
        unverified = review.checkpoint_from_events([message, rows[-2], claim_event],
            binding=binding, base='c' * 40, files=['a.py', 'b.py'], reservation_id='bad')
        self.assertEqual(unverified['reviewed_files'], [])
        with self.assertRaises(RuntimeError):
            review.checkpoint_from_events(rows, binding=binding, base='different',
                files=['a.py', 'b.py'], reservation_id='r', previous=result)

    def test_quota_reconciliation_settles_once_without_erasing_newer_restriction(self):
        with tempfile.TemporaryDirectory() as temporary:
            ledger = SharedCallLedger.initialize(Path(temporary) / 'calls.sqlite', [
                ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: 100)
            owner = 'attempt'
            ledger.reserve('r', owner, 'claude', 'review', 'group')
            ledger.started('r', owner, {'kind': 'executor_invocation'})
            ledger.uncertain('r', owner, 'terminal_or_children_unverified')
            expected = [list(item) for item in ledger.db.execute(
                'SELECT provider,credential_ref,group_id,state,resume_at,reason,calls,'
                'runtime_seconds,cost_usd,cost_unknown FROM accounts WHERE group_id=? '
                'ORDER BY provider,credential_ref', ('group',))]
            fact = {'category': 'quota', 'duration_seconds': 30,
                    'total_cost_usd': 11, 'reset_at': 200}
            self.assertTrue(ledger.settle('r', owner, 'event', fact, terminated=True,
                                          expected_accounts=expected))
            self.assertFalse(ledger.settle('r', owner, 'event', fact, terminated=True,
                                           expected_accounts=expected))
            self.assertEqual(ledger.account('claude', 'review', 'group')['state'], 'UNKNOWN')
            self.assertTrue(ledger.reconcile_settled_quota('r', 'claude', 'review', 'group', 'event'))
            self.assertFalse(ledger.reconcile_settled_quota('r', 'claude', 'review', 'group', 'event'))
            account = ledger.account('claude', 'review', 'group')
            self.assertEqual((account['calls'], account['cost_usd'], account['state']), (1, 11, 'COOLDOWN'))
            ledger.db.execute("UPDATE accounts SET state='UNKNOWN',reason='new_restriction' WHERE group_id='group'")
            with self.assertRaises(SharedCallError):
                ledger.reconcile_settled_quota('r', 'claude', 'review', 'group', 'event')
            self.assertEqual(ledger.account('claude', 'review', 'group')['state'], 'UNKNOWN')
            ledger.close()

    def test_crash_after_reservation_recovers_only_unstarted_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / 'pr-35-head'
            attempt.mkdir()
            (attempt / 'pr.diff').write_text('diff --git a/a.py b/a.py\n')
            ledger = SharedCallLedger.initialize(root / 'shared.sqlite', [
                ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
            ledger.reserve('reservation', str(attempt), 'claude', 'review', 'group')
            manifest = {'attempt_dir': str(attempt), 'quota_group': 'group'}
            self.assertTrue(tick.recover_unstarted(manifest, ledger))
            self.assertEqual(ledger.reservation('reservation')['state'], 'CANCELLED')
            self.assertFalse(attempt.exists())
            self.assertEqual(len(list(root.glob(attempt.name + '-unstarted-*'))), 1)
            self.assertEqual(ledger.account('claude', 'review', 'group')['calls'], 0)
            attempt.mkdir()
            (attempt / 'facts.jsonl').write_text('{"state":"prompt_delivery_started"}\n')
            ledger.reserve('second', str(attempt), 'claude', 'review', 'group')
            self.assertFalse(tick.recover_unstarted(manifest, ledger))
            self.assertEqual(ledger.reservation('second')['state'], 'RESERVED')
            (attempt / 'facts.jsonl').unlink()
            ledger.started('second', str(attempt), {'kind': 'executor_invocation'})
            self.assertFalse(tick.recover_unstarted(manifest, ledger))
            self.assertEqual(ledger.reservation('second')['state'], 'STARTED')
            ledger.close()

    def test_restart_tick_waits_for_reset_and_exact_settlement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clock = [100]
            attempt = root / ('pr-35-' + 'a' * 40)
            attempt.mkdir()
            patch_text = 'diff --git a/a.py b/a.py\n+x\n'
            diff_sha = hashlib.sha256(patch_text.encode()).hexdigest()
            prompt = f'--- PATCH {diff_sha} BEGIN ---\n{patch_text}\n--- PATCH END ---'
            prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
            binding = {'pr': 35, 'head': 'a' * 40, 'nonce': 'n',
                       'diff_sha256': diff_sha, 'prompt_sha256': prompt_sha}
            (attempt / 'pr.diff').write_text(patch_text)
            (attempt / 'prompt.txt').write_text(prompt)
            facts = [{'state': 'starting', 'binding': binding},
                     {'state': 'prompt_delivery_started'},
                     {'state': 'prompt_delivered', 'prompt_sha256': prompt_sha},
                     {'state': 'closed', 'exit_code': 1}]
            (attempt / 'facts.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in facts))
            rows = [{'type': 'rate_limit_event', 'rate_limit_info': {
                        'status': 'rejected', 'resetsAt': 200}},
                    {'type': 'result', 'is_error': True, 'terminal_reason': 'api_error',
                     'api_error_status': 429,
                     'queued_turn_count': 0, 'subagent_stats': {
                         'spawned': 0, 'completed': 0, 'failed': 0, 'killed': {}, 'refused': {}}}]
            stream = ''.join(json.dumps(x) + '\n' for x in rows)
            (attempt / 'events.jsonl').write_text(stream)
            (attempt / 'events.live.jsonl').write_text(stream)
            ledger = SharedCallLedger.initialize(root / 'shared.sqlite', [
                ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: clock[0])
            ledger.reserve('r', str(attempt), 'claude', 'review', 'group')
            ledger.started('r', str(attempt), {'kind': 'executor_invocation'})
            event_id = 'e' * 64
            ledger.settle('r', str(attempt), event_id, {'category': 'quota',
                'reset_at': 200, 'duration_seconds': 1, 'total_cost_usd': 2}, terminated=True)
            receipt = {'pr': 35, 'url': 'https://example.test/pull/35', 'head': 'a' * 40,
                       'base': 'c' * 40, 'diff_sha256': diff_sha, 'prompt_sha256': prompt_sha,
                       'completion_verified': False, 'input_delivery_verified': True,
                       'binding_unchanged_after_review': True, 'incomplete_reason': None,
                       'shared_reservation_id': 'r', 'shared_settlement_event': event_id}
            checkpoint = review.checkpoint_from_events(rows, binding=binding, base=receipt['base'],
                files=['a.py'], reservation_id='r', settlement_event=event_id,
                events_sha256=hashlib.sha256(stream.encode()).hexdigest(), repo=root)
            review.replace_private(attempt / 'checkpoint.json', checkpoint)
            receipt['events_sha256'] = hashlib.sha256(stream.encode()).hexdigest()
            receipt['checkpoint_sha256'] = hashlib.sha256((attempt / 'checkpoint.json').read_bytes()).hexdigest()
            (attempt / 'interpretation.json').write_text(json.dumps({
                'events_sha256': receipt['events_sha256'],
                'checkpoint_sha256': receipt['checkpoint_sha256'],
                'reservation_id': 'r', 'settlement_event': event_id}))
            (attempt / 'receipt.json').write_text(json.dumps(receipt))
            manifest = {'pr': 35, 'pr_url': receipt['url'], 'head': receipt['head'],
                        'base': receipt['base'], 'diff_sha256': diff_sha,
                        'attempt_dir': str(attempt), 'checkout': str(root),
                        'credential_ref': 'review', 'quota_group': 'group'}
            self.assertEqual(tick.inspect(manifest, review, ledger, now=199)['state'], 'WAITING_QUOTA')
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'READY')
            archived = attempt.with_name(attempt.name + '-quota-restarted')
            attempt.rename(archived)
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'READY')
            duplicate = attempt.with_name(attempt.name + '-quota-duplicate')
            shutil.copytree(archived, duplicate)
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'],
                             'NEEDS_RECONCILIATION')
            shutil.rmtree(duplicate)
            attempt = archived.rename(attempt)
            checkpoint = review.previous_checkpoint(attempt, head=receipt['head'],
                base=receipt['base'], diff_sha256=diff_sha, files=['a.py'], repo=root)
            forged = {**checkpoint, 'reviewed_files': ['a.py'], 'remaining_files': []}
            (attempt / 'checkpoint.json').write_text(json.dumps(forged))
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'NEEDS_RECONCILIATION')
            review.replace_private(attempt / 'checkpoint.json', checkpoint)
            # Current-format attempt: the immutable interpretation and
            # checkpoint were written before evidence-based settlement.
            old_interpretation = (attempt / 'interpretation.json').read_text()
            current_checkpoint = review.checkpoint_from_events(rows, binding=binding,
                base=receipt['base'], files=['a.py'], reservation_id='r',
                events_sha256=receipt['events_sha256'], repo=root)
            review.replace_private(attempt / 'checkpoint.json', current_checkpoint)
            current_receipt = {**receipt, 'shared_settlement_event': None,
                'checkpoint_sha256': hashlib.sha256((attempt / 'checkpoint.json').read_bytes()).hexdigest()}
            (attempt / 'receipt.json').write_text(json.dumps(current_receipt))
            (attempt / 'interpretation.json').write_text(json.dumps({
                'events_sha256': receipt['events_sha256'],
                'checkpoint_sha256': current_receipt['checkpoint_sha256'],
                'reservation_id': 'r', 'settlement_event': None}))
            original_bytes = {name: (attempt / name).read_bytes() for name in
                ('receipt.json', 'interpretation.json', 'checkpoint.json', 'events.jsonl')}
            current_sidecar = {'source_receipt_sha256': hashlib.sha256(
                original_bytes['receipt.json']).hexdigest(),
                'source_events_sha256': receipt['events_sha256'],
                'shared_reservation_id': 'r', 'shared_settlement_event': event_id,
                'category': 'quota', 'reset_at': 200}
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'],
                             'NEEDS_RECONCILIATION')
            (attempt / 'reconciliation.json').write_text(json.dumps(current_sidecar))
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'READY')
            self.assertEqual({name: (attempt / name).read_bytes() for name in original_bytes}, original_bytes)
            (attempt / 'reconciliation.json').unlink()
            (attempt / 'receipt.json').write_text(json.dumps(receipt))
            (attempt / 'interpretation.json').write_text(old_interpretation)
            review.replace_private(attempt / 'checkpoint.json', checkpoint)
            unreconciled = {**receipt, 'shared_settlement_event': None}
            unreconciled.pop('events_sha256')
            unreconciled.pop('checkpoint_sha256')
            (attempt / 'receipt.json').write_text(json.dumps(unreconciled))
            (attempt / 'checkpoint.json').unlink()
            sidecar = {'source_receipt_sha256': hashlib.sha256((attempt / 'receipt.json').read_bytes()).hexdigest(),
                       'source_events_sha256': hashlib.sha256((attempt / 'events.jsonl').read_bytes()).hexdigest(),
                       'shared_reservation_id': 'r', 'shared_settlement_event': event_id,
                       'category': 'quota', 'reset_at': 200}
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'NEEDS_RECONCILIATION')
            (attempt / 'reconciliation.json').write_text(json.dumps(sidecar))
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'READY')
            sidecar['source_events_sha256'] = '0' * 64
            (attempt / 'reconciliation.json').write_text(json.dumps(sidecar))
            self.assertEqual(tick.inspect(manifest, review, ledger, now=200)['state'], 'NEEDS_RECONCILIATION')
            (attempt / 'reconciliation.json').unlink()
            (attempt / 'receipt.json').write_text(json.dumps(receipt))
            review.replace_private(attempt / 'checkpoint.json', checkpoint)
            ledger.db.execute("UPDATE accounts SET state='UNKNOWN',reason='new_restriction' WHERE group_id='group'")
            self.assertEqual(tick.inspect(manifest, review, ledger, now=201)['state'], 'NEEDS_RECONCILIATION')
            ledger.db.execute("UPDATE accounts SET state='COOLDOWN',reason='quota' WHERE group_id='group'")
            clock[0] = 201
            with review.reserve_attempt(attempt, False, resume_quota=True,
                    quota_binding={'pr_number': 35, 'pr_url': receipt['url'],
                                   'head': receipt['head'], 'base': receipt['base'],
                                   'diff_sha256': diff_sha}, ledger=ledger,
                    quota_group='group') as old:
                self.assertTrue(old.name.startswith(attempt.name + '-quota-'))
                ledger.reserve('r2', str(attempt), 'claude', 'review', 'group')
                ledger.started('r2', str(attempt), {'kind': 'executor_invocation'})
                ledger.settle('r2', str(attempt), 'event2', {'category': 'success',
                    'duration_seconds': 1, 'total_cost_usd': 1}, terminated=True)
                new_binding = {**binding, 'nonce': 'second'}
                new_prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
                new_binding['prompt_sha256'] = new_prompt_sha
                report = (f'판정: PASS\n대상 HEAD: {receipt["head"]}\n'
                          f'패치 SHA-256: {diff_sha}\n검토 범위: a.py\n미검토: 없음')
                claim = {'head': receipt['head'], 'patch_sha256': diff_sha,
                         'reviewed_files': ['a.py']}
                clean = {'spawned': 0, 'completed': 0, 'failed': 0, 'killed': {}, 'refused': {}}
                new_events = [*settings_events(new_binding),
                    {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                        {'type': 'tool_use', 'id': 'read-final', 'name': 'Read',
                         'input': {'file_path': str(root / 'a.py')}}]}},
                    {'type': 'user', 'message': {'content': [
                        {'type': 'tool_result', 'tool_use_id': 'read-final', 'content': 'read'}]}},
                    {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                        {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps(claim)}]}},
                    {'type': 'result', 'subtype': 'success', 'is_error': False,
                     'stop_reason': 'end_turn', 'terminal_reason': 'completed',
                     'queued_turn_count': 0, 'permission_denials': [],
                     'subagent_stats': clean, 'result': report, 'total_cost_usd': 1}]
                new_stream = ''.join(json.dumps(x) + '\n' for x in new_events)
                (attempt / 'events.jsonl').write_text(new_stream)
                (attempt / 'events.live.jsonl').write_text(new_stream)
                (attempt / 'facts.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in [
                    {'state': 'starting', 'binding': new_binding},
                    {'state': 'prompt_delivery_started'},
                    {'state': 'prompt_delivered', 'prompt_sha256': new_prompt_sha},
                    {'state': 'closed', 'exit_code': 0}]))
                (attempt / 'pr.diff').write_text(patch_text)
                (attempt / 'prompt.txt').write_text(prompt)
                (attempt / 'report.md').write_text(report)
                finished = review.checkpoint_from_events(new_events, binding=new_binding,
                    base=receipt['base'], files=['a.py'], reservation_id='r2',
                    settlement_event='event2', events_sha256=hashlib.sha256(new_stream.encode()).hexdigest(),
                    repo=root)
                review.replace_private(attempt / 'checkpoint.json', finished)
                finished_receipt = {**receipt, 'completion_verified': True, 'review_passed': True,
                    'verdict': 'PASS', 'shared_reservation_id': 'r2', 'shared_settlement_event': 'event2',
                    'events_sha256': hashlib.sha256(new_stream.encode()).hexdigest(),
                    'checkpoint_sha256': hashlib.sha256((attempt / 'checkpoint.json').read_bytes()).hexdigest()}
                (attempt / 'interpretation.json').write_text(json.dumps({
                    'kind': 'single', 'events_sha256': finished_receipt['events_sha256'],
                    'checkpoint_sha256': finished_receipt['checkpoint_sha256'],
                    'reservation_id': 'r2', 'settlement_event': 'event2', 'completion_verified': True}))
                (attempt / 'receipt.json').write_text(json.dumps(finished_receipt))
            self.assertEqual(tick.inspect(manifest, review, ledger, now=201)['state'], 'DONE')
            ledger.close()


if __name__ == '__main__':
    unittest.main()
