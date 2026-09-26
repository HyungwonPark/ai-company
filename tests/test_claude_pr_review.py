import importlib.util
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_company.shared_calls import CapacityUnavailable, SharedCallLedger


PATH = Path(__file__).resolve().parents[1] / 'scripts/review_pr_with_claude.py'
SPEC = importlib.util.spec_from_file_location('review_pr_with_claude', PATH)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)
HEAD = 'a' * 40
DIFF_SHA = 'b' * 64
PASS_REPORT = (f'판정: PASS\n대상 HEAD: {HEAD}\n패치 SHA-256: {DIFF_SHA}\n'
               '검토 범위: 패치 전체와 변경 파일\n미검토: 없음')


class ClaudePRReviewTests(unittest.TestCase):
    def test_rejected_quota_without_verified_reset_never_releases_shared_account(self):
        for reset in (9999999999, None, 'not-a-time', 0, -1, 1):
            with self.subTest(reset=reset), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cli = root / 'claude'
                cli.write_text('fake-cli')
                relay = root / 'relay'
                relay.write_text('fake-relay')
                ledger_path = root / 'shared.db'
                ledger = SharedCallLedger.initialize(ledger_path, [
                    ('claude', 'review', 'account', 'AVAILABLE', None, None, 0, 0, 0, 0)])
                self.addCleanup(ledger.close)
                pr = {'url': 'https://github.com/example/repo/pull/31', 'headRefOid': HEAD}

                def command(*args):
                    if args == ('git', 'rev-parse', 'HEAD'):
                        return HEAD
                    if args == ('git', 'status', '--porcelain'):
                        return ''
                    if args == ('git', 'rev-parse', '--show-toplevel'):
                        return str(root)
                    if args[0] == 'gh':
                        return json.dumps(pr)
                    if args[-1] == '--version':
                        return '2.1.280'
                    raise AssertionError(args)

                info = {'status': 'rejected'}
                if reset is not None:
                    info['resetsAt'] = reset
                events = [
                    {'type': 'rate_limit_event', 'rate_limit_info': info},
                    {'type': 'result', 'is_error': True, 'terminal_reason': 'api_error',
                     'queued_turn_count': 0, 'duration_ms': 1000,
                     'subagent_stats': {'spawned': 0, 'completed': 0, 'failed': 0,
                                        'killed': {}, 'refused': {}}},
                ]
                def invoke(_cmd, _prompt, directory):
                    (directory / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
                    return 1, events, None
                argv = ['review', '31', '--shared-call-ledger', str(ledger_path),
                        '--credential-ref', 'review', '--quota-group', 'account',
                        '--adoption-receipt', str(root / 'adoption.json')]
                with patch('sys.argv', argv), patch.object(Path, 'home', return_value=root), \
                     patch.object(REVIEW, 'SharedCallLedger', return_value=ledger), \
                     patch.object(REVIEW, 'shared_adoption_verified', return_value=True), \
                     patch.object(REVIEW, 'command', side_effect=command), \
                     patch.object(REVIEW, 'reviewable', return_value=[]), \
                     patch.object(REVIEW.shutil, 'which', return_value=str(cli)), \
                     patch.object(REVIEW, 'CONTROL', relay), \
                     patch.object(REVIEW, 'complete_pr_patch', return_value=('diff --git a/a b/a\n+x\n', 'b'*40, 'b'*40)), \
                     patch.object(REVIEW, 'invoke', side_effect=invoke), \
                     patch.object(REVIEW, 'input_delivery_verified', return_value=True), \
                     patch.object(REVIEW, 'binding_unchanged', return_value=True):
                    self.assertEqual(REVIEW.main(), 1)
                account = ledger.account('claude', 'review', 'account')
                self.assertEqual(account['calls'], 1)
                verified = reset == 9999999999
                self.assertEqual(account['state'], 'COOLDOWN' if verified else 'UNKNOWN')
                directory = root / '.local/state/ai-company/claude-pr-reviews' / f'pr-31-{HEAD}'
                receipt = json.loads((directory / 'receipt.json').read_text())
                self.assertTrue(receipt['provider_quota_rejected'])
                self.assertEqual(receipt['quota_reset_verified'], verified)
                self.assertEqual(json.loads((directory / 'events.jsonl').read_text().splitlines()[0]), events[0])
                reservation = ledger.reservation(receipt['shared_reservation_id'])
                self.assertFalse(ledger.settle(receipt['shared_reservation_id'], str(directory),
                                               receipt['shared_settlement_event'], json.loads(reservation['result']),
                                               terminated=True))
                self.assertEqual(ledger.account('claude', 'review', 'account')['calls'], 1)
                reopened = SharedCallLedger(ledger_path)
                try:
                    self.assertEqual(reopened.account('claude', 'review', 'account')['state'], account['state'])
                    self.assertEqual(reopened.account('claude', 'review', 'account')['calls'], 1)
                finally:
                    reopened.close()
                if not verified:
                    with self.assertRaises(CapacityUnavailable):
                        ledger.reserve('other-worker', 'other-queue', 'claude', 'review', 'account')

    def test_nonfinite_quota_reset_needs_reconciliation(self):
        events = [{'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': float('nan')}},
                  {'type': 'result', 'is_error': True, 'terminal_reason': 'api_error',
                   'queued_turn_count': 0,
                   'subagent_stats': {'spawned': 0, 'completed': 0, 'failed': 0,
                                      'killed': {}, 'refused': {}}}]
        self.assertEqual(len(REVIEW.rejected_quota_events(events)), 1)
        self.assertIsNone(REVIEW.rejected_quota_reset(events))

    def test_quota_resume_requires_bound_terminal_limit_and_stopped_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'attempt'
            directory.mkdir()
            patch_text = 'diff --git a/a b/a\n+new\n'
            diff_sha = hashlib.sha256(patch_text.encode()).hexdigest()
            prompt_text = f'--- PATCH {diff_sha} BEGIN ---\n{patch_text}\n--- PATCH END ---'
            prompt_sha = hashlib.sha256(prompt_text.encode()).hexdigest()
            delivery_binding = {'nonce': 'fixture', 'pr': 29, 'head': HEAD,
                                'diff_sha256': diff_sha, 'prompt_sha256': prompt_sha}
            receipt = {'pr': 29, 'url': 'https://github.com/example/repo/pull/29', 'head': HEAD,
                       'base': 'c' * 40, 'diff_sha256': diff_sha, 'prompt_sha256': prompt_sha,
                       'completion_verified': False, 'input_delivery_verified': True,
                       'binding_unchanged_after_review': True, 'incomplete_reason': None}
            facts = [{'state': 'starting', 'binding': delivery_binding}, {'state': 'prompt_delivery_started'},
                     {'state': 'prompt_delivered', 'prompt_sha256': prompt_sha},
                     {'state': 'closed', 'exit_code': 0}]
            events = [{'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 200}},
                      {'type': 'result', 'is_error': True, 'terminal_reason': 'api_error',
                       'queued_turn_count': 0, 'subagent_stats': {'spawned': 0, 'completed': 0,
                           'failed': 0, 'killed': {}, 'refused': {}}}]
            (directory / 'receipt.json').write_text(json.dumps(receipt))
            (directory / 'facts.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in facts))
            (directory / 'events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
            (directory / 'pr.diff').write_text(patch_text)
            (directory / 'prompt.txt').write_text(prompt_text)
            receipt_hash = hashlib.sha256((directory / 'receipt.json').read_bytes()).hexdigest()
            ledger = SharedCallLedger.initialize(Path(temporary) / 'shared.db', [
                ('claude', 'review', 'quota', 'COOLDOWN', 200, 'quota', 1, 1, 0, 1),
            ], legacy_review_imports=[(receipt_hash, 'quota', 200)], clock=lambda: 200)
            self.addCleanup(ledger.close)
            binding = dict(pr_number=29, pr_url=receipt['url'], head=HEAD, base=receipt['base'],
                           diff_sha256=diff_sha, ledger=ledger, quota_group='quota')
            self.assertFalse(REVIEW.quota_resume_verified(directory, **binding, now=199))
            self.assertTrue(REVIEW.quota_resume_verified(directory, **binding, now=200))
            receipt['shared_reservation_id'] = 'missing'
            receipt['shared_settlement_event'] = 'missing-event'
            (directory / 'receipt.json').write_text(json.dumps(receipt))
            self.assertFalse(REVIEW.quota_resume_verified(directory, **binding, now=200))
            receipt.pop('shared_reservation_id')
            receipt.pop('shared_settlement_event')
            (directory / 'receipt.json').write_text(json.dumps(receipt))
            self.assertFalse(REVIEW.quota_resume_verified(directory, **{**binding, 'head': 'f' * 40}, now=200))
            with patch.object(REVIEW.time, 'time', return_value=200):
                with REVIEW.reserve_attempt(directory, False, resume_quota=True,
                                            quota_binding={key: value for key, value in binding.items()
                                                           if key not in ('ledger', 'quota_group')},
                                            ledger=ledger, quota_group='quota'):
                    self.assertFalse((directory / 'receipt.json').exists())
                    self.assertEqual(len(list(directory.parent.glob('attempt-quota-*'))), 1)
            self.assertTrue(directory.exists())
            events[0]['rate_limit_info']['status'] = 'allowed_warning'
            (directory / 'events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in events))
            (directory / 'receipt.json').write_text(json.dumps(receipt))
            (directory / 'facts.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in facts))
            self.assertFalse(REVIEW.quota_resume_verified(directory, **binding, now=300))

    def test_quota_resume_rejects_missing_settlement_and_unknown_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = SharedCallLedger.initialize(root / 'shared.db', [
                ('claude', 'review', 'quota', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ])
            self.addCleanup(ledger.close)
            events = [{'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 200}},
                      {'type': 'result', 'is_error': True, 'terminal_reason': 'api_error',
                       'queued_turn_count': 0, 'subagent_stats': {'failed': 0, 'killed': {}, 'refused': {}}}]
            self.assertFalse(REVIEW.terminal_children_stopped(events))
            events[-1]['subagent_stats'].update({'spawned': 0, 'completed': 0})
            self.assertEqual(REVIEW.rejected_quota_reset(events), 200)
            self.assertFalse(ledger.legacy_review_imported('a' * 64, 'quota', 200))

    def test_adoption_receipt_binds_reviewed_runner_relay_and_ledger_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / 'shared.db'
            shared.touch()
            receipt = root / 'adoption.json'
            value = {'schema_version': 1, 'shared_call_ledger': str(shared),
                     'adopted_callers': sorted(REVIEW.ADOPTED_CALLERS),
                     'installed_code_commit': 'a' * 40,
                     'review_runner_sha256': hashlib.sha256(PATH.read_bytes()).hexdigest(),
                     'review_relay_sha256': hashlib.sha256(REVIEW.CONTROL.read_bytes()).hexdigest(),
                     'shared_calls_sha256': hashlib.sha256(REVIEW.SHARED_CONTROL.read_bytes()).hexdigest()}
            receipt.write_text(json.dumps(value))
            self.assertTrue(REVIEW.shared_adoption_verified(receipt, shared))
            value['review_runner_sha256'] = '0' * 64
            receipt.write_text(json.dumps(value))
            self.assertFalse(REVIEW.shared_adoption_verified(receipt, shared))

    def test_complete_patch_uses_exact_git_base_and_head_not_truncated_gh_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)

            def git(*args):
                result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                                        text=True, check=True)
                return result.stdout.strip()

            git('init', '-q')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            (repo/'one.py').write_text('before\n')
            git('add', '.')
            git('commit', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            (repo/'one.py').write_bytes(b'after\r\n')
            (repo/'two.py').write_text('second file\n')
            git('add', '.')
            git('commit', '-qm', 'head')
            head = git('rev-parse', 'HEAD')
            original = REVIEW.command

            def command(*args):
                if args[:3] == ('git', 'rev-parse', '--show-toplevel'):
                    return str(repo) + '\n'
                if args[:4] == ('gh', 'repo', 'view', '--json'):
                    return json.dumps({'nameWithOwner': 'example/repo'})
                if args[:2] == ('gh', 'api'):
                    return json.dumps({'base': {'sha': base}, 'head': {'sha': head}})
                if args[:3] == ('gh', 'pr', 'diff'):
                    raise AssertionError('truncated GitHub patch must not be used')
                return original(*args)

            with patch.object(REVIEW, 'command', side_effect=command):
                complete, found_base, merge_base = REVIEW.complete_pr_patch(29, head)
                self.assertIn('diff --git a/one.py b/one.py', complete)
                self.assertIn('diff --git a/two.py b/two.py', complete)
                self.assertIn('+after\r\n', complete)
                self.assertEqual((found_base, merge_base), (base, base))
                with self.assertRaises(RuntimeError):
                    REVIEW.complete_pr_patch(29, 'f' * 40)

    def test_only_exact_open_head_with_green_ci_is_reviewable(self):
        pr = {'state': 'OPEN', 'headRefOid': 'abc', 'statusCheckRollup': [
            {'name': 'unit (Python 3.11)', 'workflowName': 'CI', 'conclusion': 'SUCCESS'},
            {'name': 'unit (Python 3.12)', 'workflowName': 'CI', 'conclusion': 'SUCCESS'}]}
        self.assertEqual(len(REVIEW.reviewable(pr, 'abc')), 2)
        with self.assertRaises(RuntimeError):
            REVIEW.reviewable(pr, 'old')
        with self.assertRaises(RuntimeError):
            REVIEW.reviewable({**pr, 'statusCheckRollup': [
                *pr['statusCheckRollup'], {'name': 'Browser', 'conclusion': None}]}, 'abc')
        with self.assertRaises(RuntimeError):
            REVIEW.reviewable({**pr, 'statusCheckRollup': [
                {'name': 'browser', 'workflowName': 'Console UI', 'conclusion': 'SUCCESS'}]}, 'abc')

    def test_review_tools_are_read_only_and_permitted_in_dont_ask_mode(self):
        args = REVIEW.review_tool_args(Path('/tmp/review-repo'), {'enableWorkflows': True})
        self.assertEqual(args[args.index('--permission-mode') + 1], 'dontAsk')
        self.assertEqual(args[args.index('--tools') + 1], args[args.index('--allowedTools') + 1])
        self.assertEqual(set(args[args.index('--allowedTools') + 1].split(',')),
                         {'Read', 'Grep', 'Glob', 'Workflow', 'Task', 'TaskOutput', 'TaskStop'})
        self.assertIn('--restricted', args)

    def test_applied_settings_and_model_response_are_required(self):
        nonce = 'attempt'
        control = lambda suffix, applied: {'type': 'control_response', 'response': {
            'request_id': nonce + suffix, 'response': {'applied': applied, 'has_errors': False}}}
        events = [control('-before', REVIEW.APPLIED),
                  {'type': 'assistant', 'message': {'model': REVIEW.MODEL, 'content': []}},
                  {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': PASS_REPORT,
                   'stop_reason': 'end_turn', 'terminal_reason': 'completed', 'queued_turn_count': 0,
                   'permission_denials': [],
                   'subagent_stats': {'spawned': 0, 'completed': 0, 'killed': {}, 'refused': {}}},
                  control('-after', REVIEW.APPLIED)]
        def summarize(rows, **kwargs):
            return REVIEW.summarize(rows, nonce, 0, input_verified=True,
                                    head=HEAD, diff_sha256=DIFF_SHA, **kwargs)
        self.assertTrue(summarize(events)['review_passed'])
        self.assertFalse(REVIEW.summarize(events, nonce, 0, head=HEAD, diff_sha256=DIFF_SHA)['review_passed'])
        self.assertFalse(summarize(events[:-1])['review_passed'])
        self.assertFalse(summarize(events, incomplete_reason='timeout')['review_passed'])
        self.assertFalse(summarize([control('-before', {**REVIEW.APPLIED, 'ultracode': False}),
                                    *events[1:]])['review_passed'])
        self.assertFalse(summarize([*events[:2], {**events[2], 'subagent_stats': None},
                                    events[3]])['review_passed'])
        self.assertIsNone(summarize([*events[:2], {**events[2], 'result': '판정: PASS\n판정: REVISE'},
                                     events[3]])['verdict'])
        self.assertIsNone(summarize([*events[:2], {**events[2], 'result': '예: 판정: PASS\n판정: REVISE'},
                                     events[3]])['verdict'])
        self.assertFalse(summarize([*events[:2], {**events[2], 'result': PASS_REPORT.replace(HEAD, 'wrong')},
                                    events[3]])['review_passed'])
        self.assertFalse(summarize([*events[:2], {**events[2], 'result': PASS_REPORT.replace('미검토: 없음', '미검토: tests 파일')},
                                    events[3]])['review_passed'])

    def test_denied_tool_never_counts_as_completion_or_workflow_use(self):
        nonce = 'attempt'
        events = [
            {'type': 'control_response', 'response': {'request_id': nonce + suffix,
                'response': {'applied': REVIEW.APPLIED, 'has_errors': False}}}
            for suffix in ('-before', '-after')]
        events += [
            {'type': 'assistant', 'message': {'model': REVIEW.MODEL, 'content': [
                {'type': 'tool_use', 'id': 'denied', 'name': 'Workflow'},
                {'type': 'tool_use', 'id': 'worked', 'name': 'Read'}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'denied', 'is_error': True},
                {'type': 'tool_result', 'tool_use_id': 'worked'}]}},
            {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': PASS_REPORT,
             'stop_reason': 'end_turn', 'terminal_reason': 'completed', 'queued_turn_count': 0,
             'permission_denials': [{'tool_use_id': 'denied'}],
             'subagent_stats': {'spawned': 0, 'completed': 0, 'killed': {}, 'refused': {}}}]
        summary = REVIEW.summarize(events, nonce, 0, input_verified=True, head=HEAD, diff_sha256=DIFF_SHA)
        self.assertFalse(summary['review_passed'])
        self.assertFalse(summary['workflow_tool_used'])
        self.assertEqual(summary['tools_used'], ['Read'])
        self.assertEqual(summary['permission_denials_count'], 1)
        events[-2]['message']['content'][0]['is_error'] = False
        events[-1]['permission_denials'] = []
        complete = REVIEW.summarize(events, nonce, 0, input_verified=True, head=HEAD, diff_sha256=DIFF_SHA)
        self.assertTrue(complete['review_passed'])
        self.assertTrue(complete['workflow_tool_used'])

    def test_unrelated_or_failed_read_cannot_replace_bound_patch_delivery(self):
        nonce = 'attempt'
        controls = [{'type': 'control_response', 'response': {'request_id': nonce + suffix,
                     'response': {'applied': REVIEW.APPLIED, 'has_errors': False}}}
                    for suffix in ('-before', '-after')]
        result = {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': PASS_REPORT,
                  'stop_reason': 'end_turn', 'terminal_reason': 'completed', 'queued_turn_count': 0,
                  'permission_denials': [],
                  'subagent_stats': {'spawned': 0, 'completed': 0, 'killed': {}, 'refused': {}}}
        for failed in (False, True):
            events = [controls[0], {'type': 'assistant', 'message': {'model': REVIEW.MODEL,
                      'content': [{'type': 'tool_use', 'id': 'read-other', 'name': 'Read',
                                   'input': {'file_path': '/tmp/unrelated.txt'}}]}},
                      {'type': 'user', 'message': {'content': [{'type': 'tool_result',
                       'tool_use_id': 'read-other', 'is_error': failed}]}}, result, controls[1]]
            self.assertFalse(REVIEW.summarize(events, nonce, 0, head=HEAD,
                             diff_sha256=DIFF_SHA)['review_passed'])
        result['subagent_stats'] = {'spawned': 1, 'completed': 1, 'killed': {}, 'refused': {}}
        events[1]['message']['content'][0]['name'] = 'Agent'
        events[2]['message']['content'][0]['is_error'] = False
        self.assertTrue(REVIEW.summarize(events, nonce, 0, input_verified=True,
                        head=HEAD, diff_sha256=DIFF_SHA)['review_passed'])

    def test_binding_must_still_match_after_review(self):
        with patch.object(REVIEW, 'command', side_effect=['abc\n', '', '{"headRefOid":"abc"}']):
            self.assertTrue(REVIEW.binding_unchanged(29, 'abc'))
        with patch.object(REVIEW, 'command', side_effect=['def\n']):
            self.assertFalse(REVIEW.binding_unchanged(29, 'abc'))

    def test_input_delivery_requires_bound_complete_patch_and_closed_relay(self):
        patch_text = 'diff --git a/a.py b/a.py\n+safe\r\n'
        patch_sha = hashlib.sha256(patch_text.encode()).hexdigest()
        prompt = f'검수할 전체 PATCH\n--- PATCH {patch_sha} BEGIN ---\n{patch_text}\n--- PATCH END ---'
        binding = {'nonce': 'one', 'pr': 29, 'head': HEAD,
                   'diff_sha256': hashlib.sha256(patch_text.encode()).hexdigest(),
                   'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
        rows = [{'state': 'starting', 'binding': binding},
                {'state': 'prompt_delivery_started'},
                {'state': 'prompt_delivered', 'prompt_sha256': binding['prompt_sha256']},
                {'state': 'closed', 'exit_code': 0}]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'pr.diff').write_bytes(patch_text.encode())
            (directory/'prompt.txt').write_bytes(prompt.encode())
            facts = directory/'facts.jsonl'
            facts.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
            self.assertTrue(REVIEW.input_delivery_verified(directory, binding, 0))
            self.assertFalse(REVIEW.input_delivery_verified(directory, {**binding, 'head': 'wrong'}, 0))
            facts.write_text('\n'.join(json.dumps(row) for row in rows if row['state'] != 'prompt_delivered')+'\n')
            self.assertFalse(REVIEW.input_delivery_verified(directory, binding, 0))
            facts.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
            (directory/'pr.diff').write_text(patch_text[:12])
            self.assertFalse(REVIEW.input_delivery_verified(directory, binding, 0))

    def test_timeout_keeps_partial_output_and_never_claims_completion(self):
        class Process:
            pid = 12345
            returncode = -15
            stdin = stdout = stderr = None
            count = 0

            def communicate(self, *_args, **_kwargs):
                self.count += 1
                if self.count == 1:
                    raise subprocess.TimeoutExpired('claude', 1)
                return '{"type":"assistant","message":{"model":"claude-opus-5-5"}}\n', 'stopped'

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(REVIEW.subprocess, 'Popen', return_value=Process()), \
                    patch.object(REVIEW.os, 'killpg'):
                code, events, reason = REVIEW.invoke(['claude'], '', Path(temporary), timeout_seconds=1)
            self.assertEqual(reason, 'timeout')
            self.assertEqual(code, -15)
            self.assertEqual(len(events), 1)
            self.assertTrue((Path(temporary)/'events.jsonl').exists())
            self.assertFalse(REVIEW.summarize(events, 'attempt', code, reason)['review_passed'])

    def test_signal_stops_child_group_and_keeps_output(self):
        class Process:
            pid = 12345
            returncode = -15
            stdin = stdout = stderr = None
            count = 0

            def communicate(self, *_args, **_kwargs):
                self.count += 1
                if self.count == 1:
                    signal.raise_signal(signal.SIGHUP)
                if self.count == 2:
                    signal.raise_signal(signal.SIGHUP)
                return '', 'interrupted'

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(REVIEW.subprocess, 'Popen', return_value=Process()), \
                    patch.object(REVIEW.os, 'killpg') as stop:
                _, _, reason = REVIEW.invoke(['claude'], '', Path(temporary), timeout_seconds=1)
            self.assertEqual(reason, 'interrupted')
            stop.assert_called_once_with(12345, signal.SIGTERM)
            self.assertEqual((Path(temporary)/'stderr.txt').read_text(), 'interrupted')

    def test_signal_during_cleanup_preserves_completed_output(self):
        class Process:
            pid = 12345
            returncode = 0
            stdin = stdout = stderr = None

            def communicate(self, *_args, **_kwargs):
                return '{"type":"result"}\n', ''

        native_mask = signal.pthread_sigmask
        calls = 0

        def mask(how, numbers):
            nonlocal calls
            calls += 1
            if calls == 3:
                signal.raise_signal(signal.SIGHUP)
            return native_mask(how, numbers)

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(REVIEW.subprocess, 'Popen', return_value=Process()), \
                    patch.object(REVIEW.signal, 'pthread_sigmask', side_effect=mask):
                _, events, reason = REVIEW.invoke(['claude'], '', Path(temporary), timeout_seconds=1)
            self.assertEqual(reason, 'interrupted')
            self.assertEqual(len(events), 1)
            self.assertTrue((Path(temporary)/'events.jsonl').exists())

    def test_killed_child_with_open_pipes_keeps_partial_evidence(self):
        class Pipe:
            def close(self):
                pass

        class Process:
            pid = 12345
            returncode = -9
            stdin = None
            stdout = stderr = Pipe()

            def communicate(self, *_args, **_kwargs):
                raise subprocess.TimeoutExpired('claude', 1, output=b'{"type":"assistant"}\n', stderr=b'partial')

            def wait(self, **_kwargs):
                return -9

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(REVIEW.subprocess, 'Popen', return_value=Process()), \
                    patch.object(REVIEW.os, 'killpg') as stop:
                code, events, reason = REVIEW.invoke(['claude'], '', Path(temporary), timeout_seconds=1)
            self.assertEqual(code, -9)
            self.assertEqual(reason, 'kill_timeout')
            self.assertEqual(len(events), 1)
            self.assertEqual(stop.call_count, 2)
            self.assertEqual((Path(temporary)/'stderr.txt').read_text(), 'partial')

    def test_only_closed_attempt_without_prompt_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'review'
            with REVIEW.reserve_attempt(directory, False):
                pass
            with self.assertRaises(RuntimeError):
                with REVIEW.reserve_attempt(directory, True):
                    pass
            (directory/'facts.jsonl').write_text(json.dumps({'state':'closed'})+'\n')
            with REVIEW.reserve_attempt(directory, True):
                pass
            self.assertTrue(directory.exists())
            self.assertEqual(len(list(Path(temporary).glob('review-unstarted-*'))), 1)
            (directory/'facts.jsonl').write_text('\n'.join(json.dumps(row) for row in [
                {'state':'prompt_delivery_started'}, {'state':'closed'}])+'\n')
            with self.assertRaises(RuntimeError):
                with REVIEW.reserve_attempt(directory, True):
                    pass

    def test_concurrent_retries_cannot_move_new_active_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'pr-29-abc'
            directory.mkdir()
            (directory/'facts.jsonl').write_text('{"state":"closed"}\n')
            entered = threading.Event()
            release = threading.Event()
            original = Path.read_text
            results = []

            def delayed(path, *args, **kwargs):
                if path == directory/'facts.jsonl' and threading.current_thread().name == 'first':
                    entered.set()
                    self.assertTrue(release.wait(5))
                return original(path, *args, **kwargs)

            def retry():
                try:
                    with REVIEW.reserve_attempt(directory, True):
                        if threading.current_thread().name == 'first':
                            (directory/'facts.jsonl').write_text('{"state":"prompt_delivery_started"}\n')
                            (directory/'active-first').touch()
                    results.append('granted')
                except RuntimeError:
                    results.append('refused')

            with patch.object(Path, 'read_text', delayed):
                first = threading.Thread(target=retry, name='first')
                second = threading.Thread(target=retry, name='second')
                first.start()
                self.assertTrue(entered.wait(5))
                second.start()
                second.join(5)
                release.set()
                first.join(5)
            self.assertEqual(sorted(results), ['granted', 'refused'])
            self.assertTrue((directory/'active-first').exists())
            self.assertFalse(any((path/'active-first').exists()
                                 for path in directory.parent.glob('pr-29-abc-unstarted-*')))

    def test_first_reservation_and_retry_race_only_grants_one_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'pr-29-abc'
            start = threading.Barrier(2)
            results = []

            def reserve(retry):
                start.wait(5)
                try:
                    with REVIEW.reserve_attempt(directory, retry):
                        pass
                    results.append('granted')
                except RuntimeError:
                    results.append('refused')

            threads = [threading.Thread(target=reserve, args=(retry,)) for retry in (False, True)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertEqual(sorted(results), ['granted', 'refused'])

    def test_closed_relay_cannot_be_retried_while_first_runner_writes_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'review'
            closed = threading.Event()
            finish = threading.Event()

            def first():
                with REVIEW.reserve_attempt(directory, False):
                    (directory/'facts.jsonl').write_text('{"state":"closed"}\n')
                    closed.set()
                    self.assertTrue(finish.wait(5))
                    REVIEW.write_private(directory/'report.md', 'first runner report')

            worker = threading.Thread(target=first)
            worker.start()
            self.assertTrue(closed.wait(5))
            with self.assertRaises(RuntimeError):
                with REVIEW.reserve_attempt(directory, True):
                    pass
            finish.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual((directory/'report.md').read_text(), 'first runner report')
            with REVIEW.reserve_attempt(directory, True):
                self.assertFalse((directory/'report.md').exists())
            archives = list(directory.parent.glob('review-unstarted-*'))
            self.assertEqual(len(archives), 1)
            self.assertEqual((archives[0]/'report.md').read_text(), 'first runner report')


if __name__ == '__main__':
    unittest.main()
