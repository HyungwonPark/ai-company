"""Pinned skill choices travel with one reviewed plan into the assigned role."""

from hashlib import sha256
import json
import unittest

from ai_company import skill_catalog
from ai_company.automation_contracts import PMPlanContent
from ai_company.runtime import ExecutionBlocked
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

    def test_public_candidate_cannot_reuse_trusted_skill_identity(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_public_sources": (
            "https://github.com/example/skills/blob/" + "b" * 40 + "/review/SKILL.md",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["security"]
        self.h.worker = self.h.open()
        external = {"skill_id": "review", "status": "review_pending"}
        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_fetch", return_value=external):
            plan = self.plan()
        chosen = plan["content"]["skill_selection"]
        self.assertEqual(chosen["roles"]["impl"], [])
        self.assertEqual(chosen["outcome"], "lookup_failed")
        self.assertIn("충돌", chosen["reason"])

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

    def test_failed_configured_source_does_not_exceed_budget_with_search_fallback(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_public_sources": ("https://github.com/example/skills/blob/" + "b" * 40 + "/SKILL.md",),
            "skill_search_terms": ("accessibility",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()
        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_fetch",
                   side_effect=skill_catalog.SkillCatalogError("source unavailable")), \
             patch("ai_company.skill_selection.SkillResearchStore.run_search") as search:
            plan = self.plan()
        search.assert_not_called()
        self.assertEqual(plan["content"]["skill_selection"]["outcome"], "lookup_failed")
        self.assertEqual(plan["content"]["skill_selection"]["roles"]["impl"], [])

    def test_public_search_documents_reach_pm_and_only_matching_role(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.plan["roles"][1]["required_capabilities"] = ["testing"]
        self.h.worker = self.h.open()
        original = self.h.execute
        seen = []
        def pm_uses_evidence(agent, state, provider, worktree, prompt, session_id, **kwargs):
            result = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if state["stage"] == "pm":
                research = state["specification"]["plan"]["skill_research"]
                seen.append(research)
                candidate = research["candidates"][0]
                result.result["structured_output"]["plan"]["skill_recommendations"] = {
                    "impl": [candidate["skill_id"]]}
            return result
        self.h.execute = pm_uses_evidence
        self.h.worker.close(); self.h.worker = self.h.open()
        commit = "b" * 40
        def public_response(url, _limit, _timeout):
            if "/search/repositories" in url:
                return json.dumps({"items": [{"full_name": "example/agent-skills",
                    "html_url": "https://github.com/example/agent-skills",
                    "default_branch": "main"}]}).encode()
            if url.endswith("/commits/main"):
                return json.dumps({"sha": commit}).encode()
            if "/git/trees/" in url:
                return json.dumps({"tree": [{"path": path, "type": "blob"} for path in
                    ("skills/accessibility/SKILL.md", "LICENSE")], "truncated": False}).encode()
            if url.endswith("/skills/accessibility/SKILL.md"):
                return b"---\nname: accessibility\n---\nCheck keyboard access and focus.\n"
            if url.endswith("/LICENSE"):
                return b"MIT License\n"
            raise AssertionError(url)
        from unittest.mock import patch
        with patch.object(skill_catalog, "_public_get", side_effect=public_response) as fetched:
            plan = self.plan()
        self.assertEqual(fetched.call_count, 5)
        self.assertIn("keyboard access", seen[0]["candidates"][0]["document_excerpt"])
        self.assertIn("MIT License", seen[0]["candidates"][0]["license_excerpt"])
        selected = plan["content"]["skill_selection"]
        self.assertEqual(selected["outcome"], "review_pending")
        self.assertEqual(len(selected["roles"]["impl"]), 1)
        self.assertEqual(selected["roles"]["test"], [])
        self.assertEqual(selected["roles"]["impl"][0]["research_match_terms"], ["accessibility"])
        self.assertFalse(selected["roles"]["impl"][0]["selected"])
        run = self.confirm(plan)["run"]
        self.h.worker.run_once()
        self.assertEqual(self.h.worker.store.get_run(run["id"])["roles"]["impl"]["skill_delivery"]["documents"], [])

    def test_document_discovery_failure_is_attributed_only_to_its_search_term(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility", "testing")})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.plan["roles"][1]["required_capabilities"] = ["testing"]
        self.h.worker = self.h.open()

        def search(_store, _key, _lookup_id, term, **_kwargs):
            return {"status": "found", "term": term, "repositories": [{
                "repository": f"example/{term}", "default_branch": "main"}], "lookups_used": 1}

        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_search", search), \
             patch("ai_company.skill_selection.SkillResearchStore.run_discover",
                   return_value={"status": "no_matching_document", "lookups_used": 2}) as discover:
            plan = self.plan()
        self.assertEqual(discover.call_count, 1)
        selection = plan["content"]["skill_selection"]
        self.assertEqual(selection["role_outcomes"]["impl"], "no_matching_document")
        self.assertEqual(selection["role_outcomes"]["test"], "search_found_unpinned")

    def test_unsearched_third_term_does_not_inherit_other_role_failure(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility", "security", "testing")})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.plan["roles"][1]["required_capabilities"] = ["testing"]
        self.h.worker = self.h.open()

        def search(_store, _key, _lookup_id, term, **_kwargs):
            return {"status": "found" if term == "accessibility" else "no_results",
                "term": term, "repositories": [{"repository": "example/a",
                "default_branch": "main"}] if term == "accessibility" else []}

        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_search", search), \
             patch("ai_company.skill_selection.SkillResearchStore.run_discover",
                   return_value={"status": "no_matching_document"}):
            plan = self.plan()
        self.assertEqual(plan["content"]["skill_selection"]["role_outcomes"]["test"], "not_searched")

    def test_document_fetch_failure_is_attributed_to_discovered_term(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()

        from unittest.mock import patch
        with patch("ai_company.skill_selection.SkillResearchStore.run_search", return_value={
                 "status": "found", "repositories": [{"repository": "example/a",
                 "default_branch": "main"}]}), \
             patch("ai_company.skill_selection.SkillResearchStore.run_discover", return_value={
                 "status": "found", "source_url": "https://github.com/example/a/blob/" + "a" * 40 + "/SKILL.md",
                 "license_path": "LICENSE"}), \
             patch("ai_company.skill_selection.SkillResearchStore.run_fetch", return_value={
                 "status": "lookup_failed", "reason": "document unavailable"}):
            plan = self.plan()
        self.assertEqual(plan["content"]["skill_selection"]["role_outcomes"]["impl"], "lookup_failed")

    def test_pm_cannot_recommend_public_candidate_for_unrelated_role(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": (),
            "skill_search_terms": ("accessibility",)})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.plan["roles"][1]["required_capabilities"] = ["testing"]
        self.h.worker = self.h.open()
        candidate = {"skill_id": "public-example", "name": "후보", "source_url":
            "https://github.com/example/skills/blob/" + "a" * 40 + "/SKILL.md",
            "source_ref": "a" * 40, "version": "a" * 40, "bundle_sha256": "f" * 64,
            "files": {"SKILL.md": "e" * 64}, "license": {"id": "unreviewed", "path": None,
            "redistribution": "not_assessed"}, "compatibility": {"providers": [], "runners": []},
            "dependencies": [], "permissions": [], "status": "review_pending",
            "research_match_terms": ["accessibility"], "research_origin": "public_search"}
        plan = PMPlanContent.model_validate({**self.h.plan,
            "skill_recommendations": {"test": [candidate["skill_id"]]}})
        with self.assertRaises(ExecutionBlocked):
            self.h.worker._select_skills(plan, {"candidates": [candidate],
                "status": "review_pending", "search_matches": 1})

    def test_missing_search_allowlist_is_distinct_from_no_results(self):
        self.h.worker.close()
        self.h.config = self.h.config.model_copy(update={"skill_catalog": ()})
        self.h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
        self.h.worker = self.h.open()
        plan = self.plan()
        self.assertEqual(plan["content"]["skill_selection"]["outcome"], "lookup_not_configured")
