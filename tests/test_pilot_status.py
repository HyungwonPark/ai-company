"""Contract tests for the shared public role-state summary."""
import unittest
from types import MappingProxyType

from ai_company.pilot_status import summarize_role_states


class SummarizeRoleStatesTests(unittest.TestCase):
    def assert_summary(self, states, expected):
        result = summarize_role_states(states)
        self.assertIsInstance(result, dict)
        self.assertEqual(result, expected)
        for key, count in result.items():
            with self.subTest(count_key=key):
                self.assertIs(type(count), int)
        self.assertEqual(result["total"], len(states))
        self.assertEqual(
            result["completed"] + result["waiting"]
            + result["blocked"] + result["active"],
            result["total"],
        )

    def test_empty_input(self):
        self.assert_summary(
            {},
            {"total": 0, "completed": 0, "waiting": 0, "blocked": 0, "active": 0},
        )

    def test_each_completed_status(self):
        for status in ("CONTRIBUTION_READY", "MERGE_READY", "DEMO_READY"):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 1, "waiting": 0, "blocked": 0, "active": 0},
                )

    def test_each_blocked_status(self):
        for status in ("BLOCKED", "NO_ELIGIBLE_AGENT", "NEEDS_RECONCILIATION"):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 0, "waiting": 0, "blocked": 1, "active": 0},
                )

    def test_waiting_prefix_including_empty_and_unfamiliar_suffixes(self):
        for status in (
            "WAITING_", "WAITING_RETRY", "WAITING_REVIEW", "WAITING_APPROVAL",
            "WAITING_UNFAMILIAR_FUTURE_SUFFIX", "WAITING_BLOCKED", "WAITING_DEMO_READY",
        ):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 0, "waiting": 1, "blocked": 0, "active": 0},
                )

    def test_other_strings_are_active(self):
        for status in (
            "RUNNING", "UNKNOWN_FUTURE_STATUS", "",
            "contribution_ready", "merge_ready", "demo_ready",
            "blocked", "no_eligible_agent", "needs_reconciliation",
            "waiting_", "waiting_review", "WAITING", "NOT_WAITING_REVIEW",
            "PREFIX_WAITING_", "CONTRIBUTION_READY_EXTRA", "BLOCKED_EXTRA",
        ):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 0, "waiting": 0, "blocked": 0, "active": 1},
                )

    def test_mixed_categories_and_repeated_statuses_count_each_role(self):
        self.assert_summary(
            {
                "author": "CONTRIBUTION_READY",
                "integrator": "MERGE_READY",
                "demo": "DEMO_READY",
                "second_author": "CONTRIBUTION_READY",
                "reviewer": "WAITING_REVIEW",
                "second_reviewer": "WAITING_REVIEW",
                "scheduler": "WAITING_",
                "worker": "BLOCKED",
                "second_worker": "BLOCKED",
                "allocator": "NO_ELIGIBLE_AGENT",
                "reconciler": "NEEDS_RECONCILIATION",
                "developer": "RUNNING",
                "second_developer": "RUNNING",
                "observer": "UNKNOWN_FUTURE_STATUS",
            },
            {"total": 14, "completed": 4, "waiting": 3, "blocked": 4, "active": 3},
        )

    def test_ordinary_mapping_is_unchanged(self):
        states = {
            "author": "DEMO_READY",
            "reviewer": "WAITING_REVIEW",
            "worker": "BLOCKED",
            "observer": "UNKNOWN_FUTURE_STATUS",
        }
        before = states.copy()
        before_items = list(states.items())
        self.assert_summary(
            states,
            {"total": 4, "completed": 1, "waiting": 1, "blocked": 1, "active": 1},
        )
        self.assertEqual(states, before)
        self.assertEqual(list(states.items()), before_items)

    def test_read_only_mapping_is_accepted_and_unchanged(self):
        backing = {
            "author": "CONTRIBUTION_READY",
            "reviewer": "WAITING_UNFAMILIAR_FUTURE_SUFFIX",
            "worker": "NEEDS_RECONCILIATION",
            "observer": "",
        }
        before = backing.copy()
        states = MappingProxyType(backing)
        self.assert_summary(
            states,
            {"total": 4, "completed": 1, "waiting": 1, "blocked": 1, "active": 1},
        )
        self.assertEqual(backing, before)
        self.assertEqual(states, before)


if __name__ == "__main__":
    unittest.main()
