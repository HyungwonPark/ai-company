"""Immutable role pools, execution settings, budgets and review evidence."""

import re
from typing import Literal
from pydantic import Field, field_validator, model_serializer, model_validator
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
    configuration_evidence: Literal["runtime_metadata", "cli_configuration", "cli_configuration_v2"] = "runtime_metadata"

    @model_serializer(mode="wrap")
    def preserve_legacy_digest(self, handler):
        document = handler(self)
        if self.configuration_evidence == "runtime_metadata":
            document.pop("configuration_evidence", None)
        return document

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
    execution_scope: Literal["full", "planning", "contribution", "integration"] = "full"
    inherited_authors: tuple[dict, ...] = ()
    inherited_pm_sessions: tuple[dict, ...] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_digest(self, handler):
        document = handler(self)
        if self.execution_scope == "full":
            document.pop("execution_scope", None)
        if not self.inherited_authors:
            document.pop("inherited_authors", None)
        if not self.inherited_pm_sessions:
            document.pop("inherited_pm_sessions", None)
        return document

    @field_validator("inherited_authors", "inherited_pm_sessions")
    @classmethod
    def valid_inherited_sessions(cls, sessions):
        if len(sessions) > 1000:
            raise ValueError("too many inherited sessions")
        for session in sessions:
            if (set(session) != {"provider", "session_id"} or session["provider"] not in ("codex", "claude")
                    or not isinstance(session["session_id"], str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", session["session_id"])):
                raise ValueError("inherited author/PM identity requires exact provider and session_id")
        if len({(s["provider"], s["session_id"]) for s in sessions}) != len(sessions):
            raise ValueError("inherited sessions must be unique")
        return sessions

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.execution_scope == "planning" and self.approved_plan:
            raise ValueError("planning cannot skip PM work with an approved plan")
        if self.execution_scope in ("contribution", "integration") and not self.approved_plan:
            raise ValueError("contribution/integration require the confirmed plan")
        if self.execution_scope == "integration" and not self.inherited_authors:
            raise ValueError("integration requires inherited authors to enforce independent review")
        if self.execution_scope != "integration" and (self.inherited_authors or self.inherited_pm_sessions):
            raise ValueError("inherited review exclusions belong to integration scope")
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




class ContributionStageReport(StageReport):
    commit_requested: bool

    @model_validator(mode="after")
    def contribution_verdict(self):
        if self.role != "developer":
            raise ValueError("contribution report is only for development")
        if self.verdict == "DONE" and not self.commit_requested:
            raise ValueError("contribution DONE requires an explicit runner commit request")
        return self


class PMPlanStageReport(StageReport):
    # A local validator avoids automation_contracts -> flow_contracts circular imports.
    plan: dict | None

    @field_validator("plan")
    @classmethod
    def typed_plan(cls, value):
        if value is None:
            return None
        from ai_company.automation_contracts import PMPlanContent
        return PMPlanContent.model_validate(value).model_dump(mode="json")

    @model_validator(mode="after")
    def planning_verdict(self):
        if self.role != "pm" or self.verdict not in ("PASS", "BLOCK"):
            raise ValueError("planning report must be PM PASS or BLOCK")
        if self.verdict == "PASS" and self.plan is None:
            raise ValueError("planning PASS requires a validated plan")
        return self

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        from ai_company.automation_contracts import PMPlanContent
        schema = super().model_json_schema(*args, **kwargs)
        plan_schema = PMPlanContent.model_json_schema()
        schema.setdefault("$defs", {}).update(plan_schema.pop("$defs", {}))
        schema["properties"]["plan"] = {"anyOf": [plan_schema, {"type": "null"}]}
        return schema


def budget_available(task: dict, policy: FlowPolicy) -> bool:
    usage = task["usage"]
    return (usage["executions"] < policy.max_executions
            and usage["runtime_seconds"] < policy.max_runtime_seconds
            and usage["repairs"] <= policy.max_repairs
            and (policy.max_cost_usd is None or (not usage["cost_unknown"]
                 and usage["cost_usd"] < policy.max_cost_usd)))
