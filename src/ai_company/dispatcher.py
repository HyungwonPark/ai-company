"""Deterministic role assignment and failover over the persistent session queue."""

import json
from pathlib import Path
import time
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith.run_helpers import tracing_context

from ai_company.adapters.session_cli import run_session
from ai_company.contracts import Finding, digest
from ai_company.flow_contracts import AgentProfile, FlowSpec, StageReport, budget_available
from ai_company.flow_evidence import Verifier, allowed, handoff
from ai_company.flow_graph import build_graph
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import _git, SessionQueue, SessionSpec, execution_alive, repository_lock, repository_snapshot
from ai_company.storage import controller_lock


class Dispatcher:
    def __init__(self, root: Path, *, clock=time.time, verifier=None, executor=None):
        self.root, self.clock = root.resolve(), clock
        self.queue = SessionQueue(self.root / "sessions", clock=clock)
        self.db = self.queue.db
        self.verifier = verifier or Verifier(self.root)
        self.executor = executor
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flow_events(id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
                kind TEXT NOT NULL, at REAL NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quota_groups(group_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                resume_at REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS credential_groups(provider TEXT PRIMARY KEY,
                credential_ref TEXT NOT NULL, group_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS flow_migrations(id TEXT PRIMARY KEY, document TEXT NOT NULL);
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
                     "spec_digest": digest(spec), "stage": "developer" if spec.approved_plan else "pm",
                     "status": "READY", "reason": "approved plan" if spec.approved_plan else "PM decision needed",
                     "snapshot": snapshot, "plan": spec.plan, "last_completed_stage": "approved_plan" if spec.approved_plan else "submitted",
                     "generation": 0, "active": None, "executions": [], "findings": [], "verification": None,
                     "reviews": {}, "authors": [], "pm_sessions": [], "resume_at": self.clock(),
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
            if (not agent.enabled or not agent.verified() or not required.issubset(agent.capabilities)
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

    def _executor(self, spec, state):
        agent = next(a for a in spec.agents if a.agent_id == state["active"]["agent_id"])

        def execute(provider, worktree, prompt, session_id, **kwargs):
            kwargs["timeout_seconds"] = min(kwargs["timeout_seconds"], spec.policy.max_runtime_seconds - state["usage"]["runtime_seconds"])
            prompt += ("\nReturn ONLY the required structured stage report. Use expected_report identifiers unchanged. "
                       "Developer: commit allowed code changes and return DONE. Review: inspect the exact candidate; "
                       "return PASS, REVISE with evidence, or BLOCK. Do not merge or deploy. "
                       "Do not claim a different model or change acceptance criteria.")
            if self.executor:
                return self.executor(agent, state, provider, worktree, prompt, session_id, **kwargs)
            if spec.mode != "live":
                raise ExecutionBlocked("fixture mode requires an explicit fixture executor")
            return run_session(provider, worktree, prompt, session_id, **kwargs, model=agent.model,
                               reasoning_effort=agent.reasoning_effort, ultracode_enabled=agent.ultracode_enabled, output_schema=StageReport.model_json_schema(),
                               permission="workspace-write" if state["stage"] == "developer" else "read-only", isolate_cgroup=True, capture_configuration=True,
                               max_cost_usd=(spec.policy.max_cost_usd - state["usage"]["cost_usd"]
                                             if spec.policy.max_cost_usd is not None else None))
        return execute

    def _consume(self, state, spec, job):
        active = state["active"]
        delta = job["attempt_count"] - active["accounted_attempts"]
        if delta <= 0:
            return
        result = job["result"] or {}
        state["usage"]["executions"] += delta
        state["usage"]["runtime_seconds"] += max(0, result.get("duration_seconds", spec.policy.retry.execution_timeout_seconds))
        cost = result.get("total_cost_usd")
        if isinstance(cost, (float, int)) and not isinstance(cost, bool) and cost >= 0:
            state["usage"]["cost_usd"] += cost
        else:
            state["usage"]["cost_unknown"] = True
        active["accounted_attempts"] = job["attempt_count"]
        active["session_id"] = job["session_id"]
        if active["role"] == "developer" and job["session_id"]:
            author = {"provider": job["provider"], "session_id": job["session_id"]}
            if author not in state["authors"]:
                state["authors"].append(author)

    def accept_report(self, state, spec, job):
        active = state["active"]
        current = self.get(state["task_id"])
        if current["generation"] != active["generation"] or (current["active"] or {}).get("execution_id") != active["execution_id"]:
            raise ExecutionBlocked("late result belongs to a superseded generation")
        result = job["result"] or {}
        report = StageReport.model_validate(result.get("structured_output"))
        agent = next(a for a in spec.agents if a.agent_id == active["agent_id"])
        if result.get("observed_models") != [agent.model] or result.get("observed_efforts") != [agent.reasoning_effort]:
            raise ExecutionBlocked("actual model/reasoning metadata is missing or differs from the assigned configuration")
        if agent.ultracode_enabled and result.get("observed_ultracode") != [True]:
            raise ExecutionBlocked("Ultracode workflow configuration is not confirmed by runtime metadata")
        if (report.execution_id, report.generation, report.role, report.task_digest, report.policy_digest) != (
                active["execution_id"], active["generation"], active["role"], digest(spec.task), digest(spec.policy)):
            raise ExecutionBlocked("stage result identity or immutable policy does not match")
        if report.candidate_sha != job["head_commit"]:
            raise ExecutionBlocked("report does not target the actual candidate HEAD")
        if active["role"] != "developer" and job["repository_snapshot"] != active["input_snapshot"]:
            raise ExecutionBlocked("read-only role changed the repository")
        session = {"provider": job["provider"], "session_id": job["session_id"]}
        if active["role"] in ("reviewer", "final"):
            if session in state["authors"] or (active["role"] == "final" and session in state["pm_sessions"]):
                raise ExecutionBlocked("author/PM session cannot approve its own work")
            if report.verification_digest != digest(state["verification"]):
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
        state["last_completed_stage"] = state["stage"]
        state.update(stage=progress["next_stage"], status=progress["status"], resume_at=self.clock())
        if state["status"] in ("BLOCKED", "STOPPED"):
            state["resume_at"] = None
        state["usage"]["repairs"] = progress["repairs"]
        if state["active"]:
            state["executions"].append(state["active"])
        state["active"] = None
        if state["stage"] == "developer":
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
                state["plan"] = {"summary": report.summary, "approved": report.verdict == "PASS"}
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
                remote = self.verifier.remote(spec, state)
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
                    state["status"] = "CHECK_RUNNING"
                    with self.db:
                        self._save(state, "check_started")
                    self.queue._write_guard(guard)
                    state["verification"] = self.verifier.check(spec, state)
                    state["usage"]["runtime_seconds"] += state["verification"].get("runtime_seconds", 0)
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
                remote = self.verifier.remote(spec, state)
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
        result = self.queue.run_once(executor=self._executor(spec, state), job_id=active["job_id"])
        if result["status"] in ("IDLE", "BUSY"):
            # Queue recovery may have changed a RUNNING fact to reconciliation.
            saved = self.queue.get(active["job_id"])
            if saved["status"] == "NEEDS_RECONCILIATION":
                self._handle_job(state, spec, saved)
            return False
        self._handle_job(state, spec, result)
        return True

    def run_once(self):
        with controller_lock(self.root / "dispatcher"):
            # Observe ALL saved waits before choosing work: otherwise another
            # task could use an account whose cooldown died with the worker.
            for pending in self.tasks():
                active = pending["active"]
                if active and active["job_id"]:
                    fact = self.queue.get(active["job_id"])
                    if fact["status"] in ("WAITING_QUOTA", "WAITING_RETRY"):
                        self._ingest_waiting_result(pending, FlowSpec.model_validate(pending["specification"]), fact)
            for state in sorted(self.tasks(), key=lambda s: (s["resume_at"] or float("inf"), s["task_id"])):
                if state["resume_at"] is None or state["resume_at"] > self.clock():
                    continue
                try:
                    if self._tick(state):
                        return self.get(state["task_id"])
                except Exception as exc:
                    state.update(status="NEEDS_RECONCILIATION" if isinstance(exc, OSError) else "BLOCKED",
                                 reason=str(exc) if isinstance(exc, ExecutionBlocked) else type(exc).__name__, resume_at=None)
                    with self.db:
                        self._save(state, "guard_blocked")
            return {"status": "IDLE", "reason": "no executable task; durable reservations retained"}

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
            if (replacement.task != previous.task or replacement.worktree != previous.worktree
                    or replacement.mode != previous.mode or replacement.dependencies != previous.dependencies
                    or replacement.checks != previous.checks or replacement.remote_ci != previous.remote_ci
                    or replacement.approved_plan != previous.approved_plan or replacement.plan != previous.plan):
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
