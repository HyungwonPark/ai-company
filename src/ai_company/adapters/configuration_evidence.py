"""CLI-produced configuration evidence, distinct from provider execution telemetry.

Codex 0.154.0 exposes thread settings via app-server and per-turn settings in
its rollout. The latter format is version-specific: fail closed on other versions.
Never infer settings from model-generated text or read authentication files.
"""
from datetime import datetime
import json
from pathlib import Path
import re


def codex_turn_configuration(session_id: str, worktree: Path, started_at: float,
                             ended_at: float, codex_home: Path) -> dict:
    unavailable = {"source": "codex_rollout", "scope": "cli_turn_configuration", "status": "unavailable"}
    if not re.fullmatch(r"[0-9a-f-]{36}", session_id):
        return unavailable
    root = (codex_home / "sessions").resolve()
    paths = list(root.glob("*/*/*/rollout-*-" + session_id + ".jsonl"))
    if len(paths) != 1 or paths[0].is_symlink() or not paths[0].resolve().is_relative_to(root):
        return unavailable
    meta = None
    contexts = []
    try:
        if paths[0].stat().st_size > 64_000_000:
            return unavailable
        with paths[0].open() as stream:
            for line in stream:
                # Large tool outputs are neither configuration nor copied evidence.
                if len(line) > 2_000_000:
                    continue
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                    continue
                payload = event["payload"]
                if event.get("type") == "session_meta":
                    meta = payload
                if event.get("type") != "turn_context":
                    continue
                at = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp()
                if started_at <= at <= ended_at and Path(payload.get("cwd", "")) == worktree.resolve():
                    contexts.append({"turn_id": payload.get("turn_id"), "model": payload.get("model"),
                                     "reasoning_effort": payload.get("effort"), "recorded_at": at})
        if (not meta or meta.get("id") != session_id or meta.get("cli_version") != "0.154.0"
                or Path(meta.get("cwd", "")) != worktree.resolve() or not contexts
                or any(not all(c.get(k) for k in ("turn_id", "model", "reasoning_effort")) for c in contexts)):
            return unavailable
        return {**unavailable, "status": "observed", "session_id": session_id, "cli_version": meta["cli_version"],
                "contexts": contexts, "backend_model_verified": False}
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
