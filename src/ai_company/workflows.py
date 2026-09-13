"""LangGraph owns progress; the harness/ledger own each execution's facts."""

from contextlib import closing
from pathlib import Path
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith.run_helpers import tracing_context

from ai_company.adapters.fake import FakeVerifier
from ai_company.contracts import Finding, Policy, Result, Task, Verification, digest
from ai_company.harness import Harness, Registry
from ai_company.runtime import ExecutionBlocked, ReconciliationRequired, Runner
from ai_company.storage import Ledger, controller_lock


class State(TypedDict):
    task_id: str
    mode: str
    status: str
    reason: str
    attempt: int
    started_at: float
    candidate: str
    feedback: list[dict]
    verification: dict | None
    review: dict | None
    route: str
    history: list[str]


class DevelopmentLoop:
    def __init__(self, task: Task, harness: Harness, verifier: FakeVerifier):
        self.task, self.harness, self.verifier = task, harness, verifier

    def build(self, checkpointer: SqliteSaver):
        graph = StateGraph(State)
        for name in ("implement", "verify", "review", "repair", "gate"):
            node = getattr(self, name)
            graph.add_node(name, self.guard(name, node))
        graph.add_edge(START, "implement")
        for name in ("implement", "verify", "review", "repair"):
            graph.add_conditional_edges(name, lambda state: state["route"],
                                        {key: key for key in ("implement", "verify", "review", "repair", "gate")} | {"end": END})
        graph.add_edge("gate", END)
        return graph.compile(checkpointer=checkpointer)

    def guard(self, name, node):
        def guarded(state: State):
            try:
                if time.time() - state["started_at"] >= self.harness.policy.task_timeout_seconds:
                    raise ExecutionBlocked("task time budget exhausted")
                result = node(state)
            except ReconciliationRequired as exc:
                result = {"status": "NEEDS_RECONCILIATION", "reason": str(exc), "route": "end"}
            except ExecutionBlocked as exc:
                result = {"status": "BLOCKED", "reason": str(exc), "route": "end"}
            return result | {"history": state["history"] + [name]}
        return guarded

    def execute(self, role: str, state: State) -> Result:
        return self.harness.execute(
            self.task, role, state["attempt"], state["candidate"], state["started_at"],
            tuple(Finding.model_validate(f) for f in state["feedback"]),
            Verification.model_validate(state["verification"]) if state["verification"] else None,
        )

    def implement(self, state: State) -> dict:
        result = self.execute("developer", state)
        if result.commit_sha == state["candidate"]:
            raise ExecutionBlocked("implementation supplied no changed revision")
        return {"candidate": result.commit_sha, "verification": None, "review": None, "route": "verify"}

    def validate_verification(self, evidence: Verification, candidate: str) -> None:
        expected = (candidate, self.task.base_sha, candidate, digest(self.task), digest(self.harness.policy))
        actual = (evidence.head_sha, evidence.base_sha, evidence.tested_sha,
                  evidence.task_digest, evidence.policy_digest)
        if actual != expected:
            raise ExecutionBlocked("verification is bound to a different commit, spec, or policy")
        names = [check.name for check in evidence.checks]
        if len(names) != len(set(names)) or set(names) != set(self.task.required_checks):
            raise ExecutionBlocked("verification has missing, duplicate, or unexpected checks")
        if any(check.outcome not in ("success", "failure") for check in evidence.checks):
            raise ExecutionBlocked("required check did not complete")

    def verify(self, state: State) -> dict:
        evidence = self.verifier.verify(self.task, self.harness.policy, state["candidate"])
        self.validate_verification(evidence, state["candidate"])
        failures = [Finding(finding_id=f"CI-{index}", detail=f"Required check failed: {check.name}",
                            evidence=check.evidence).model_dump(mode="json")
                    for index, check in enumerate(evidence.checks) if check.outcome == "failure"]
        old = {f["finding_id"]: f for f in state["feedback"]}
        old.update({f["finding_id"]: f for f in failures})
        return {"verification": evidence.model_dump(mode="json"), "feedback": list(old.values()),
                "route": "repair" if failures else "review"}

    def review(self, state: State) -> dict:
        result = self.execute("reviewer", state)
        if result.verdict == "BLOCK":
            raise ExecutionBlocked("review requires a human decision")
        pending = {f["finding_id"]: f for f in state["feedback"]
                   if f["finding_id"] not in result.resolved_findings}
        pending.update({f.finding_id: f.model_dump(mode="json") for f in result.findings})
        return {"review": result.model_dump(mode="json"), "feedback": list(pending.values()),
                "route": "gate" if result.verdict == "PASS" else "repair"}

    def repair(self, state: State) -> dict:
        if state["attempt"] >= min(self.task.max_repairs, self.harness.policy.max_repairs):
            return {"status": "STOPPED", "reason": "repair budget exhausted", "route": "end"}
        return {"attempt": state["attempt"] + 1, "route": "implement"}

    def gate(self, state: State) -> dict:
        evidence = Verification.model_validate(state["verification"])
        self.validate_verification(evidence, state["candidate"])
        review = Result.model_validate(state["review"])
        if (review.verdict != "PASS" or review.commit_sha != state["candidate"] or state["feedback"]
                or any(check.outcome != "success" for check in evidence.checks)):
            raise ExecutionBlocked("completion lacks passing checks and an independent review")
        if self.verifier.observe(state["candidate"], self.task.base_sha) != (state["candidate"], self.task.base_sha):
            raise ExecutionBlocked("head or base changed after verification; fresh work is required")
        return {"status": "DEMO_READY", "reason": "simulation passed; real merge readiness is not evaluated", "route": "end"}


def run_task(task: Task, root: Path, registry: Registry, policy: Policy | None = None,
             verifier: FakeVerifier | None = None, *, interrupt_after: list[str] | None = None,
             runner: Runner | None = None) -> State:
    policy, verifier = policy or Policy(), verifier or FakeVerifier()
    root = root.resolve()
    with controller_lock(root), closing(Ledger(root / "executions.sqlite")) as ledger:
        binding = digest({"task": digest(task), "policy": digest(policy), "agents": registry.manifest(),
                          "verifier": verifier.identity(), "workflow_version": 1})
        started = ledger.bind(task.task_id, binding, task.model_dump_json())
        harness = Harness(root, ledger, registry, policy, runner)
        with SqliteSaver.from_conn_string(str(root / "checkpoints.sqlite")) as checkpoints:
            loop = DevelopmentLoop(task, harness, verifier)
            graph = loop.build(checkpoints)
            config = {"configurable": {"thread_id": task.task_id}, "recursion_limit": 200}
            # This local simulator never sends traces containing tasks to a tracing service.
            with tracing_context(enabled=False):
                snapshot = graph.get_state(config)
                if snapshot.values and not snapshot.next:
                    if snapshot.values["status"] == "DEMO_READY":
                        try:
                            loop.gate(snapshot.values)
                        except ExecutionBlocked as exc:
                            graph.update_state(config, {"status": "BLOCKED", "reason": str(exc),
                                                        "route": "end"}, as_node="gate")
                            return graph.get_state(config).values
                    return snapshot.values
                initial: State = dict(task_id=task.task_id, mode="simulation", status="RUNNING", reason="",
                                      attempt=0, started_at=started, candidate=task.base_sha, feedback=[],
                                      verification=None, review=None, route="implement", history=[])
                return graph.invoke(None if snapshot.values else initial, config,
                                    interrupt_after=interrupt_after, durability="sync")
