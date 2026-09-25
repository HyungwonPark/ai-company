import importlib.util
from pathlib import Path
import unittest


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
                  {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'PASS'},
                  control('-after', REVIEW.APPLIED)]
        self.assertTrue(REVIEW.summarize(events, nonce, 0)['verified'])
        self.assertFalse(REVIEW.summarize(events[:-1], nonce, 0)['verified'])
        self.assertFalse(REVIEW.summarize([control('-before', {**REVIEW.APPLIED, 'ultracode': False}),
                                           *events[1:]], nonce, 0)['verified'])


if __name__ == '__main__':
    unittest.main()
