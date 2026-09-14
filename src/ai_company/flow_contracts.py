"""Immutable role pools, execution settings, budgets and review evidence."""

from typing import Literal
from pydantic import Field, model_validator
from ai_company.contracts import Contract, Task, Key, Text, Digest, Commit, Finding
from ai_company.sessions import RetryPolicy

FlowRole = Literal["pm", "developer", "reviewer", "final"]


class AgentProfile(Contract):
    agent_id: Key
    provider: Literal["codex", "claude"]
    requested_label: Text
    model: Text
    reasoning_effort: Text
    ultracode_enabled: bool = False
    verified_ultracode: bool | None = None
    roles: tuple[FlowRole, ...]
    capabilities: tuple[Key, ...] = ("structured_result", "read_repository")
    allowed_paths: tuple[Text, ...] = ()
    credential_ref: Key
    quota_group: Key
    enabled: bool = True
    verified_model: str | None = None
    verified_effort: str | None = None
    verification_evidence: str | None = None

    def verified(self) -> bool:
        return (self.verified_model == self.model and self.verified_effort == self.reasoning_effort
                and (not self.ultracode_enabled or self.verified_ultracode is True)
                and bool(self.verification_evidence))


class FlowPolicy(Contract):
    version: Key = "failover-v1"
    candidates: dict[FlowRole, tuple[Key, ...]]
    max_executions: int = Field(default=24, ge=1, le=1000)
    max_runtime_seconds: float = Field(default=1800, gt=0, le=86400, allow_inf_nan=False)
    max_cost_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_repairs: int = Field(default=3, ge=0, le=20)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @model_validator(mode="after")
    def complete_roles(self):
        if set(self.candidates) != {"pm", "developer", "reviewer", "final"}:
            raise ValueError("all four role pools must be explicitly declared")
        if any(len(v) != len(set(v)) for v in self.candidates.values()):
            raise ValueError("candidate order must not contain duplicates")
        return self


class CheckCommand(Contract):
    argv: tuple[Text, ...] = Field(min_length=1)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)


class RemoteCI(Contract):
    pr_number: int = Field(gt=0)
    required_checks: tuple[Text, ...] = Field(min_length=1)
    trusted_workflow_path: Text = ".github/workflows/ci.yml"
    trusted_workflow_digest: Digest
    # An attestation published by the approved CI workflow binds actual checkout,
    # task and policy, rather than inferring tested SHA from the check-run label.
    attestation_artifact: Key = "ai-company-evidence"


class FlowSpec(Contract):
    task: Task
    worktree: Text
    agents: tuple[AgentProfile, ...] = Field(min_length=1)
    policy: FlowPolicy
    checks: dict[Key, CheckCommand]
    remote_ci: RemoteCI | None = None
    approved_plan: bool = False
    plan: dict = Field(default_factory=dict)
    dependencies: tuple[Key, ...] = ()
    mode: Literal["live", "fixture"] = "live"

    @model_validator(mode="after")
    def validate_configuration(self):
        by_id = {a.agent_id: a for a in self.agents}
        if len(by_id) != len(self.agents):
            raise ValueError("agent IDs must be unique")
        groups = {}
        for agent in self.agents:
            if agent.ultracode_enabled and (agent.provider != "claude" or agent.reasoning_effort != "xhigh"):
                raise ValueError("Ultracode is a Claude workflow setting with xhigh reasoning")
        for agent in self.agents:
            identity = (agent.provider, agent.credential_ref)
            if identity in groups and groups[identity] != agent.quota_group:
                raise ValueError("one credential/account must use one conservative quota group")
            groups[identity] = agent.quota_group
        for role, candidates in self.policy.candidates.items():
            for name in candidates:
                if name not in by_id or role not in by_id[name].roles:
                    raise ValueError("role candidate is missing or does not support the role")
                agent = by_id[name]
                if role in ("pm", "final") and (agent.provider, agent.model, agent.reasoning_effort) != (
                        "codex", "gpt-6-astra", "ultra"):
                    raise ValueError("PM and final review require the designated Astra Ultra configuration")
                if role in ("developer", "reviewer") and agent.provider == "claude" and (
                        agent.model != "claude-opus-5" or agent.reasoning_effort != "xhigh" or not agent.ultracode_enabled):
                    raise ValueError("Claude roles require Opus 5, xhigh and Ultracode")
                if role in ("developer", "reviewer") and agent.provider == "codex" and (
                        agent.model, agent.reasoning_effort) != ("gpt-6-astra", "high"):
                    raise ValueError("Codex development/first review require Astra High")
        if set(self.checks) != set(self.task.required_checks):
            raise ValueError("approved check commands must exactly cover required checks")
        return self


class StageReport(Contract):
    execution_id: Digest
    generation: int = Field(ge=1)
    role: FlowRole
    task_digest: Digest
    policy_digest: Digest
    candidate_sha: Commit
    verification_digest: Digest | None
    verdict: Literal["DONE", "PASS", "REVISE", "BLOCK"]
    findings: tuple[Finding, ...] = ()
    resolved_findings: tuple[Key, ...] = ()
    summary: Text


def budget_available(task: dict, policy: FlowPolicy) -> bool:
    usage = task["usage"]
    return (usage["executions"] < policy.max_executions
            and usage["runtime_seconds"] < policy.max_runtime_seconds
            and usage["repairs"] <= policy.max_repairs
            and (policy.max_cost_usd is None or (not usage["cost_unknown"]
                 and usage["cost_usd"] < policy.max_cost_usd)))
