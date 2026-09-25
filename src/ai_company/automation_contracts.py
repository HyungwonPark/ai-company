"""Bounded PM proposals and server-owned automation configuration."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from ai_company.contracts import Contract, Commit, Digest, Key, Text
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy
from ai_company.harness.guidance import GuidanceRef


class RolePlan(Contract):
    key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=100)
    responsibility: Text
    goal: Text
    acceptance: list[Text] = Field(min_length=1, max_length=20)
    allowed_paths: list[Text] = Field(min_length=1, max_length=20)
    depends_on: list[str] = Field(max_length=16)
    required_capabilities: list[str] = Field(default_factory=list, max_length=5)
    skill_required: bool = False

    @model_serializer(mode="wrap")
    def preserve_legacy_role(self, handler):
        value = handler(self)
        if not self.required_capabilities:
            value.pop("required_capabilities", None)
        if not self.skill_required:
            value.pop("skill_required", None)
        return value

    @model_validator(mode="after")
    def confined_paths(self):
        import re
        if any(not re.fullmatch(r"[a-z][a-z0-9+#.-]{1,39}", value) for value in self.required_capabilities):
            raise ValueError("role capabilities must be generic bounded technical terms")
        if self.skill_required and not self.required_capabilities:
            raise ValueError("mandatory role skill needs a named capability")
        for value in self.allowed_paths:
            path = PurePosixPath(value)
            parts = value.rstrip("/").split("/")
            if (path.is_absolute() or "\\" in value
                    or any(part in ("", ".", "..", ".git", ".github", ".env") for part in parts)
                    or any(part.startswith(".env.") for part in parts)):
                raise ValueError("roles require confined source paths outside Git, CI and secrets")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("duplicate dependency")
        return self


class Requirement(Contract):
    id: Key
    source: Text
    acceptance: Text
    verification: Text
    role_keys: list[Key] = Field(min_length=1, max_length=8)


class MaterialQuestion(Contract):
    id: Key
    prompt: Text
    reason: Text
    options: list[Text] = Field(default_factory=list, max_length=5)
    recommendation: Text | None = None
    status: Literal["open", "answered", "assumed", "excluded"]
    resolution: Text | None = None
    answer_message_id: Key | None = None
    source_request_id: Key | None = None
    source_question_id: Key | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_question(self, handler):
        value = handler(self)
        if self.answer_message_id is None:
            value.pop("answer_message_id", None)
        if self.source_request_id is None:
            value.pop("source_request_id", None)
        if self.source_question_id is None:
            value.pop("source_question_id", None)
        return value

    @model_validator(mode="after")
    def resolved_has_basis(self):
        if self.status != "open" and not self.resolution:
            raise ValueError("resolved material questions need an answer, assumption or explicit exclusion")
        if bool(self.source_request_id) != bool(self.source_question_id):
            raise ValueError("question source needs both request and question IDs")
        return self


class PlanFinding(Contract):
    id: Key
    evidence: Text
    impact: Text
    alternatives: list[Text] = Field(min_length=1, max_length=5)
    recommendation: Text
    blocking: bool
    status: Literal["open", "resolved", "deferred"]
    resolution: Text | None = None

    @model_validator(mode="after")
    def disposition(self):
        if self.status != "open" and not self.resolution:
            raise ValueError("disposed findings need a recorded reason")
        if self.blocking and self.status == "deferred":
            raise ValueError("blocking findings cannot be deferred")
        return self


class PMRequirements(Contract):
    version: Literal[2]
    revision: int = Field(ge=1)
    goal_digest: Digest
    problem: Text
    users_and_flow: Text
    scope: list[Text] = Field(min_length=1, max_length=20)
    exclusions: list[Text] = Field(max_length=20)
    assumptions: list[Text] = Field(max_length=20)
    questions: list[MaterialQuestion] = Field(max_length=20)
    findings: list[PlanFinding] = Field(max_length=20)
    requirements: list[Requirement] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique_ids(self):
        for items in (self.questions, self.findings, self.requirements):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError("requirement, question and finding IDs must be unique")
        return self


class PMPlanContent(Contract):
    summary: Text
    roles: list[RolePlan] = Field(min_length=2, max_length=8)
    completion_criteria: list[Text] = Field(min_length=1, max_length=20)
    execution_spec_proposal: dict | None = None
    requirements_review: PMRequirements | None = None
    skill_selection: dict | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_content(self, handler):
        value = handler(self)
        if self.execution_spec_proposal is None:
            value.pop("execution_spec_proposal", None)
        if self.requirements_review is None:
            value.pop("requirements_review", None)
        if self.skill_selection is None:
            value.pop("skill_selection", None)
        return value

    @model_validator(mode="after")
    def dependency_graph(self):
        if self.execution_spec_proposal is not None:
            from ai_company.execution_specs import ExecutionSpecSelection
            ExecutionSpecSelection.model_validate(self.execution_spec_proposal)
        by_key = {role.key: role for role in self.roles}
        if len(by_key) != len(self.roles):
            raise ValueError("role keys must be unique")
        for role in self.roles:
            if role.key in role.depends_on or set(role.depends_on) - by_key.keys():
                raise ValueError("role dependencies must name other proposed roles")
        remaining = set(by_key)
        while remaining:
            ready = {key for key in remaining if not set(by_key[key].depends_on) & remaining}
            if not ready:
                raise ValueError("cyclic role dependencies")
            remaining -= ready
        # Integration cannot silently choose which author's conflicting edit wins.
        for index, role in enumerate(self.roles):
            for other in self.roles[index + 1:]:
                for first in role.allowed_paths:
                    for second in other.allowed_paths:
                        a, b = first.rstrip("/"), second.rstrip("/")
                        if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                            raise ValueError("role output paths must not overlap")
        if sum(not role.depends_on for role in self.roles) < 2:
            raise ValueError("the plan must contain at least two independent roles")
        return self


class AutomationCI(Contract):
    workflow_path: str = Field(pattern=r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
    workflow_digest: Digest
    required_checks: tuple[Text, ...] = Field(min_length=1)
    artifact_name: Key = "ai-company-evidence"


class AutomationConfig(Contract):
    """Installed by a trusted local operator; never supplied by an HTTP request."""

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    source_clone: Text
    base_sha: Commit
    base_branch: str = Field(min_length=1, max_length=150)
    allowed_paths: tuple[Text, ...] = Field(min_length=1)
    checks: dict[Key, CheckCommand] = Field(min_length=1)
    agents: tuple[AgentProfile, ...] = Field(min_length=1)
    policy: FlowPolicy
    ci: AutomationCI
    mode: Literal["live", "fixture"] = "live"
    poll_seconds: float = Field(default=2, ge=0.1, le=60)
    pm_timeout_seconds: int = Field(default=180, ge=1, le=1800)
    max_parallel: int = Field(default=2, ge=1, le=2)
    guidance: GuidanceRef | None = None
    skill_catalog: tuple[dict, ...] = Field(default=(), max_length=20)
    skill_public_sources: tuple[str, ...] = Field(default=(), max_length=6)
    skill_search_terms: tuple[str, ...] = Field(default=(), max_length=6)

    @model_serializer(mode="wrap")
    def preserve_legacy_configuration(self, handler):
        value = handler(self)
        if self.guidance is None:
            value.pop("guidance", None)
        if not self.skill_catalog:
            value.pop("skill_catalog", None)
        if not self.skill_public_sources:
            value.pop("skill_public_sources", None)
        if not self.skill_search_terms:
            value.pop("skill_search_terms", None)
        return value
