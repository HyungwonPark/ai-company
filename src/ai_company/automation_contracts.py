"""Bounded PM proposals and server-owned automation configuration."""

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from ai_company.contracts import Contract, Commit, Digest, Key, Text
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy


class RolePlan(Contract):
    key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=100)
    responsibility: Text
    goal: Text
    acceptance: list[Text] = Field(min_length=1, max_length=20)
    allowed_paths: list[Text] = Field(min_length=1, max_length=20)
    depends_on: list[str] = Field(max_length=16)

    @model_validator(mode="after")
    def confined_paths(self):
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


class PMPlanContent(Contract):
    summary: Text
    roles: list[RolePlan] = Field(min_length=2, max_length=8)
    completion_criteria: list[Text] = Field(min_length=1, max_length=20)
    execution_spec_proposal: dict | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_content(self, handler):
        value = handler(self)
        if self.execution_spec_proposal is None:
            value.pop("execution_spec_proposal", None)
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
