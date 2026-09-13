"""Context assembly, role binding, workspace preparation, lifecycle and result gates."""

from pathlib import Path
import time

from pydantic import ValidationError

from ai_company.adapters import Adapter
from ai_company.contracts import Context, Finding, Policy, Request, Result, Role, Task, Verification, digest
from ai_company.runtime import ExecutionBlocked, ReconciliationRequired, Runner
from ai_company.storage import Ledger


class Registry:
    def __init__(self) -> None:
        self.bindings: dict[Role, Adapter] = {}

    def register(self, role: Role, adapter: Adapter) -> None:
        if role not in adapter.info.roles or adapter.info.mode != "simulation":
            raise ExecutionBlocked("adapter does not support this role in simulation mode")
        if role in self.bindings:
            raise ExecutionBlocked("role is already registered")
        self.bindings[role] = adapter

    def resolve(self, role: Role) -> Adapter:
        if role not in self.bindings:
            raise ExecutionBlocked(f"no adapter registered for {role}")
        return self.bindings[role]

    def manifest(self) -> dict:
        return {role: self.resolve(role).info.model_dump(mode="json")
                for role in ("developer", "reviewer")}


class Harness:
    def __init__(self, root: Path, ledger: Ledger, registry: Registry, policy: Policy,
                 runner: Runner | None = None):
        self.root, self.ledger, self.registry, self.policy = root.resolve(), ledger, registry, policy
        self.runner = runner or Runner()

    def execute(self, task: Task, role: Role, attempt: int, input_sha: str,
                started_at: float, feedback: tuple[Finding, ...] = (),
                verification: Verification | None = None) -> Result:
        adapter = self.registry.resolve(role)
        context = Context(task=task, policy=self.policy, role=role, attempt=attempt,
                          input_sha=input_sha, feedback=feedback, verification=verification)
        run_id = digest({"context": digest(context), "adapter": digest(adapter.info)})
        saved = self.ledger.lookup(run_id)
        if saved is not None:
            return self._validate(saved, context, run_id)
        remaining = self.policy.task_timeout_seconds - (time.time() - started_at)
        if remaining <= 0:
            raise ExecutionBlocked("task time budget exhausted")
        try:
            healthy = adapter.healthcheck()
        except Exception as exc:
            raise ExecutionBlocked(f"adapter healthcheck error: {type(exc).__name__}") from exc
        if not healthy:
            raise ExecutionBlocked("adapter healthcheck failed")
        workspace = self.root / "workspaces" / task.task_id / run_id
        if not workspace.resolve().is_relative_to(self.root):
            raise ExecutionBlocked("workspace escapes the state directory")
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        request = Request(run_id=run_id, context=context, workspace=str(workspace))
        self.ledger.start(run_id, task.task_id, request.model_dump_json(), self.policy.max_runs)
        try:
            result = self.runner.run(adapter, request, min(remaining, self.policy.run_timeout_seconds))
        except ReconciliationRequired:
            # Keep the unfinished fact: restarting must never relaunch this execution.
            raise
        except ExecutionBlocked as exc:
            self.ledger.block(run_id, str(exc))
            raise
        try:
            result = self._validate(result, context, run_id)
        except ExecutionBlocked as exc:
            self.ledger.block(run_id, str(exc))
            raise
        self.ledger.finish(run_id, result.model_dump_json())
        return result

    @staticmethod
    def _validate(value: Result | dict, context: Context, run_id: str) -> Result:
        try:
            # Revalidate even an adapter-created model; do not trust model_construct/copy.
            raw = value.model_dump(mode="json") if isinstance(value, Result) else value
            result = Result.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise ExecutionBlocked("malformed adapter result") from exc
        if (result.run_id, result.context_digest, result.role) != (run_id, digest(context), context.role):
            raise ExecutionBlocked("stale or mismatched adapter result")
        if result.outcome != "succeeded":
            raise ExecutionBlocked("agent execution failed; code repair is not assumed")
        if context.role == "reviewer":
            if result.commit_sha != context.input_sha:
                raise ExecutionBlocked("review targets a different commit")
            if result.verdict == "PASS" and not {f.finding_id for f in context.feedback}.issubset(result.resolved_findings):
                raise ExecutionBlocked("review omitted previous unresolved findings")
        return result
