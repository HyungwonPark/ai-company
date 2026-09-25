import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'scripts/review_pr_with_claude.py'
SPEC = importlib.util.spec_from_file_location('review_pr_with_claude', PATH)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class ClaudePRReviewTests(unittest.TestCase):
    def test_only_exact_open_head_with_green_ci_is_reviewable(self):
        pr = {'state': 'OPEN', 'headRefOid': 'abc', 'statusCheckRollup': [
            {'name': 'Python', 'conclusion': 'SUCCESS'}, {'name': 'optional', 'conclusion': 'SKIPPED'}]}
        self.assertEqual(len(REVIEW.reviewable(pr, 'abc')), 2)
        with self.assertRaises(RuntimeError):
            REVIEW.reviewable(pr, 'old')
        with self.assertRaises(RuntimeError):
            REVIEW.reviewable({**pr, 'statusCheckRollup': [
                {'name': 'Python', 'conclusion': 'SUCCESS'}, {'name': 'Browser', 'conclusion': None}]}, 'abc')

    def test_applied_settings_and_model_response_are_required(self):
        nonce = 'attempt'
        control = lambda suffix, applied: {'type': 'control_response', 'response': {
            'request_id': nonce + suffix, 'response': {'applied': applied, 'has_errors': False}}}
        events = [control('-before', REVIEW.APPLIED),
                  {'type': 'assistant', 'message': {'model': REVIEW.MODEL, 'content': []}},
                  {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '판정: PASS',
                   'stop_reason': 'end_turn', 'terminal_reason': 'completed', 'queued_turn_count': 0,
                   'subagent_stats': {'spawned': 0, 'completed': 0, 'killed': {}, 'refused': {}}},
                  control('-after', REVIEW.APPLIED)]
        self.assertTrue(REVIEW.summarize(events, nonce, 0)['review_passed'])
        self.assertFalse(REVIEW.summarize(events[:-1], nonce, 0)['review_passed'])
        self.assertFalse(REVIEW.summarize(events, nonce, 0, incomplete_reason='timeout')['review_passed'])
        self.assertFalse(REVIEW.summarize([control('-before', {**REVIEW.APPLIED, 'ultracode': False}),
                                           *events[1:]], nonce, 0)['review_passed'])
        self.assertFalse(REVIEW.summarize([*events[:2], {**events[2], 'subagent_stats': None},
                                           events[3]], nonce, 0)['review_passed'])
        self.assertIsNone(REVIEW.summarize([*events[:2], {**events[2], 'result': '판정: PASS\n판정: REVISE'},
                                            events[3]], nonce, 0)['verdict'])
        self.assertIsNone(REVIEW.summarize([*events[:2], {**events[2], 'result': '예: 판정: PASS\n판정: REVISE'},
                                            events[3]], nonce, 0)['verdict'])

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
            REVIEW.reserve_attempt(directory, False)
            with self.assertRaises(RuntimeError):
                REVIEW.reserve_attempt(directory, True)
            (directory/'facts.jsonl').write_text(json.dumps({'state':'closed'})+'\n')
            REVIEW.reserve_attempt(directory, True)
            self.assertTrue(directory.exists())
            self.assertEqual(len(list(Path(temporary).glob('review-unstarted-*'))), 1)
            (directory/'facts.jsonl').write_text('\n'.join(json.dumps(row) for row in [
                {'state':'prompt_delivery_started'}, {'state':'closed'}])+'\n')
            with self.assertRaises(RuntimeError):
                REVIEW.reserve_attempt(directory, True)


if __name__ == '__main__':
    unittest.main()
