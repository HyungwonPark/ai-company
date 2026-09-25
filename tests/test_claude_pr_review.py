import importlib.util
from pathlib import Path
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
                   'subagent_stats': {'spawned': 0, 'completed': 0}},
                  control('-after', REVIEW.APPLIED)]
        self.assertTrue(REVIEW.summarize(events, nonce, 0)['review_passed'])
        self.assertFalse(REVIEW.summarize(events[:-1], nonce, 0)['review_passed'])
        self.assertFalse(REVIEW.summarize(events, nonce, 0, timed_out=True)['review_passed'])
        self.assertFalse(REVIEW.summarize([control('-before', {**REVIEW.APPLIED, 'ultracode': False}),
                                           *events[1:]], nonce, 0)['review_passed'])

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
                code, events, timed_out = REVIEW.invoke(['claude'], '', Path(temporary), timeout_seconds=1)
            self.assertTrue(timed_out)
            self.assertEqual(code, -15)
            self.assertEqual(len(events), 1)
            self.assertTrue((Path(temporary)/'events.jsonl').exists())
            self.assertFalse(REVIEW.summarize(events, 'attempt', code, timed_out)['review_passed'])


if __name__ == '__main__':
    unittest.main()
