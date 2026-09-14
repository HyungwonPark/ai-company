"""Automation flow scopes and explicitly narrower CLI configuration policy."""
from uuid import UUID
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.contracts import digest
from ai_company.flow_contracts import FlowSpec, PMPlanStageReport, StageReport
from ai_company.harness.prompts import stage_prompt
from ai_company.runtime import ExecutionBlocked
from test_dispatcher import Crash, FlowFixture


class AutomationDispatcherTests(FlowFixture):
    def setUp(self):
        super().setUp()
        self.evidence_mutation = None
        self.pm_plan = {"summary": "Implement independent parts", "roles": [
            {"key": "api", "name": "API", "responsibility": "API", "goal": "API", "acceptance": ["API passes"], "allowed_paths": ["src/api/"], "depends_on": []},
            {"key": "ui", "name": "UI", "responsibility": "UI", "goal": "UI", "acceptance": ["UI passes"], "allowed_paths": ["src/ui/"], "depends_on": []}],
            "completion_criteria": ["Integrated checks and independent final review pass"]}

    def execute(self, agent, state, provider, worktree, prompt, session_id, **kwargs):
        spec = FlowSpec.model_validate(state["specification"])
        if spec.execution_scope == "contribution":
            with patch.object(self, "commit"):
                outcome = super().execute(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if outcome.category == "success":
                outcome.result["structured_output"]["commit_requested"] = True
        else:
            outcome = super().execute(agent, state, provider, worktree, prompt, session_id, **kwargs)
        if outcome.category != "success":
            return outcome
        if spec.execution_scope == "planning":
            outcome.result["structured_output"]["plan"] = self.pm_plan
        if spec.policy.configuration_evidence == "cli_configuration":
            outcome.session_id = session_id or str(UUID(int=len(self.calls)))
            outcome.result["observed_models"] = []; outcome.result["observed_efforts"] = []
            outcome.result["configuration_evidence"] = {
                "source": "codex_rollout", "scope": "cli_turn_configuration", "status": "observed", "cli_version": "0.154.0",
                "session_id": outcome.session_id, "backend_model_verified": False,
                "contexts": [{"turn_id": str(UUID(int=100 + len(self.calls))), "model": agent.model,
                              "reasoning_effort": agent.reasoning_effort, "recorded_at": self.now}]}
            if self.evidence_mutation:
                self.evidence_mutation(outcome.result)
        return outcome

    def integration(self):
        return self.spec.model_copy(update={"execution_scope": "integration", "inherited_authors": ({"provider": "codex", "session_id": "writer-1"},),
            "inherited_pm_sessions": ({"provider": "codex", "session_id": "planner-1"},)})

    def cli_spec(self, scope="planning"):
        agents = tuple(agent.model_copy(update={"verified_model": None, "verified_effort": None, "verification_evidence": None, "verified_ultracode": None}) for agent in self.spec.agents)
        return self.spec.model_copy(update={"execution_scope": scope, "approved_plan": scope != "planning", "agents": agents,
            "policy": self.spec.policy.model_copy(update={"configuration_evidence": "cli_configuration"})})

    def test_legacy_default_serialization_and_digests_do_not_change(self):
        self.assertEqual(digest(self.spec.policy), "6b57defe3d250e7605db1951797a4575d3e519d41bafecad99f9539456f525a3")
        normalized = self.spec.model_copy(update={"worktree": "/fixture/worktree", "task": self.task.model_copy(update={"base_sha": "a" * 40})})
        self.assertEqual(digest(normalized), "a35bdd5366810397917474eb74c5108149274be9d75d0a895c53fabea5ee587c")
        default_document = normalized.model_dump(mode="json")
        self.assertNotIn("configuration_evidence", default_document["policy"])
        self.assertNotIn("execution_scope", default_document)
        self.assertNotIn("inherited_authors", default_document)
        self.assertNotIn("inherited_pm_sessions", default_document)
        self.assertEqual(digest(FlowSpec.model_validate(default_document)), digest(normalized))
        self.assertNotEqual(digest(self.cli_spec().policy), digest(self.spec.policy))

    def test_planning_finishes_with_typed_plan_without_development(self):
        self.submit(self.spec.model_copy(update={"execution_scope": "planning", "approved_plan": False}))
        result = self.tick()
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["plan"], self.pm_plan)
        self.assertEqual(result["pm_sessions"], [{"provider": "codex", "session_id": "session-1"}])
        self.assertIsNone(result["resume_at"])
        self.assertIsNone(result["active"])
        self.restart(); self.tick()
        self.assertEqual([call["role"] for call in self.calls], ["pm"])
        self.assertEqual(self.git("rev-parse", "HEAD"), self.task.base_sha)

    def test_contribution_requires_commit_and_stops_before_checks(self):
        self.submit(self.spec.model_copy(update={"execution_scope": "contribution"}))
        result = self.tick()
        self.assertEqual(result["status"], "CONTRIBUTION_READY")
        self.assertIsNone(result["resume_at"])
        self.assertNotEqual(result["snapshot"]["head_commit"], self.task.base_sha)
        self.assertEqual(result["authors"], [{"provider": "codex", "session_id": "session-1"}])
        self.assertIsNone(result["verification"])
        job = self.dispatcher.queue.get(result["executions"][-1]["job_id"])
        self.assertEqual(job["result"]["structured_output"]["candidate_sha"], self.task.base_sha)
        self.assertEqual(job["result"]["runner_commit"]["candidate_sha"], result["snapshot"]["head_commit"])
        self.assertNotEqual(job["result"]["runner_commit"]["candidate_sha"], job["result"]["structured_output"]["candidate_sha"])
        self.restart(); self.tick()
        self.assertEqual(len(self.calls), 1)

    def test_integration_starts_at_check_and_inherits_review_exclusions(self):
        spec = self.integration()
        self.submit(spec)
        self.assertEqual(self.state()["stage"], "check")
        self.assertEqual(self.state()["authors"], list(spec.inherited_authors))
        self.assertEqual(self.state()["pm_sessions"], list(spec.inherited_pm_sessions))
        result = self.finish()
        self.assertEqual(result["status"], "DEMO_READY")
        self.assertEqual([call["role"] for call in self.calls], ["reviewer", "final"])

    def test_integration_check_failure_waits_for_role_repair_without_developer(self):
        self.verifier.fail_check = True
        self.submit(self.integration())
        result = self.tick()
        self.assertEqual(result["status"], "WAITING_ROLE_REPAIR")
        self.assertIsNone(result["resume_at"])
        self.assertTrue(result["findings"])
        self.assertFalse(result["verification"]["passed"])
        self.restart(); self.tick()
        self.assertEqual(self.calls, [])

    def test_integration_revise_preserves_findings_and_never_runs_developer(self):
        self.submit(self.integration()); self.tick(); self.script = ["reject"]
        result = self.tick()
        self.assertEqual(result["status"], "WAITING_ROLE_REPAIR")
        self.assertEqual(result["findings"][0]["finding_id"], "R1")
        self.assertIsNone(result["resume_at"])
        self.assertEqual(result["reviews"]["reviewer"]["verdict"], "REVISE")
        self.restart(); self.tick()
        self.assertEqual([call["role"] for call in self.calls], ["reviewer"])

    def test_integration_final_revise_also_waits_without_developer(self):
        self.submit(self.integration()); self.tick(); self.tick(); self.script = ["reject"]
        result = self.tick()
        self.assertEqual(result["status"], "WAITING_ROLE_REPAIR")
        self.assertEqual(result["reviews"]["final"]["verdict"], "REVISE")
        self.assertTrue(result["findings"])
        self.assertIsNone(result["resume_at"])
        self.restart(); self.tick()
        self.assertEqual([call["role"] for call in self.calls], ["reviewer", "final"])

    def test_inherited_author_cannot_pass_review(self):
        spec = self.integration().model_copy(update={"inherited_authors": ({"provider": "claude", "session_id": "session-1"},)})
        # Candidate ordering prefers Codex after a Claude author; bind that identity instead.
        spec = spec.model_copy(update={"inherited_authors": ({"provider": "codex", "session_id": "session-1"}, {"provider": "claude", "session_id": "session-1"})})
        self.submit(spec); self.tick(); result = self.tick()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("own work", result["reason"])

    def test_inherited_pm_cannot_pass_final_review(self):
        spec = self.integration().model_copy(update={"inherited_pm_sessions": ({"provider": "codex", "session_id": "session-2"},)})
        self.submit(spec); self.tick(); self.tick(); result = self.tick()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("own work", result["reason"])

    def test_cli_opt_in_accepts_turn_settings_without_fabricating_runtime_verification(self):
        spec = self.cli_spec()
        self.submit(spec); result = self.tick()
        self.assertEqual(result["status"], "PLAN_READY")
        for agent in spec.agents:
            self.assertIsNone(agent.verified_model); self.assertIsNone(agent.verified_effort)
        profiles, available, _ = self.dispatcher._candidates(self.cli_spec("contribution"), {"stage": "developer", "authors": []})
        self.assertEqual([a.provider for a in profiles], ["codex"])
        self.assertEqual([a.provider for a in available], ["codex"])
        verified_spec = self.spec.model_copy(update={"policy": spec.policy})
        profiles, _, _ = self.dispatcher._candidates(verified_spec, {"stage": "developer", "authors": []})
        self.assertEqual([a.provider for a in profiles], ["codex"])

    def test_strict_default_still_rejects_missing_runtime_metadata(self):
        self.submit(self.spec.model_copy(update={"execution_scope": "planning", "approved_plan": False}))
        self.script = ["missing_model"]
        result = self.tick()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("actual model", result["reason"])

    def test_cli_evidence_rejects_wrong_session_model_effort_window_and_backend_claim(self):
        mutations = [
            lambda r: r["configuration_evidence"].update(session_id=str(UUID(int=999))),
            lambda r: r["configuration_evidence"]["contexts"][0].update(model="different"),
            lambda r: r["configuration_evidence"]["contexts"][0].update(reasoning_effort="high"),
            lambda r: r["configuration_evidence"]["contexts"][0].update(recorded_at=self.now - 1),
            lambda r: r["configuration_evidence"].update(backend_model_verified=True),
            lambda r: r["configuration_evidence"].update(contexts=[]),
            lambda r: r["configuration_evidence"].update(cli_version="unknown"),
            lambda r: r.update(observed_models=["different"]),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                task = self.task.model_copy(update={"task_id": "invalid-cli-" + str(index)})
                spec = self.cli_spec().model_copy(update={"task": task})
                self.submit(spec); self.evidence_mutation = mutate
                self.dispatcher.run_once()
                result = self.dispatcher.get(task.task_id)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertIsNone(result["resume_at"])

    def test_task_allowlist_never_executes_unrelated_or_empty_selection(self):
        self.submit()
        other_task = self.task.model_copy(update={"task_id": "selected-task"})
        self.submit(self.spec.model_copy(update={"task": other_task}))
        self.assertEqual(self.dispatcher.run_once(task_ids=set())["status"], "IDLE")
        self.assertEqual(self.calls, [])
        self.dispatcher.run_once(task_ids={other_task.task_id})
        self.assertEqual(self.state()["generation"], 0)
        self.assertEqual(self.dispatcher.get(other_task.task_id)["usage"]["executions"], 1)
        self.assertEqual(len(self.calls), 1)

    def test_allowlist_recovers_foreign_wait_facts_before_selected_account_claim(self):
        self.submit(); self.script = [self.quota()]
        with patch.object(self.dispatcher, "_handle_job", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.tick()
        self.restart()
        self.assertEqual(self.state()["usage"]["executions"], 0)
        self.assertEqual(self.dispatcher.run_once(task_ids=set())["status"], "IDLE")
        self.assertEqual(self.state()["usage"]["executions"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.state()["active"]["agent_id"], "astra")
        other_task = self.task.model_copy(update={"task_id": "selected-after-quota"})
        self.submit(self.spec.model_copy(update={"task": other_task}))
        self.dispatcher.run_once(task_ids={other_task.task_id})
        self.assertEqual(self.calls[-1]["agent"], "claude")
        self.assertEqual(self.state()["active"]["agent_id"], "astra")
        self.assertEqual(self.state()["usage"]["executions"], 1)

    def test_scope_cannot_be_changed_by_policy_migration(self):
        self.submit()
        changed = self.spec.model_copy(update={"execution_scope": "contribution"})
        with self.assertRaises(ExecutionBlocked):
            self.dispatcher.migrate_policy(self.task.task_id, changed, "Must not weaken completion gates")

    def test_scope_validation_and_planning_prompt_schema_do_not_weaken_default_reports(self):
        with self.assertRaises(ValidationError):
            FlowSpec.model_validate(self.spec.model_copy(update={"execution_scope": "planning"}).model_dump(mode="json"))
        with self.assertRaises(ValidationError):
            FlowSpec.model_validate(self.spec.model_copy(update={"execution_scope": "integration"}).model_dump(mode="json"))
        self.submit(self.spec.model_copy(update={"execution_scope": "planning", "approved_plan": False})); self.tick()
        execution = self.state()["executions"][-1]
        report = self.dispatcher.queue.get(execution["job_id"])["result"]["structured_output"]
        PMPlanStageReport.model_validate(report)
        PMPlanStageReport.model_validate({**report, "verdict": "BLOCK", "plan": None})
        with self.assertRaises(ValidationError):
            PMPlanStageReport.model_validate({**report, "plan": None})
        with self.assertRaises(ValidationError):
            StageReport.model_validate(report)
        schema = PMPlanStageReport.model_json_schema()
        self.assertIn("roles", schema["properties"]["plan"]["anyOf"][0]["properties"])
        normal = stage_prompt("checkpoint", provider="codex", role="pm")
        planning = stage_prompt("checkpoint", provider="codex", role="pm", planning=True)
        self.assertIn("do not add a plan field", normal)
        self.assertNotIn("do not add a plan field", planning)
        self.assertIn("PMPlanStageReport", planning)
