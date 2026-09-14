"""Contract tests for the public role-state summary interface."""

from types import MappingProxyType
import unittest

from ai_company.pilot_status import summarize_role_states


class SummarizeRoleStatesTests(unittest.TestCase):
    def assert_summary(self, states, expected):
        actual = summarize_role_states(states)
        self.assertIsInstance(actual, dict)
        self.assertEqual(actual, expected)
        for key, value in actual.items():
            with self.subTest(count=key):
                self.assertIs(type(value), int)
        self.assertEqual(actual["total"], len(states))
        self.assertEqual(
            actual["completed"] + actual["waiting"]
            + actual["blocked"] + actual["active"],
            actual["total"],
        )

    def test_empty_input_has_exactly_five_zero_counts(self):
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

    def test_waiting_prefix_accepts_empty_known_and_unfamiliar_suffixes(self):
        for status in (
            "WAITING_", "WAITING_RETRY", "WAITING_REVIEW", "WAITING_QUOTA",
            "WAITING_UNFAMILIAR_FUTURE_STATE", "WAITING_BLOCKED", "WAITING_DEMO_READY",
        ):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 0, "waiting": 1, "blocked": 0, "active": 0},
                )

    def test_remaining_strings_are_active(self):
        for status in (
            "RUNNING", "UNKNOWN_FUTURE_STATE", "",
            "contribution_ready", "merge_ready", "demo_ready",
            "blocked", "no_eligible_agent", "needs_reconciliation",
            "waiting_", "waiting_retry", "Waiting_REVIEW",
            "WAITING", "NOT_WAITING_RETRY", "prefix WAITING_REVIEW suffix",
            "DEMO_READY_EXTRA", "BLOCKED_EXTRA",
        ):
            with self.subTest(status=status):
                self.assert_summary(
                    {"role": status},
                    {"total": 1, "completed": 0, "waiting": 0, "blocked": 0, "active": 1},
                )

    def test_mixed_categories_and_ordinary_mapping_is_unchanged(self):
        states = {
            "author": "CONTRIBUTION_READY",
            "integrator": "MERGE_READY",
            "simulator": "DEMO_READY",
            "retry": "WAITING_RETRY",
            "reviewer": "WAITING_REVIEW",
            "future": "WAITING_UNFAMILIAR_FUTURE_STATE",
            "guard": "BLOCKED",
            "dispatcher": "NO_ELIGIBLE_AGENT",
            "recovery": "NEEDS_RECONCILIATION",
            "worker": "RUNNING",
            "unknown": "UNKNOWN_FUTURE_STATE",
            "empty": "",
            "lowercase": "demo_ready",
        }
        before = states.copy()
        before_items = list(states.items())
        self.assert_summary(
            states,
            {"total": 13, "completed": 3, "waiting": 3, "blocked": 3, "active": 4},
        )
        self.assertEqual(states, before)
        self.assertEqual(list(states.items()), before_items)

    def test_repeated_statuses_count_each_distinct_role(self):
        self.assert_summary(
            {
                "completed_a": "MERGE_READY", "completed_b": "MERGE_READY",
                "waiting_a": "WAITING_RETRY", "waiting_b": "WAITING_RETRY",
                "blocked_a": "BLOCKED", "blocked_b": "BLOCKED",
                "active_a": "RUNNING", "active_b": "RUNNING",
            },
            {"total": 8, "completed": 2, "waiting": 2, "blocked": 2, "active": 2},
        )

    def test_read_only_mapping_is_accepted(self):
        states = {
            "author": "CONTRIBUTION_READY", "reviewer": "WAITING_REVIEW",
            "guard": "BLOCKED", "worker": "RUNNING",
        }
        before = states.copy()
        self.assert_summary(
            MappingProxyType(states),
            {"total": 4, "completed": 1, "waiting": 1, "blocked": 1, "active": 1},
        )
        self.assertEqual(states, before)


if __name__ == "__main__":
    unittest.main()
