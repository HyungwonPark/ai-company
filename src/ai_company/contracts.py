"""Immutable input/output contracts shared by every role and adapter."""

from hashlib import sha256
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=8000)]
Key = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Role = Literal["developer", "reviewer"]


def digest(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Task(Contract):
    task_id: Key
    version: Annotated[StrictInt, Field(ge=1)] = 1
    goal: Text
    acceptance: Annotated[tuple[Text, ...], Field(min_length=1)]
    repository: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    base_sha: Commit
    allowed_paths: Annotated[tuple[Text, ...], Field(min_length=1)]
    required_checks: Annotated[tuple[Key, ...], Field(min_length=1)]
    max_repairs: Annotated[StrictInt, Field(ge=0, le=20)] = 3

    @field_validator("allowed_paths")
    @classmethod
    def relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for value in paths:
            path = PurePosixPath(value)
            if path.is_absolute() or "\\" in value or any(
                part in ("", ".", "..", ".git") for part in value.rstrip("/").split("/")
            ):
                raise ValueError("allowed_paths must be relative paths outside .git")
        return paths

    @field_validator("required_checks")
    @classmethod
    def unique_checks(cls, checks: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(checks)) != len(checks):
            raise ValueError("required_checks must be unique")
        return checks


class Policy(Contract):
    version: Key = "v0.1-simulation"
    max_repairs: Annotated[StrictInt, Field(ge=0, le=20)] = 3
    max_runs: Annotated[StrictInt, Field(ge=1, le=100)] = 16
    run_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 30
    task_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=86400)] = 300


class Finding(Contract):
    finding_id: Key
    detail: Text
    evidence: Text


class Check(Contract):
    name: Key
    outcome: Literal["success", "failure", "skipped", "cancelled", "pending"]
    evidence: Text


class Verification(Contract):
    source: Literal["simulation"] = "simulation"
    head_sha: Commit
    base_sha: Commit
    tested_sha: Commit
    task_digest: Digest
    policy_digest: Digest
    checks: tuple[Check, ...]


class Context(Contract):
    task: Task
    policy: Policy
    role: Role
    attempt: Annotated[StrictInt, Field(ge=0)]
    input_sha: Commit
    feedback: tuple[Finding, ...] = ()
    verification: Verification | None = None
    tools: tuple[Literal["simulate"], ...] = ("simulate",)


class AdapterInfo(Contract):
    agent_id: Key
    version: Key
    roles: Annotated[tuple[Role, ...], Field(min_length=1)]
    mode: Literal["simulation"] = "simulation"
    configuration_digest: Digest


class Request(Contract):
    run_id: Digest
    context: Context
    workspace: Text


class Result(Contract):
    run_id: Digest
    context_digest: Digest
    role: Role
    outcome: Literal["succeeded", "failed"] = "succeeded"
    commit_sha: Commit | None = None
    verdict: Literal["PASS", "REVISE", "BLOCK"] | None = None
    findings: tuple[Finding, ...] = ()
    resolved_findings: tuple[Key, ...] = ()
    evidence: Annotated[tuple[Text, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def coherent_result(self) -> "Result":
        if self.outcome == "failed":
            if self.verdict == "PASS":
                raise ValueError("failed execution cannot approve")
            return self
        if self.commit_sha is None:
            raise ValueError("successful result needs a commit")
        if self.role == "developer" and (self.verdict or self.findings or self.resolved_findings):
            raise ValueError("developer cannot supply a review verdict")
        if self.role == "reviewer":
            if self.verdict is None:
                raise ValueError("reviewer needs a verdict")
            if self.verdict == "PASS" and self.findings:
                raise ValueError("PASS cannot have unresolved findings")
            if self.verdict == "REVISE" and not self.findings:
                raise ValueError("REVISE needs actionable findings")
        ids = [finding.finding_id for finding in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding IDs must be unique")
        if len(self.resolved_findings) != len(set(self.resolved_findings)) or set(ids).intersection(self.resolved_findings):
            raise ValueError("resolved findings must be unique and cannot remain unresolved")
        return self
