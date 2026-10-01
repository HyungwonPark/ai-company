"""Commit confined developer output without granting the model Git metadata writes.

The original structured report remains unchanged. A runner-owned receipt binds
its input HEAD and exact report to the committed tree, parent and resulting HEAD.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from ai_company.contracts import digest
from ai_company.flow_evidence import allowed
from ai_company.runtime import ExecutionBlocked


def _git(path, *args, env=None):
    environment = {key: value for key, value in (env or os.environ).items()
                   if not key.startswith("GIT_") or key in {"GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                                           "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-C", str(path), "-c", "core.hooksPath=/dev/null",
                             "-c", "core.fsmonitor=false", "-c", "core.attributesFile=/dev/null",
                             "-c", "commit.gpgsign=false", *args], env=environment,
                            capture_output=True, timeout=30)
    if result.returncode:
        raise ExecutionBlocked("runner commit Git operation failed: " + result.stderr.decode(errors="replace")[:500])
    return result.stdout.decode().strip("\n")


def _write_receipt(path, receipt):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def commit_contribution(spec, state, outcome, worktree, *, output_dir):
    if spec.execution_scope != "contribution" or state["stage"] != "developer":
        raise ExecutionBlocked("runner commit is restricted to contribution development")
    result = outcome.result or {}
    report = result.get("structured_output")
    if outcome.category != "success" or not isinstance(report, dict) or report.get("verdict") != "DONE":
        return outcome
    from ai_company.flow_contracts import ContributionStageReport
    ContributionStageReport.model_validate(report)
    expected = state["active"]
    if (report.get("commit_requested") is not True or report.get("candidate_sha") != expected["input_snapshot"]["head_commit"]
            or report.get("execution_id") != expected["execution_id"] or report.get("generation") != expected["generation"]
            or report.get("role") != "developer" or report.get("task_digest") != digest(spec.task)
            or report.get("policy_digest") != digest(spec.policy)):
        raise ExecutionBlocked("contribution report does not authorize its exact assigned input")
    if result.get("cgroup_stopped") is not True and spec.mode == "live":
        raise ExecutionBlocked("developer process group must be stopped before committing output")
    worktree = Path(worktree).resolve()
    receipt_path = Path(output_dir) / "runner-commit.json"
    binding = {"source": "runner_commit", "input_sha": report["candidate_sha"], "report_digest": digest(report),
               "spec_digest": digest(spec), "execution_id": expected["execution_id"]}
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if any(receipt.get(key) != value for key, value in binding.items()):
            raise ExecutionBlocked("runner commit receipt belongs to another execution")
        current = _git(worktree, "rev-parse", "HEAD")
        if current != receipt["candidate_sha"]:
            raise ExecutionBlocked("prepared runner commit needs reconciliation before execution resumes")
        if _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ExecutionBlocked("committed contribution has subsequent changes")
        result["runner_commit"] = receipt
        return outcome
    if _git(worktree, "rev-parse", "HEAD") != binding["input_sha"]:
        raise ExecutionBlocked("developer changed Git history outside the runner commit protocol")
    paths = set(_git(worktree, "diff", "--name-only", "-z", "HEAD").split("\0"))
    paths.update(_git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    paths.discard("")
    if not paths:
        raise ExecutionBlocked("developer produced no candidate changes")
    for name in paths:
        path = worktree / name
        if (not allowed(name, spec.task.allowed_paths) or ".git" in Path(name).parts
                or path.is_symlink() or not path.resolve().is_relative_to(worktree)):
            raise ExecutionBlocked("developer output exceeds confined contribution paths")
        if path.exists() and (not path.is_file() or path.stat().st_size > 1_000_000):
            raise ExecutionBlocked("developer output is not a bounded regular file")
    # A private index makes the commit tree independent of any partially staged
    # model output. Hooks are disabled for these runner-owned Git commands only.
    fd, index = tempfile.mkstemp(prefix="contribution-index-", dir=output_dir)
    os.close(fd)
    os.unlink(index)
    env = {**os.environ, "GIT_INDEX_FILE": index,
           "GIT_AUTHOR_NAME": "AI Company Worker", "GIT_AUTHOR_EMAIL": "worker@ai-company.invalid",
           "GIT_COMMITTER_NAME": "AI Company Worker", "GIT_COMMITTER_EMAIL": "worker@ai-company.invalid"}
    try:
        _git(worktree, "read-tree", binding["input_sha"], env=env)
        _git(worktree, "add", "--all", "--", *sorted(paths), env=env)
        tree = _git(worktree, "write-tree", env=env)
        commit = _git(worktree, "commit-tree", tree, "-p", binding["input_sha"],
                      "-m", "Implement contribution " + spec.task.task_id, env=env)
        receipt = {**binding, "candidate_sha": commit, "tree_sha": tree, "paths": sorted(paths)}
        _write_receipt(receipt_path, receipt)
        _git(worktree, "update-ref", "HEAD", commit, binding["input_sha"])
        _git(worktree, "reset", "--mixed", commit)
    finally:
        Path(index).unlink(missing_ok=True)
        Path(index + ".lock").unlink(missing_ok=True)
    if _git(worktree, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ExecutionBlocked("runner commit did not produce a clean candidate")
    result["runner_commit"] = receipt
    return outcome


def validate_contribution_receipt(spec, state, job, report):
    receipt = (job.get("result") or {}).get("runner_commit")
    raw = (job.get("result") or {}).get("structured_output")
    worktree = Path(spec.worktree)
    if (not isinstance(receipt, dict) or receipt.get("source") != "runner_commit"
            or report.commit_requested is not True
            or receipt.get("input_sha") != state["active"]["input_snapshot"]["head_commit"]
            or receipt.get("input_sha") != report.candidate_sha
            or receipt.get("candidate_sha") != job["head_commit"]
            or receipt.get("execution_id") != report.execution_id
            or receipt.get("spec_digest") != digest(spec) or receipt.get("report_digest") != digest(raw)):
        raise ExecutionBlocked("contribution is missing its exact runner commit receipt")
    if (_git(worktree, "rev-parse", "HEAD") != receipt["candidate_sha"]
            or _git(worktree, "show", "-s", "--format=%P", receipt["candidate_sha"]) != receipt["input_sha"]
            or _git(worktree, "rev-parse", receipt["candidate_sha"] + "^{tree}") != receipt.get("tree_sha")):
        raise ExecutionBlocked("runner receipt does not match the actual single-parent candidate tree")
    changed = set(_git(worktree, "diff", "--name-only", "-z", receipt["input_sha"], receipt["candidate_sha"]).split("\0")) - {""}
    if not changed or changed != set(receipt.get("paths", [])) or any(not allowed(name, spec.task.allowed_paths) for name in changed):
        raise ExecutionBlocked("runner receipt changed paths do not match the committed contribution")
