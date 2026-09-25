"""Durable PM-to-Dispatcher coordination; HTTP never runs models or Git.

The coordinator only creates immutable tasks and observes their stored facts.
Execution capacity, quota recovery and review acceptance remain Dispatcher-owned.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from ai_company.automation_contracts import AutomationConfig, PMPlanContent
from ai_company.contracts import Task, digest
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import FlowSpec, ProjectBudget, RemoteCI
from ai_company.flow_evidence import allowed
from ai_company.management import ManagementError, ManagementStore
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import _git
from ai_company.storage import controller_lock


class Automation:
    def __init__(self, root: Path, config: AutomationConfig, *, clock=time.time,
                 git=None, dispatcher_factory=Dispatcher, execution_catalog=None,
                 project_context=None):
        from ai_company.automation_git import AutomationGit
        self.root, self.config, self.clock = Path(root).resolve(), config, clock
        self.configuration_digest = digest(config)
        self.execution_catalog = execution_catalog
        self.project_context = project_context
        self.store = ManagementStore(self.root, clock=clock, execution_catalog=execution_catalog)
        self.dispatcher_factory = dispatcher_factory
        self.dispatcher = dispatcher_factory(self.root, clock=clock)
        git_root = self.root / "automation-git"
        if project_context:
            git_root = git_root / project_context[0] / digest(project_context[1])
        self.git = git or AutomationGit(git_root, config)

    def _context_config(self, record):
        reference = record.get("execution_spec")
        if reference:
            return self.store.execution_config_for(record["project_id"], reference)
        return self.config

    def _coordinate(self, record, operation):
        if operation == "_pm" and record["state"] in ("completed", "stale", "blocked", "answer_needed"):
            return
        if operation == "_run" and record["state"] in ("blocked", "fixture_complete", "completed", "rejected", "awaiting_approval"):
            return self._run(record)
        if not record.get("execution_spec"):
            return getattr(self, operation)(record)
        config = self._context_config(record)
        worker = Automation(self.root, config, clock=self.clock,
            dispatcher_factory=self.dispatcher_factory, execution_catalog=self.execution_catalog,
            project_context=(record["project_id"], record["execution_spec"]))
        try:
            return getattr(worker, operation)(record)
        finally:
            worker.close()

    def close(self):
        self.dispatcher.close()
        self.store.close()

    def delegate(self, authorization):
        """Trusted operator intake; never inferred from a model or HTTP body."""
        if not isinstance(authorization, dict):
            raise ExecutionBlocked("delegation requires a structured operator receipt")
        with controller_lock(self.root / "automation-coordinator", blocking=True):
            rows = self.store.db.execute("SELECT document FROM management_plans WHERE json_extract(document,'$.digest')=?",
                                         (authorization.get("plan_digest"),)).fetchall()
            if len(rows) != 1:
                raise ExecutionBlocked("delegation requires one exact stored plan digest")
            plan = json.loads(rows[0][0])
            config = self._context_config(plan)
            if plan["configuration_digest"] != digest(config) or plan["mode"] != config.mode:
                raise ExecutionBlocked("delegation cannot change the confirmed execution configuration")
            return self.store.delegate_validation(plan["digest"], authorization)

    def _delegation(self, run):
        if not run.get("delegation_id"):
            return None
        record = self.store.get_delegation(run["delegation_id"])
        if (record["digest"] != run["delegation_digest"]
                or digest({k: v for k, v in record.items() if k != "digest"}) != record["digest"]
                or record["run_id"] != run["id"] or record["plan_digest"] != run["plan_digest"]
                or record["parent_run_id"] != run["parent_run_id"]
                or run["created_at"] <= record["authorization"]["received_at"]):
            raise ExecutionBlocked("delegation does not authorize this new execution and exact plan")
        return record

    def _task(self, task_id, goal, acceptance, base_sha, paths):
        return Task(task_id=task_id, goal=goal, acceptance=tuple(acceptance),
                    repository=self.config.repository, base_sha=base_sha,
                    allowed_paths=tuple(paths), required_checks=tuple(self.config.checks),
                    max_repairs=self.config.policy.max_repairs)

    def _spec(self, task, clone, scope, plan, **extra):
        policy = self.config.policy
        if scope == "planning":
            policy = policy.model_copy(update={"retry": policy.retry.model_copy(update={
                "execution_timeout_seconds": min(self.config.pm_timeout_seconds, policy.retry.execution_timeout_seconds)})})
        if self.project_context:
            project_id, reference = self.project_context
            plan = {**plan, "project_id": project_id, "execution_spec": reference}
            extra["project_budget"] = ProjectBudget(scope_id=project_id, max_parallel=self.config.max_parallel, **{
                key: getattr(self.config.policy, key) for key in
                ("max_cost_usd", "max_runtime_seconds", "max_executions", "max_repairs")})
            role_key = plan.get("role", {}).get("key")
            selection = self.store.get_execution_spec(project_id, reference["version"])["selection"]
            pool = selection.get("role_candidates", {}).get(role_key)
            if pool:
                policy = policy.model_copy(update={"candidates": {**policy.candidates, "developer": tuple(pool)}})
        if self.config.guidance is not None:
            from ai_company.harness.guidance import load
            load(self.config.guidance)
            plan = {**plan, "guidance": self.config.guidance.model_dump(mode="json")}
        return FlowSpec(task=task, worktree=str(clone), agents=self.config.agents,
                        policy=policy, checks=self.config.checks,
                        approved_plan=scope not in ("planning", "plan_review"), plan={**plan, "automation_configuration": self.configuration_digest},
                        mode=self.config.mode, execution_scope=scope, **extra)

    def _existing(self, task_id):
        try:
            state = self.dispatcher.get(task_id)
        except ExecutionBlocked:
            return None
        spec = FlowSpec.model_validate(state["specification"])
        if (digest(spec) != state["spec_digest"] or spec.mode != self.config.mode
                or spec.plan.get("automation_configuration") != self.configuration_digest):
            raise ExecutionBlocked("existing automation execution belongs to another immutable configuration")
        if self.project_context and (spec.plan.get("project_id"), spec.plan.get("execution_spec")) != self.project_context:
            raise ExecutionBlocked("existing automation task belongs to another project specification")
        return state

    @staticmethod
    def _execution(state):
        return {"task_id": state["task_id"], "status": state["status"],
                "stage": state["stage"], "reason": state["reason"],
                "resume_at": state["resume_at"], "generation": state["generation"],
                "active": state["active"], "candidate_sha": state["snapshot"]["head_commit"],
                "usage": state["usage"]}

    def _validate_plan(self, value):
        from ai_company.execution_specs import path_subset
        plan = PMPlanContent.model_validate(value)
        config = self.config
        if plan.execution_spec_proposal is not None:
            if self.execution_catalog is None or self.project_context:
                raise ExecutionBlocked("execution proposal requires an unbound request and a trusted catalog")
            config = self.execution_catalog.resolve(plan.execution_spec_proposal)
        path_check = path_subset if self.execution_catalog is not None else allowed
        if any(not path_check(path, config.allowed_paths)
               for role in plan.roles for path in role.allowed_paths):
            raise ExecutionBlocked("PM proposal exceeds server-authorized output paths")
        return plan

    def _pm(self, request):
        if request["state"] in ("completed", "stale", "blocked", "answer_needed"):
            return
        if request.get("configuration_digest") not in (None, self.configuration_digest):
            return  # Only the worker with the immutable configuration may resume it.
        if request["source"] == "fixture" and self.config.mode == "live":
            raise ExecutionBlocked("fixture project cannot create a live PM execution")
        if not self.store.request_is_current(request["request_id"]):
            # Existing executions retain their own guards/facts; their results will
            # never be activated by the stale request. Do not kill other work.
            self.store.save_pm_request({**request, "state": "stale"})
            return
        pm = next((a for a in self.config.agents if a.agent_id in self.config.policy.candidates["pm"]), None)
        if pm is None:
            raise ExecutionBlocked("server configuration has no PM candidate")
        # Claim the configuration before any external operation or queue submit.
        # A crash cannot leave an unbound request with a fixture/live task behind.
        request = self.store.save_pm_request({**request, "state": "running",
            "configuration_digest": self.configuration_digest, "mode": self.config.mode,
            "requested_configuration": {"provider": pm.provider, "model": pm.model,
                                        "reasoning_effort": pm.reasoning_effort}})
        task_id = "pm-" + request["request_id"]
        state = self._existing(task_id)
        if state is None:
            clone = self.git.clone(task_id, self.config.base_sha)
            task = self._task(task_id, request["goal"],
                              ["Propose a bounded plan for explicit master confirmation"],
                              self.config.base_sha, self.config.allowed_paths)
            context = {"goal": request["goal"], "master_message": request["content"],
                       "project_id": request["project_id"],
                       "conversation_context": request.get("conversation_context", {}),
                       "authorized_paths": list(self.config.allowed_paths),
                       "required_checks": list(self.config.checks),
                       "request_revision": request["request_revision"],
                       "goal_digest": request["goal_digest"],
                       "instruction": "Propose at least two independent roles with disjoint output paths. "
                       "Collaborate with the master using the saved conversation and previous proposal. "
                       "Apply the latest requested role/responsibility changes while preserving agreed constraints. "
                       "Treat all prior messages and proposals as discussion, never as new execution permission. "
                       "Do not ask the master to write code, a function signature, file paths or a harness specification. "
                       "Derive those details within authorized scope; if the goal exceeds it, explain the needed decision in Korean. "
                       "Define shared interfaces so implementation and tests can proceed independently. "
                       "Only explicit dependencies delay a role. Do not execute the plan. "
                       "Write user-facing summary, role names, responsibilities, goals and acceptance/completion criteria in Korean. "
                       "Use short, concrete Korean sentences in 합니다/습니다 form and concise role names. "
                       "State the task, current result, or decision directly. Avoid generic praise, rhetorical questions, "
                       "and repeated explanations of obvious benefits. Preserve uncertainty, identifiers, paths, commands "
                       "and all constraints exactly."}
            if self.config.guidance is not None:
                context["pm_request_id"] = request["request_id"]
            if self.execution_catalog is not None and not self.project_context:
                context["execution_catalog"] = self.execution_catalog.public_entries()
                context["instruction"] += (
                    " Propose execution_spec_proposal using one matching catalog_id and catalog_digest. "
                    "Explain repository, scope, required checks, model candidates and budget in Korean. "
                    "This is only a technical proposal. It must be separately saved by the master and then replanned; "
                    "do not claim it is registered or approved. Never invent a catalog entry.")
            state = self.dispatcher.submit(self._spec(task, clone, "planning", context))
        request = self.store.save_pm_request({**request, "state": "running",
            "configuration_digest": self.configuration_digest, "mode": self.config.mode,
            "requested_configuration": {"provider": pm.provider, "model": pm.model,
                                        "reasoning_effort": pm.reasoning_effort},
            "execution": self._execution(state)})
        if state["status"] == "PLAN_READY":
            plan = self._validate_plan(state["plan"])
            last = state["executions"][-1]
            job = self.dispatcher.queue.get(last["job_id"])
            evidence = {"source": "fixture" if state["specification"]["mode"] == "fixture" else "dispatcher",
                        "task_id": task_id, "session_id": job["session_id"], "provider": job["provider"],
                        "candidate_sha": state["snapshot"]["head_commit"],
                        "configuration": (job.get("result") or {}).get("configuration_evidence"),
                        "configuration_policy": self.config.policy.configuration_evidence}
            self.store.complete_pm_request(request["request_id"], plan.model_dump(mode="json"), evidence=evidence)
        elif state["resume_at"] is None:
            response = state.get("pm_response")
            if response and response.get('verdict') == 'BLOCK':
                self.store.save_pm_feedback(request['request_id'], response, reason=state['reason'])
            else:
                self.store.save_pm_request({**request, "state": "blocked", "reason": state["reason"]})

    def _review_plan(self, record):
        if record["status"] != "reviewing" or record.get("contract_version") != 2:
            return
        if not self.store.request_is_current(record["request_id"]):
            return
        if record["configuration_digest"] != self.configuration_digest or record["mode"] != self.config.mode:
            return
        self.store._requirements_ready(record)
        pm_session = (record.get("evidence") or {}).get("session_id")
        pm_provider = (record.get("evidence") or {}).get("provider") or "codex"
        if not pm_session:
            raise ExecutionBlocked("plan review requires recorded PM session identity")
        task_id = "plan-review-" + record["digest"][:48]
        state = self._existing(task_id)
        if state is None:
            clone = self.git.clone(task_id, self.config.base_sha)
            task = self._task(task_id, "Review the bound PM plan content without editing code",
                              ["Check requirements, decisions, contradictions and verification methods"],
                              self.config.base_sha, self.config.allowed_paths)
            request = self.store.get_pm_request(record["request_id"])
            context = {"project_id": record["project_id"], "plan_digest": record["digest"], "plan": record["content"],
                       "goal": request["goal"], "master_message": request["content"],
                       "conversation_context": request["conversation_context"],
                       "requirements_revision": record["request_revision"],
                       "review_version": record["plan_review_version"],
                       "instruction": "Review this exact proposal before master confirmation. Do not edit or approve execution."}
            state = self.dispatcher.submit(self._spec(task, clone, "plan_review", context,
                inherited_pm_sessions=({"provider": pm_provider, "session_id": pm_session},)))
        if state["status"] in ("PLAN_REVIEWED", "NEEDS_PLAN_REVISION"):
            report = state["reviews"]["reviewer"]
            job = self.dispatcher.queue.get(state["executions"][-1]["job_id"])
            self.store.complete_plan_review(record["project_id"], record["id"], {
                "plan_digest": record["digest"], "requirements_revision": record["request_revision"],
                "requirements_digest": digest(record["content"]["requirements_review"]),
                "review_version": record["plan_review_version"], "pm_session_id": pm_session,
                "session_id": job["session_id"], "task_id": task_id,
                "verdict": report["verdict"], "findings": report["findings"],
                "summary": report["summary"], "mode": record["mode"]})
        elif state["resume_at"] is None:
            self.store.note_plan_review_problem(record["project_id"], record["id"],
                                                state.get("reason") or state["status"])

    @staticmethod
    def _contribution(state):
        return {"clone": state["specification"]["worktree"],
                "base_sha": state["specification"]["task"]["base_sha"],
                "candidate_sha": state["snapshot"]["head_commit"]}

    def _role(self, run, role, plan):
        key = role.key
        prior = run["roles"].get(key, {})
        revision = prior.get("revision", 0)
        task_id = "role-" + digest([run["id"], key, revision])[:48]
        state = self._existing(task_id)
        if state is None:
            for dependency in role.depends_on:
                if run["roles"].get(dependency, {}).get("status") != "CONTRIBUTION_READY":
                    run["roles"][key] = {**prior, "status": "WAITING_DEPENDENCIES", "revision": revision}
                    return
            base = run.get("repair_base_sha", self.config.base_sha)
            dependencies = {}
            if role.depends_on:
                dependencies = {dep: self._contribution(self.dispatcher.get(run["roles"][dep]["task_id"]))
                                for dep in role.depends_on}
                _, base = self.git.aggregate(task_id + "-context", base, dependencies,
                                             {"purpose": "dependency_context", "plan_digest": run["plan_digest"]})
            clone = self.git.clone(task_id, base)
            task = self._task(task_id, role.goal, role.acceptance, base, role.allowed_paths)
            context = {"confirmed_plan": plan.model_dump(mode="json"), "role": role.model_dump(mode="json"),
                       "project_id": run["project_id"],
                       "plan_digest": run["plan_digest"], "revision": revision,
                       "repair_findings": prior.get("repair_findings", []), "dependency_artifacts": dependencies}
            if self.config.guidance is not None:
                context.update(plan_id=run["plan_id"], run_id=run["id"])
            if run.get("delegation_id"):
                context["master_delegation"] = self._delegation(run)
            state = self.dispatcher.submit(self._spec(task, clone, "contribution", context))
        self.store.link_task(run["project_id"], run["role_ids"][key], task_id, title=role.name)
        run["roles"][key] = {**prior, **self._execution(state), "revision": revision}

    def _integration(self, run, plan):
        revision = run.get("revision", 0)
        task_id = "integration-" + digest([run["id"], revision])[:48]
        state = self._existing(task_id)
        if state is None:
            delegation = self._delegation(run)
            paths = (delegation["authorization"]["allowed_paths"] if delegation else
                     [*self.config.allowed_paths, ".ai-company-ci/request.json"])
            task = self._task(task_id, "Integrate and independently verify: " + plan.summary[:7800],
                              plan.completion_criteria, self.config.base_sha, paths)
            contributions = {}
            authors = []
            for role in plan.roles:
                item = run["roles"][role.key]
                contribution = self.dispatcher.get(item["task_id"])
                authors.extend(contribution["authors"])
                if revision == 0 or role.key in run.get("repair_roles", []):
                    contributions[role.key] = self._contribution(contribution)
            for previous in run.get("integration_history", []):
                authors.extend(self.dispatcher.get(previous["task_id"])["authors"])
            authors = list({digest(author): author for author in authors}.values())
            manifest = {"base_sha": self.config.base_sha, "task_digest": digest(task),
                        "policy_digest": digest(self.config.policy)}
            clone, candidate = self.git.aggregate(task_id, run.get("repair_base_sha", self.config.base_sha),
                                                 contributions, manifest)
            if self.config.mode == "live":
                published = self.git.publish(clone, "automation/" + task_id, self.config.base_branch,
                    "Automation candidate: " + plan.summary[:140],
                    "Automatically assembled candidate from confirmed plan `" + run["plan_digest"] + "`.\n\n"
                    "This draft preserves the master approval boundary. No deployment or automatic merge.\n")
                remote_ci = RemoteCI(pr_number=published["number"], required_checks=self.config.ci.required_checks,
                                     trusted_workflow_path=self.config.ci.workflow_path,
                                     trusted_workflow_digest=self.config.ci.workflow_digest,
                                     attestation_artifact=self.config.ci.artifact_name)
            else:
                published, remote_ci = {}, None
            request = self.store.get_pm_request(self.store.get_plan(run["project_id"], run["plan_id"])["request_id"])
            pm_state = self.dispatcher.get(request["execution"]["task_id"])
            context = {"confirmed_plan": plan.model_dump(mode="json"), "plan_digest": run["plan_digest"],
                       "project_id": run["project_id"],
                       "contribution_artifacts": contributions}
            if self.config.guidance is not None:
                context.update(plan_id=run["plan_id"], run_id=run["id"])
            if run.get("delegation_id"):
                context["master_delegation"] = self._delegation(run)
            state = self.dispatcher.submit(self._spec(task, clone, "integration", context,
                remote_ci=remote_ci, inherited_authors=tuple(authors),
                inherited_pm_sessions=tuple(pm_state["pm_sessions"])))
        spec = FlowSpec.model_validate(state["specification"])
        number = spec.remote_ci.pr_number if spec.remote_ci else None
        run["integration"] = {**self._execution(state), "pr_number": number,
                               "pr_url": f"https://github.com/{self.config.repository}/pull/{number}" if number else None}
        # The final result belongs to this plan as a whole. Linking to one role
        # provides the existing reports view with the full independent evidence.
        self.store.link_task(run["project_id"], run["role_ids"][plan.roles[0].key], task_id,
                             title="통합 검사와 독립·최종 검수")
        if state["status"] == "WAITING_ROLE_REPAIR":
            if revision >= self.config.policy.max_repairs:
                run.update(state="blocked", reason="automatic role repair budget exhausted")
                return
            findings = state["findings"]
            # A finding that cannot be attributed safely (e.g. a failed global
            # check) is returned to all contributing roles, never silently lost.
            owners = set()
            for finding in findings:
                text = finding["detail"] + " " + finding["evidence"]
                matched = {role.key for role in plan.roles if any(path.rstrip("/") in text for path in role.allowed_paths)}
                owners.update(matched or {role.key for role in plan.roles})
            if not owners:
                raise ExecutionBlocked("repair request has no actionable findings")
            run.setdefault("integration_history", []).append(run.pop("integration"))
            run.update(revision=revision + 1, repair_roles=sorted(owners),
                       repair_base_sha=state["snapshot"]["head_commit"], state="running")
            for key in owners:
                prior = run["roles"][key]
                run.setdefault("role_history", []).append({"key": key, **prior})
                run["roles"][key] = {"revision": prior["revision"] + 1, "status": "READY",
                                     "repair_findings": findings}
        elif state["status"] in ("MERGE_READY", "DEMO_READY"):
            if self.config.mode == "fixture":
                run.update(state="fixture_complete", reason="Fixture evidence cannot authorize live release")
            else:
                # This asks the master to accept the completed development result;
                # approval is never interpreted as authority to deploy or merge.
                approval = self.store.request_approval(run["project_id"], {
                    "title": "독립 검수를 마친 개발 결과 수용",
                    "action": "후보와 보고서를 수용합니다. 배포와 병합에는 별도 승인이 필요합니다.",
                    "environment": "draft-pr", "artifact_sha": state["snapshot"]["head_commit"],
                    "cost_usd": 0, "expires_at": run["created_at"] + 7 * 86400,
                    "impact": "마스터의 결과 수용만 기록합니다. 실제 모델 비용은 확인되지 않을 수 있으며 유료 작업을 승인하지 않습니다.",
                    "rollback": "이 초안 후보를 거부합니다. 기존 브랜치와 서비스는 유지됩니다.",
                    "verification": json.dumps({"task_id": task_id, "verification_digest": digest(state["verification"]),
                                                 "reviews_digest": digest(state["reviews"])})},
                    idempotency_key="automation-" + task_id)
                run.update(state="awaiting_approval", approval_id=approval["id"],
                           reason="same candidate passed checks, remote CI, independent review and Astra final review")
        elif state["resume_at"] is None:
            run.update(state="blocked", reason=state["reason"])
        else:
            run.update(state="running" if state["status"] == "READY" else "waiting", reason=state["reason"])

    def _run(self, run):
        if run["state"] in ("blocked", "fixture_complete", "completed", "rejected"):
            return
        if run["state"] == "awaiting_approval":
            approval = self.store._approval(run["project_id"], run["approval_id"])
            if approval["status"] != "pending":
                run.update(state={"approved": "completed", "rejected": "rejected",
                                  "changes_requested": "blocked"}[approval["status"]],
                           reason="Master decision recorded; no deployment or merge executed")
                self.store.save_run(run)
            return
        if run["configuration_digest"] != self.configuration_digest or run["mode"] != self.config.mode:
            return
        plan_record = self.store.get_plan(run["project_id"], run["plan_id"])
        if plan_record["digest"] != run["plan_digest"] or digest(self.store._plan_binding(plan_record)) != run["plan_digest"]:
            raise ExecutionBlocked("confirmed plan binding changed")
        self.store.assert_plan_ready(plan_record)
        plan = self._validate_plan(plan_record["content"])
        self._delegation(run)
        run.setdefault("roles", {})
        run.setdefault("revision", 0)
        for role in plan.roles:
            self._role(run, role, plan)
        if all(item.get("status") == "CONTRIBUTION_READY" for item in run["roles"].values()):
            self._integration(run, plan)
        else:
            terminal = [item for item in run["roles"].values() if item.get("task_id")
                        and item.get("resume_at") is None and item["status"] != "CONTRIBUTION_READY"]
            # A blocked role does not cancel the independent tasks already queued.
            run.update(state="waiting" if terminal else "running",
                       blocked_roles=[item["task_id"] for item in terminal],
                       reason=terminal[0]["reason"] if terminal else "roles executing independently")
        self.store.save_run(run)

    def reconcile(self):
        with controller_lock(self.root / "automation-coordinator", blocking=True):
            for request in self.store.pm_requests():
                try:
                    self._coordinate(request, "_pm")
                except (ExecutionBlocked, ManagementError, ValueError, OSError) as exc:
                    current = self.store.get_pm_request(request["request_id"])
                    if current["state"] not in ("completed", "stale"):
                        self.store.save_pm_request({**current, "state": "blocked", "reason": str(exc)})
            for plan in self.store.db.execute("SELECT document FROM management_plans WHERE json_extract(document,'$.status')='reviewing'").fetchall():
                record = json.loads(plan[0])
                try:
                    self._coordinate(record, "_review_plan")
                except (ExecutionBlocked, ManagementError, ValueError, OSError) as exc:
                    # A review failure is visible in the plan; unrelated runs continue.
                    self.store.note_plan_review_problem(record["project_id"], record["id"], str(exc))
            for run in self.store.run_records():
                try:
                    self._coordinate(run, "_run")
                except (ExecutionBlocked, ManagementError, ValueError, OSError) as exc:
                    self.store.save_run({**self.store.get_run(run["id"]), "state": "blocked", "reason": str(exc)})
        return {"pm_requests": self.store.pm_requests(), "runs": self.store.run_records()}

    def run_once(self):
        current = self.reconcile()
        allowed_tasks = set()
        for request in current["pm_requests"]:
            try:
                config = self._context_config(request)
            except (ValueError, ManagementError):
                continue
            if (request["state"] == "running" and request.get("configuration_digest") == digest(config)
                    and request.get("mode") == config.mode and request.get("execution")):
                allowed_tasks.add(request["execution"]["task_id"])
        for row in self.store.db.execute("SELECT document FROM management_plans WHERE json_extract(document,'$.status')='reviewing'"):
            plan = json.loads(row[0])
            try:
                config = self._context_config(plan)
            except (ValueError, ManagementError):
                continue
            if (self.store.request_is_current(plan["request_id"])
                    and plan["configuration_digest"] == digest(config) and plan["mode"] == config.mode):
                allowed_tasks.add("plan-review-" + plan["digest"][:48])
        for run in current["runs"]:
            try:
                config = self._context_config(run)
            except (ValueError, ManagementError):
                continue
            if (run["configuration_digest"] == digest(config) and run["mode"] == config.mode
                    and run["state"] in ("pending", "preparing", "running", "waiting", "blocked")):
                # An independently blocked role does not stop its already-created
                # siblings. Terminal tasks themselves have no scheduled resume.
                allowed_tasks.update(item["task_id"] for item in run.get("roles", {}).values() if item.get("task_id"))
                if run.get("integration"):
                    allowed_tasks.add(run["integration"]["task_id"])
        def execute(_):
            worker = self.dispatcher_factory(self.root, clock=self.clock)
            try:
                return worker.run_once(task_ids=allowed_tasks)
            finally:
                worker.close()
        with ThreadPoolExecutor(max_workers=self.config.max_parallel) as workers:
            results = list(workers.map(execute, range(self.config.max_parallel)))
        status = self.reconcile()
        return {"workers": results, **status}
