"""Pinned skill choices travel with one reviewed plan into the assigned role."""

from hashlib import sha256
import unittest

from ai_company import skill_catalog
from ai_company.management import ManagementError
from tests import test_automation as fixture


class SkillAutomationTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.CoordinatorTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.skill = self.h.root / "reviewed-skill"
        self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: 화면 검수\n---\n키보드와 모바일 폭을 검사합니다.\n")
        (self.skill / "LICENSE").write_text("MIT License\n")
        url = "https://github.com/example/skills/blob/" + "a" * 40 + "/review/SKILL.md"
        hashes = {name: sha256((self.skill / name).read_bytes()).hexdigest()
                  for name in ("SKILL.md", "LICENSE")}
        entry = {"root": str(self.skill), "skill_id": "review", "source_url": url,
                 "source_ref": "a" * 40, "expected_sha256": skill_catalog._bundle_hash(url, "a" * 40, hashes),
                 "license_path": "LICENSE", "license_id": "MIT", "redistribution": "internal_only",
                 "providers": ("codex", "claude"), "runners": ("session_cli",),
                 "capabilities": ("accessibility",)}
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (entry,)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()

    def plan(self):
        for _ in range(6):
            self.h.worker.run_once()
            plans = self.h.worker.store.overview(self.h.project["id"])["plans"]
            if plans and plans[-1]["status"] == "proposed":
                return plans[-1]
        self.fail(str(plans))

    def confirm(self, plan):
        return self.h.worker.store.confirm_plan(self.h.project["id"], plan["id"], {
            "plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
            "idempotency_key": "skill-confirm-001"})

    def test_reviewed_document_is_bound_to_plan_and_role_task(self):
        plan = self.plan()
        selection = plan["content"]["skill_selection"]
        self.assertEqual(selection["status"], "selected")
        self.assertEqual(selection["roles"]["impl"][0]["bundle_sha256"],
                         self.h.config.skill_catalog[0]["expected_sha256"])
        self.assertEqual(selection["roles"]["test"], [])
        run = self.confirm(plan)["run"]
        self.h.worker.run_once()
        record = self.h.worker.store.get_run(run["id"])
        impl = record["roles"]["impl"]
        self.assertEqual(impl["skill_delivery"]["selection_digest"], selection["digest"])
        self.assertEqual(impl["skill_delivery"]["delivery"], "included_in_submitted_task")
        state = self.h.worker.dispatcher.get(impl["task_id"])
        self.assertIn("키보드와 모바일 폭", state["specification"]["plan"]["role_skill_guidance"])
        self.assertEqual(record["roles"]["test"]["skill_delivery"]["documents"], [])
        self.h.worker.run_once()
        state = self.h.worker.dispatcher.get(impl["task_id"])
        phases = {entry["phase"] for execution in [*state["executions"], state["active"]] if execution
                  for entry in execution.get("guidance_receipts", [])}
        self.assertIn("skill_prompt_prepared", phases)
        self.assertIn("skill_executor_returned", phases)
        public = self.h.worker.store.overview(self.h.project["id"])
        task = next(item for item in public["tasks"] if item["id"] == impl["task_id"])
        self.assertEqual(task["skill_delivery"]["selection_digest"], selection["digest"])
        self.assertEqual(task["skill_delivery"]["phase"], "executor_returned")

    def test_changed_skill_file_blocks_unconfirmed_plan_and_records_no_run(self):
        plan = self.plan()
        (self.skill / "SKILL.md").write_text("replaced after plan review")
        with self.assertRaises(ManagementError) as error:
            self.confirm(plan)
        self.assertEqual(error.exception.code, "skill_selection_changed")
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_unreviewed_public_candidate_stays_out_of_role_prompt(self):
        # Installed catalog intentionally has no match; a public lookup can only recommend.
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_public_sources": ("https://github.com/example/skills/blob/" + "b" * 40 + "/candidate/SKILL.md",)})
        self.h.worker = self.h.open()
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        from unittest.mock import patch
        external = {"skill_id": "candidate", "name": "후보", "source_url": self.h.config.skill_public_sources[0],
            "source_ref": "b" * 40, "version": "b" * 40, "bundle_sha256": "f" * 64,
            "files": {"SKILL.md": "e" * 64}, "license": {"id": "unreviewed", "path": None,
            "redistribution": "not_assessed"}, "compatibility": {"providers": [], "runners": []},
            "dependencies": [], "permissions": [], "status": "review_pending"}
        with patch("ai_company.skill_selection.SkillResearchStore.run_fetch", return_value=external):
            plan = self.plan()
        selection = plan["content"]["skill_selection"]
        self.assertEqual(selection["status"], "review_pending")
        self.assertFalse(selection["roles"]["impl"][0]["selected"])
        run = self.confirm(plan)["run"]
        self.h.worker.run_once()
        record = self.h.worker.store.get_run(run["id"])
        self.assertEqual(record["roles"]["impl"]["skill_delivery"]["documents"], [])

    def test_optional_public_lookup_failure_does_not_block_plan(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_public_sources": ("https://github.com/example/skills/blob/" + "b" * 40 + "/candidate/SKILL.md",)})
        self.h.worker = self.h.open()
        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_fetch",
                   side_effect=skill_catalog.SkillCatalogError("expired lookup budget")):
            plan = self.plan()
        self.assertEqual(plan["content"]["skill_selection"]["outcome"], "lookup_failed")
        self.assertTrue(plan["content"]["skill_selection"]["can_continue"])
        self.assertEqual(plan["status"], "proposed")

    def test_duplicate_public_name_is_one_pending_candidate(self):
        self.h.worker.close()
        urls = tuple("https://github.com/example/skills/blob/" + char * 40 + "/candidate/SKILL.md"
                     for char in ("b", "c"))
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (), "skill_public_sources": urls})
        self.h.worker = self.h.open()
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        from unittest.mock import patch
        def found(_store, _key, _lookup_id, url, **_kwargs):
            return {"skill_id": "candidate", "name": "후보", "source_url": url,
                "source_ref": "b" * 40, "version": "b" * 40, "bundle_sha256": "f" * 64,
                "files": {"SKILL.md": "e" * 64}, "license": {"id": "unreviewed", "path": None,
                "redistribution": "not_assessed"}, "compatibility": {"providers": [], "runners": []},
                "dependencies": [], "permissions": [], "status": "review_pending"}
        with patch("ai_company.skill_selection.SkillResearchStore.run_fetch", found):
            plan = self.plan()
        selection = plan["content"]["skill_selection"]
        self.assertEqual(len(selection["roles"]["impl"]), 1)
        self.assertEqual(selection["status"], "review_pending")

    def test_mandatory_unavailable_skill_blocks_confirmation(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": ()})
        self.h.plan["roles"][0].update(required_capabilities=["accessibility"], skill_required=True)
        self.h.worker = self.h.open()
        for _ in range(5):
            self.h.worker.run_once()
        plans = self.h.worker.store.overview(self.h.project["id"])["plans"]
        self.assertEqual(len(plans), 1)
        self.assertFalse(plans[0]["content"]["skill_selection"]["can_continue"])
        with self.assertRaises(ManagementError) as error:
            self.confirm(plans[0])
        self.assertEqual(error.exception.code, "skill_required")
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_partially_covered_required_capabilities_block_confirmation(self):
        self.h.worker.close()
        self.h.plan["roles"][0].update(
            required_capabilities=["accessibility", "security"], skill_required=True)
        self.h.worker = self.h.open()
        for _ in range(5):
            self.h.worker.run_once()
        plan = self.h.worker.store.overview(self.h.project["id"])["plans"][-1]
        selection = plan["content"]["skill_selection"]
        self.assertEqual([item["skill_id"] for item in selection["roles"]["impl"]], ["review"])
        self.assertFalse(selection["can_continue"])
        with self.assertRaises(ManagementError) as error:
            self.confirm(plan)
        self.assertEqual(error.exception.code, "skill_required")
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_generic_public_search_is_recorded_without_approving_unpinned_repository(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()
        from unittest.mock import patch
        found = {"status": "found", "term": "accessibility", "repositories": [{
            "repository": "example/skills", "url": "https://github.com/example/skills"}], "lookups_used": 1}
        with patch("ai_company.skill_selection.SkillResearchStore.run_search", return_value=found) as lookup:
            plan = self.plan()
        self.assertEqual(lookup.call_args.args[2], "accessibility")
        selection = plan["content"]["skill_selection"]
        self.assertEqual(selection["outcome"], "search_found_unpinned")
        self.assertEqual(selection["roles"]["impl"], [])
        self.assertIn("고정 커밋", selection["reason"])
        self.assertEqual(self.h.worker.store.run_records(), [])

    def test_missing_search_allowlist_is_distinct_from_no_results(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": ()})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()
        plan = self.plan()
        self.assertEqual(plan["content"]["skill_selection"]["outcome"], "lookup_not_configured")
