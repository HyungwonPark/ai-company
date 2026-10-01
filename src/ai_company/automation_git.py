"""Owned independent clones, recoverable aggregation, and draft-only publication.

Configuration is installed by the server operator. This module never accepts a
repository URL or an arbitrary output directory from a PM proposal or HTTP body.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

from ai_company.runtime import ExecutionBlocked
from ai_company.storage import controller_lock


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ExecutionBlocked("expected a full immutable commit SHA")
    return value


class AutomationGit:
    def __init__(self, root: Path, config):
        self.root, self.config = Path(root).resolve(), config
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("clones", "records", "locks"):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.repository):
            raise ExecutionBlocked("invalid configured GitHub repository")

    def _record(self, kind, key):
        return self.root / "records" / (kind + "-" + _digest(key) + ".json")

    def _save(self, path, value):
        temporary = path.with_suffix(".pending")
        with temporary.open("w") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read(path):
        try:
            return json.loads(path.read_text()) if path.exists() else None
        except (OSError, ValueError) as error:
            raise ExecutionBlocked("automation Git record requires reconciliation") from error

    def _git(self, path, *args, input=None, env=None, check=True):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
                           GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        environment.update(env or {})
        argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "core.attributesFile=/dev/null", "-c", "commit.gpgsign=false", "-c", "init.templateDir="]
        if args[0] in {"push", "ls-remote"}:
            # Preserve the authenticated GitHub CLI path without loading arbitrary global Git drivers.
            argv += ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
        argv += ["-C", str(path), *args]
        try:
            result = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=120, env=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExecutionBlocked("Git operation is unavailable or unfinished: " + args[0]) from error
        if check and result.returncode:
            raise ExecutionBlocked("Git operation failed without resetting the repository: " + args[0])
        return result.stdout.strip() if check else result

    def _origin(self):
        return "https://github.com/" + self.config.repository + ".git"

    def _owned(self, path):
        path = Path(path)
        if path.is_symlink() or path.resolve().parent != self.root / "clones":
            raise ExecutionBlocked("repository is outside the owned clone directory")
        records = [self._read(record) for record in (self.root / "records").glob("clone-*.json")]
        if not any(record and record.get("path") == str(path.resolve()) and record.get("ready") for record in records):
            raise ExecutionBlocked("repository has no completed ownership record")
        self._independent(path)
        return path.resolve()

    def _independent(self, path):
        git = Path(path) / ".git"
        if git.is_symlink() or not git.is_dir() or (git / "objects/info/alternates").exists():
            raise ExecutionBlocked("clone must have independent Git storage")
        common = Path(self._git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common != git.resolve():
            raise ExecutionBlocked("clone shares another repository's Git common directory")
        if self._git(path, "remote", "get-url", "origin") != self._origin():
            raise ExecutionBlocked("owned clone origin changed")

    def _clean(self, path):
        if self._git(path, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ExecutionBlocked("owned repository has uncommitted changes; reconciliation required")

    def _source(self, base_sha):
        candidates = [Path(self.config.source_clone).resolve()]
        for record in sorted((self.root / "records").glob("clone-*.json")):
            value = self._read(record)
            if value and value.get("ready"):
                candidates.append(self._owned(value["path"]))
        for source in candidates:
            if not self._git(source, "cat-file", "-e", base_sha + "^{commit}", check=False).returncode:
                if source != Path(self.config.source_clone).resolve():
                    self._clean(source)
                return source
        raise ExecutionBlocked("requested base commit is absent from trusted source and owned clones")

    def clone(self, key, base_sha) -> Path:
        base_sha = _commit(base_sha)
        key = str(key)
        if not key or len(key) > 500:
            raise ExecutionBlocked("invalid clone ownership key")
        record_path = self._record("clone", key)
        destination = self.root / "clones" / _digest(key)
        binding = {"key": key, "base_sha": base_sha, "repository": self.config.repository,
                   "source_clone": str(Path(self.config.source_clone).resolve()), "path": str(destination)}
        with controller_lock(self.root / "locks" / ("clone-" + _digest(key))):
            previous = self._read(record_path)
            if previous:
                if any(previous.get(name) != value for name, value in binding.items()):
                    raise ExecutionBlocked("clone ownership key is bound to another input")
                if previous.get("ready"):
                    return self._owned(destination)  # Preserve a worker's edits and advanced HEAD.
                if destination.exists() or destination.is_symlink():
                    # A stop after checkout but before the final record needs no new clone.
                    self._independent(destination)
                    self._clean(destination)
                    if self._git(destination, "rev-parse", "HEAD") != base_sha:
                        raise ExecutionBlocked("unfinished clone HEAD requires reconciliation")
                    self._save(record_path, dict(binding, ready=True))
                    return self._owned(destination)
            elif destination.exists() or destination.is_symlink():
                raise ExecutionBlocked("unowned clone destination already exists")
            source = self._source(base_sha)
            self._save(record_path, dict(binding, ready=False))
            self._git(self.root, "clone", "--no-hardlinks", "--dissociate", "--no-checkout",
                      "--", str(source), str(destination))
            self._git(destination, "remote", "set-url", "origin", self._origin())
            self._git(destination, "checkout", "--detach", base_sha)
            self._independent(destination)
            self._clean(destination)
            self._save(record_path, dict(binding, ready=True))
            return destination

    def _commit_tree(self, clone, tree, parents, message, base_sha):
        date = self._git(clone, "show", "-s", "--format=%cI", base_sha)
        environment = {"GIT_AUTHOR_NAME": "AI Company", "GIT_AUTHOR_EMAIL": "ai-company@localhost",
                       "GIT_COMMITTER_NAME": "AI Company", "GIT_COMMITTER_EMAIL": "ai-company@localhost",
                       "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(("-p", parent))
        return self._git(clone, *args, input=message + "\n", env=environment)

    def _complete_pending(self, path, record, clone):
        pending = record.get("pending")
        if pending is None:
            return
        current = self._git(clone, "rev-parse", "HEAD")
        self._clean(clone)
        if current == pending["before"]:
            self._git(clone, "checkout", "--detach", pending["after"])
        elif current != pending["after"]:
            raise ExecutionBlocked("aggregate HEAD changed outside the recorded operation")
        record["head_sha"] = pending["after"]
        record["completed"].append(pending["step"])
        record["pending"] = None
        self._save(path, record)

    def _advance(self, path, record, clone, step, new_head):
        record["pending"] = {"step": step, "before": record["head_sha"], "after": new_head}
        self._save(path, record)
        self._complete_pending(path, record, clone)

    def aggregate(self, key, base_sha, contributions, manifest) -> tuple[Path, str]:
        base_sha = _commit(base_sha)
        context_only = manifest.get("purpose") == "dependency_context"
        if not context_only and (manifest.get("base_sha") != self.config.base_sha or any(not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(k, "")))
                for k in ("task_digest", "policy_digest"))):
            raise ExecutionBlocked("CI request must bind the configured PR base, task and policy")
        request = json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if len(request.encode()) > 64_000:
            raise ExecutionBlocked("CI request exceeds the attestation size limit")
        inputs = {}
        for role, value in sorted(contributions.items()):
            clone = self._owned(value.get("clone", value.get("path")))
            start, candidate = _commit(value["base_sha"]), _commit(value["candidate_sha"])
            self._clean(clone)
            if self._git(clone, "rev-parse", "HEAD") != candidate:
                raise ExecutionBlocked("contribution HEAD differs from its reviewed candidate")
            if self._git(clone, "merge-base", "--is-ancestor", start, candidate, check=False).returncode:
                raise ExecutionBlocked("contribution does not descend from its declared base")
            # Role path validation is coordinator-owned; metadata writes also fail closed here.
            changed = self._git(clone, "diff", "--name-only", start, candidate).splitlines()
            if any(path == ".ai-company-ci" or path.startswith((".ai-company-ci/", ".github/")) for path in changed):
                raise ExecutionBlocked("role contribution changes program-owned CI metadata")
            inputs[str(role)] = {"clone": str(clone), "base_sha": start, "candidate_sha": candidate}
        binding = {"base_sha": base_sha, "contributions": inputs, "manifest": manifest}
        record_path = self._record("aggregate", str(key))
        with controller_lock(self.root / "locks" / ("aggregate-" + _digest(str(key)))):
            clone = self.clone("aggregate:" + str(key), base_sha)
            record = self._read(record_path)
            if record and record["binding"] != binding:
                raise ExecutionBlocked("aggregate key is bound to another candidate set")
            if record is None:
                record = {"binding": binding, "head_sha": base_sha, "completed": [], "pending": None}
                self._save(record_path, record)
            self._complete_pending(record_path, record, clone)
            self._clean(clone)
            if self._git(clone, "rev-parse", "HEAD") != record["head_sha"]:
                raise ExecutionBlocked("aggregate clone advanced outside its journal")
            for role, value in inputs.items():
                step = "role:" + role
                if step in record["completed"]:
                    continue
                candidate = value["candidate_sha"]
                self._git(clone, "fetch", "--no-tags", "--", value["clone"], candidate)
                if not self._git(clone, "merge-base", "--is-ancestor", candidate, record["head_sha"], check=False).returncode:
                    self._advance(record_path, record, clone, step, record["head_sha"])
                    continue
                # merge-tree computes the result without dirtying the index/worktree on conflict.
                tree = self._git(clone, "merge-tree", "--write-tree", record["head_sha"], candidate).splitlines()[0]
                new_head = self._commit_tree(clone, tree, [record["head_sha"], candidate],
                    "AI Company aggregate " + _digest(binding) + " / " + role, base_sha)
                self._advance(record_path, record, clone, step, new_head)
            if not context_only and "ci-request" not in record["completed"]:
                with tempfile.TemporaryDirectory(prefix="aggregate-index-", dir=self.root) as temporary:
                    environment = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
                    self._git(clone, "read-tree", record["head_sha"], env=environment)
                    blob = self._git(clone, "hash-object", "-w", "--stdin", input=request)
                    self._git(clone, "update-index", "--add", "--cacheinfo", "100644", blob,
                              ".ai-company-ci/request.json", env=environment)
                    tree = self._git(clone, "write-tree", env=environment)
                new_head = self._commit_tree(clone, tree, [record["head_sha"]],
                    "Bind automation candidate to task and policy " + _digest(binding), base_sha)
                self._advance(record_path, record, clone, "ci-request", new_head)
            return clone, record["head_sha"]

    def _api(self, method, path, payload=None):
        argv = ["gh", "api", "--method", method, path]
        if payload is not None:
            argv += ["--input", "-"]
        try:
            result = subprocess.run(argv, input=json.dumps(payload) if payload is not None else None,
                                    text=True, capture_output=True, timeout=60)
            if result.returncode:
                raise ExecutionBlocked("GitHub publication operation failed; retry must reconcile remote state")
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            raise ExecutionBlocked("GitHub publication outcome is unknown; reconcile before retry") from error

    def _remote_head(self, clone, branch):
        output = self._git(clone, "ls-remote", "--heads", "origin", "refs/heads/" + branch)
        lines = output.splitlines()
        if len(lines) > 1:
            raise ExecutionBlocked("remote branch lookup is ambiguous")
        return lines[0].split()[0] if lines else None

    def publish(self, clone, branch, base_branch, title, body):
        clone = self._owned(clone)
        if (not branch.startswith("automation/") or base_branch != self.config.base_branch or branch == base_branch
                or self._git(clone, "check-ref-format", "refs/heads/" + branch, check=False).returncode):
            raise ExecutionBlocked("publication requires a new automation branch and configured base")
        self._clean(clone)
        head = _commit(self._git(clone, "rev-parse", "HEAD"))
        try:
            manifest = json.loads(self._git(clone, "show", "HEAD:.ai-company-ci/request.json"))
        except ValueError as error:
            raise ExecutionBlocked("publication requires a program-owned CI request") from error
        if manifest.get("base_sha") != self.config.base_sha or any(not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(k, "")))
                for k in ("task_digest", "policy_digest")):
            raise ExecutionBlocked("publication CI request has invalid task/policy/base binding")
        workflow = self._git(clone, "show", "HEAD:" + self.config.ci.workflow_path, check=False)
        if workflow.returncode or sha256(workflow.stdout.encode()).hexdigest() != self.config.ci.workflow_digest:
            raise ExecutionBlocked("candidate workflow differs from the server-approved definition")
        binding = {"clone": str(clone), "head_sha": head, "branch": branch, "base_branch": base_branch,
                   "base_sha": self.config.base_sha, "title": title, "body": body}
        path = self._record("publish", branch)
        with controller_lock(self.root / "locks" / ("publish-" + _digest(branch))):
            record = self._read(path)
            if record and record["binding"] != binding:
                raise ExecutionBlocked("publication branch belongs to another immutable candidate")
            remote = self._remote_head(clone, branch)
            if (remote is not None and (record is None or remote != head)):
                raise ExecutionBlocked("remote branch is unowned or has changed; refusing to overwrite it")
            if self._remote_head(clone, base_branch) != self.config.base_sha:
                raise ExecutionBlocked("remote PR base changed from the configured commit")
            if record is None:
                record = {"binding": binding, "result": None}
                self._save(path, record)
            if remote is None:
                self._git(clone, "push", "--porcelain", "origin", head + ":refs/heads/" + branch)
            if self._remote_head(clone, branch) != head:
                raise ExecutionBlocked("published branch does not match the candidate")
            owner = self.config.repository.split("/")[0]
            prs = self._api("GET", "repos/" + self.config.repository + "/pulls?state=all&head=" +
                            quote(owner + ":" + branch, safe="") + "&per_page=100")
            if len(prs) > 1:
                raise ExecutionBlocked("publication branch has ambiguous PR history")
            if not prs:
                pr = self._api("POST", "repos/" + self.config.repository + "/pulls",
                               {"head": branch, "base": base_branch, "title": title, "body": body, "draft": True})
            else:
                pr = prs[0]
            pr = self._api("GET", "repos/" + self.config.repository + "/pulls/" + str(pr["number"]))
            if (pr.get("state") != "open" or pr.get("draft") is not True
                    or pr.get("head", {}).get("sha") != head or pr.get("head", {}).get("ref") != branch
                    or pr.get("head", {}).get("repo", {}).get("full_name") != self.config.repository
                    or pr.get("base", {}).get("ref") != base_branch or pr.get("base", {}).get("sha") != self.config.base_sha):
                raise ExecutionBlocked("remote draft PR identity differs from the approved publication")
            result = {"number": pr["number"], "url": pr["html_url"], "head_sha": head}
            record["result"] = result
            self._save(path, record)
            return result
