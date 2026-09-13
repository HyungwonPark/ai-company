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
        policy = spec.remote_ci
        repo = spec.task.repository
        pr = self._api(f"repos/{repo}/pulls/{policy.pr_number}")
        head, base, tested = pr["head"]["sha"], pr["base"]["sha"], pr.get("merge_commit_sha")
        if head != state["snapshot"]["head_commit"] or base != spec.task.base_sha:
            return None
        content = self._api(f"repos/{repo}/contents/{policy.trusted_workflow_path}?ref={head}")
        if sha256(base64.b64decode(content["content"])).hexdigest() != policy.trusted_workflow_digest:
            raise ExecutionBlocked("CI workflow differs from the approved definition")
        checks = self._api(f"repos/{repo}/commits/{head}/check-runs?per_page=100")["check_runs"]
        required = []
        for name in policy.required_checks:
            matching = [c for c in checks if c["name"] == name]
            if not matching:
                return None
            latest = max(matching, key=lambda c: c["id"])
            if latest.get("app", {}).get("slug") != "github-actions":
                return None
            if latest["head_sha"] != head or latest["status"] != "completed" or latest["conclusion"] != "success":
                return None
            required.append(latest)
        runs = {int(m.group(1)) for c in required
                if (m := re.search(r"/actions/runs/(\d+)", c.get("details_url", "")))}
        if len(runs) != 1:
            return None
        run_id = runs.pop()
        run = self._api(f"repos/{repo}/actions/runs/{run_id}")
        if run["head_sha"] != head or run["conclusion"] != "success":
            return None
        artifacts = self._api(f"repos/{repo}/actions/runs/{run_id}/artifacts")["artifacts"]
        matches = [a for a in artifacts if a["name"] == policy.attestation_artifact and not a["expired"]]
        if len(matches) != 1 or matches[0]["size_in_bytes"] > 1_000_000:
            return None
        data = self._api(f"repos/{repo}/actions/artifacts/{matches[0]['id']}/zip", raw=True)
        with ZipFile(BytesIO(data)) as archive:
            info = archive.getinfo("evidence.json")
            if info.file_size > 64_000:
                raise ExecutionBlocked("CI attestation exceeds its size limit")
            attestation = json.loads(archive.read(info))
        expected = {"head_sha": head, "base_sha": base, "task_digest": digest(spec.task),
                    "policy_digest": digest(spec.policy), "run_id": run_id}
        if any(attestation.get(k) != v for k, v in expected.items()):
            return None
        if not attestation.get("tested_sha") or attestation["tested_sha"] not in {head, tested}:
            return None
        current = self._api(f"repos/{repo}/pulls/{policy.pr_number}")
        if (current["head"]["sha"], current["base"]["sha"]) != (head, base):
            return None
        return {**attestation, "source": "github", "check_ids": [c["id"] for c in required]}
