"""Provider output schemas differ from default-bearing local Pydantic contracts."""
import copy
import unittest

from ai_company.adapters.session_cli import codex_output_schema
from ai_company.flow_contracts import StageReport, PMPlanStageReport, ContributionStageReport


class CodexSchemaTests(unittest.TestCase):
    def test_all_stages_require_every_declared_wire_field_without_mutating_contract(self):
        for contract in (StageReport, PMPlanStageReport, ContributionStageReport):
            original = contract.model_json_schema()
            before = copy.deepcopy(original)
            strict = codex_output_schema(original)
            def check(node):
                if isinstance(node, dict):
                    if node.get("type") == "object" and "properties" in node:
                        self.assertEqual(set(node["required"]), set(node["properties"]))
                        self.assertIs(node["additionalProperties"], False)
                    for value in node.values():
                        check(value)
                elif isinstance(node, list):
                    for value in node:
                        check(value)
            check(strict)
            self.assertEqual(original, before)
            self.assertIn("findings", strict["required"])
            self.assertIn("resolved_findings", strict["required"])
            self.assertIn({"type": "null"}, strict["properties"]["verification_digest"]["anyOf"])


if __name__ == "__main__":
    unittest.main()
