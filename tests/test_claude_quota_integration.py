"""Timer → real runner.main → temporary shared ledger → next timer inspection."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_claude_quota_checkpoint import review, tick


class QuotaIntegrationTests(unittest.TestCase):
    def scenario(self, outcomes, *, capacity_race=False, changed_target=False,
                 archive_crash=False, orphan_reservation=False, stale_mtime=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            repo.mkdir()
            for name in ('a.py', 'b.py'):
                (repo / name).write_text('value = 1\n')
            cli = root / 'claude'
            cli.write_text('mock CLI, never executed')
            head, base = 'a' * 40, 'b' * 40
            url = 'https://example.test/pull/35'
            source = 'diff --git a/a.py b/a.py\n+a\ndiff --git a/b.py b/b.py\n+b\n'
            diff_sha = hashlib.sha256(source.encode()).hexdigest()
            ledger_path = root / 'calls.sqlite'
            review.SharedCallLedger.initialize(ledger_path, [
                ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)]).close()
            attempt = root / '.local/state/ai-company/claude-pr-reviews' / ('pr-35-' + head)
            manifest = {'pr': 35, 'pr_url': url, 'head': head, 'base': base,
                        'diff_sha256': diff_sha, 'attempt_dir': str(attempt),
                        'checkout': str(repo), 'ledger': str(ledger_path),
                        'credential_ref': 'review', 'quota_group': 'group',
                        'adoption_receipt': str(root / 'adoption.json')}
            remote = {'head': head, 'base': base, 'url': url, 'patch': source}
            calls = []
            binding_checks = []

            def command(*argv):
                if argv == ('git', 'rev-parse', 'HEAD'):
                    return remote['head']
                if argv == ('git', 'status', '--porcelain'):
                    return ''
                if argv == ('git', 'rev-parse', '--show-toplevel'):
                    return str(repo)
                if argv == (str(cli), '--version'):
                    return '2.1.283'
                if argv[:3] == ('gh', 'pr', 'view'):
                    return json.dumps({'url': remote['url'], 'headRefOid': remote['head']})
                if argv[:3] == ('gh', 'repo', 'view'):
                    return json.dumps({'nameWithOwner': 'example/review'})
                if argv[:2] == ('gh', 'api'):
                    binding_checks.append(True)
                    observed_base = ('c' * 40 if changed_target == 'late'
                                     and len(binding_checks) == 3 else remote['base'])
                    return json.dumps({'base': {'sha': observed_base}})
                raise AssertionError(argv)

            def invoke(_cmd, _input, directory):
                index = len(calls)
                calls.append(index)
                outcome = outcomes[index]
                config = json.loads((directory / 'config.json').read_text())
                binding = config['binding']
                now = time.time()
                read_file = 'a.py' if index == 0 else 'b.py'
                settings = {'applied': review.APPLIED, 'has_errors': False}
                events = [
                    {'type': 'control_response', 'response': {'request_id': binding['nonce'] + '-before',
                                                           'response': settings}},
                    {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                        {'type': 'tool_use', 'id': 'read', 'name': 'Read',
                         'input': {'file_path': str(repo / read_file)}}]}},
                    {'type': 'user', 'message': {'content': [
                        {'type': 'tool_result', 'tool_use_id': 'read', 'content': 'read'}]}},
                    {'type': 'assistant', 'message': {'model': review.MODEL, 'content': [
                        {'type': 'text', 'text': 'REVIEW_PROGRESS_JSON: ' + json.dumps({
                            'head': head, 'patch_sha256': diff_sha,
                            'reviewed_files': [read_file],
                            **({'findings': [{'file': 'a.py', 'evidence': 'line 1'}]}
                               if index == 0 and outcomes[-1] == 'revise' else {})})}]}},
                ]
                terminal = {'type': 'result', 'session_id': f'session-{index}',
                            'is_error': outcome == 'quota', 'queued_turn_count': 0,
                            'subagent_stats': {'spawned': 0, 'completed': 0, 'failed': 0,
                                               'killed': {}, 'refused': {}},
                            'total_cost_usd': 1, 'duration_ms': 1000,
                            'permission_denials': []}
                if outcome == 'quota':
                    events.append({'type': 'rate_limit_event', 'rate_limit_info': {
                        'status': 'rejected', 'resetsAt': now - 1}})
                    terminal.update(terminal_reason='api_error', api_error_status=429)
                else:
                    verdict = 'REVISE' if outcome == 'revise' else 'PASS'
                    terminal.update(subtype='success', terminal_reason='completed',
                        stop_reason='end_turn', result=(f'판정: {verdict}\n대상 HEAD: {head}\n'
                        f'패치 SHA-256: {diff_sha}\n검토 범위: a.py, b.py\n미검토: 없음'))
                events += [terminal, {'type': 'control_response', 'response': {
                    'request_id': binding['nonce'] + '-after', 'response': settings}}]
                stream = ''.join(json.dumps(event) + '\n' for event in events)
                (directory / 'events.jsonl').write_text(stream)
                (directory / 'events.live.jsonl').write_text(stream)
                code = int(outcome == 'quota')
                facts = [{'state': 'starting', 'binding': binding, 'at': now - 3},
                         {'state': 'prompt_delivery_started'},
                         {'state': 'prompt_delivered', 'prompt_sha256': binding['prompt_sha256']},
                         {'state': 'closed', 'exit_code': code, 'at': now}]
                (directory / 'facts.jsonl').write_text(''.join(json.dumps(fact) + '\n' for fact in facts))
                return code, events, None

            initial = ['runner.py', '35', '--shared-call-ledger', str(ledger_path),
                       '--credential-ref', 'review', '--quota-group', 'group',
                       '--adoption-receipt', manifest['adoption_receipt']]
            with (patch.object(review, 'command', side_effect=command),
                  patch.object(review, 'shared_adoption_verified', return_value=True),
                  patch.object(review, 'reviewable', return_value=[]),
                  patch.object(review.shutil, 'which', return_value=str(cli)),
                  patch.object(review, 'complete_pr_patch',
                               side_effect=lambda *_: (remote['patch'], remote['base'], remote['base'])),
                  patch.object(review, 'invoke', side_effect=invoke),
                  patch.object(Path, 'home', return_value=root)):
                with patch.object(sys, 'argv', initial), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(review.main(), 1)
                ledger = review.SharedCallLedger(ledger_path)
                try:
                    status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                    self.assertEqual(status['state'], 'READY')
                    if changed_target:
                        command_line = tick.resume_command(Path('runner.py'), manifest, status)
                        if changed_target == 'head':
                            remote['head'] = 'c' * 40
                        elif changed_target == 'base':
                            remote['base'] = 'c' * 40
                        elif changed_target == 'patch':
                            remote['patch'] += '+new\n'
                        elif changed_target == 'url':
                            remote['url'] = 'https://example.test/pull/other'
                        with patch.object(sys, 'argv', ['runner.py', *command_line[4:]]):
                            with self.assertRaises(RuntimeError):
                                review.main()
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(ledger.account('claude', 'review', 'group')['calls'], 1)
                        if changed_target == 'late':
                            self.assertEqual(tick.inspect(manifest, review, ledger,
                                             now=time.time() + 1)['state'], 'READY')
                        return
                    if capacity_race:
                        ledger.reserve('other', 'other-owner', 'claude', 'review', 'group')
                        command_line = tick.resume_command(Path('runner.py'), manifest, status)
                        with patch.object(sys, 'argv', ['runner.py', *command_line[4:]]):
                            with self.assertRaises(RuntimeError):
                                review.main()
                        self.assertEqual(len(calls), 1)
                        ledger.cancel_unstarted('other', 'other-owner',
                            evidence='queue_unclaimed_no_guard_no_process')
                        status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                        self.assertEqual(status['state'], 'READY')
                        self.assertIn('-quota-', status['attempt'])
                    if archive_crash:
                        with self.assertRaisesRegex(RuntimeError, 'synthetic stop'):
                            with review.reserve_attempt(attempt, False, resume_quota=True,
                                    expected_previous=Path(status['attempt']),
                                    quota_binding={'pr_number': 35, 'pr_url': url,
                                        'head': head, 'base': base, 'diff_sha256': diff_sha,
                                        'expected_reservation_id': status['reservation_id']},
                                    ledger=ledger, quota_group='group'):
                                raise RuntimeError('synthetic stop after archive')
                        status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                        self.assertEqual(status['state'], 'READY')
                        self.assertIn('-quota-', status['attempt'])
                    if orphan_reservation:
                        archived = attempt.with_name(attempt.name + '-quota-orphan')
                        attempt.rename(archived)
                        attempt.mkdir()
                        (attempt / 'pr.diff').write_text(source)
                        ledger.reserve('orphan', str(attempt), 'claude', 'review', 'group')
                        self.assertTrue(tick.recover_unstarted(manifest, ledger))
                        self.assertEqual(ledger.reservation('orphan')['state'], 'CANCELLED')
                        status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                        self.assertEqual(status['state'], 'READY')
                    for index in range(1, len(outcomes)):
                        command_line = tick.resume_command(Path('runner.py'), manifest, status)
                        with patch.object(sys, 'argv', ['runner.py', *command_line[4:]]), \
                             contextlib.redirect_stdout(io.StringIO()):
                            review.main()
                        status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                        self.assertEqual(status['state'],
                                         'WAITING_QUOTA' if outcomes[index] == 'quota'
                                         and status.get('next_check_at', 0) > time.time() + 1 else
                                         'READY' if outcomes[index] == 'quota' else 'DONE')
                        if stale_mtime and index == 1:
                            older = next(attempt.parent.glob(attempt.name + '-quota-*'))
                            newer = attempt.with_name(attempt.name + '-quota-second')
                            attempt.rename(newer)
                            os.utime(older, (time.time() + 3600, time.time() + 3600))
                            status = tick.inspect(manifest, review, ledger, now=time.time() + 1)
                            self.assertEqual(status['state'], 'READY')
                            self.assertEqual(status['attempt'], str(newer))
                    checkpoint = review.previous_checkpoint(attempt, head=head, base=base,
                        diff_sha256=diff_sha, files=['a.py', 'b.py'], repo=repo)
                    self.assertEqual(len(checkpoint['lineage']), len(outcomes))
                    self.assertEqual(ledger.account('claude', 'review', 'group')['calls'], len(outcomes))
                    if outcomes[-1] == 'revise':
                        self.assertEqual(checkpoint['findings'][0]['status'], 'open')
                finally:
                    ledger.close()

    def test_quota_then_pass_replays_saved_checkpoint(self):
        self.scenario(['quota', 'pass'])

    def test_quota_then_quota_then_revise_preserves_lineage_and_finding(self):
        self.scenario(['quota', 'quota', 'revise'])

    def test_changed_target_never_reaches_invocation(self):
        for changed in ('head', 'base', 'patch', 'url', 'late'):
            with self.subTest(changed=changed):
                self.scenario(['quota', 'pass'], changed_target=changed)

    def test_capacity_race_archives_attempt_and_resumes_original_owner(self):
        self.scenario(['quota', 'pass'], capacity_race=True)

    def test_archive_crash_resumes_original_settled_attempt(self):
        self.scenario(['quota', 'pass'], archive_crash=True)

    def test_cancelled_unstarted_attempt_preserves_quota_predecessor(self):
        self.scenario(['quota', 'pass'], orphan_reservation=True)

    def test_old_archive_mtime_cannot_replace_latest_lineage(self):
        self.scenario(['quota', 'quota', 'pass'], stale_mtime=True)


if __name__ == '__main__':
    unittest.main()
