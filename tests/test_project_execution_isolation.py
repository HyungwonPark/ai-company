"""Independent project-boundary scenarios using temporary Git and SQLite only.

No model, network Git operation, production API, or worker process is invoked.
Assertions concern preserved authority and effects, not implementation layout.
"""

import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.legacy_pm import use_legacy_requests
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from pydantic import ValidationError

from ai_company.automation import Automation
from ai_company.adapters.session_cli import SessionOutcome
from ai_company.automation_contracts import AutomationCI, AutomationConfig
from ai_company.contracts import Task, digest
from ai_company.dispatcher import Dispatcher
from ai_company.execution_specs import ExecutionCatalog
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy, FlowSpec, ProjectBudget
from ai_company.management import ManagementError, ManagementStore
from ai_company.runtime import ExecutionBlocked


class ProjectExecutionIsolationTests(unittest.TestCase):
    def setUp(self):
        use_legacy_requests(self)
        temporary = tempfile.TemporaryDirectory(prefix="project-execution-isolation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 1000.0
        self.sequence = 0
        self.calls = []
        agents = []
        for name, provider, effort, roles in [
            ("pm", "codex", "ultra", ("pm",)),
            ("dev", "codex", "high", ("developer", "reviewer")),
            ("backup", "claude", "xhigh", ("developer", "reviewer")),
            ("final", "codex", "ultra", ("final",)),
        ]:
            model = "gpt-6-astra" if provider == "codex" else "claude-opus-5"
            agents.append(AgentProfile(
                agent_id=name, provider=provider, requested_label=name, model=model,
                reasoning_effort=effort, roles=roles, ultracode_enabled=provider == "claude",
                verified_ultracode=provider == "claude", verified_model=model,
                verified_effort=effort, verification_evidence="fixture-only",
                capabilities=("structured_result", "read_repository", "write_repository"),
                allowed_paths=("src/", "tests/", ".ai-company-ci/request.json"),
                credential_ref=provider + "-account", quota_group=provider + "-shared",
            ))
        self.configs = {}
        for name in ("alpha", "beta"):
            repository = self.root / name
            repository.mkdir()
            self.git(repository, "init", "-q")
            self.git(repository, "config", "user.name", "fixture")
            self.git(repository, "config", "user.email", "fixture@example.invalid")
            self.git(repository, "remote", "add", "origin", f"https://github.com/fixture/{name}.git")
            (repository / "src").mkdir()
            (repository / "tests").mkdir()
            (repository / "src/original.txt").write_text(name + " original\n")
            self.git(repository, "add", ".")
            self.git(repository, "commit", "-qm", "fixture base")
            self.configs[name] = AutomationConfig(
                repository=f"fixture/{name}", source_clone=str(repository),
                base_sha=self.git(repository, "rev-parse", "HEAD"), base_branch="main",
                allowed_paths=("src/", "tests/"), checks={"unit": CheckCommand(argv=("true",))},
                agents=tuple(agents), policy=FlowPolicy(
                    candidates={"pm": ("pm",), "developer": ("dev", "backup"),
                                "reviewer": ("backup", "dev"), "final": ("final",)},
                    max_executions=16, max_runtime_seconds=600, max_repairs=2),
                ci=AutomationCI(workflow_path=".github/workflows/fixture.yml",
                                workflow_digest="a" * 64, required_checks=("fixture-check",)),
                mode="fixture",
            )
        self.catalog = ExecutionCatalog(self.configs)
        self.store = ManagementStore(self.root / "state", clock=lambda: self.now,
                                     execution_catalog=self.catalog)
        self.addCleanup(self.store.close)
        self.dispatcher = Dispatcher(self.root / "state", clock=lambda: self.now,
                                     executor=self.no_execution)
        self.addCleanup(lambda: self.dispatcher.close())

    @staticmethod
    def git(repository, *args):
        return subprocess.check_output(
            ["git", "-C", str(repository), "-c", "core.hooksPath=/dev/null", *args],
            text=True, stderr=subprocess.PIPE).strip()

    def no_execution(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("This boundary scenario must not invoke an executor")

    def project(self, name):
        return self.store.create_project({"name": name, "goal": name + "의 고정된 목표"})

    def register(self, project, catalog_id="alpha", *, base_version=0, **selection):
        self.sequence += 1
        return self.store.register_execution_spec(project["id"], {
            "base_version": base_version, "idempotency_key": f"spec-intent-{self.sequence}",
            "selection": {"catalog_id": catalog_id,
                          "catalog_digest": digest(self.configs[catalog_id]), **selection},
        })

    def claimed_request(self, project, specification):
        message = self.store.post_message(project["id"], {"content": "이 명세 안에서 역할을 제안하세요."})
        request = self.store.get_pm_request(message["id"])
        config = self.store.execution_config_for(
            project["id"], self.store.execution_spec_reference(specification))
        return self.store.save_pm_request({**request, "state": "running",
                                           "configuration_digest": digest(config), "mode": "fixture"},
                                          expected_state="pending")

    @staticmethod
    def plan_content():
        return {"summary": "두 책임을 독립적으로 수행하는 모의 계획", "roles": [
            {"key": key, "name": key, "responsibility": key + " 책임", "goal": key + " 결과",
             "acceptance": ["원래 제한을 지킵니다."], "allowed_paths": [path], "depends_on": []}
            for key, path in [("impl", "src/result.txt"), ("test", "tests/result.txt")]
        ], "completion_criteria": ["두 결과를 같은 후보에서 확인합니다."]}

    def proposal(self, project, specification):
        request = self.claimed_request(project, specification)
        return self.store.complete_pm_request(request["request_id"], self.plan_content(), evidence={"source": "fixture"})

    def confirmation(self, plan, specification, key="confirm-original-intent"):
        return {"plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
                "execution_spec_digest": specification["digest"], "idempotency_key": key}

    def confirm(self, project, plan, specification, key="confirm-original-intent"):
        return self.store.confirm_plan(project["id"], plan["id"], self.confirmation(plan, specification, key))

    def submit_flow(self, project, plan, specification, task_id, *, project_budget=None, worktree=None):
        reference = self.store.execution_spec_reference(specification)
        config = self.store.execution_config_for(project["id"], reference)
        task = Task(task_id=task_id, repository=config.repository, base_sha=config.base_sha,
                    goal="격리된 모의 작업", acceptance=("No execution",),
                    allowed_paths=("src/result.txt",), required_checks=tuple(config.checks),
                    max_repairs=config.policy.max_repairs)
        return self.dispatcher.submit(FlowSpec(
            task=task, worktree=str(worktree or config.source_clone), agents=config.agents, policy=config.policy,
            checks=config.checks, mode="fixture", approved_plan=True, execution_scope="contribution",
            project_budget=project_budget,
            plan={"plan_digest": plan["digest"], "project_id": project["id"],
                  "execution_spec": reference, "automation_configuration": digest(config)},
        ))

    @staticmethod
    def budget(project, *, executions=1, runtime=600):
        return ProjectBudget(scope_id=project["id"], max_executions=executions,
                             max_runtime_seconds=runtime, max_repairs=2)

    def recorded_failure(self, *args, **kwargs):
        self.calls.append({"task_id": args[1]["task_id"], "timeout_seconds": kwargs["timeout_seconds"]})
        return SessionOutcome("code_error", session_id="fixture-failed-execution",
                              result={"duration_seconds": 3.0, "total_cost_usd": 0.01})

    def test_registration_does_not_start_pm_or_authorize_execution(self):
        project = self.project("명세 등록만")
        specification = self.register(project)
        self.assertEqual(self.store.get_execution_spec(project["id"], specification["version"]), specification)
        self.assertEqual(self.store.pm_requests(project["id"]), [])
        self.assertEqual(self.store.run_records(project["id"]), [])
        self.assertEqual(self.dispatcher.tasks(), [])
        self.assertEqual(self.store.overview(project["id"])["approvals"], [])
        self.assertEqual(self.calls, [])

    def test_old_unconfirmed_plan_cannot_be_confirmed_after_new_specification(self):
        project = self.project("명세 전환")
        first = self.register(project)
        plan = self.proposal(project, first)
        before = copy.deepcopy(self.store.get_plan(project["id"], plan["id"]))
        second = self.register(project, base_version=first["version"], budget={"max_executions": 8})
        with self.assertRaises(ManagementError):
            self.confirm(project, plan, first)
        self.assertEqual(self.store.run_records(project["id"]), [])
        self.assertEqual(self.store.get_plan(project["id"], plan["id"])["digest"], before["digest"])
        self.assertEqual(self.store.get_execution_spec(project["id"], first["version"]), first)
        self.assertNotEqual(first["digest"], second["digest"])

    def test_new_specification_preserves_old_confirmation_receipt_and_effective_config(self):
        project = self.project("확정 보존")
        first = self.register(project)
        reference = self.store.execution_spec_reference(first)
        original_config = self.store.execution_config_for(project["id"], reference).model_dump(mode="json")
        plan = self.proposal(project, first)
        confirmed = self.confirm(project, plan, first)
        approval = self.store.request_approval(project["id"], {
            "title": "이전 후보 검토", "action": "accept_development_result", "environment": "draft-pr",
            "artifact_sha": self.configs["alpha"].base_sha, "cost_usd": 0, "expires_at": self.now + 3600,
            "impact": "이 후보의 수용만 기록", "rollback": "이전 후보 유지", "verification": "모의 원문 근거",
        })
        self.register(project, base_version=first["version"], budget={"max_executions": 8})
        repeated = self.confirm(project, plan, first)
        self.assertEqual(repeated, confirmed)
        self.assertEqual(self.store.get_run(confirmed["run"]["id"])["execution_spec"], reference)
        self.assertEqual(self.store.execution_config_for(project["id"], reference).model_dump(mode="json"), original_config)
        self.assertEqual(len(self.store.run_records(project["id"])), 1)
        current_approval = next(item for item in self.store.overview(project["id"])["approvals"] if item["id"] == approval["id"])
        self.assertEqual(current_approval, approval, "a new specification never rewrites the old approval subject")

    def test_late_pm_completion_retains_old_specification_without_activating_old_plan(self):
        project = self.project("늦은 PM 결과")
        first = self.register(project)
        request = self.claimed_request(project, first)
        second = self.register(project, base_version=first["version"], budget={"max_executions": 8})
        late = self.store.complete_pm_request(request["request_id"], self.plan_content(), evidence={"source": "fixture"})
        self.assertEqual(late["status"], "stale")
        self.assertEqual(late["execution_spec"], self.store.execution_spec_reference(first))
        self.assertEqual(self.store.get_pm_request(request["request_id"])["state"], "stale")
        current_message = self.store.post_message(project["id"], {"content": "새 명세로 다시 제안해주세요."})
        current = self.store.get_pm_request(current_message["id"])
        self.assertEqual(current["execution_spec"], self.store.execution_spec_reference(second))
        self.assertEqual(current["state"], "pending")
        self.assertEqual(self.store.run_records(project["id"]), [])

    def test_project_reference_and_plan_cannot_be_borrowed_by_another_project(self):
        first_project, other_project = self.project("첫 프로젝트"), self.project("다른 프로젝트")
        first, other = self.register(first_project), self.register(other_project)
        reference = self.store.execution_spec_reference(first)
        self.assertNotEqual(first["digest"], other["digest"], "same selection has project-bound identity")
        with self.assertRaises((ManagementError, ExecutionBlocked)):
            self.store.execution_config_for(other_project["id"], reference)
        plan = self.proposal(first_project, first)
        with self.assertRaises(ManagementError):
            self.confirm(other_project, plan, first)
        self.assertEqual(self.store.run_records(other_project["id"]), [])

    def test_late_task_of_old_run_keeps_original_harness_and_specification(self):
        project = self.project("늦은 이전 작업")
        first = self.register(project)
        original_plan = self.proposal(project, first)
        original = self.confirm(project, original_plan, first)["run"]
        second = self.register(project, base_version=first["version"], budget={"max_executions": 8})
        new_plan = self.proposal(project, second)
        new_run = self.confirm(project, new_plan, second, "confirm-second-intent")["run"]
        self.assertNotEqual(original["harness_version"], new_run["harness_version"])
        task = self.submit_flow(project, original_plan, first, "late-original-role")
        link = self.store.link_task(project["id"], original["role_ids"]["impl"], task["task_id"])
        self.assertEqual(link["harness_version"], original["harness_version"], "late work must not inherit a newer harness")
        state = self.dispatcher.get(task["task_id"])
        self.assertEqual(state["specification"]["plan"]["execution_spec"], self.store.execution_spec_reference(first))

    def test_an_unlinked_task_cannot_first_be_claimed_by_another_projects_role(self):
        first_project, other_project = self.project("작업 원소유자"), self.project("다른 역할 소유자")
        first, other = self.register(first_project), self.register(other_project, "beta")
        first_plan, other_plan = self.proposal(first_project, first), self.proposal(other_project, other)
        first_run = self.confirm(first_project, first_plan, first)["run"]
        other_run = self.confirm(other_project, other_plan, other)["run"]
        task = self.submit_flow(first_project, first_plan, first, "unlinked-owned-task")
        with self.assertRaises(ManagementError):
            self.store.link_task(other_project["id"], other_run["role_ids"]["impl"], task["task_id"])
        self.assertEqual(self.store.overview(other_project["id"])["tasks"], [])
        linked = self.store.link_task(first_project["id"], first_run["role_ids"]["impl"], task["task_id"])
        self.assertEqual(linked["execution_spec"], self.store.execution_spec_reference(first))
        self.assertEqual(self.dispatcher.get(task["task_id"])["specification"]["task"]["repository"], "fixture/alpha")

    def test_catalog_retirement_does_not_rewrite_a_terminal_runs_recorded_outcome(self):
        project = self.project("완료 기록 보존")
        specification = self.register(project)
        plan = self.proposal(project, specification)
        run = self.confirm(project, plan, specification)["run"]
        terminal = self.store.save_run({**run, "state": "fixture_complete", "reason": "모의 완료 원문"})
        retired_catalog = ExecutionCatalog({"beta": self.configs["beta"]})
        worker = Automation(self.root / "state", self.configs["beta"], clock=lambda: self.now,
                            execution_catalog=retired_catalog)
        try:
            worker.reconcile()
        finally:
            worker.close()
        self.assertEqual(self.store.get_run(run["id"]), terminal,
                         "retiring a catalog entry must not relabel historical completion as blocked")

    def test_shared_account_cooldown_survives_other_project_spec_and_restart(self):
        first_project, other_project = self.project("한도 발생"), self.project("한도 공유")
        first = self.register(first_project, candidates={"developer": ["dev"]})
        first_plan = self.proposal(first_project, first)
        self.confirm(first_project, first_plan, first)
        self.submit_flow(first_project, first_plan, first, "quota-origin")
        with self.dispatcher.db:
            self.dispatcher.db.execute("UPDATE quota_groups SET state='COOLDOWN',resume_at=?,reason='fixture quota' WHERE group_id=?",
                                       (self.now + 600, "codex-shared"))
        other = self.register(other_project, "beta", candidates={"developer": ["dev"]})
        other_plan = self.proposal(other_project, other)
        self.confirm(other_project, other_plan, other)
        self.submit_flow(other_project, other_plan, other, "quota-other-project")
        self.register(other_project, "beta", base_version=other["version"], candidates={"developer": ["dev"]},
                      budget={"max_executions": 8})
        self.dispatcher.close()
        self.dispatcher = Dispatcher(self.root / "state", clock=lambda: self.now, executor=self.no_execution)
        self.dispatcher.run_once(task_ids={"quota-other-project"})
        waiting = self.dispatcher.get("quota-other-project")
        self.assertEqual(waiting["status"], "WAITING_CAPACITY")
        self.assertGreaterEqual(waiting["resume_at"], self.now + 600)
        self.assertEqual(self.calls, [])
        self.assertEqual(tuple(self.dispatcher.db.execute(
            "SELECT credential_ref,group_id FROM credential_groups WHERE provider='codex'").fetchone()),
            ("codex-account", "codex-shared"))

    def test_catalog_digest_and_executable_fields_cannot_be_forged_in_selection(self):
        project = self.project("등록 입력 경계")
        forbidden = [
            {"catalog_digest": "f" * 64}, {"source_clone": "/etc"},
            {"checks": {"unit": {"argv": ["sh", "-c", "true"]}}},
            {"credential_ref": "fresh-account"}, {"quota_group": "fresh-quota"},
            {"verified_model": "requested-is-not-observed"}, {"allowed_paths": ["../outside"]},
            {"candidates": {"final": ["dev"]}}, {"budget": {"max_executions": 17}},
        ]
        for change in forbidden:
            with self.subTest(change=change):
                self.sequence += 1
                with self.assertRaises((ManagementError, ExecutionBlocked, ValidationError, ValueError)):
                    self.store.register_execution_spec(project["id"], {
                        "base_version": 0, "idempotency_key": f"invalid-selection-{self.sequence}",
                        "selection": {"catalog_id": "alpha", "catalog_digest": digest(self.configs["alpha"]), **change},
                    })
        self.assertEqual(self.store.pm_requests(project["id"]), [])
        self.assertEqual(self.dispatcher.tasks(), [])

    def test_new_specification_and_task_cannot_reset_consumed_project_allowance(self):
        project = self.project("누적 예산")
        first = self.register(project, budget={"max_executions": 1})
        first_plan = self.proposal(project, first)
        self.confirm(project, first_plan, first)
        self.submit_flow(project, first_plan, first, "budget-consumed", project_budget=self.budget(project))
        self.dispatcher.executor = self.recorded_failure
        self.dispatcher.run_once(task_ids={"budget-consumed"})
        consumed = self.dispatcher.get("budget-consumed")
        self.assertEqual(consumed["usage"]["executions"], 1)
        self.assertNotIn("project_reservation", consumed, "a recorded attempt replaces its reservation")
        second = self.register(project, base_version=first["version"], budget={"max_executions": 1})
        second_plan = self.proposal(project, second)
        self.confirm(project, second_plan, second, "confirm-budget-version-two")
        self.submit_flow(project, second_plan, second, "budget-new-version", project_budget=self.budget(project))
        self.dispatcher.close()
        self.dispatcher = Dispatcher(self.root / "state", clock=lambda: self.now, executor=self.recorded_failure)
        self.dispatcher.run_once(task_ids={"budget-new-version"})
        blocked = self.dispatcher.get("budget-new-version")
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(len(self.calls), 1, "a new version and restart must not grant another execution")
        self.dispatcher.run_once(task_ids={"budget-consumed", "budget-new-version"})
        self.assertEqual(sum(item["usage"]["executions"] for item in self.dispatcher.tasks()), 1)

    def test_inflight_reservation_prevents_parallel_overcommit_and_limits_timeout(self):
        project = self.project("동시 예산 예약")
        specification = self.register(project)
        plan = self.proposal(project, specification)
        self.confirm(project, plan, specification)
        clone = self.root / "independent-second-clone"
        self.git(self.root, "clone", "--quiet", "--no-hardlinks", str(self.configs["alpha"].source_clone), str(clone))
        self.git(clone, "remote", "set-url", "origin", "https://github.com/fixture/alpha.git")
        budget = self.budget(project, runtime=11)
        self.submit_flow(project, plan, specification, "budget-running", project_budget=budget)
        self.submit_flow(project, plan, specification, "budget-parallel", project_budget=budget, worktree=clone)
        started, release = Event(), Event()

        def held_execution(*args, **kwargs):
            self.calls.append({"task_id": args[1]["task_id"], "timeout_seconds": kwargs["timeout_seconds"]})
            started.set()
            if not release.wait(8):
                raise AssertionError("test did not release the held fixture executor")
            return SessionOutcome("code_error", session_id="fixture-held-execution",
                                  result={"duration_seconds": 3.0, "total_cost_usd": 0.01})

        def run_first():
            worker = Dispatcher(self.root / "state", clock=lambda: self.now, executor=held_execution)
            try:
                return worker.run_once(task_ids={"budget-running"})
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_first)
            try:
                self.assertTrue(started.wait(5), "first execution reached the reserved external boundary")
                self.assertIn("project_reservation", self.dispatcher.get("budget-running"))
                self.dispatcher.run_once(task_ids={"budget-parallel"})
                self.assertEqual(self.dispatcher.get("budget-parallel")["status"], "WAITING_PROJECT_BUDGET")
                self.assertEqual(len(self.calls), 1)
                self.assertLessEqual(self.calls[0]["timeout_seconds"], 11)
            finally:
                release.set()
            future.result(timeout=10)
        self.assertNotIn("project_reservation", self.dispatcher.get("budget-running"))

    def test_legacy_task_usage_is_included_without_rewriting_the_legacy_specification(self):
        project = self.project("기존 사용량")
        message = self.store.post_message(project["id"], {"content": "기존 설정으로 제안하세요."})
        request = self.store.get_pm_request(message["id"])
        config = self.configs["alpha"]
        self.store.save_pm_request({**request, "state": "running", "configuration_digest": digest(config), "mode": "fixture"})
        old_plan = self.store.complete_pm_request(message["id"], self.plan_content(), evidence={"source": "fixture"})
        old_run = self.store.confirm_plan(project["id"], old_plan["id"], {
            "plan_digest": old_plan["digest"], "base_harness_version": old_plan["base_harness_version"],
            "idempotency_key": "legacy-confirmation",
        })["run"]
        legacy = FlowSpec(task=Task(task_id="legacy-consumed", repository=config.repository, base_sha=config.base_sha,
                                   goal="기존 모의 작업", acceptance=("No real execution",), allowed_paths=("src/result.txt",),
                                   required_checks=tuple(config.checks)),
                          worktree=config.source_clone, agents=config.agents, policy=config.policy, checks=config.checks,
                          approved_plan=True, execution_scope="contribution", mode="fixture",
                          plan={"plan_digest": old_plan["digest"], "automation_configuration": digest(config)})
        state = self.dispatcher.submit(legacy)
        self.store.link_task(project["id"], old_run["role_ids"]["impl"], state["task_id"])
        self.dispatcher.executor = self.recorded_failure
        self.dispatcher.run_once(task_ids={state["task_id"]})
        self.assertEqual(self.dispatcher.get(state["task_id"])["usage"]["executions"], 1)
        current = self.register(project, budget={"max_executions": 1})
        new_plan = self.proposal(project, current)
        self.confirm(project, new_plan, current)
        self.submit_flow(project, new_plan, current, "new-after-legacy", project_budget=self.budget(project))
        self.dispatcher.run_once(task_ids={"new-after-legacy"})
        self.assertEqual(self.dispatcher.get("new-after-legacy")["status"], "BLOCKED")
        self.assertEqual(len(self.calls), 1, "registering the first project budget must not erase legacy usage")
        self.assertEqual(self.dispatcher.get(state["task_id"])["spec_digest"], state["spec_digest"])
        self.assertNotIn("project_budget", self.dispatcher.get(state["task_id"])["specification"])

    def test_a_new_cost_cap_waits_for_an_older_execution_with_unbounded_cost(self):
        project = self.project("비용 상한 전환")
        first = self.register(project)
        old_plan = self.proposal(project, first)
        self.confirm(project, old_plan, first)
        self.submit_flow(project, old_plan, first, "unbounded-running",
                         project_budget=self.budget(project, executions=16))
        second = self.register(project, base_version=first["version"], budget={"max_cost_usd": 1.0})
        new_plan = self.proposal(project, second)
        self.confirm(project, new_plan, second, "cost-cap-confirmation")
        clone = self.root / "new-cost-cap-clone"
        self.git(self.root, "clone", "--quiet", "--no-hardlinks", self.configs["alpha"].source_clone, str(clone))
        self.git(clone, "remote", "set-url", "origin", "https://github.com/fixture/alpha.git")
        capped = self.budget(project, executions=16).model_copy(update={"max_cost_usd": 1.0})
        self.submit_flow(project, new_plan, second, "new-capped-call", project_budget=capped, worktree=clone)
        started, release = Event(), Event()

        def held_execution(*args, **kwargs):
            started.set()
            if not release.wait(8):
                raise AssertionError("unbounded fixture execution was not released")
            return SessionOutcome("code_error", session_id="unbounded-fixture",
                                  result={"duration_seconds": 3.0, "total_cost_usd": 0.01})

        def run_first():
            worker = Dispatcher(self.root / "state", clock=lambda: self.now, executor=held_execution)
            try:
                return worker.run_once(task_ids={"unbounded-running"})
            finally:
                worker.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_first)
            try:
                self.assertTrue(started.wait(5))
                self.dispatcher.run_once(task_ids={"new-capped-call"})
                self.assertEqual(self.calls, [], "an unknown older cost must not be treated as zero")
                self.assertEqual(self.dispatcher.get("new-capped-call")["status"], "WAITING_PROJECT_BUDGET")
            finally:
                release.set()
            future.result(timeout=10)

    def test_waiting_result_crash_replaces_project_reservation_with_usage_exactly_once(self):
        class InterruptedAfterDurableResult(BaseException):
            pass

        for category in ("quota", "transient_network"):
            with self.subTest(category=category):
                project = self.project("예약 회수 " + category)
                specification = self.register(project, candidates={"developer": ["dev"]})
                plan = self.proposal(project, specification)
                self.confirm(project, plan, specification)
                task_id = "crash-" + category
                self.submit_flow(project, plan, specification, task_id,
                                 project_budget=self.budget(project, executions=2))

                def waiting_execution(*args, **kwargs):
                    self.calls.append(task_id)
                    return SessionOutcome(category, session_id="fixture-wait-" + category,
                                          reset_at=5000 if category == "quota" else None,
                                          result={"duration_seconds": 3.0, "total_cost_usd": 0.01})

                # Each scenario begins with available shared capacity. This is a
                # fixture setup, never a reset performed by project registration.
                with self.dispatcher.db:
                    self.dispatcher.db.execute("UPDATE quota_groups SET state='AVAILABLE',resume_at=NULL")
                self.dispatcher.executor = waiting_execution
                with patch.object(self.dispatcher, "_handle_job", side_effect=InterruptedAfterDurableResult):
                    with self.assertRaises(InterruptedAfterDurableResult):
                        self.dispatcher.run_once(task_ids={task_id})
                self.assertEqual(self.dispatcher.get(task_id)["usage"]["executions"], 0)
                self.assertIn("project_reservation", self.dispatcher.get(task_id))
                self.dispatcher.close()
                self.dispatcher = Dispatcher(self.root / "state", clock=lambda: self.now, executor=self.no_execution)
                before = len(self.calls)
                self.dispatcher.run_once(task_ids={task_id})
                self.dispatcher.run_once(task_ids={task_id})
                state = self.dispatcher.get(task_id)
                self.assertEqual(len(self.calls), before)
                self.assertEqual(state["usage"]["executions"], 1)
                self.assertEqual(state["usage"]["runtime_seconds"], 3.0)
                self.assertAlmostEqual(state["usage"]["cost_usd"], 0.01)
                self.assertNotIn("project_reservation", state)
                self.assertGreater(state["resume_at"], self.now)

    def test_local_checks_use_remaining_project_time_and_leave_no_new_model_allowance(self):
        project = self.project("검사 시간도 누적")
        specification = self.register(project)
        plan = self.proposal(project, specification)
        self.confirm(project, plan, specification)
        budget = self.budget(project, executions=16, runtime=7)
        state = self.submit_flow(project, plan, specification, "checking-project-time", project_budget=budget)
        # Reconstruct the durable boundary after development. The actual Verifier
        # executes against this clean local candidate; only process launch is fake.
        state.update(stage="check", status="READY", active=None)
        with self.dispatcher.db:
            self.dispatcher._save(state, "fixture_candidate_ready_for_checks")
        elapsed, timeouts = [0.0], []

        class CheckProcess:
            pid = 99999999

            def wait(self, timeout):
                timeouts.append(timeout)
                elapsed[0] += timeout
                return 0

        original_popen = subprocess.Popen

        def launch(argv, *args, **kwargs):
            return CheckProcess() if argv == ["true"] else original_popen(argv, *args, **kwargs)

        with patch("ai_company.flow_evidence.subprocess.Popen", side_effect=launch), \
                patch("ai_company.flow_evidence.time.monotonic", side_effect=lambda: 100.0 + elapsed[0]), \
                patch("ai_company.adapters.session_cli._processes", return_value=[]):
            self.dispatcher.run_once(task_ids={state["task_id"]})
        checked = self.dispatcher.get(state["task_id"])
        self.assertEqual(timeouts, [7.0], "the larger command timeout must narrow to the project remainder")
        self.assertEqual(checked["usage"]["runtime_seconds"], 7.0)
        self.assertEqual(checked["usage"]["executions"], 0, "a local check is not another model attempt")
        self.assertNotIn("project_reservation", checked)
        self.submit_flow(project, plan, specification, "after-project-checks", project_budget=budget)
        self.dispatcher.run_once(task_ids={"after-project-checks"})
        self.assertEqual(self.dispatcher.get("after-project-checks")["status"], "BLOCKED")
        self.assertEqual(self.calls, [])
