"""CLI-produced configuration evidence, distinct from provider execution telemetry.

 Codex 0.154.0 and the inspected 0.157.0 rollout expose per-turn settings.
The format is version-specific: fail closed on other versions.
Never infer settings from model-generated text or read authentication files.
"""
from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess


# Distinct profiles let a newly supported format remain an explicit decision.
# Stored "unavailable" evidence is never promoted by a later code release.
CODEX_ROLLOUT_PROFILES = {
    "0.154.0": "codex-0.154-turn-context-v1",
    "0.157.0": "codex-0.157-turn-context-v1",
}
_SESSION_ID = re.compile(r"[0-9a-f-]{36}\Z")
_TURN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")


def codex_configuration_reason(evidence: dict, *, session_id: str, started_at: float,
                               ended_at: float, model: str | None = None,
                               reasoning_effort: str | None = None) -> str | None:
    """One evidence contract for storage acceptance and the read-only display."""
    if not isinstance(evidence, dict) or evidence.get("status") != "observed":
        if isinstance(evidence, dict) and evidence.get("reason") == "unsupported_cli_version":
            return "unsupported_cli_version"
        return "missing_configuration_evidence"
    if (isinstance(started_at, bool) or isinstance(ended_at, bool)
            or not isinstance(started_at, (int, float)) or not isinstance(ended_at, (int, float))
            or not math.isfinite(started_at) or not math.isfinite(ended_at) or ended_at < started_at):
        return "configuration_mismatch"
    version = evidence.get("cli_version")
    profile = CODEX_ROLLOUT_PROFILES.get(version) if isinstance(version, str) else None
    if profile is None:
        return "unsupported_cli_version"
    # Historic 0.154.0 observations predate explicit profile recording.
    if (version == "0.154.0" and evidence.get("profile") not in (None, profile)
            or version == "0.157.0" and evidence.get("profile") != profile):
        return "missing_configuration_evidence"
    if (evidence.get("source") != "codex_rollout" or evidence.get("scope") != "cli_turn_configuration"
            or evidence.get("backend_model_verified") is not False
            or evidence.get("session_id") != session_id or not isinstance(session_id, str)
            or not _SESSION_ID.fullmatch(session_id)):
        return "configuration_mismatch"
    contexts = evidence.get("contexts")
    if not isinstance(contexts, list) or not 1 <= len(contexts) <= 128:
        return "missing_configuration_evidence"
    for context in contexts:
        if not isinstance(context, dict):
            return "configuration_mismatch"
        at = context.get("recorded_at")
        if (not isinstance(context.get("turn_id"), str) or not _TURN_ID.fullmatch(context["turn_id"])
                or not isinstance(context.get("model"), str) or not context["model"]
                or not isinstance(context.get("reasoning_effort"), str) or not context["reasoning_effort"]
                or isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at)
                or not started_at <= at <= ended_at
                or model is not None and context["model"] != model
                or reasoning_effort is not None and context["reasoning_effort"] != reasoning_effort):
            return "configuration_mismatch"
    if len({c["model"] for c in contexts}) != 1 or len({c["reasoning_effort"] for c in contexts}) != 1:
        return "configuration_mismatch"
    return None


def _rollout_path(session_id: str, codex_home: Path) -> Path | None:
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        return None
    root = (codex_home / "sessions").resolve()
    paths = list(root.glob("*/*/*/rollout-*-" + session_id + ".jsonl"))
    if len(paths) != 1 or paths[0].is_symlink() or not paths[0].resolve().is_relative_to(root):
        return None
    return paths[0]


def codex_cli_identity(executable: str | None = None, *, session_id: str | None = None,
                       codex_home: Path | None = None, path: str | None = None,
                       run=subprocess.run) -> tuple[dict | None, str | None]:
    """Probe the native executable reached by this worker's PATH; no model call.

    A script or unknown wrapper cannot prove which native executable it starts.
    The returned absolute path is pinned across reservation and invocation.
    """
    selected = executable or shutil.which("codex", path=path or os.environ.get("PATH"))
    if not selected:
        return None, "codex_cli_missing"
    try:
        native = Path(selected).resolve(strict=True)
        if not native.is_file() or not os.access(native, os.X_OK):
            return None, "codex_cli_missing"
        with native.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                return None, "codex_native_executable_unverified"
            stream.seek(0)
            identity_hash = sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                identity_hash.update(block)
        completed = run([str(native), "--version"], capture_output=True, text=True, timeout=10)
        if completed.returncode != 0:
            return None, "codex_cli_version_unverified"
        match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)\s*", completed.stdout)
        if not match or match[1] not in CODEX_ROLLOUT_PROFILES:
            return None, "unsupported_cli_version"
        # A replacement during --version does not bind the earlier hash.
        with native.open("rb") as stream:
            after = sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                after.update(block)
        if after.hexdigest() != identity_hash.hexdigest():
            return None, "codex_cli_changed"
        version = match[1]
        if session_id is not None:
            session_path = _rollout_path(session_id, codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
            if session_path is None or session_path.stat().st_size > 64_000_000:
                return None, "codex_resume_profile_unverified"
            with session_path.open() as stream:
                first = json.loads(stream.readline())
            old = first.get("payload", {}) if first.get("type") == "session_meta" else {}
            # Mixed-version resume has not been independently observed.
            if old.get("id") != session_id or old.get("cli_version") != version:
                return None, "codex_resume_profile_unverified"
        return {"path": str(native), "sha256": identity_hash.hexdigest(),
                "version": version, "profile": CODEX_ROLLOUT_PROFILES[version]}, None
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None, "codex_cli_version_unverified"


def codex_turn_configuration(session_id: str, worktree: Path, started_at: float,
                             ended_at: float, codex_home: Path) -> dict:
    unavailable = {"source": "codex_rollout", "scope": "cli_turn_configuration", "status": "unavailable"}
    path = _rollout_path(session_id, codex_home)
    if path is None:
        return unavailable
    meta = None
    contexts = []
    try:
        if path.stat().st_size > 64_000_000:
            return unavailable
        with path.open() as stream:
            for line in stream:
                # Large tool outputs are neither configuration nor copied evidence.
                if len(line) > 2_000_000:
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                    continue
                payload = event["payload"]
                if event.get("type") == "session_meta":
                    if meta is not None:
                        return unavailable
                    meta = payload
                if event.get("type") != "turn_context":
                    continue
                at = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
                if started_at <= at <= ended_at:
                    if Path(payload.get("cwd", "")) != worktree.resolve():
                        return unavailable
                    contexts.append({"turn_id": payload.get("turn_id"), "model": payload.get("model"),
                                     "reasoning_effort": payload.get("effort"), "recorded_at": at})
        if meta and meta.get("id") == session_id and meta.get("cli_version") not in CODEX_ROLLOUT_PROFILES:
            return {**unavailable, "reason": "unsupported_cli_version"}
        if (not meta or meta.get("id") != session_id
                or Path(meta.get("cwd", "")) != worktree.resolve() or not contexts
                or any(not all(c.get(k) for k in ("turn_id", "model", "reasoning_effort")) for c in contexts)):
            return unavailable
        evidence = {**unavailable, "status": "observed", "session_id": session_id, "cli_version": meta["cli_version"],
                    "profile": CODEX_ROLLOUT_PROFILES[meta["cli_version"]],
                    "contexts": contexts, "backend_model_verified": False}
        return evidence if codex_configuration_reason(evidence, session_id=session_id,
            started_at=started_at, ended_at=ended_at) is None else unavailable
    except (OSError, ValueError, TypeError, KeyError):
        return unavailable


def claude_control_configuration(event: dict, request_id: str, model: str) -> dict | None:
    if event.get("type") != "control_response":
        return None
    response = event.get("response", {})
    if response.get("request_id") != request_id or response.get("subtype") != "success":
        return None
    payload = response.get("response", {})
    matches = [m for m in payload.get("models", []) if m.get("resolvedModel") == model or m.get("value") == model]
    return {"source": "claude_control_initialize", "scope": "supported_configuration",
            "model": model, "supported_efforts": sorted({e for m in matches for e in m.get("supportedEffortLevels", [])}),
            "applied_effort": None, "applied_ultracode": None}
