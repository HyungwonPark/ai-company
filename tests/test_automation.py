"""Real SQLite/Git orchestration with explicitly fixture-only model/CI evidence."""
import copy
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.automation import Automation
from ai_company.automation_contracts import AutomationConfig, AutomationCI
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy, FlowSpec
from ai_company.management import ManagementStore


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.repo = self.root / "source"; self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "test")
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        self.git(self.repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
        (self.repo / "src").mkdir(); (self.repo / "tests").mkdir()
        (self.repo / "src/original.txt").write_text("original\n")
        self.git(self.repo, "add", "."); self.git(self.repo, "commit", "-qm", "initial")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        profiles = []
        for name, provider, effort, roles in [("pm", "codex", "ultra", ("pm",)),
                ("dev", "codex", "high", ("developer", "reviewer")),
                ("backup", "claude", "xhigh", ("developer", "reviewer")),
                ("final", "codex", "ultra", ("final",))]:
            model = "gpt-6-astra" if provider == "codex" else "claude-opus-5"
            profiles.append(AgentProfile(agent_id=name, provider=provider, requested_label=name,
                model=model, reasoning_effort=effort, roles=roles,
                ultracode_enabled=provider == "claude", verified_ultracode=provider == "claude",
                capabilities=("structured_result", "read_repository", "write_repository"),
                allowed_paths=("src/", "tests/", ".ai-company-ci/request.json"), credential_ref=provider,
                quota_group=provider, verified_model=model, verified_effort=effort, verification_evidence="fixture"))
        self.config = AutomationConfig(repository="owner/repo", source_clone=str(self.repo), base_sha=self.base,
            base_branch="main", allowed_paths=("src/", "tests/"), checks={"unit": CheckCommand(argv=("true",))},
            agents=tuple(profiles), policy=FlowPolicy(candidates={"pm": ("pm",), "developer": ("dev", "backup"),
                "reviewer": ("backup", "dev"), "final": ("final",)}),
            ci=AutomationCI(workflow_path=".github/workflows/automation-evidence.yml", workflow_digest="a" * 64,
                            required_checks=("automation-checks",)), mode="fixture")
        self.plan = {"summary": "Build two independently owned outputs", "roles": [
            {"key": "impl", "name": "Implementation", "responsibility": "Implement", "goal": "Create implementation",
             "acceptance": ["Implementation present"], "allowed_paths": ["src/result.txt"], "depends_on": []},
            {"key": "test", "name": "Test", "responsibility": "Test", "goal": "Create tests",
             "acceptance": ["Tests present"], "allowed_paths": ["tests/result.txt"], "depends_on": []}],
            "completion_criteria": ["Both outputs inspected together, checks and independent reviews pass"]}
        self.now = 1000.0; self.calls = []; self.lock = threading.Lock()
        self.active = 0; self.maximum = 0; self.barrier = None; self.reject_once = False; self.quota_once = False
        self.checks = []; self.remotes = []
        self.worker = self.open(); self.addCleanup(lambda: self.worker.close())
        self.project = self.worker.store.create_project({"name": "Automatic PM test", "goal": "Create two outputs"})
        self.worker.store.post_message(self.project["id"], {"content": "Plan this within src/ and tests/"})

    @staticmethod
    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True, stderr=subprocess.PIPE).strip()

    def open(self):
        def factory(root, **kwargs):
            return Dispatcher(root, verifier=self, executor=self.execute, **kwargs)
        return Automation(self.root / "state", self.config, clock=lambda: self.now, dispatcher_factory=factory)

    def check(self, spec, state):
        self.checks.append(state["snapshot"]["head_commit"])
        return dict(head_sha=state["snapshot"]["head_commit"], task_digest=digest(spec.task),
                    policy_digest=digest(spec.policy), checks=[dict(name="unit", success=True)],
                    passed=True, runtime_seconds=1, remote=None)

    def remote(self, spec, state):
        self.remotes.append(state["snapshot"]["head_commit"])
        return dict(source="fixture", head_sha=state["snapshot"]["head_commit"],
                    task_digest=digest(spec.task), policy_digest=digest(spec.policy), run_id=1)

    def execute(self, agent, state, provider, worktree, prompt, session_id, **kwargs):
        spec = FlowSpec.model_validate(state["specification"])
        with self.lock:
            self.calls.append((state["task_id"], state["stage"], str(worktree)))
            count = len(self.calls)
            if state["stage"] == "developer":
                self.active += 1; self.maximum = max(self.maximum, self.active)
                quota = self.quota_once
                self.quota_once = False
            else:
                quota = False
        sid = session_id or "fixture-session-" + str(count)
        if state["stage"] == "developer":
            try:
                if self.barrier:
                    self.barrier.wait(timeout=10)
                if quota:
                    return SessionOutcome("quota", session_id=sid, reset_at=self.now + 30,
                                          result={"duration_seconds": 1, "total_cost_usd": 0.01})
                path = Path(worktree) / spec.task.allowed_paths[0]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture output " + str(count) + "\n")
                time.sleep(0.03)
            finally:
                with self.lock:
                    self.active -= 1
        report = dict(execution_id=state["active"]["execution_id"], generation=state["generation"], role=state["stage"],
            task_digest=digest(spec.task), policy_digest=digest(spec.policy), candidate_sha=self.git(worktree, "rev-parse", "HEAD"),
            verification_digest=digest(state["verification"]) if state["verification"] else None,
            verdict="DONE" if state["stage"] == "developer" else "PASS", findings=[], resolved_findings=[], summary="Fixture result")
        if state["stage"] == "pm":
            plan = copy.deepcopy(self.plan)
            plan["requirements_review"] = {
                "version": 2, "revision": spec.plan["request_revision"],
                "goal_digest": spec.plan["goal_digest"], "problem": "Create two verifiable outputs",
                "users_and_flow": "A project owner requests and confirms two independent outputs",
                "scope": ["Create the two approved outputs"], "exclusions": [], "assumptions": [],
                "questions": [], "findings": [],
                "requirements": [{"id": "R-" + role["key"], "source": "master goal",
                    "acceptance": role["acceptance"][0], "verification": "Inspect the role output and run unit checks",
                    "role_keys": [role["key"]]} for role in plan["roles"]]}
            report["plan"] = plan
        elif state["stage"] == "developer":
            report["commit_requested"] = True
        elif state["stage"] == "reviewer" and spec.execution_scope != "plan_review" and self.reject_once:
            self.reject_once = False
            report.update(verdict="REVISE", findings=[dict(finding_id="FIX-IMPL", detail="Fix implementation output", evidence="src/result.txt")])
        return SessionOutcome("success", session_id=sid, result={"duration_seconds": 1, "total_cost_usd": 0.01,
            "observed_models": [agent.model], "observed_efforts": [agent.reasoning_effort],
            "observed_ultracode": [agent.ultracode_enabled], "structured_output": report})

    def confirm(self):
        for _ in range(4):
            self.worker.run_once()
            proposals = self.worker.store.overview(self.project["id"])["plans"]
            if proposals and proposals[0]["status"] == "proposed":
                break
        self.assertEqual(len(proposals), 1, self.worker.store.pm_requests())
        plan = proposals[0]
        self.assertEqual(plan["status"], "proposed", plan)
        self.assertEqual(self.worker.store.run_records(), [])
        return self.worker.store.confirm_plan(self.project["id"], plan["id"], {
            "plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
            "idempotency_key": "fixture-confirm-once"})["run"]

    def finish(self, limit=16):
        for _ in range(limit):
            result = self.worker.run_once()
            if result["runs"][0]["state"] in ("fixture_complete", "blocked"):
                return result["runs"][0]
            self.now += 60
        self.fail(str(result["runs"]))

    def test_plan_confirmation_parallel_clones_same_candidate_and_restart(self):
        run = self.confirm()
        self.barrier = threading.Barrier(2)
        self.worker.run_once()
        self.barrier = None
        self.assertEqual(self.maximum, 2)
        roles = self.worker.store.get_run(run["id"])["roles"]
        self.assertTrue(all(v["status"] == "CONTRIBUTION_READY" for v in roles.values()), roles)
        paths = [Path(self.worker.dispatcher.get(v["task_id"])["specification"]["worktree"]) for v in roles.values()]
        self.assertNotEqual(self.git(paths[0], "rev-parse", "--git-common-dir"), "")
        self.assertNotEqual(paths[0].resolve(), paths[1].resolve())
        self.assertTrue(all((p / ".git").is_dir() for p in paths))
        self.worker.close(); self.worker = self.open()
        result = self.finish()
        self.assertEqual(result["state"], "fixture_complete", result)
        self.assertEqual(self.worker.store.overview(self.project["id"])["readiness"]["execution"], "fixture_complete")
        sha = result["integration"]["candidate_sha"]
        self.assertEqual(set(self.checks + self.remotes), {sha})
        state = self.worker.dispatcher.get(result["integration"]["task_id"])
        self.assertEqual({r["candidate_sha"] for r in state["reviews"].values()}, {sha})
        self.assertEqual(len(state["authors"]), 2)
        before = len(self.calls)
        self.worker.run_once()
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.worker.store.overview(self.project["id"])["approvals"], [])

    def test_role_discussion_reaches_pm_without_starting_old_or_new_plan(self):
        self.worker.run_once()
        prior = self.worker.store.overview(self.project['id'])['plans'][0]
        followup = self.worker.store.post_message(self.project['id'], {'content': '검사 역할의 완료 조건을 더 쉽게 설명해주세요'})
        request = self.worker.store.get_pm_request(followup['id'])
        self.plan['roles'][1]['acceptance'] = ['입력이 비어 있어도 올바르게 처리합니다']
        self.worker.run_once()
        state = self.worker.dispatcher.get('pm-' + followup['id'])
        context = state['specification']['plan']
        self.assertEqual(context['conversation_context'], request['conversation_context'])
        self.assertEqual(context['conversation_context']['previous_proposal']['digest'], prior['digest'])
        self.assertEqual(context['master_message'], followup['content'])
        self.assertEqual(context['authorized_paths'], list(self.config.allowed_paths))
        self.assertTrue(all(stage == 'pm' for _, stage, _ in self.calls))
        self.assertEqual(self.worker.store.run_records(), [])
        self.assertEqual(self.worker.store.overview(self.project['id'])['plans'][-1]['content']['roles'][1]['acceptance'], self.plan['roles'][1]['acceptance'])

    def test_pm_clarification_is_readable_durable_and_not_a_plan(self):
        original = self.execute
        clarification = '화면과 서버 중 어떤 부분부터 만들까요? 현재 허용된 범위를 확인해주세요.'
        def ask(*args, **kwargs):
            result = original(*args, **kwargs)
            report = result.result['structured_output']
            report.update(verdict='BLOCK', plan=None, summary=clarification)
            return result
        self.execute = ask
        self.worker.run_once()
        overview = self.worker.store.overview(self.project['id'])
        request = overview['pm_requests'][0]
        self.assertEqual(request['state'], 'blocked')
        self.assertEqual(overview['messages'][-1]['content'], clarification)
        self.assertEqual(overview['plans'], [])
        self.assertEqual(overview['runs'], [])
        self.worker.close(); self.worker = self.open()
        self.worker.run_once()
        self.assertEqual(len(self.worker.store.overview(self.project['id'])['messages']), 2)
        followup = self.worker.store.post_message(self.project['id'], {'content': '허용된 서버 기능부터 만들어요'})
        self.assertEqual(self.worker.store.get_pm_request(followup['id'])['conversation_context']['messages'][-1]['content'], clarification)

    def test_revision_returns_to_responsible_role_without_repeating_other_role(self):
        self.confirm(); self.reject_once = True
        result = self.finish(24)
        self.assertEqual(result["state"], "fixture_complete", result)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["roles"]["impl"]["revision"], 1)
        self.assertEqual(result["roles"]["test"]["revision"], 0)
        self.assertEqual(len(result["integration_history"]), 1)
        self.assertNotEqual(result["integration_history"][0]["candidate_sha"], result["integration"]["candidate_sha"])

    def test_quota_handoff_is_visible_and_other_independent_role_continues(self):
        self.confirm(); self.quota_once = True
        result = self.finish(24)
        self.assertEqual(result["state"], "fixture_complete", result)
        states = [self.worker.dispatcher.get(item["task_id"]) for item in result["roles"].values()]
        self.assertTrue(any(s["generation"] >= 2 for s in states))
        overview = self.worker.store.overview(self.project["id"])
        self.assertTrue(any(task["handoffs"] for task in overview["tasks"]))

    def test_crash_after_role_submit_before_link_does_not_duplicate_execution(self):
        self.confirm()
        class Crash(BaseException):
            pass
        with patch.object(self.worker.store, "link_task", side_effect=Crash()):
            with self.assertRaises(Crash):
                self.worker.reconcile()
        self.worker.close(); self.worker = self.open()
        result = self.finish()
        self.assertEqual(result["state"], "fixture_complete", result)
        self.assertEqual(sum(role == "developer" for _, role, _ in self.calls), 2)

    def test_out_of_scope_pm_proposal_cannot_be_confirmed(self):
        self.plan["roles"][0]["allowed_paths"] = ["outside.txt"]
        self.worker.run_once()
        self.assertEqual(self.worker.store.pm_requests()[0]["state"], "blocked")
        self.assertEqual(self.worker.store.overview(self.project["id"])["plans"], [])


    def test_crash_after_pm_submit_preserves_fixture_binding_against_live_worker(self):
        class Crash(BaseException):
            pass
        submit = self.worker.dispatcher.submit
        def submitted_then_crash(spec):
            submit(spec)
            raise Crash()
        with patch.object(self.worker.dispatcher, "submit", side_effect=submitted_then_crash):
            with self.assertRaises(Crash):
                self.worker.reconcile()
        request = self.worker.store.pm_requests()[0]
        self.assertEqual(request["configuration_digest"], self.worker.configuration_digest)
        self.assertEqual(request["mode"], "fixture")
        self.assertEqual(len(self.worker.dispatcher.tasks()), 1)
        other = Automation(self.root / "state", self.config.model_copy(update={"mode": "live"}),
                           clock=lambda: self.now, dispatcher_factory=self.worker.dispatcher_factory)
        try:
            other.run_once()
            self.assertEqual(self.calls, [])
            self.assertEqual(other.store.pm_requests()[0]["mode"], "fixture")
            self.assertEqual(other.store.overview(self.project["id"])["plans"], [])
        finally:
            other.close()



    def test_repaired_dependent_role_receives_current_dependency_candidate(self):
        self.plan["roles"].append({"key": "consumer", "name": "Consumer", "responsibility": "Use implementation", "goal": "Create consumer",
            "acceptance": ["Consumer uses implementation"], "allowed_paths": ["src/consumer.txt"], "depends_on": ["impl"]})
        observed_inputs = []
        original = self.execute
        def record_dependency(agent, state, provider, worktree, prompt, session_id, **kwargs):
            spec = FlowSpec.model_validate(state["specification"])
            if state["stage"] == "developer" and spec.plan["role"]["key"] == "consumer":
                observed_inputs.append((Path(worktree) / "src/result.txt").read_text())
            result = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            report = (result.result or {}).get("structured_output", {})
            if state["stage"] == "reviewer" and report.get("verdict") == "REVISE":
                report["findings"][0]["evidence"] = "src/result.txt and src/consumer.txt"
            return result
        self.execute = record_dependency
        self.confirm(); self.reject_once = True
        result = self.finish(30)
        self.assertEqual(result["state"], "fixture_complete", result)
        self.assertEqual(result["roles"]["consumer"]["revision"], 1)
        self.assertEqual(len(observed_inputs), 2)
        self.assertNotEqual(observed_inputs[0], observed_inputs[1])
        repaired = self.worker.dispatcher.get(result["roles"]["impl"]["task_id"])
        self.assertEqual(observed_inputs[1], (Path(repaired["specification"]["worktree"]) / "src/result.txt").read_text())

    def test_blocked_branch_does_not_prevent_ready_dependency_chain_from_starting(self):
        self.plan["roles"].append({"key": "consumer", "name": "Consumer", "responsibility": "Use tests", "goal": "Create consumer",
            "acceptance": ["Consumer uses tests"], "allowed_paths": ["tests/consumer.txt"], "depends_on": ["test"]})
        original = self.execute
        def block_implementation(agent, state, provider, worktree, prompt, session_id, **kwargs):
            result = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
            spec = FlowSpec.model_validate(state["specification"])
            if state["stage"] == "developer" and spec.plan["role"]["key"] == "impl":
                result.category = "permission"
            return result
        self.execute = block_implementation
        run = self.confirm()
        for _ in range(8):
            self.worker.run_once(); self.now += 60
        result = self.worker.store.get_run(run["id"])
        self.assertEqual(result["roles"]["impl"]["status"], "BLOCKED")
        self.assertEqual(result["roles"]["test"]["status"], "CONTRIBUTION_READY")
        self.assertEqual(result["roles"]["consumer"]["status"], "CONTRIBUTION_READY")
        self.assertFalse(result.get("integration"))



if __name__ == "__main__":
    unittest.main()
