"""Regress the provider/tool mismatch observed in the supervised 2026-09-14 reviews.

These tests inspect the prompt delivered at the real runner boundary without model
calls. They do not claim that static instructions prove actual tool use or review.
"""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import StageReport
from ai_company.harness.prompts import stage_prompt


class StagePromptTests(unittest.TestCase):
    def setUp(self):
        self.identifiers = dict(execution_id="1" * 64, generation=9, role="reviewer",
                                task_digest="2" * 64, policy_digest="3" * 64,
                                verification_digest="4" * 64)
        self.checkpoint = "Persisted checkpoint:\n" + json.dumps({"checkpoint": {
            "expected_report": self.identifiers, "snapshot": {"head_commit": "5" * 40},
            "findings": [{"finding_id": "R1", "detail": "Missing boundary", "evidence": "src/value.py:4"}]}})

    def test_claude_review_uses_allowed_reads_instead_of_denied_context_execution(self):
        prompt = stage_prompt(self.checkpoint, provider="claude", role="reviewer")
        self.assertIn("use only Read, Glob, and Grep", prompt)
        self.assertIn("Bash and command execution are not available", prompt)
        self.assertIn("Do not route inspection through context-mode", prompt)
        self.assertIn("Workflow, Task, or Skill as a substitute", prompt)
        self.assertNotIn("git rev-parse", prompt)
        self.assertIn("runner-owned identity evidence", prompt)

    def test_astra_final_uses_actual_command_tools_without_claude_tool_requirements(self):
        prompt = stage_prompt(self.checkpoint, provider="codex", role="final")
        self.assertIn("available command-execution tool", prompt)
        self.assertIn("git rev-parse HEAD", prompt)
        self.assertIn("git --no-optional-locks status", prompt)
        self.assertIn("--no-ext-diff --no-textconv HEAD", prompt)
        self.assertNotIn("use only Read, Glob, and Grep", prompt)
        self.assertNotIn("Bash and command execution are not available", prompt)
        self.assertIn("do not repair it yourself", prompt)

    def test_report_contract_keeps_checkpoint_identifiers_and_resolution_evidence(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                prompt = stage_prompt(self.checkpoint, provider=provider, role="reviewer")
                self.assertTrue(prompt.startswith(self.checkpoint + "\n\n"))
                for name in StageReport.model_fields:
                    self.assertIn(name, prompt)
                self.assertIn("preserving a null verification_digest", prompt)
                self.assertIn("finding_id, detail, and evidence", prompt)
                self.assertIn("only after verifying its resolution", prompt)
                self.assertIn("Return REVISE with concrete findings", prompt)
                self.assertIn("Return BLOCK when required access/evidence is missing", prompt)

    def test_developer_requires_real_new_commit_and_clean_worktree(self):
        for provider in ("claude", "codex"):
            with self.subTest(provider=provider):
                prompt = stage_prompt(self.checkpoint, provider=provider, role="developer")
                self.assertIn("new candidate commit that differs from the input snapshot HEAD", prompt)
                self.assertIn("clean worktree including untracked files", prompt)
                self.assertIn("instead of claiming DONE", prompt)
                self.assertNotIn("This is a read-only stage", prompt)
                self.assertNotIn("use only Read, Glob, and Grep", prompt)

    def test_pm_does_not_receive_developer_commit_or_review_approval_instructions(self):
        prompt = stage_prompt(self.checkpoint, provider="codex", role="pm")
        self.assertIn("This is a read-only stage", prompt)
        self.assertIn("PM PASS is not a code review or execution approval", prompt)
        self.assertIn("do not add a plan field", prompt)
        self.assertNotIn("Commit the allowed changes", prompt)

    def test_unknown_provider_or_stage_is_not_silently_given_another_tool_contract(self):
        for provider, role in (("other", "reviewer"), ("codex", "check")):
            with self.subTest(provider=provider, role=role), self.assertRaises(ValueError):
                stage_prompt(self.checkpoint, provider=provider, role=role)


class DispatcherPromptBoundaryTests(unittest.TestCase):
    def test_live_runner_gets_provider_prompt_without_permission_or_runtime_option_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            dispatcher = Dispatcher(Path(directory))
            self.addCleanup(dispatcher.close)
            for provider, role in (("claude", "reviewer"), ("codex", "final"), ("codex", "developer")):
                with self.subTest(provider=provider, role=role):
                    agent = SimpleNamespace(agent_id="assigned", model="assigned-model", reasoning_effort="high",
                                            ultracode_enabled=False)
                    spec = SimpleNamespace(agents=[agent], mode="live", policy=SimpleNamespace(
                        max_runtime_seconds=90, max_cost_usd=None))
                    state = {"active": {"agent_id": "assigned"}, "stage": role,
                             "usage": {"runtime_seconds": 10, "cost_usd": 0}}
                    with patch("ai_company.dispatcher.run_session", return_value="runner-result") as run:
                        result = dispatcher._executor(spec, state)(provider, Path(directory), "original checkpoint",
                                                                  "existing-session", timeout_seconds=100)
                    self.assertEqual(result, "runner-result")
                    args, options = run.call_args
                    self.assertEqual((args[0], args[1], args[3]), (provider, Path(directory), "existing-session"))
                    self.assertEqual(args[2], stage_prompt("original checkpoint", provider=provider, role=role))
                    self.assertEqual(options["permission"], "workspace-write" if role == "developer" else "read-only")
                    self.assertEqual(options["timeout_seconds"], 80)
                    self.assertEqual(options["output_schema"], StageReport.model_json_schema())
                    self.assertTrue(options["isolate_cgroup"])
                    self.assertTrue(options["capture_configuration"])

    def test_fixture_executor_receives_same_provider_stage_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            dispatcher = Dispatcher(Path(directory), executor=lambda *args, **kwargs: calls.append(args))
            self.addCleanup(dispatcher.close)
            agent = SimpleNamespace(agent_id="assigned")
            spec = SimpleNamespace(agents=[agent], mode="fixture", policy=SimpleNamespace(max_runtime_seconds=90))
            state = {"active": {"agent_id": "assigned"}, "stage": "reviewer", "usage": {"runtime_seconds": 0}}
            dispatcher._executor(spec, state)("claude", Path(directory), "checkpoint", None, timeout_seconds=10)
            self.assertEqual(calls[0][4], stage_prompt("checkpoint", provider="claude", role="reviewer"))
