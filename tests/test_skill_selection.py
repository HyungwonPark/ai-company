"""Durable research reservations and pinned role-specific skill delivery."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai_company import skill_catalog as skills
from ai_company import skill_selection as selection


COMMIT = "a" * 40
URL = f"https://github.com/example/agent-skills/blob/{COMMIT}/skills/review/SKILL.md"


class SkillSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skill = self.root / "review"; self.skill.mkdir()
        (self.skill / "SKILL.md").write_text("---\nname: review\n---\nCheck accessibility.\n")
        (self.skill / "LICENSE").write_text("MIT License\n")
        hashes = {path: sha256((self.skill / path).read_bytes()).hexdigest()
                  for path in ("SKILL.md", "LICENSE")}
        self.config = {"root": str(self.skill), "skill_id": "review", "source_url": URL,
                       "source_ref": COMMIT, "expected_sha256": skills._bundle_hash(URL, COMMIT, hashes),
                       "license_path": "LICENSE", "license_id": "MIT",
                       "redistribution": "internal_only", "providers": ("codex",),
                       "runners": ("session_cli",), "capabilities": ("accessibility",)}
        self.catalog = selection.load_trusted_catalog((self.config,))
        self.policy = {"max_searches": 1, "max_fetches": 2, "max_bytes": 400_000,
                       "max_elapsed_ms": 10_000, "max_model_calls": 0, "max_tokens": 0,
                       "max_cost_microusd": 0, "expires_at": 10_000}

    def store(self):
        result = selection.SkillResearchStore(self.root / "research.sqlite", clock=lambda: 1_000)
        self.addCleanup(result.close)
        return result

    def test_role_selection_pins_bundle_and_only_delivers_its_documents(self):
        chosen = selection.build_selection({"implementation": [], "reviewer": [
            {"skill_id": "review", "reason": "키보드 확인", "requirements": ["r1"], "selected": True}]},
            self.catalog)
        self.assertEqual(chosen["roles"]["reviewer"][0]["status"], "approved_document")
        self.assertEqual(chosen["roles"]["reviewer"][0]["requirements"], ["r1"])
        self.assertEqual(chosen["roles"]["reviewer"][0]["delivery_id"], None)
        self.assertEqual(selection.validate_selection(chosen, self.catalog), chosen["digest"])
        developer_text, developer_receipt = selection.materialize_role(
            chosen, "implementation", self.catalog, provider="codex", runner="session_cli")
        self.assertEqual(developer_text, "")
        self.assertEqual(developer_receipt["documents"], [])
        text, receipt = selection.materialize_role(
            chosen, "reviewer", self.catalog, provider="codex", runner="session_cli")
        self.assertIn("Check accessibility", text)
        self.assertEqual(receipt["selection_digest"], chosen["digest"])
        self.assertEqual(receipt["delivery"], "prepared")
        self.assertEqual(receipt["model_compliance"], "unverified")
        forged = selection.build_selection({"reviewer": [
            {"skill_id": "review", "reason": "키보드 확인", "requirements": ["r1"], "selected": True}]},
            self.catalog)
        forged["roles"]["reviewer"][0]["files"]["SKILL.md"] = "0" * 64
        self.assertNotEqual(self.catalog["review"]["entry"]["files"]["SKILL.md"], "0" * 64)
        with self.assertRaisesRegex(skills.SkillCatalogError, "digest"):
            selection.validate_selection(forged, self.catalog)
        with self.assertRaises(skills.SkillCatalogError):
            selection.materialize_role(chosen, "reviewer", self.catalog,
                                       provider="claude", runner="session_cli")
        (self.skill / "SKILL.md").write_text("changed after review")
        with self.assertRaisesRegex(skills.SkillCatalogError, "changed"):
            selection.materialize_role(chosen, "reviewer", self.catalog,
                                       provider="codex", runner="session_cli")

    def test_external_candidate_is_visible_but_not_deliverable(self):
        external = {**self.catalog["review"]["entry"], "skill_id": "other",
                    "status": "review_pending", "origin": "public_github"}
        with self.assertRaisesRegex(skills.SkillCatalogError, "unreviewed"):
            selection.build_selection({"reviewer": [{"skill_id": "other", "selected": True}]},
                                      self.catalog, external_candidates=(external,))
        candidate = selection.build_selection({"reviewer": [{"skill_id": "other", "selected": False,
                                      "reason": "검토 필요", "requirements": ["r1"]}]}, self.catalog,
                                      external_candidates=(external,), outcome="external_candidate")
        self.assertEqual(candidate["status"], "review_pending")
        self.assertEqual(selection.materialize_role(candidate, "reviewer", self.catalog,
                                                  provider="codex", runner="session_cli")[1]["documents"], [])
        candidate["roles"]["reviewer"][0]["selected"] = True
        with self.assertRaisesRegex(skills.SkillCatalogError, "digest"):
            selection.validate_selection(candidate, self.catalog)

    def test_restart_reuses_result_and_in_progress_does_not_repeat_network(self):
        first = self.store()
        with patch.object(skills, "search_public_skills", return_value={"status": "no_results",
                                                                          "lookups_used": 1}) as lookup:
            result = first.run_search("project", "search1", "accessibility",
                                      allowed_terms=("accessibility",), policy=self.policy)
        self.assertEqual(result["status"], "no_results")
        self.assertFalse(result["cache_hit"])
        first.close()
        reopened = self.store()
        with patch.object(skills, "search_public_skills") as lookup_again:
            cached = reopened.run_search("project", "search1", "accessibility",
                                         allowed_terms=("accessibility",), policy=self.policy)
        self.assertEqual(cached["status"], "no_results")
        self.assertTrue(cached["cache_hit"])
        same_input_new_id = reopened.run_search("project", "search_new_id", "accessibility",
                                                allowed_terms=("accessibility",), policy=self.policy)
        self.assertTrue(same_input_new_id["cache_hit"])
        lookup.assert_called_once(); lookup_again.assert_not_called()
        self.assertEqual(reopened.snapshot("project")["searches"], 1)
        with self.assertRaisesRegex(skills.SkillCatalogError, "budget"):
            reopened.run_search("project", "search2", "python",
                                allowed_terms=("accessibility", "python"), policy=self.policy)
        with self.assertRaisesRegex(skills.SkillCatalogError, "cannot change"):
            reopened.run_search("project", "search1", "accessibility", allowed_terms=("accessibility",),
                                policy={**self.policy, "max_searches": 2})
        reopened.clock = lambda: 10_001
        with self.assertRaisesRegex(skills.SkillCatalogError, "expired"):
            reopened.run_search("project", "search1", "accessibility",
                                allowed_terms=("accessibility",), policy=self.policy)

    def test_crash_after_reservation_keeps_uncertain_result_and_budget(self):
        store = self.store()
        self.assertIsNone(store._reserve("project", "fetch1", "fetch",
                                       {"url": URL}, self.policy, calls=2, bytes_limit=100,
                                       timeout=1))
        same = store._reserve("project", "fetch1", "fetch", {"url": URL}, self.policy,
                              calls=2, bytes_limit=100, timeout=1)
        self.assertEqual(same["status"], "lookup_pending")
        different_id = store._reserve("project", "fetch_again", "fetch", {"url": URL}, self.policy,
                                      calls=2, bytes_limit=100, timeout=1)
        self.assertEqual(different_id["status"], "lookup_pending")
        self.assertEqual(different_id["lookup_id"], "fetch1")
        self.assertEqual(store.snapshot("project")["fetches"], 2)
        with self.assertRaisesRegex(skills.SkillCatalogError, "different public input"):
            store._reserve("project", "fetch1", "fetch", {"url": "different"}, self.policy,
                           calls=2, bytes_limit=100, timeout=1)

    def test_pinned_fetch_budget_and_cached_result(self):
        store = self.store()
        result = {"status": "review_pending", "source_url": URL, "lookups_used": 2}
        with patch.object(skills, "fetch_public_skill", return_value=result) as fetch:
            first = store.run_fetch("project", "fetch1", URL, license_path="LICENSE", policy=self.policy)
            second = store.run_fetch("project", "fetch1", URL, license_path="LICENSE", policy=self.policy)
        self.assertEqual(first["status"], "review_pending")
        self.assertTrue(second["cache_hit"])
        fetch.assert_called_once()
        self.assertEqual(store.snapshot("project")["fetches"], 2)
        with self.assertRaisesRegex(skills.SkillCatalogError, "budget"):
            store.run_fetch("project", "fetch2", URL, policy=self.policy)


if __name__ == "__main__":
    unittest.main()
