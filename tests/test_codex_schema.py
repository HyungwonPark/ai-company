"""The exact Codex wire schema is strict while local v1 records stay readable."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from ai_company.adapters.session_cli import (codex_output_schema, codex_output_schema_digest,
                                             validate_codex_output_schema_file)
from ai_company.flow_contracts import StageReport, PMPlanStageReport, PlanReviewStageReport, ContributionStageReport


class CodexSchemaTests(unittest.TestCase):
    def test_all_stages_require_every_declared_wire_field_without_mutating_contract(self):
        for contract in (StageReport, PMPlanStageReport, PlanReviewStageReport, ContributionStageReport):
            original = contract.model_json_schema()
            before = copy.deepcopy(original)
            strict = codex_output_schema(original)
            def check(node):
                if isinstance(node, dict):
                    if node.get("type") == "object":
                        self.assertEqual(list(node["required"]), list(node["properties"]))
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

    def test_pm_wire_closes_four_previously_open_nodes(self):
        original = PMPlanStageReport.model_json_schema()
        strict = codex_output_schema(original)
        props = strict["properties"]["plan"]["anyOf"][0]["properties"]
        self.assertEqual(props["skill_selection"], {"type": "null"})
        self.assertEqual(props["skill_recommendations"]["type"], "array")
        self.assertEqual(props["execution_spec_proposal"]["anyOf"][0]["properties"]["candidates"]["type"], "array")
        self.assertIn("questions", strict["properties"]["requirements_feedback"]["anyOf"][0]["properties"])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "schema.json"
            path.write_text(json.dumps(strict, sort_keys=True, separators=(",", ":")))
            self.assertEqual(validate_codex_output_schema_file(path), codex_output_schema_digest(original))

    def test_wire_plan_round_trips_to_existing_stored_contract(self):
        feedback = {"version": 2, "revision": 1, "goal_digest": "a" * 64,
                    "problem": "목표", "users_and_flow": "사용자", "scope": ["구현"],
                    "exclusions": [], "assumptions": [], "questions": [{
                        "id": "Q1", "prompt": "어느 화면부터?", "reason": "범위를 정해야 합니다.",
                        "options": ["모바일", "데스크톱"], "recommendation": "모바일",
                        "status": "answered", "resolution": "모바일 먼저",
                        "answer_message_id": None, "source_request_id": None,
                        "source_question_id": None, "source_plan_id": None,
                        "source_repair_attempt": None, "evidence_kind": "master_goal",
                        "goal_quote": "모바일 먼저"}], "findings": [],
                    "requirements": [{"id": "R1", "source": "요청", "acceptance": "완료",
                                      "verification": "검사", "role_keys": ["impl"]}]}
        plan = {"summary": "작업", "roles": [
            {"key": key, "name": key, "responsibility": "담당", "goal": "구현", "acceptance": ["완료"],
             "allowed_paths": [f"src/{key}.py"], "depends_on": [],
             "required_capabilities": [], "skill_required": False} for key in ("impl", "test")],
            "completion_criteria": ["완료"], "requirements_review": feedback,
            "execution_spec_proposal": {"catalog_id": "pilot", "catalog_digest": "c" * 64,
                "allowed_paths": None, "candidates": {"developer": ["a", "b"]},
                "role_candidates": {"impl": ["a"]}, "budget": {"max_cost_usd": None,
                "max_runtime_seconds": 120, "max_executions": None, "max_repairs": None}},
            "skill_recommendations": {"impl": ["s1", "s2"]}}
        report = {"execution_id": "a" * 64, "generation": 1, "role": "pm", "task_digest": "a" * 64,
                  "policy_digest": "a" * 64, "candidate_sha": "b" * 40,
                  "verification_digest": None, "verdict": "PASS", "findings": [],
                  "resolved_findings": [], "summary": "완료", "requirements_feedback": feedback}
        old = PMPlanStageReport.model_validate({**report, "plan": plan}).model_dump(mode="json")
        proposal = plan["execution_spec_proposal"]
        wire = {**plan, "wire_contract": "pm-plan-wire-v1", "skill_selection": None,
                "skill_recommendations": [{"role_key": "impl", "skill_ids": ["s1", "s2"]}],
                "execution_spec_proposal": {**proposal,
                    "candidates": [{"role": "developer", "agent_ids": ["a", "b"]}],
                    "role_candidates": [{"role_key": "impl", "agent_ids": ["a"]}]}}
        self.assertEqual(PMPlanStageReport.model_validate({**report, "plan": wire}).model_dump(mode="json"), old)
        changed = [
            {**wire, "skill_selection": {"roles": {"impl": []}}},
            {**wire, "skill_recommendations": [{"role_key": "impl", "skill_ids": ["s1"]}] * 2},
            {**wire, "skill_recommendations": [{"role_key": "other", "skill_ids": ["s1"]}]},
            {**wire, "execution_spec_proposal": {**wire["execution_spec_proposal"],
                "role_candidates": [{"role_key": "impl", "agent_ids": ["a"]}] * 2}},
        ]
        for candidate in changed:
            with self.subTest(candidate=candidate), self.assertRaises(ValidationError):
                PMPlanStageReport.model_validate({**report, "plan": candidate})

    def test_open_or_unsupported_final_schema_fails_preflight(self):
        invalid = [
            {"type": "object", "additionalProperties": True},
            {"type": "object", "properties": {"x": {"type": "object", "additionalProperties": {"type": "string"}}}},
            {"type": "object", "properties": {"x": {"allOf": [{"type": "string"}]}}},
            {"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}},
            {"type": "object", "$defs": {"Name": {"type": "string"}},
             "properties": {"x": {"$ref": "#/$defs/Name", "additionalProperties": True}}},
        ]
        for schema in invalid:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                codex_output_schema(schema)

    def test_each_original_pm_open_object_is_rejected_before_the_cli(self):
        original = PMPlanStageReport.model_json_schema()
        open_object = {"anyOf": [{"type": "object", "additionalProperties": True}, {"type": "null"}]}
        for field in ("execution_spec_proposal", "skill_selection", "skill_recommendations",
                      "requirements_feedback"):
            malformed = copy.deepcopy(original)
            location = (malformed["properties"] if field == "requirements_feedback"
                        else malformed["properties"]["plan"]["anyOf"][0]["properties"])
            location[field] = open_object
            with self.subTest(field=field), self.assertRaises(ValueError):
                codex_output_schema(malformed)


if __name__ == "__main__":
    unittest.main()
