"""Pinned skill inspection and bounded public research, without installation."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai_company import skill_catalog as skills


COMMIT = "a" * 40
URL = f"https://github.com/example/agent-skills/blob/{COMMIT}/skills/review/SKILL.md"


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "SKILL.md").write_text("---\nname: review\n---\nCheck focus and keyboard.\n")
        (self.root / "references").mkdir()
        (self.root / "references" / "checks.md").write_text("Check the rendered UI.\n")
        (self.root / "LICENSE").write_text("MIT License\n")
        self.hashes = {path: sha256((self.root / path).read_bytes()).hexdigest()
                       for path in ("SKILL.md", "references/checks.md", "LICENSE")}
        self.expected = skills._bundle_hash(URL, COMMIT, self.hashes)

    def inspect(self, **changes):
        kwargs = {"skill_id": "review", "source_url": URL, "source_ref": COMMIT,
                  "expected_sha256": self.expected, "reference_paths": ("references/checks.md",),
                  "license_path": "LICENSE", "license_id": "MIT", "redistribution": "internal_only",
                  "providers": ("codex",), "runners": ("session_cli",),
                  "capabilities": ("accessibility",)}
        return skills.inspect_installed_skill(self.root, **{**kwargs, **changes})

    def test_approved_local_bundle_rechecked_before_delivery(self):
        entry = self.inspect()
        self.assertEqual(entry["bundle_sha256"], self.expected)
        self.assertEqual(entry["files"], self.hashes)
        self.assertEqual(entry["status"], "approved_document")
        text = skills.validate_pinned_bundle(entry, self.root, provider="codex", runner="session_cli")
        self.assertIn("Check focus and keyboard", text)
        self.assertIn("Check the rendered UI", text)
        reordered = json.loads(json.dumps(entry, sort_keys=True))
        self.assertEqual(skills.validate_pinned_bundle(reordered, self.root,
                                                       provider="codex", runner="session_cli"), text)
        with self.assertRaises(skills.SkillCatalogError):
            skills.validate_pinned_bundle(entry, self.root, provider="claude", runner="session_cli")
        (self.root / "references" / "checks.md").write_text("Changed after plan review")
        with self.assertRaisesRegex(skills.SkillCatalogError, "changed"):
            skills.validate_pinned_bundle(entry, self.root, provider="codex", runner="session_cli")

    def test_missing_review_or_symlink_cannot_be_delivered(self):
        with self.assertRaisesRegex(skills.SkillCatalogError, "differs"):
            self.inspect(expected_sha256="0" * 64)
        with self.assertRaisesRegex(skills.SkillCatalogError, "license"):
            self.inspect(license_id="unknown")
        entry = self.inspect(dependencies=("node",))
        self.assertEqual(entry["status"], "review_pending")
        with self.assertRaisesRegex(skills.SkillCatalogError, "not been approved"):
            skills.validate_pinned_bundle(entry, self.root, provider="codex", runner="session_cli")
        target = self.root / "elsewhere.md"; target.write_text("outside")
        (self.root / "references" / "checks.md").unlink()
        (self.root / "references" / "checks.md").symlink_to(target)
        with self.assertRaisesRegex(skills.SkillCatalogError, "symlink"):
            self.inspect()

    def test_public_source_is_pinned_and_restricts_hosts_and_paths(self):
        for url in ("http://github.com/x/y/blob/" + COMMIT + "/SKILL.md",
                    "https://127.0.0.1/x/y/blob/" + COMMIT + "/SKILL.md",
                    "https://github.com/x/y/blob/main/SKILL.md",
                    "https://github.com/x/y/blob/" + COMMIT + "/../SKILL.md",
                    "https://github.com/x/y/blob/" + COMMIT + "/SKILL.md?token=secret"):
            with self.subTest(url=url), self.assertRaises(skills.SkillCatalogError):
                skills.fetch_public_skill(url)

    def test_public_read_is_bounded_unapproved_and_failure_is_distinct(self):
        data = {"https://raw.githubusercontent.com/example/agent-skills/" + COMMIT + "/skills/review/SKILL.md":
                    b"---\nname: review\n---\nUntrusted instructions\n",
                "https://raw.githubusercontent.com/example/agent-skills/" + COMMIT + "/skills/review/references/checks.md":
                    b"Reference\n",
                "https://raw.githubusercontent.com/example/agent-skills/" + COMMIT + "/LICENSE": b"MIT License\n"}
        with patch.object(skills, "_public_get", side_effect=lambda url, *_: data[url]):
            entry = skills.fetch_public_skill(URL, reference_paths=("references/checks.md",), license_path="LICENSE")
        self.assertEqual(entry["status"], "review_pending")
        self.assertEqual(entry["source_ref"], COMMIT)
        self.assertEqual(entry["lookups_used"], 3)
        self.assertEqual(entry["files"]["LICENSE"], sha256(b"MIT License\n").hexdigest())
        with self.assertRaises(skills.SkillCatalogError):
            skills.validate_pinned_bundle(entry, self.root, provider="codex", runner="session_cli")
        with patch.object(skills, "_public_get", side_effect=OSError("offline")):
            failure = skills.fetch_public_skill(URL)
        self.assertEqual(failure["status"], "lookup_failed")
        self.assertEqual(failure["lookups_used"], 1)

    def test_public_search_sends_only_allowlisted_generic_term(self):
        with self.assertRaises(skills.SkillCatalogError):
            skills.search_public_skills("my private project", allowed_terms=("accessibility",))
        with self.assertRaises(skills.SkillCatalogError):
            skills.search_public_skills("accessibility", allowed_terms=("testing",))
        with patch.object(skills, "_public_get", return_value=b'{"items":[]}') as read:
            empty = skills.search_public_skills("accessibility", allowed_terms=("accessibility",))
        self.assertEqual(empty["status"], "no_results")
        self.assertNotIn("private", read.call_args.args[0])
        self.assertIn("api.github.com/search/repositories", read.call_args.args[0])
        with patch.object(skills, "_public_get", side_effect=OSError("offline")):
            failed = skills.search_public_skills("accessibility", allowed_terms=("accessibility",))
        self.assertEqual(failed["status"], "lookup_failed")
        self.assertEqual(failed["lookups_used"], 1)

    def test_official_reference_is_bounded_and_never_approved(self):
        with self.assertRaises(skills.SkillCatalogError):
            skills.read_official_reference("https://localhost/specification")
        with patch.object(skills, "_public_get", return_value=b"Official document"):
            evidence = skills.read_official_reference("https://agentskills.io/specification")
        self.assertEqual(evidence["status"], "read_unreviewed")
        self.assertEqual(evidence["content_sha256"], sha256(b"Official document").hexdigest())
        self.assertNotIn("approved", evidence)

    def test_reuse_pending_failure_and_no_skill_are_separate(self):
        installed = self.inspect()
        pending = {"skill_id": "new", "bundle_sha256": "f" * 64,
                   "status": "review_pending", "capabilities": ["python"]}
        selected = skills.recommend_skills({"ui": ["accessibility"], "code": ["python"],
                                            "none": [], "unknown": ["go"]}, [installed],
                                           external_candidates=[pending], lookup_status="lookup_failed")
        self.assertEqual(selected["ui"]["recommendations"][0]["status"], "approved_document")
        self.assertEqual(selected["code"]["recommendations"][0]["status"], "review_pending")
        self.assertEqual(selected["none"]["status"], "no_additional_skill")
        self.assertEqual(selected["unknown"]["status"], "lookup_failed")
        self.assertEqual(skills.research_key(capabilities=["python"], environment="python",
                                             roles=["code"], catalog_version="1"),
                         skills.research_key(capabilities=["python", "python"], environment="python",
                                             roles=["code"], catalog_version="1"))


if __name__ == "__main__":
    unittest.main()
