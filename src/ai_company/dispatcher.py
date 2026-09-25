"""Deterministic role assignment and failover over the persistent session queue."""

from contextlib import ExitStack
import json
import math
import re
from pathlib import Path
import time
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith.run_helpers import tracing_context

from ai_company.adapters.session_cli import run_session
from ai_company.contracts import Finding, digest
from ai_company.flow_contracts import AgentProfile, ContributionStageReport, FlowSpec, PMPlanStageReport, PlanReviewStageReport, StageReport, budget_available
from ai_company.flow_evidence import Verifier, allowed, handoff
from ai_company.flow_graph import build_graph
from ai_company.harness.prompts import stage_prompt
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import _git, SessionQueue, SessionSpec, execution_alive, repository_lock, repository_snapshot
from ai_company.storage import controller_lock, suspended_lock


class Dispatcher:
    def __init__(self, root: Path, *, clock=time.time, verifier=None, executor=None):
        self.root, self.clock = root.resolve(), clock
        self.queue = SessionQueue(self.root / "sessions", clock=clock)
        self.db = self.queue.db
        self.verifier = verifier or Verifier(self.root)
        self.executor = executor
        self._scheduler_lock = None
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flow_events(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, at REAL NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quota_groups(group_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                resume_at REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS credential_groups(provider TEXT PRIMARY KEY,
                credential_ref TEXT NOT NULL, group_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flow_migrations(id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS guidance_deliveries(
                id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                phase TEXT NOT NULL, document TEXT NOT NULL,
                UNIQUE(execution_id, attempt, phase));
        """)

    def close(self):
        self.queue.close()

    def get(self, task_id):
        row = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ExecutionBlocked("unknown logical task")
        return json.loads(row[0])

    def tasks(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT document FROM flow_tasks ORDER BY task_id")]

    def _save(self, state, kind):
        state["updated_at"] = self.clock()
        data = json.dumps(state, ensure_ascii=False)
        self.db.execute("INSERT OR REPLACE INTO flow_tasks VALUES (?,?)", (state["task_id"], data))
        self.db.execute("INSERT INTO flow_events(task_id,kind,at,document) VALUES (?,?,?,?)",
                        (state["task_id"], kind, self.clock(), data))

    def submit(self, spec: FlowSpec):
        spec = FlowSpec.model_validate(spec.model_dump(mode="json"))
        if "guidance" in spec.plan:
            from ai_company.harness.guidance import load
            load(spec.plan["guidance"])
        snapshot = repository_snapshot(Path(spec.worktree))
        if snapshot["repository"].lower() != spec.task.repository.lower():
            raise ExecutionBlocked("logical task repository does not match worktree")
        if spec.task.task_id in spec.dependencies:
            raise ExecutionBlocked("task cannot depend on itself")
        with controller_lock(self.root / "dispatcher"), self.db:
            existing = self.db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (spec.task.task_id,)).fetchone()
            if existing:
                state = json.loads(existing[0])
                if state["spec_digest"] != digest(spec):
                    raise ExecutionBlocked("task already has an immutable policy; use explicit migration")
                return state
            for agent in spec.agents:
                row = self.db.execute("SELECT credential_ref,group_id FROM credential_groups WHERE provider=?",
                                      (agent.provider,)).fetchone()
                if row and tuple(row) != (agent.credential_ref, agent.quota_group):
                    raise ExecutionBlocked("existing CLI account must keep its registered shared quota group")
                self.db.execute("INSERT OR IGNORE INTO credential_groups VALUES (?,?,?)",
                                (agent.provider, agent.credential_ref, agent.quota_group))
                self.db.execute("INSERT OR IGNORE INTO quota_groups VALUES (?,'AVAILABLE',NULL,NULL)", (agent.quota_group,))
            state = {"task_id": spec.task.task_id, "specification": spec.model_dump(mode="json"),
                     "spec_digest": digest(spec), "stage": "check" if spec.execution_scope == "integration" else ("reviewer" if spec.execution_scope == "plan_review" else ("developer" if spec.approved_plan else "pm")),
                     "status": "READY", "reason": "approved plan" if spec.approved_plan else "PM decision needed",
                     "snapshot": snapshot, "plan": spec.plan, "last_completed_stage": "approved_plan" if spec.approved_plan else "submitted",
                     "generation": 0, "active": None, "executions": [], "findings": [], "verification": None,
                     "reviews": {}, "authors": [dict(a) for a in spec.inherited_authors],
                     "pm_sessions": [dict(a) for a in spec.inherited_pm_sessions], "resume_at": self.clock(),
                     "usage": {"executions": 0, "runtime_seconds": 0.0, "cost_usd": 0.0, "cost_unknown": False, "repairs": 0},
                     "created_at": self.clock(), "updated_at": self.clock()}
            self._save(state, "submitted")
            return state

    def _candidates(self, spec, state, exclude=()):
        role = state["stage"]
        profiles = {a.agent_id: a for a in spec.agents}
        names = list(spec.policy.candidates[role])
        if role == "reviewer" and state["authors"]:
            writer = state["authors"][-1]["provider"]
            names.sort(key=lambda name: profiles[name].provider == writer)
        eligible, available, times = [], [], []
        for name in names:
            agent = profiles[name]
            required = {"structured_result", "read_repository"}
            if role == "developer":
                required.add("write_repository")
            configuration_eligible = (agent.provider == "codex" and not agent.ultracode_enabled
                                      if spec.policy.configuration_evidence == "cli_configuration" else agent.verified())
            if spec.policy.configuration_evidence == "cli_configuration_v2":
                from ai_company.adapters.claude_files import candidate_eligible
                configuration_eligible = (agent.provider == "codex" and not agent.ultracode_enabled
                                          or candidate_eligible(agent, spec, role))
            if (not agent.enabled or not configuration_eligible or not required.issubset(agent.capabilities)
                    or any(not allowed(path.rstrip("/"), agent.allowed_paths) for path in spec.task.allowed_paths)
                    or (spec.policy.max_cost_usd is not None and agent.provider != "claude")):
                continue
            eligible.append(agent)
            group = self.db.execute("SELECT state,resume_at FROM quota_groups WHERE group_id=?", (agent.quota_group,)).fetchone()
            if name in exclude or (group and group[0] in ("DISABLED", "UNKNOWN")):
                continue
            if group and group[0] == "COOLDOWN" and group[1] > self.clock():
                times.append(group[1])
                continue
            available.append(agent)
        return eligible, available, times

    def _wait(self, state, spec, times):
        state["status"] = {"pm": "WAITING_PM", "final": "WAITING_FINAL_REVIEW"}.get(state["stage"], "WAITING_CAPACITY")
        state["resume_at"] = min(times) if times else self.clock() + spec.policy.retry.backoff_max_seconds
        state["reason"] = "all eligible role candidates are unavailable"

    def _assign(self, state, spec, agent, previous=None):
        with repository_lock(state["snapshot"]):
            if repository_snapshot(Path(spec.worktree)) != state["snapshot"]:
                raise ExecutionBlocked("repository changed before role handoff")
            if previous and self.queue._read_guard(self.queue.get(previous["job_id"])) is not None:
                raise ExecutionBlocked("previous execution guard is not cleared")
            if previous and execution_alive(self.queue.get(previous["job_id"])["process"]):
                raise ExecutionBlocked("previous execution is still alive")
            if previous and state.get("project_reservation"):
                old = self.queue.get(previous["job_id"])
                reservation = state["project_reservation"]
                if (reservation["job_id"] != old["job_id"] or old["attempt_count"] >= reservation["attempt_count"]
                        or old["status"] not in ("READY", "WAITING_QUOTA", "WAITING_RETRY")):
                    raise ExecutionBlocked("previous project reservation requires result reconciliation")
                # The queue did not claim this reserved attempt and has no live
                # process or guard. Transfer ownership without reserving it twice.
                state.pop("project_reservation")
            generation = state["generation"] + 1
            execution_id = digest({"task_id": state["task_id"], "generation": generation, "nonce": uuid4().hex})
            bundle = handoff(spec, state, previous or {}, self.root / "handoffs" / state["task_id"] / execution_id)
            active = {"execution_id": execution_id, "generation": generation, "agent_id": agent.agent_id,
                      "provider": agent.provider, "role": state["stage"], "previous_execution_id": previous["execution_id"] if previous else None,
                      "session_id": None, "job_id": None, "accounted_attempts": 0,
                      "task_digest": digest(spec.task), "policy_digest": digest(spec.policy),
                      "input_snapshot": state["snapshot"], "handoff": bundle}
            with self.db:
                if previous:
                    old = self.queue.get(previous["job_id"])
                    old.update(status="SUPERSEDED", resume_at=None, reason="ownership transferred to generation " + str(generation))
                    self.queue._save(old)
                    previous["superseded_by"] = execution_id
                    state["executions"].append(previous)
                state.update(generation=generation, active=active, status="HANDOFF_PENDING", resume_at=self.clock())
                self._save(state, "ownership_transferred" if previous else "role_assigned")

    def _materialize(self, state, spec):
        active = state["active"]
        if repository_snapshot(Path(spec.worktree)) != active["input_snapshot"]:
            raise ExecutionBlocked("repository changed after handoff checkpoint")
        agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
        checkpoint = dict(active["handoff"], expected_report={"execution_id": active["execution_id"],
                          "generation": active["generation"], "role": active["role"], "task_digest": digest(spec.task),
                          "policy_digest": digest(spec.policy), "verification_digest": digest(state["verification"]) if state["verification"] else None})
        job = self.queue.submit(SessionSpec(task=spec.task, agent_id=agent.agent_id, provider=agent.provider,
                                worktree=spec.worktree, last_completed_stage=state["last_completed_stage"],
                                checkpoint=checkpoint, retry_policy=spec.policy.retry),
                                execution_key=active["execution_id"], managed_by=state["task_id"])
        active["job_id"] = job["job_id"]
        state["status"] = "READY"
        with self.db:
            self._save(state, "session_materialized")

    def _guidance_event(self, record, phase, **facts):
        """Append-only delivery facts; never mutate accounting or model evidence."""
        key = digest([record["execution_id"], record["attempt"], phase])
        value = {**record, "id": key, "phase": phase, "at": self.clock(), **facts}
        with self.db:
            prior = self.db.execute("SELECT document FROM guidance_deliveries WHERE id=?", (key,)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if any(saved.get(name) != item for name, item in {**record, **facts}.items() if name != "prepared_at"):
                    raise ExecutionBlocked("guidance delivery identity was reused with different content")
                return saved
            self.db.execute("INSERT INTO guidance_deliveries VALUES (?,?,?,?,?)",
                (key, record["execution_id"], record["attempt"], phase, json.dumps(value, ensure_ascii=False)))
        return value

    def _guided_external(self, record, function, *args, **kwargs):
        if record is None:
            return self._external(function, *args, **kwargs)
        # This is the executor invocation boundary, not proof that preflight,
        # subprocess launch or a model call succeeded. Crashes retain this fact.
        self._guidance_event(record, "executor_invocation_started")
        try:
            outcome = self._external(function, *args, **kwargs)
        except Exception as error:
            self._guidance_event(record, "executor_failed", error_type=type(error).__name__)
            raise
        delivered = self._guidance_event(record, "executor_returned", outcome_category=outcome.category)
        outcome.result = {**(outcome.result or {}), "guidance_receipt": delivered}
        return outcome

    def _executor(self, spec, state):
        scope = getattr(spec, "execution_scope", "full")
        agent = next(a for a in spec.agents if a.agent_id == state["active"]["agent_id"])
        file_tools = spec.policy.configuration_evidence == "cli_configuration_v2" and agent.provider == "claude"

        def execute(provider, worktree, prompt, session_id, **kwargs):
            kwargs["timeout_seconds"] = min(kwargs["timeout_seconds"], spec.policy.max_runtime_seconds - state["usage"]["runtime_seconds"])
            reservation = state.get("project_reservation")
            if reservation is not None:
                kwargs["timeout_seconds"] = min(kwargs["timeout_seconds"], reservation["runtime_seconds"])
            prompt = stage_prompt(prompt, provider=provider, role=state["stage"], planning=scope == "planning", plan_review=scope == "plan_review",
                                  contribution=scope == "contribution", file_tools=file_tools)
            guidance = None
            if "guidance" in spec.plan:
                from ai_company.harness.guidance import augment, receipt
                prompt, document_hash = augment(prompt, spec.plan["guidance"], state["stage"])
                job = self.queue.get(state["active"]["job_id"])
                if job["status"] != "RUNNING" or job["attempt_count"] < 1:
                    raise ExecutionBlocked("guidance delivery requires a claimed queue attempt")
                guidance = receipt(spec, state, job, prompt, document_hash, self.clock())
                self._guidance_event(guidance, "prepared")
                original_spawn = kwargs.get("on_spawn")
                def observed_spawn(identity):
                    if original_spawn:
                        original_spawn(identity)
                    self._guidance_event(guidance, "process_started")
                kwargs["on_spawn"] = observed_spawn
            skill = spec.plan.get("skill_delivery")
            skill_record = None
            if skill is not None:
                job = self.queue.get(state["active"]["job_id"])
                if (scope != "contribution" or state["stage"] != "developer"
                        or job["status"] != "RUNNING" or job["attempt_count"] < 1
                        or skill.get("task_id") != state["task_id"]):
                    raise ExecutionBlocked("role skill delivery needs its claimed developer attempt")
                skill_record = {"execution_id": state["active"]["execution_id"],
                    "attempt": job["attempt_count"], "delivery_id": skill["delivery_id"],
                    "selection_digest": skill["selection_digest"], "role_key": skill["role_key"],
                    "documents": skill["documents"], "model_compliance": "unverified"}
                self._guidance_event(skill_record, "skill_prompt_prepared", prompt_digest=digest(prompt))
                original_spawn = kwargs.get("on_spawn")
                def skill_spawn(identity):
                    if original_spawn:
                        original_spawn(identity)
                    self._guidance_event(skill_record, "skill_process_started")
                kwargs["on_spawn"] = skill_spawn
            def invoke(function, *args, **options):
                if skill_record is not None:
                    self._guidance_event(skill_record, "skill_executor_invocation_started")
                try:
                    result = self._guided_external(guidance, function, *args, **options)
                except Exception as error:
                    if skill_record is not None:
                        self._guidance_event(skill_record, "skill_executor_failed", error_type=type(error).__name__)
                    raise
                if skill_record is not None:
                    self._guidance_event(skill_record, "skill_executor_returned", outcome_category=result.category)
                return result
            report_type = {"planning": PMPlanStageReport, "plan_review": PlanReviewStageReport,
                           "contribution": ContributionStageReport}.get(scope, StageReport)
            if self.executor:
                outcome = invoke(self.executor, agent, state, provider, worktree, prompt, session_id, **kwargs)
            else:
                if spec.mode != "live":
                    raise ExecutionBlocked("fixture mode requires an explicit fixture executor")
                cost_limits = [value for value in (
                    spec.policy.max_cost_usd - state["usage"]["cost_usd"] if spec.policy.max_cost_usd is not None else None,
                    reservation.get("cost_usd") if reservation else None) if value is not None]
                runner = run_session
                options = {"permission": "workspace-write" if state["stage"] == "developer" else "read-only",
                           "isolate_cgroup": True, "capture_configuration": True}
                if file_tools:
                    from ai_company.adapters.claude_files import run_claude_files
                    from ai_company.adapters.claude_observation import persisted_attempt
                    runner = run_claude_files
                    job = self.queue.get(state["active"]["job_id"])
                    binding, _ = persisted_attempt(self.db, job)
                    options = {"binding": binding, "writable_paths": spec.task.allowed_paths if state["stage"] == "developer" else ()}
                outcome = invoke(runner, provider, worktree, prompt, session_id, **kwargs, **options, model=agent.model,
                                   reasoning_effort=agent.reasoning_effort, ultracode_enabled=agent.ultracode_enabled, output_schema=report_type.model_json_schema(),
                                   max_cost_usd=min(cost_limits) if cost_limits else None)
            if scope == "contribution" and state["stage"] == "developer":
                from ai_company.contribution_commit import commit_contribution
                outcome = self._external(commit_contribution, spec, state, outcome, worktree, output_dir=kwargs["output_dir"])
            return outcome
        return execute

    def _consume(self, state, spec, job):
        active = state["active"]
        if "guidance" in spec.plan or "skill_delivery" in spec.plan:
            active["guidance_receipts"] = [json.loads(row[0]) for row in self.db.execute(
                "SELECT document FROM guidance_deliveries WHERE execution_id=? ORDER BY attempt, rowid",
                (active["execution_id"],))]
        delta = job["attempt_count"] - active["accounted_attempts"]
        if delta <= 0:
            return
        result = job["result"] or {}
        state["usage"]["executions"] += delta
        duration = result.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration < 0:
            duration = spec.policy.retry.execution_timeout_seconds
        state["usage"]["runtime_seconds"] += duration
        cost = result.get("total_cost_usd")
        if isinstance(cost, (float, int)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            state["usage"]["cost_usd"] += cost
        else:
            state["usage"]["cost_unknown"] = True
        active["accounted_attempts"] = job["attempt_count"]
        active["session_id"] = job["session_id"]
        reservation = state.get("project_reservation")
        if reservation and (reservation["job_id"] == job["job_id"]
                            and reservation["attempt_count"] <= job["attempt_count"]):
            state.pop("project_reservation")
        if active["role"] == "developer" and job["session_id"]:
            author = {"provider": job["provider"], "session_id": job["session_id"]}
            if author not in state["authors"]:
                state["authors"].append(author)

    def _validate_cli_configuration(self, agent, job, result):
        evidence = result.get("configuration_evidence")
        if (agent.provider != "codex" or agent.ultracode_enabled or not isinstance(evidence, dict)
                or evidence.get("source") != "codex_rollout" or evidence.get("scope") != "cli_turn_configuration"
                or evidence.get("status") != "observed" or evidence.get("cli_version") != "0.154.0"
                or evidence.get("backend_model_verified") is not False
                or evidence.get("session_id") != job["session_id"]
                or not re.fullmatch(r"[0-9a-f-]{36}", str(job["session_id"]))):
            raise ExecutionBlocked("assigned Codex CLI turn configuration is missing or unbound")
        contexts = evidence.get("contexts")
        if not isinstance(contexts, list) or not 1 <= len(contexts) <= 128:
            raise ExecutionBlocked("Codex configuration requires bounded nonempty turn contexts")
        claimed = self.db.execute("""SELECT occurred_at FROM session_events WHERE job_id=?
            AND json_extract(document,'$.attempt_count')=? AND json_extract(document,'$.status')='RUNNING'
            ORDER BY sequence LIMIT 1""", (job["job_id"], job["attempt_count"])).fetchone()
        if not claimed:
            raise ExecutionBlocked("Codex configuration has no matching persisted execution window")
        started, ended = claimed[0], job["updated_at"]
        for context in contexts:
            at = context.get("recorded_at") if isinstance(context, dict) else None
            if (not isinstance(context, dict) or not isinstance(context.get("turn_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", context["turn_id"])
                    or (context.get("model"), context.get("reasoning_effort")) != (agent.model, agent.reasoning_effort)
                    or isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)
                    or not started <= at <= ended):
                raise ExecutionBlocked("Codex turn model/effort/time differs from the assigned execution")
        # CLI settings are a narrower, explicit policy choice. Contrary backend
        # telemetry remains a failure; settings never manufacture verified_* data.
        if (result.get("observed_models") not in (None, [], [agent.model])
                or result.get("observed_efforts") not in (None, [], [agent.reasoning_effort])):
            raise ExecutionBlocked("runtime metadata conflicts with Codex CLI configuration")

    def accept_report(self, state, spec, job):
        active = state["active"]
        current = self.get(state["task_id"])
        if current["generation"] != active["generation"] or (current["active"] or {}).get("execution_id") != active["execution_id"]:
            raise ExecutionBlocked("late result belongs to a superseded generation")
        result = job["result"] or {}
        report_type = {"planning": PMPlanStageReport, "plan_review": PlanReviewStageReport,
                       "contribution": ContributionStageReport}.get(spec.execution_scope, StageReport)
        report = report_type.model_validate(result.get("structured_output"))
        agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
        if spec.policy.configuration_evidence == "cli_configuration_v2" and agent.provider == "claude":
            from ai_company.adapters.claude_observation import persisted_attempt, validate_claude_result
            binding, claimed_at = persisted_attempt(self.db, job)
            if any(binding[key] != active[key] for key in ("execution_id", "generation", "role", "task_digest", "policy_digest")):
                raise ExecutionBlocked("Claude persisted attempt differs from the assigned generation")
            validate_claude_result(result, binding=binding, session_id=job["session_id"],
                started_at=claimed_at, ended_at=job["updated_at"],
                writable_paths=spec.task.allowed_paths if active["role"] == "developer" else ())
        elif spec.policy.configuration_evidence in ("cli_configuration", "cli_configuration_v2"):
            self._validate_cli_configuration(agent, job, result)
        else:
            if result.get("observed_models") != [agent.model] or result.get("observed_efforts") != [agent.reasoning_effort]:
                raise ExecutionBlocked("actual model/reasoning metadata is missing or differs from the assigned configuration")
            if agent.ultracode_enabled and result.get("observed_ultracode") != [True]:
                raise ExecutionBlocked("Ultracode workflow configuration is not confirmed by runtime metadata")
        if (report.execution_id, report.generation, report.role, report.task_digest, report.policy_digest) != (
                active["execution_id"], active["generation"], active["role"], digest(spec.task), digest(spec.policy)):
            raise ExecutionBlocked("stage result identity or immutable policy does not match")
        if spec.execution_scope == "contribution":
            from ai_company.contribution_commit import validate_contribution_receipt
            validate_contribution_receipt(spec, state, job, report)
        elif report.candidate_sha != job["head_commit"]:
            raise ExecutionBlocked("report does not target the actual candidate HEAD")
        if active["role"] != "developer" and job["repository_snapshot"] != active["input_snapshot"]:
            raise ExecutionBlocked("read-only role changed the repository")
        session = {"provider": job["provider"], "session_id": job["session_id"]}
        if active["role"] in ("reviewer", "final"):
            if session in state["authors"] or (active["role"] == "final" or spec.execution_scope == "plan_review") and session in state["pm_sessions"]:
                raise ExecutionBlocked("author/PM session cannot approve its own work")
            if report.verification_digest != (None if spec.execution_scope == "plan_review" else digest(state["verification"])):
                raise ExecutionBlocked("review does not bind the required checks and remote CI")
            pending = {f["finding_id"] for f in state["findings"]}
            if report.verdict == "PASS" and (report.findings or not pending.issubset(report.resolved_findings)):
                raise ExecutionBlocked("approval drops unresolved findings")
        if active["role"] == "developer" and _git(Path(spec.worktree), "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise ExecutionBlocked("developer left uncommitted files outside the candidate commit")
        if active["role"] == "developer" and (report.verdict != "DONE" or job["head_commit"] == active["input_snapshot"]["head_commit"]):
            raise ExecutionBlocked("developer must commit a changed candidate and return DONE")
        return report

    def _advance(self, state, spec, verdict="DONE", checks_passed=False):
        with SqliteSaver.from_conn_string(str(self.root / "flow-checkpoints.sqlite")) as saver, tracing_context(enabled=False):
            graph = build_graph(saver)
            progress = graph.invoke({"stage": state["stage"], "verdict": verdict, "checks_passed": checks_passed,
                                     "repairs": state["usage"]["repairs"], "max_repairs": min(spec.task.max_repairs, spec.policy.max_repairs)},
                                    {"configurable": {"thread_id": state["task_id"]}}, durability="sync")
        completed_stage = state["stage"]
        state["last_completed_stage"] = completed_stage
        state.update(stage=progress["next_stage"], status=progress["status"], resume_at=self.clock())
        if state["status"] == "READY":
            state["reason"] = {"developer": "implementation stage is ready",
                               "check": "candidate awaits required checks",
                               "reviewer": "required local and remote checks passed; independent review is ready",
                               "final": "independent review passed; designated final review is ready",
                               "gate": "final review passed; remote evidence will be refreshed"}.get(
                                   state["stage"], "next stage is ready")
        if spec.execution_scope == "plan_review" and completed_stage == "reviewer":
            state.update(stage="reviewer", status="PLAN_REVIEWED" if verdict == "PASS" else "NEEDS_PLAN_REVISION",
                         resume_at=None, reason="Independent plan content review recorded")
        elif spec.execution_scope == "planning" and completed_stage == "pm" and verdict == "PASS":
            state.update(stage="pm", status="PLAN_READY", resume_at=None, reason="PM proposal is ready for master confirmation")
        elif spec.execution_scope == "contribution" and completed_stage == "developer" and verdict == "DONE":
            state.update(stage="developer", status="CONTRIBUTION_READY", resume_at=None,
                         reason="committed contribution is ready for integration; checks and review remain")
        elif spec.execution_scope == "integration" and state["stage"] == "developer":
            state.update(status="WAITING_ROLE_REPAIR", resume_at=None,
                         reason="integration findings must return to contribution roles; integration cannot develop")
        if state["status"] in ("BLOCKED", "STOPPED"):
            state["resume_at"] = None
        state["usage"]["repairs"] = progress["repairs"]
        if state["active"]:
            state["executions"].append(state["active"])
        state["active"] = None
        if state["stage"] == "developer" and spec.execution_scope not in ("integration", "contribution"):
            state["reviews"] = {}
            state["verification"] = None
        with self.db:
            self._save(state, "graph_advanced")

    def _ingest_waiting_result(self, state, spec, job):
        """Commit execution facts once, before any capacity/ownership decision."""
        active = state["active"]
        if active.get("waiting_observed_attempts", 0) >= job["attempt_count"]:
            return
        agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
        # Accounting, author attribution, shared cooldown and observation marker
        # share one transaction. A crash before commit leaves the fact pending.
        with self.db:
            self._consume(state, spec, job)
            if job["status"] == "WAITING_QUOTA":
                resume = job["resume_at"] or spec.policy.retry.next_time(
                    self.clock(), job["retry_count"], 0, job["reset_at"])[0]
                self.db.execute("""UPDATE quota_groups SET
                    state=CASE WHEN state IN ('DISABLED','UNKNOWN') THEN state ELSE 'COOLDOWN' END,
                    resume_at=MAX(COALESCE(resume_at,0),?), reason=? WHERE group_id=?""",
                    (resume, job["last_category"], agent.quota_group))
            try:
                with repository_lock(job["repository_snapshot"]):
                    if execution_alive(job["process"]) or self.queue._read_guard(job):
                        raise ExecutionBlocked("saved waiting execution has an unresolved process/guard")
                    actual = repository_snapshot(Path(spec.worktree))
                    if actual != job["repository_snapshot"]:
                        raise ExecutionBlocked("repository differs from the saved waiting execution fact")
                    for key in ("repository", "git_common_dir", "worktree"):
                        if actual[key] != state["snapshot"][key]:
                            raise ExecutionBlocked("waiting execution changed repository identity")
                    state["snapshot"] = actual
                    active["input_snapshot"] = actual
                    bundle = handoff(spec, state, active, self.root / "handoffs" / state["task_id"] /
                                     active["execution_id"] / ("attempt-" + str(job["attempt_count"])))
                    active["handoff"] = bundle
                    # Preserve immutable submission binding; this is execution
                    # checkpoint data, including the same expected report IDs.
                    job["checkpoint"] = dict(bundle, expected_report=job["checkpoint"]["expected_report"])
                    self.queue._save(job)
                    state.update(status="READY" if job["status"] == "WAITING_QUOTA" else "WAITING_CAPACITY",
                                 resume_at=self.clock() if job["status"] == "WAITING_QUOTA"
                                 else job["resume_at"], reason="saved waiting execution observed")
            except (ExecutionBlocked, OSError, ValueError) as exc:
                # Even external mutation must not erase actual cost or cooldown.
                state.update(status="NEEDS_RECONCILIATION", resume_at=None, reason=str(exc))
            active["waiting_observed_attempts"] = job["attempt_count"]
            self._save(state, "waiting_result_observed")

    def _handle_job(self, state, spec, job):
        active = state["active"]
        agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
        if job["status"] in ("WAITING_QUOTA", "WAITING_RETRY"):
            self._ingest_waiting_result(state, spec, job)
            if state["status"] == "NEEDS_RECONCILIATION":
                return
            if job["status"] == "WAITING_QUOTA":
                _, available, times = self._candidates(spec, state, exclude=(agent.agent_id,))
                if available and budget_available(state, spec.policy):
                    self._assign(state, spec, available[0], previous=active)
                elif not job["session_id"]:
                    state.update(status="NEEDS_RECONCILIATION", resume_at=None,
                                 reason="quota has no resumable session and no eligible replacement")
                else:
                    self._wait(state, spec, times + [job["resume_at"]])
            else:
                state.update(status="WAITING_CAPACITY", resume_at=job["resume_at"], reason="transient network retry; no provider switch")
        elif job["status"] == "SESSION_COMPLETED":
            self._consume(state, spec, job)
            report = self.accept_report(state, spec, job)
            state["snapshot"] = job["repository_snapshot"]
            handoff(spec, state, active, self.root / "handoffs" / state["task_id"] / active["execution_id"])
            if active["role"] == "pm":
                state["plan"] = report.plan if spec.execution_scope == "planning" else {"summary": report.summary, "approved": report.verdict == "PASS"}
                if spec.execution_scope == "planning":
                    state["pm_response"] = report.model_dump(mode="json")
                state["pm_sessions"].append({"provider": job["provider"], "session_id": job["session_id"]})
            elif active["role"] in ("reviewer", "final"):
                state["reviews"][active["role"]] = report.model_dump(mode="json")
                outstanding = {f["finding_id"]: f for f in state["findings"] if f["finding_id"] not in report.resolved_findings}
                outstanding.update({f.finding_id: f.model_dump(mode="json") for f in report.findings})
                state["findings"] = list(outstanding.values())
            self._advance(state, spec, report.verdict)
            return
        elif job["status"] == "NEEDS_CONTEXT_HANDOFF":
            self._consume(state, spec, job)
            state.update(status="NEEDS_CONTEXT_HANDOFF", resume_at=None, reason="context checkpoint requires a new session")
        elif job["status"] in ("BLOCKED", "NEEDS_RECONCILIATION"):
            self._consume(state, spec, job)
            state.update(status=job["status"], resume_at=None, reason=job["reason"])
            if job["last_category"] == "authentication":
                self.db.execute("UPDATE quota_groups SET state='DISABLED',reason='authentication' WHERE group_id=?", (agent.quota_group,))
        else:
            state.update(status="NEEDS_RECONCILIATION", resume_at=None, reason="uncertain session execution")
        with self.db:
            self._save(state, "session_observed")

    def _tick(self, state):
        spec = FlowSpec.model_validate(state["specification"])
        if spec.execution_scope == "integration" and state["stage"] == "developer":
            state.update(status="WAITING_ROLE_REPAIR", resume_at=None,
                         reason="integration may not execute developer work; return findings to contribution roles")
            with self.db:
                self._save(state, "integration_repair_wait")
            return True
        if state["status"] == "CHECK_RUNNING":
            state.update(status="NEEDS_RECONCILIATION", resume_at=None, reason="worker stopped during local checks")
            with self.db:
                self._save(state, "check_interrupted")
            return True
        if state["active"] and state["active"]["job_id"]:
            fact = self.queue.get(state["active"]["job_id"])
            if fact["status"] not in ("READY", "WAITING_QUOTA", "WAITING_RETRY", "RUNNING"):
                self._handle_job(state, spec, fact)
                return True
        if state["stage"] not in ("check", "gate") and not budget_available(state, spec.policy):
            state.update(status="BLOCKED", resume_at=None, reason="cumulative budget exhausted or cost unobservable")
            with self.db:
                self._save(state, "budget_blocked")
            return False
        for dependency in spec.dependencies:
            try:
                ready = self.get(dependency)["status"] == "MERGE_READY"
            except ExecutionBlocked:
                ready = False
            if not ready:
                state.update(status="WAITING_DEPENDENCIES", resume_at=self.clock() + 60)
                with self.db:
                    self._save(state, "dependency_wait")
                return False
        if state["stage"] in ("check", "gate"):
            if _git(Path(spec.worktree), "status", "--porcelain=v1", "--untracked-files=all").strip():
                raise ExecutionBlocked("checks and final gate require a clean candidate worktree")
            if repository_snapshot(Path(spec.worktree)) != state["snapshot"]:
                raise ExecutionBlocked("candidate changed; new implementation/check/review cycle required")
            if state["stage"] == "gate":
                with repository_lock(state["snapshot"]):
                    remote = self._external(self.verifier.remote, spec, state)
                    if not remote or remote != state["verification"]["remote"]:
                        state.update(stage="check", verification=None, reviews={}, status="WAITING_CHECKS",
                                     resume_at=self.clock() + 60, reason="remote evidence changed; fresh checks and review required")
                    elif repository_snapshot(Path(spec.worktree)) != state["snapshot"]:
                        raise ExecutionBlocked("candidate changed while checking remote evidence")
                    elif not state["findings"] and all(state["reviews"].get(role, {}).get("verdict") == "PASS" for role in ("reviewer", "final")):
                        state.update(status="MERGE_READY" if spec.mode == "live" else "DEMO_READY",
                                     resume_at=self.clock() + 60 if spec.mode == "live" else None,
                                     reason="current candidate, required checks and independent final review agree")
                    else:
                        raise ExecutionBlocked("final gate is missing required review evidence")
                    with self.db:
                        self._save(state, "gate_checked")
                    return True
            with repository_lock(state["snapshot"]):
                guard = {"job_id": "flow-check-" + state["task_id"], "repository_snapshot": state["snapshot"],
                         "process": None, "lease_owner": "check-" + str(state["generation"])}
                if self.queue._read_guard(guard):
                    raise ExecutionBlocked("repository execution guard requires reconciliation before checks")
                if state["verification"] is None:
                    if not self._reserve_project_budget(state, spec, {
                            "job_id": "check-" + state["task_id"], "attempt_count": 0}, checking=True):
                        return False
                    state["status"] = "CHECK_RUNNING"
                    with self.db:
                        self._save(state, "check_started")
                    self.queue._write_guard(guard)
                    state["verification"] = self._external(self.verifier.check, spec, state)
                    state["usage"]["runtime_seconds"] += state["verification"].get("runtime_seconds", 0)
                    state.pop("project_reservation", None)
                    state["status"] = "READY"
                    with self.db:
                        self._save(state, "check_completed")
                    self.queue._clear_guard(guard)
                evidence = state["verification"]
                if (evidence.get("head_sha") != state["snapshot"]["head_commit"]
                        or evidence.get("task_digest") != digest(spec.task)
                        or evidence.get("policy_digest") != digest(spec.policy)
                        or sorted(c["name"] for c in evidence["checks"]) != sorted(spec.task.required_checks)
                        or evidence["passed"] != all(c["success"] for c in evidence["checks"])):
                    raise ExecutionBlocked("check evidence does not bind the required candidate and policy")
                if not state["verification"]["passed"]:
                    state["findings"] += [Finding(finding_id="CHECK-" + c["name"], detail="Required check failed",
                                                 evidence=c.get("log", "check")).model_dump(mode="json")
                                          for c in state["verification"]["checks"] if not c["success"]]
                    self._advance(state, spec, checks_passed=False)
                    return True
                remote = self._external(self.verifier.remote, spec, state)
                if remote is None:
                    state.update(status="WAITING_CHECKS", resume_at=self.clock() + 60, reason="required remote checkout attestation missing")
                    with self.db:
                        self._save(state, "ci_wait")
                    return False
                if spec.mode == "live" and (spec.remote_ci is None or remote.get("source") != "github"):
                    raise ExecutionBlocked("live completion requires actual GitHub checkout attestation")
                state["verification"]["remote"] = remote
                self._advance(state, spec, checks_passed=True)
                return True
        if state["status"] == "HANDOFF_PENDING":
            self._materialize(state, spec)
        if not state["active"]:
            eligible, available, times = self._candidates(spec, state)
            if not eligible:
                state.update(status="NO_ELIGIBLE_AGENT", resume_at=None, reason="role/model/capability/permission configuration is not eligible")
                with self.db:
                    self._save(state, "no_eligible_agent")
                return False
            if not available:
                self._wait(state, spec, times)
                with self.db:
                    self._save(state, "capacity_wait")
                return False
            self._assign(state, spec, available[0])
            self._materialize(state, spec)
        active = state["active"]
        job = self.queue.get(active["job_id"])
        # A prior worker may already have committed a result before updating the graph.
        if job["status"] not in ("READY", "WAITING_QUOTA", "WAITING_RETRY", "RUNNING"):
            self._handle_job(state, spec, job)
            return True
        if job["status"] == "READY":
            _, available, times = self._candidates(spec, state)
            if not any(a.agent_id == active["agent_id"] for a in available):
                if available:
                    self._assign(state, spec, available[0], previous=active)
                    return True
                self._wait(state, spec, times)
                with self.db:
                    self._save(state, "shared_capacity_wait")
                return False
        if job["status"] == "WAITING_RETRY":
            _, available, _ = self._candidates(spec, state)
            if not any(a.agent_id == active["agent_id"] for a in available):
                # A transient retry keeps its owner, but another task may have
                # exhausted or disabled the same account while it was waiting.
                agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
                group = self.db.execute("SELECT state,resume_at FROM quota_groups WHERE group_id=?",
                                        (agent.quota_group,)).fetchone()
                wake = (group[1] if group and group[0] == "COOLDOWN" and group[1] > self.clock()
                        else self.clock() + spec.policy.retry.backoff_max_seconds)
                state.update(status="WAITING_CAPACITY", resume_at=max(job["resume_at"] or self.clock(), wake),
                             reason="same-session retry waiting for shared account capacity")
                with self.db:
                    self._save(state, "shared_retry_capacity_wait")
                return False
        if job["status"] == "WAITING_QUOTA" and not job["session_id"]:
            self._handle_job(state, spec, job)
            return True
        if job["status"] == "WAITING_QUOTA":
            eligible, available, times = self._candidates(spec, state)
            alternative = [a for a in available if a.agent_id != active["agent_id"]]
            if alternative:
                self._assign(state, spec, alternative[0], previous=active)
                return True
            if not any(a.agent_id == active["agent_id"] for a in available) or job["resume_at"] is None:
                self._wait(state, spec, times)
                with self.db:
                    self._save(state, "capacity_wait")
                return False
        if not self._reserve_project_budget(state, spec, job):
            return False
        result = self.queue.run_once(executor=self._executor(spec, state), job_id=active["job_id"])
        if result["status"] in ("IDLE", "BUSY"):
            # Queue recovery may have changed a RUNNING fact to reconciliation.
            saved = self.queue.get(active["job_id"])
            if saved["status"] == "NEEDS_RECONCILIATION":
                self._handle_job(state, spec, saved)
            return False
        self._handle_job(state, spec, result)
        return True

    def _reserve_project_budget(self, state, spec, job, *, checking=False):
        """Reserve under the dispatcher lock before an external call releases it.

        Stored attempts remain the usage source of truth. An interrupted or
        unobserved attempt keeps its reservation until _consume records its fact.
        Registering a new project specification never modifies these rows.
        """
        budget = spec.project_budget
        if budget is None:
            return True
        previous = state.get("project_reservation")
        if previous:
            if (previous["job_id"] != job["job_id"]
                    or previous["attempt_count"] != job["attempt_count"] + 1):
                raise ExecutionBlocked("project allowance has an unobserved execution reservation")
            return True
        usage = dict(executions=0, runtime_seconds=0.0, cost_usd=0.0, repairs=0, cost_unknown=False)
        reserved = dict(executions=0, runtime_seconds=0.0, cost_usd=0.0)
        ownership = self._project_task_ownership()
        legacy_pending = False
        reserved_slots = 0
        unbounded_cost_pending = False
        for item in self.tasks():
            item_budget = item["specification"].get("project_budget")
            owners = ownership.get(item["task_id"], set())
            if item_budget:
                owners.add(item_budget["scope_id"])
            if len(owners) > 1:
                raise ExecutionBlocked("task has ambiguous project budget ownership")
            if budget.scope_id not in owners:
                continue
            if item["task_id"] == state["task_id"]:
                item = state
            for field in ("executions", "runtime_seconds", "cost_usd", "repairs"):
                usage[field] += item["usage"][field]
            usage["cost_unknown"] |= item["usage"]["cost_unknown"]
            pending = item.get("project_reservation")
            if pending:
                reserved_slots += 1
                if pending["cost_usd"] is None:
                    unbounded_cost_pending = True
                for field in reserved:
                    reserved[field] += pending[field] or 0
            elif not item_budget and item.get("resume_at") is not None:
                # Older approved tasks retain their policy. Their unreserved
                # attempts must settle before a new project cap can be used.
                legacy_pending = True
        exhausted = (usage["executions"] >= budget.max_executions
                     or usage["runtime_seconds"] >= budget.max_runtime_seconds
                     or usage["repairs"] > budget.max_repairs
                     or (budget.max_cost_usd is not None and
                         (usage["cost_unknown"] or usage["cost_usd"] >= budget.max_cost_usd)))
        remaining_runtime = budget.max_runtime_seconds - usage["runtime_seconds"] - reserved["runtime_seconds"]
        remaining_cost = (None if budget.max_cost_usd is None else
                          budget.max_cost_usd - usage["cost_usd"] - reserved["cost_usd"])
        occupied = (legacy_pending or reserved_slots >= budget.max_parallel
                    or (budget.max_cost_usd is not None and unbounded_cost_pending)
                    or usage["executions"] + reserved["executions"] >= budget.max_executions
                    or remaining_runtime <= 0 or (remaining_cost is not None and remaining_cost <= 0))
        if exhausted or occupied:
            state.update(status="BLOCKED" if exhausted else "WAITING_PROJECT_BUDGET",
                         reason="프로젝트 누적 예산 소진 또는 비용 미확인" if exhausted else "같은 프로젝트의 실행 중 예산 예약 대기",
                         resume_at=None if exhausted else self.clock() + 5)
            with self.db:
                self._save(state, "project_budget_exhausted" if exhausted else "project_budget_wait")
            return False
        reservation = {"job_id": job["job_id"], "attempt_count": job["attempt_count"] + 1,
                       "kind": "check" if checking else "model",
                       "executions": 0 if checking else 1, "runtime_seconds": min(remaining_runtime,
                           sum(c.timeout_seconds for c in spec.checks.values()) if checking else spec.policy.retry.execution_timeout_seconds,
                           spec.policy.max_runtime_seconds - state["usage"]["runtime_seconds"]),
                       "cost_usd": 0 if checking else remaining_cost}
        state["project_reservation"] = reservation
        with self.db:
            self._save(state, "project_budget_reserved")
        return True

    def _project_task_ownership(self):
        """Read historic ownership without migrating old immutable task specs."""
        owners = {}
        def add(task_id, project_id):
            if task_id and project_id:
                owners.setdefault(task_id, set()).add(project_id)
        for item in self.tasks():
            add(item["task_id"], item["specification"].get("plan", {}).get("project_id"))
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "management_links" in tables:
            for row in self.db.execute("SELECT flow_task_id,project_id FROM management_links"):
                add(*row)
        if "management_pm_requests" in tables:
            for row in self.db.execute("SELECT project_id,document FROM management_pm_requests"):
                value = json.loads(row[1])
                add((value.get("execution") or {}).get("task_id"), row[0])
        if "management_runs" in tables:
            for row in self.db.execute("SELECT project_id,document FROM management_runs"):
                value = json.loads(row[1])
                items = [*value.get("roles", {}).values(), value.get("integration", {}),
                         *value.get("role_history", []), *value.get("integration_history", [])]
                for item in items:
                    add(item.get("task_id"), row[0])
        return owners

    def _external(self, function, *args, **kwargs):
        # The per-task and repository locks remain held. SQLite transactions must
        # be committed before this boundary; other roles can schedule meanwhile.
        if self.db.in_transaction:
            raise ExecutionBlocked("cannot run external work inside a scheduler transaction")
        with suspended_lock(self._scheduler_lock):
            return function(*args, **kwargs)

    def _orphaned_executions(self):
        reserved = set()
        for state in self.tasks():
            try:
                with controller_lock(self.root / "flow-owners" / digest(state["task_id"])):
                    active = state["active"]
                    job = (self.queue.get(active["job_id"]) if active and active["job_id"] else
                           {"job_id": "flow-check-" + state["task_id"], "repository_snapshot": state["snapshot"],
                            "process": None})
                    marker = self.queue._read_guard(job)
                    matches = marker and (marker["job_id"], marker["queue_root"]) == (job["job_id"], str(self.queue.root))
                    unknown_or_live_guard = matches and (marker.get("process") is None or execution_alive(marker["process"]))
                    if execution_alive(job["process"]) or unknown_or_live_guard:
                        reserved.add(str(self.queue._guard_path(job)))
            except ExecutionBlocked as exc:
                if "another controller" not in str(exc):
                    return 2  # Unreadable ownership cannot authorize extra work.
            except (OSError, ValueError):
                return 2
        return len(reserved)

    def _recover_queue(self):
        try:
            with controller_lock(self.queue.root / "session-worker"):
                self.queue._recover_interrupted_workers()
        except ExecutionBlocked as exc:
            if "another controller" not in str(exc):
                raise

    def _observe_waits(self):
        for pending in self.tasks():
            active = pending["active"]
            if active and active["job_id"]:
                fact = self.queue.get(active["job_id"])
                if fact["status"] in ("WAITING_QUOTA", "WAITING_RETRY"):
                    self._ingest_waiting_result(pending, FlowSpec.model_validate(pending["specification"]), fact)

    def _occupied_slots(self):
        occupied = 0
        for slot in range(2):
            try:
                with controller_lock(self.root / "flow-slots" / str(slot)):
                    pass
            except ExecutionBlocked:
                occupied += 1
        return occupied

    def run_once(self, *, task_ids=None):
        allowed_task_ids = None if task_ids is None else frozenset(task_ids)
        with controller_lock(self.root / "dispatcher", blocking=True) as scheduler_lock:
            self._scheduler_lock = scheduler_lock
            try:
                # Re-observe after every external yield. Task snapshots and
                # capacity decisions from before another worker ran are stale.
                capacity_blocked = False
                self._recover_queue()
                self._observe_waits()
                for candidate in sorted(self.tasks(), key=lambda s: (s["resume_at"] or float("inf"), s["task_id"])):
                    if allowed_task_ids is not None and candidate["task_id"] not in allowed_task_ids:
                        continue
                    self._recover_queue()
                    self._observe_waits()
                    orphaned = self._orphaned_executions()
                    with ExitStack() as ownership:
                        try:
                            ownership.enter_context(controller_lock(self.root / "flow-owners" / digest(candidate["task_id"])))
                        except ExecutionBlocked:
                            continue
                        state = self.get(candidate["task_id"])
                        if state["resume_at"] is None or state["resume_at"] > self.clock():
                            continue
                        # Recovery must not need an execution slot. These two
                        # paths only persist uncertain facts, never call a model.
                        active = state["active"]
                        uncertain = active and active["job_id"] and self.queue.get(active["job_id"])["status"] == "NEEDS_RECONCILIATION"
                        if state["status"] == "CHECK_RUNNING" or uncertain:
                            self._tick(state)
                            return self.get(state["task_id"])
                        # Live CLI orphans retain virtual slots after flock is
                        # released by a dead controller. Reads/recovery still work.
                        if self._occupied_slots() + orphaned >= 2:
                            capacity_blocked = True
                            continue
                        # Waiting tasks release the slot after a bounded pass.
                        for slot in range(2):
                            try:
                                ownership.enter_context(controller_lock(self.root / "flow-slots" / str(slot)))
                                break
                            except ExecutionBlocked:
                                continue
                        else:
                            capacity_blocked = True
                            continue
                        try:
                            if self._tick(state):
                                return self.get(state["task_id"])
                        except Exception as exc:
                            if isinstance(exc, ExecutionBlocked) and "another session owns" in str(exc):
                                # Separate Git clones can run concurrently. Shared
                                # common dirs keep the original cross-queue guard.
                                continue
                            state.update(status="NEEDS_RECONCILIATION" if isinstance(exc, OSError) else "BLOCKED",
                                         reason=str(exc) if isinstance(exc, ExecutionBlocked) else type(exc).__name__, resume_at=None)
                            with self.db:
                                self._save(state, "guard_blocked")
                return {"status": "BUSY" if capacity_blocked else "IDLE",
                        "reason": "execution capacity reserved by active or orphaned workers" if capacity_blocked
                        else "no executable task; durable reservations retained"}
            finally:
                self._scheduler_lock = None

    def context_handoff(self, task_id):
        with controller_lock(self.root / "dispatcher"):
            state = self.get(task_id)
            if state["status"] != "NEEDS_CONTEXT_HANDOFF":
                raise ExecutionBlocked("context handoff requires the context-specific state")
            spec = FlowSpec.model_validate(state["specification"])
            old = state["active"]
            job = self.queue.get(old["job_id"])
            state["snapshot"] = job["repository_snapshot"]
            agent = next(a for a in spec.agents if a.agent_id == old["agent_id"])
            self._assign(state, spec, agent, previous=old)
            return state

    def migrate_policy(self, task_id, replacement: FlowSpec, reason: str):
        """Explicit, audited transition. No budget, task or evidence reset shortcut."""
        if not reason.strip():
            raise ExecutionBlocked("migration requires an operator reason")
        replacement = FlowSpec.model_validate(replacement.model_dump(mode="json"))
        with controller_lock(self.root / "dispatcher"):
            state = self.get(task_id)
            previous = FlowSpec.model_validate(state["specification"])
            if previous.project_budget is not None or replacement.project_budget is not None:
                raise ExecutionBlocked("confirmed project specifications cannot be changed by flow policy migration")
            if (replacement.task != previous.task or replacement.worktree != previous.worktree
                    or replacement.mode != previous.mode or replacement.dependencies != previous.dependencies
                    or replacement.checks != previous.checks or replacement.remote_ci != previous.remote_ci
                    or replacement.approved_plan != previous.approved_plan or replacement.plan != previous.plan
                    or replacement.execution_scope != previous.execution_scope
                    or replacement.inherited_authors != previous.inherited_authors
                    or replacement.inherited_pm_sessions != previous.inherited_pm_sessions):
                raise ExecutionBlocked("migration may change agent pools and budgets, not task/check acceptance")
            with repository_lock(state["snapshot"]):
                if repository_snapshot(Path(previous.worktree)) != state["snapshot"]:
                    raise ExecutionBlocked("repository changed before migration")
                active = state["active"]
                job = self.queue.get(active["job_id"]) if active and active["job_id"] else None
                if job and (job["status"] not in ("READY", "WAITING_QUOTA", "WAITING_RETRY", "BLOCKED")
                            or execution_alive(job["process"]) or self.queue._read_guard(job)):
                    raise ExecutionBlocked("migration requires a confirmed quiescent execution")
                for agent in replacement.agents:
                    row = self.db.execute("SELECT credential_ref,group_id FROM credential_groups WHERE provider=?",
                                          (agent.provider,)).fetchone()
                    if not row or tuple(row) != (agent.credential_ref, agent.quota_group):
                        raise ExecutionBlocked("migration cannot relabel the existing account quota")
                migration_id = uuid4().hex
                with self.db:
                    if job:
                        self._consume(state, previous, job)
                        job.update(status="SUPERSEDED", resume_at=None, reason="explicit policy migration " + migration_id)
                        self.queue._save(job)
                    record = {"migration_id": migration_id, "task_id": task_id, "reason": reason,
                              "at": self.clock(), "previous": state, "replacement": replacement.model_dump(mode="json")}
                    self.db.execute("INSERT INTO flow_migrations VALUES (?,?)", (migration_id, json.dumps(record)))
                    if active:
                        state["executions"].append(active)
                    state.update(specification=replacement.model_dump(mode="json"), spec_digest=digest(replacement),
                                 active=None, verification=None, reviews={}, status="READY", resume_at=self.clock())
                    # Policy-bound evidence must be recollected; never grandfather an approval.
                    if state["stage"] in ("check", "reviewer", "final", "gate"):
                        state["stage"] = "check"
                    self._save(state, "policy_migrated")
                return {"migration_id": migration_id, "task_id": task_id, "status": state["status"]}

    def rollback_policy(self, migration_id, reason: str):
        row = self.db.execute("SELECT document FROM flow_migrations WHERE id=?", (migration_id,)).fetchone()
        if not row:
            raise ExecutionBlocked("unknown migration")
        record = json.loads(row[0])
        return self.migrate_policy(record["task_id"], FlowSpec.model_validate(record["previous"]["specification"]),
                                   "rollback " + migration_id + ": " + reason)
