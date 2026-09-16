"""Program-owned handoffs, local checks and GitHub checkout attestations."""

import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import time
import os
import shutil
from uuid import uuid4
from zipfile import ZipFile
from urllib.parse import quote

from ai_company.contracts import digest
from ai_company.flow_contracts import FlowSpec
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import _git, repository_snapshot


def allowed(path: str, paths) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in paths)


def handoff(spec: FlowSpec, state: dict, execution: dict, destination: Path) -> dict:
    root = Path(spec.worktree).resolve()
    snapshot = repository_snapshot(root)
    changed = set(filter(None, _git(root, "diff", "--name-only", "-z", spec.task.base_sha).decode().split("\0")))
    changed.update(filter(None, _git(root, "diff", "--cached", "--name-only", "-z").decode().split("\0")))
    changed.update(filter(None, _git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")))
    if any(not allowed(path, spec.task.allowed_paths) for path in changed):
        raise ExecutionBlocked("code changes exceed the immutable allowed paths")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = []
    for name in sorted(changed):
        path = root / name
        if (any(part.lower() in {".git", ".env", "credentials", "auth.json", "credentials.json"}
                or part.lower().endswith((".pem", ".key")) or part.startswith(".env.") for part in Path(name).parts)
                or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents)):

            raise ExecutionBlocked("sensitive or symlink file cannot enter a handoff")
        if not path.exists():
            files.append({"path": name, "deleted": True})
            continue
        if path.stat().st_size > 2_000_000:
            raise ExecutionBlocked("handoff file exceeds the bounded snapshot size")
        content = path.read_bytes()
        object_id = sha256(content).hexdigest()
        copy = destination / object_id
        if not copy.exists():
            copy.write_bytes(content)
            copy.chmod(0o600)
        files.append({"path": name, "sha256": object_id, "content_ref": str(copy), "bytes": len(content)})
    bundle = {"task": spec.task.model_dump(mode="json"), "policy_digest": digest(spec.policy),
              "spec_digest": digest(spec), "stage": state["stage"], "task_id": state["task_id"],
              "generation": state["generation"], "previous_execution": {k: v for k, v in execution.items() if k != "handoff"},
              "last_completed_stage": state["last_completed_stage"], "findings": state["findings"],
              "verification": state["verification"], "plan": state["plan"], "usage": state["usage"],
              "snapshot": snapshot, "files": files, "next_action": state["stage"],
              "partial_changes_are_unverified": True}
    if spec.policy.configuration_evidence == "cli_configuration_v2":
        # dirty_digest is a fingerprint even for a clean worktree. File-only
        # reviewers cannot run Git; supply the runner's actual status separately.
        bundle["repository_state"] = {
            "source": "runner_git_status", "snapshot_digest": digest(snapshot),
            "head_commit": snapshot["head_commit"],
            "clean": not bool(_git(root, "status", "--porcelain=v1", "--untracked-files=all").strip()),
        }
    return bundle


class Verifier:
    def __init__(self, root: Path):
        self.root = root

    def check(self, spec: FlowSpec, state: dict) -> dict:
        candidate = repository_snapshot(Path(spec.worktree))["head_commit"]
        records = []
        start = time.monotonic()
        output_dir = self.root / "checks" / spec.task.task_id / str(state["generation"])
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, command in spec.checks.items():
            log = output_dir / (name + ".log")
            remaining = spec.policy.max_runtime_seconds - state["usage"]["runtime_seconds"] - (time.monotonic() - start)
            if remaining <= 0:
                raise ExecutionBlocked("cumulative execution time exhausted before checks")
            unit = None
            argv = list(command.argv)
            if spec.mode == "live":
                unit = "ai-company-run-" + uuid4().hex + ".service"
                argv[0] = shutil.which(argv[0]) or argv[0]
                argv = ["systemd-run", "--user", "--quiet", "--wait", "--collect", "--pipe",
                        "--service-type=exec", "--unit=" + unit, "--property=KillMode=control-group",
                        "--property=TimeoutStopSec=5", "--property=UMask=0077",
                        "--property=WorkingDirectory=" + spec.worktree,
                        "--setenv=PATH=" + os.environ.get("PATH", "/usr/bin:/bin"), "--", *argv]
            try:
                # A check cannot leave descendants running after a timeout.
                with log.open("wb") as output:
                    log.chmod(0o600)
                    process = subprocess.Popen(argv, cwd=spec.worktree, stdout=output,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=min(command.timeout_seconds, remaining))
                    from ai_company.adapters.session_cli import _processes, _terminate_group
                    if any(p["pgid"] == process.pid for p in _processes()):
                        _terminate_group(process, process.pid)
                        raise ExecutionBlocked("check left live descendants")
                except subprocess.TimeoutExpired:
                    from ai_company.adapters.session_cli import _terminate_group
                    if not _terminate_group(process, process.pid):
                        raise ExecutionBlocked("check process termination unconfirmed")
                    code = -1
            except OSError as exc:
                raise ExecutionBlocked("check command could not start") from exc
            finally:
                if unit:
                    from ai_company.adapters.session_cli import stop_service
                    if not stop_service(unit):
                        raise ExecutionBlocked("check cgroup termination unconfirmed")
            records.append({"name": name, "success": code == 0, "exit_code": code, "log": str(log)})
        if repository_snapshot(Path(spec.worktree)) != state["snapshot"]:
            raise ExecutionBlocked("checks changed the candidate worktree")
        return {"head_sha": candidate, "task_digest": digest(spec.task), "policy_digest": digest(spec.policy),
                "checks": records, "passed": all(c["success"] for c in records),
                "runtime_seconds": time.monotonic() - start, "remote": None}

    @staticmethod
    def _api(path: str, raw=False):
        result = subprocess.run(["gh", "api", path], capture_output=True, timeout=30)
        if result.returncode:
            raise ExecutionBlocked("GitHub evidence lookup failed")
        return result.stdout if raw else json.loads(result.stdout)

    def remote(self, spec: FlowSpec, state: dict) -> dict | None:
        if spec.remote_ci is None:
            return None
        policy, repo = spec.remote_ci, spec.task.repository
        path = policy.trusted_workflow_path
        if not re.fullmatch(r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml", path):
            raise ExecutionBlocked("approved workflow must be a repository workflow file")
        pr = self._api(f"repos/{repo}/pulls/{policy.pr_number}")
        head, base, merge = pr["head"]["sha"], pr["base"]["sha"], pr.get("merge_commit_sha")
        if head != state["snapshot"]["head_commit"] or base != spec.task.base_sha:
            return None
        approved = self._api(f"repos/{repo}/actions/workflows/{quote(Path(path).name)}")
        if approved.get("path") != path or not isinstance(approved.get("id"), int):
            raise ExecutionBlocked("approved workflow identity cannot be resolved")
        checks = self._api(f"repos/{repo}/commits/{head}/check-runs?per_page=100")["check_runs"]
        required = []
        for name in policy.required_checks:
            matching = [c for c in checks if c["name"] == name]
            if not matching:
                return None
            latest = max(matching, key=lambda c: c["id"])
            if (latest.get("app", {}).get("slug") != "github-actions" or latest["head_sha"] != head
                    or latest["status"] != "completed" or latest["conclusion"] != "success"):
                return None
            required.append(latest)
        # URLs locate a run; they are not proof that a check belongs to it.
        located = [re.fullmatch(r"https://github\.com/" + re.escape(repo)
                               + r"/actions/runs/(\d+)(?:/job/\d+)?", c.get("details_url", "")) for c in required]
        if not all(located) or len({m.group(1) for m in located}) != 1:
            return None
        run_id = int(located[0].group(1))
        run = self._api(f"repos/{repo}/actions/runs/{run_id}")
        if (run.get("id") != run_id or run.get("workflow_id") != approved["id"] or run.get("path") != path
                or run.get("repository", {}).get("full_name") != repo or run.get("head_sha") != head
                or run.get("status") != "completed" or run.get("conclusion") != "success"
                or run.get("referenced_workflows") or not isinstance(run.get("check_suite_id"), int)):
            return None
        # Support only events whose workflow definition ref we can establish.
        if run.get("event") == "pull_request" and merge:
            workflow_sha = merge
            workflow_ref = f"{repo}/{path}@refs/pull/{policy.pr_number}/merge"
        elif run.get("event") in ("push", "workflow_dispatch") and run.get("head_branch"):
            workflow_sha = head
            workflow_ref = f"{repo}/{path}@refs/heads/{run['head_branch']}"
        else:
            return None
        for commit in {head, workflow_sha}:
            content = self._api(f"repos/{repo}/contents/{path}?ref={commit}")
            if sha256(base64.b64decode(content["content"])).hexdigest() != policy.trusted_workflow_digest:
                raise ExecutionBlocked("executed CI workflow differs from the approved definition")
        attempt = run.get("run_attempt")
        if not isinstance(attempt, int) or attempt < 1:
            return None
        jobs = self._api(f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")["jobs"]
        for check in required:
            if check.get("check_suite", {}).get("id") != run.get("check_suite_id"):
                return None
            owners = [j for j in jobs if j.get("check_run_url") == f"https://api.github.com/repos/{repo}/check-runs/{check['id']}"]
            if len(owners) != 1:
                return None
            job = owners[0]
            if (job.get("run_id"), job.get("run_attempt"), job.get("head_sha"), job.get("name"),
                    job.get("status"), job.get("conclusion")) != (run_id, attempt, head, check["name"], "completed", "success"):
                return None
        artifacts = self._api(f"repos/{repo}/actions/runs/{run_id}/artifacts")["artifacts"]
        matches = [a for a in artifacts if a["name"] == policy.attestation_artifact and not a["expired"]]
        if len(matches) != 1 or matches[0]["size_in_bytes"] > 1_000_000:
            return None
        artifact = self._api(f"repos/{repo}/actions/artifacts/{matches[0]['id']}")
        if (artifact.get("id") != matches[0]["id"] or artifact.get("name") != policy.attestation_artifact
                or artifact.get("expired") is not False or artifact.get("size_in_bytes", 1_000_001) > 1_000_000
                or artifact.get("workflow_run", {}).get("id") != run_id
                or artifact.get("workflow_run", {}).get("head_sha") != head):
            return None
        data = self._api(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", raw=True)
        if len(data) > 1_000_000:
            raise ExecutionBlocked("CI artifact exceeds its size limit")
        with ZipFile(BytesIO(data)) as archive:
            if archive.namelist().count("evidence.json") != 1:
                raise ExecutionBlocked("CI artifact has ambiguous attestation entries")
            info = archive.getinfo("evidence.json")
            if info.file_size > 64_000:
                raise ExecutionBlocked("CI attestation exceeds its size limit")
            attestation = json.loads(archive.read(info))
        expected = {"head_sha": head, "base_sha": base, "task_digest": digest(spec.task),
                    "policy_digest": digest(spec.policy), "run_id": run_id, "run_attempt": attempt,
                    "workflow_ref": workflow_ref, "workflow_sha": workflow_sha}
        if any(attestation.get(k) != v for k, v in expected.items()):
            return None
        if not attestation.get("tested_sha") or attestation["tested_sha"] not in {head, merge}:
            return None
        current = self._api(f"repos/{repo}/pulls/{policy.pr_number}")
        current_run = self._api(f"repos/{repo}/actions/runs/{run_id}")
        if ((current["head"]["sha"], current["base"]["sha"], current.get("merge_commit_sha")) != (head, base, merge)
                or any(current_run.get(k) != run.get(k) for k in
                       ("workflow_id", "path", "head_sha", "run_attempt", "status", "conclusion", "check_suite_id"))):
            return None
        return {**attestation, "source": "github", "workflow_id": approved["id"],
                "workflow_digest": policy.trusted_workflow_digest, "artifact_id": artifact["id"],
                "check_ids": [c["id"] for c in required]}
