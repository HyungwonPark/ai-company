"""Reproduce confirmation of an unconfirmed plan after server version changes.

Run from the PR #27 checkout with its dependencies installed:
    PYTHONPATH=src:. python /path/to/repro_review_version.py

Uses the original fixture and temporary SQLite/Git data; changes no product files.
"""

from tests.test_pm_requirements import PMRequirementsTests
from ai_company.management import ManagementStore


def main():
    test = PMRequirementsTests()
    original_guidance = ManagementStore.PM_GUIDANCE_VERSION
    original_review = ManagementStore.PLAN_REVIEW_VERSION
    try:
        test.setUp()
        plan = test.plan()
        assert plan["status"] == "proposed"
        assert test.h.worker.store.run_records() == []
        print("before", plan["status"], plan["pm_guidance_version"], plan["plan_review_version"])

        ManagementStore.PM_GUIDANCE_VERSION = "pm-requirements-v3"
        ManagementStore.PLAN_REVIEW_VERSION = "plan-content-review-v2"
        print("changed_server_versions", ManagementStore.PM_GUIDANCE_VERSION,
              ManagementStore.PLAN_REVIEW_VERSION)
        print("request_is_current", test.h.worker.store.request_is_current(plan["request_id"]))

        result = test.confirm(plan)
        print("confirmation", result["plan"]["status"], result["run"]["state"],
              "runs", len(test.h.worker.store.run_records()))
    finally:
        ManagementStore.PM_GUIDANCE_VERSION = original_guidance
        ManagementStore.PLAN_REVIEW_VERSION = original_review
        test.doCleanups()


if __name__ == "__main__":
    main()
