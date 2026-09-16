"""Reader summaries must keep the full original plan and its authority unchanged."""
import copy
import unittest

from ai_company.collaboration import document_sources


class ManagerDocumentTests(unittest.TestCase):
    def fixture(self):
        return {"project": {"id": "p", "goal": "Build a bounded feature"}, "plans": [{
            "id": "plan", "digest": "a" * 64, "content": {
                "summary": "Implement and inspect independently",
                "roles": [{"key": "impl", "name": "Implementation", "allowed_paths": ["src/only.py"],
                           "depends_on": [], "acceptance": ["Do not modify existing data"]}],
                "completion_criteria": ["Do not deploy or merge"],
            }}]}

    def test_translatable_summary_keeps_full_plan_protected(self):
        overview = self.fixture()
        original = copy.deepcopy(overview)
        doc = next(item for item in document_sources(overview) if item["kind"] == "plan")
        self.assertEqual(doc["fields"], {"summary": "Implement and inspect independently", "role:0:name": "Implementation",
            "role:0:responsibility": "", "role:0:goal": "", "role:0:acceptance:0": "Do not modify existing data",
            "completion:0": "Do not deploy or merge"})
        self.assertEqual(doc["protected"]["content"], original["plans"][0]["content"])
        self.assertEqual(doc["protected"]["plan_digest"], "a" * 64)
        self.assertEqual(doc["source_ref"], {"plan_id": "plan", "plan_digest": "a" * 64})
        self.assertEqual(overview, original)

    def test_goal_or_full_plan_change_invalidates_old_translation_source(self):
        overview = self.fixture()
        old = {doc["id"]: doc["source_digest"] for doc in document_sources(overview)}
        overview["project"]["goal"] = "A changed goal"
        # Even an inconsistent supplied digest cannot reuse a translation for changed authority.
        overview["plans"][0]["content"]["roles"][0]["allowed_paths"] = ["src/different.py"]
        new = {doc["id"]: doc["source_digest"] for doc in document_sources(overview)}
        self.assertNotEqual(old["project:p"], new["project:p"])
        self.assertNotEqual(old["plan:plan"], new["plan:plan"])


if __name__ == "__main__":
    unittest.main()
