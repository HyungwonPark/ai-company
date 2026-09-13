"""Bounded CLI invocations; scheduling and repository ownership belong to the queue.

Only provider error envelopes are classified. Model text and stderr are never
evidence of a retryable error. Codex exec currently exposes message-only errors;
the narrow message allowlist must be reviewed when upgrading the CLI.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from typing import Callable


@dataclass
class SessionOutcome:
    category: str
    session_id: str | None = None
    reset_at: float | None = None
    message: str = ""
    result: dict | None = None


# Structured provider codes, not arbitrary strings found in command output.
_ERROR_CODES = {
    "usage_limit_reached": "quota", "insufficient_quota": "authentication",
    "rate_limit_exceeded": "rate_limit", "rate_limit_error": "rate_limit",
    "rate_limit": "rate_limit", "too_many_requests": "rate_limit",
    "authentication_failed": "authentication", "authentication_error": "authentication",
    "invalid_api_key": "authentication", "unauthorized": "authentication",
    "permission_denied": "permission", "permission_error": "permission",
    "forbidden": "permission", "approval_required": "approval_required",
    "context_length_exceeded": "context_exhausted",
    "context_window_exceeded": "context_exhausted",
    "request_too_large": "context_exhausted",
    "test_failure": "test_failure", "test_failed": "test_failure",
    "code_error": "code_error", "invalid_request": "code_error",
    "invalid_request_error": "code_error",
    "connection_error": "transient_network", "connection_reset": "transient_network",
    "connection_timeout": "transient_network", "network_error": "transient_network",
    "billing_error": "authentication",  # Requires account intervention, never retry.
}


def _valid_session_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value) is not None


def _reset_at(value: object) -> float | None:
    """Accept explicit UTC epoch seconds or timezone-qualified ISO timestamps."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            parsed = float(value)
        elif isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return None
                parsed = dt.timestamp()
        else:
            return None
    except (ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _error_outcome(error: object, provider: str) -> SessionOutcome:
    if isinstance(error, str):
        error = {"message": error}
    if not isinstance(error, dict):
        return SessionOutcome("unknown", message="Unrecognized provider error envelope")
    message = error.get("message", "")
    message = message if isinstance(message, str) else ""
    reset = _reset_at(error.get("reset_at", error.get("resets_at", error.get("resetsAt"))))
    for key in ("code", "type"):
        code = error.get(key)
        if isinstance(code, str) and code in _ERROR_CODES:
            return SessionOutcome(_ERROR_CODES[code], reset_at=reset, message=message)
    category = "unknown"
    if provider == "codex":
        # These messages occur in the CLI's error envelope, not agent_message.
        if re.match(r"^You've hit your usage limit(?:\. | for [^\n]+\. Switch to another model now,)", message):
            category = "quota"
        elif message.startswith("rate limit exceeded: "):
            category = "rate_limit"
        elif message.startswith("Codex ran out of room in the model's context window."):
            category = "context_exhausted"
        elif message.startswith("Your access token could not be refreshed"):
            category = "authentication"
        elif re.match(r"^unexpected status 401 Unauthorized\b", message):
            category = "authentication"
        elif re.match(r"^unexpected status 403 Forbidden\b", message):
            category = "permission"
        elif re.match(r"^unexpected status 429 Too Many Requests\b", message):
            category = "rate_limit"
        elif message == "request timed out" or re.match(
            r"^stream disconnected before completion: connection (?:closed|reset(?: by peer)?)(?:$|[.;])", message
        ):
            category = "transient_network"
        elif message.startswith(("sandbox error: ", "codex-linux-sandbox was required but not provided")):
            category = "permission"
        elif message.startswith(("Quota exceeded. Check your plan and billing details.",
                                 "Your workspace is out of credits.",
                                 "You hit your spend cap ",
                                 "To use Codex with your ChatGPT plan, upgrade to Plus:")):
            category = "authentication"
    return SessionOutcome(category, reset_at=reset, message=message or "Unrecognized provider error")


def _read_outcome(provider: str, stdout_path: Path, session_id: str | None,
                  exit_code: int) -> SessionOutcome:
    pending: SessionOutcome | None = None
    terminal: SessionOutcome | None = None
    observed_session = session_id
    mismatched = False
    reset: float | None = None
    invalid = False
    with stdout_path.open(encoding="utf-8", errors="replace") as stream:
        while line := stream.readline(2_000_001):
            # Drain oversized lines in bounded chunks instead of loading them.
            if len(line) > 2_000_000:
                invalid = True
                while not line.endswith("\n"):
                    line = stream.readline(2_000_001)
                    if not line:
                        break
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                invalid = True
                continue
            if not isinstance(event, dict):
                invalid = True
                continue
            kind = event.get("type")
            found_session = None
            if provider == "codex" and kind == "thread.started":
                found_session = event.get("thread_id")
            elif provider == "claude" and kind in {"system", "assistant", "result", "rate_limit_event"}:
                found_session = event.get("session_id")
            if found_session is not None and not _valid_session_id(found_session):
                mismatched = True
            elif found_session is not None:
                if observed_session is not None and observed_session != found_session:
                    mismatched = True
                observed_session = found_session
            if provider == "codex":
                if kind == "turn.failed":
                    terminal = _error_outcome(event.get("error"), provider)
                elif kind == "error":
                    pending = _error_outcome(event.get("error", event), provider)
                elif kind == "turn.completed":
                    terminal = SessionOutcome("success", result=event)
            else:
                if kind == "rate_limit_event":
                    info = event.get("rate_limit_info", {})
                    if isinstance(info, dict) and info.get("status") == "rejected":
                        reset = _reset_at(info.get("resetsAt"))
                        pending = SessionOutcome("quota", reset_at=reset,
                                                 message="Claude quota rejected")
                elif kind == "assistant" and isinstance(event.get("error"), str):
                    pending = _error_outcome({"code": event["error"]}, provider)
                elif kind == "error":
                    pending = _error_outcome(event.get("error", event), provider)
                elif kind == "result":
                    if event.get("permission_denials"):
                        terminal = SessionOutcome("permission", message="Claude reported permission denials")
                    elif event.get("stop_reason") == "model_context_window_exceeded":
                        terminal = SessionOutcome("context_exhausted", message="Claude context window exhausted")
                    elif event.get("terminal_reason") in {"aborted_streaming", "aborted_tools"}:
                        terminal = SessionOutcome("reconciliation", message="Claude turn was interrupted")
                    elif event.get("terminal_reason") == "max_turns":
                        terminal = SessionOutcome("code_error", message="Claude reached its turn limit")
                    elif event.get("is_error") is False and event.get("subtype") == "success":
                        terminal = SessionOutcome("success", result=event)
                    elif event.get("subtype") in {"error_max_turns", "error_max_budget_usd",
                                                   "error_max_structured_output_retries"}:
                        terminal = SessionOutcome("code_error", message=f"Claude stopped: {event['subtype']}")
                    else:
                        terminal = _error_outcome(event.get("error", {}), provider)
                        status = event.get("api_error_status")
                        if status == 401:
                            terminal = SessionOutcome("authentication", message="Claude API HTTP 401")
                        elif status == 403:
                            terminal = SessionOutcome("permission", message="Claude API HTTP 403")
                        elif status == 429:
                            terminal = SessionOutcome("rate_limit", reset_at=reset,
                                                       message="Claude API HTTP 429")
                        elif status is not None:
                            terminal = SessionOutcome("unknown", message=f"Unclassified Claude API status: {status}")
                        elif terminal.category == "unknown" and pending is not None and not event.get("errors"):
                            terminal = pending
    outcome = terminal or pending or SessionOutcome("unknown", message="No recognized terminal CLI event")
    if mismatched:
        return SessionOutcome("reconciliation", session_id=session_id,
                              message="CLI reported a different session ID")
    outcome.session_id = observed_session
    if outcome.reset_at is None and outcome.category in {"quota", "rate_limit"}:
        outcome.reset_at = reset
    if outcome.category == "success" and (exit_code != 0 or invalid):
        outcome = SessionOutcome("unknown", session_id=observed_session,
                                 message="CLI success conflicted with exit status or malformed stream")
    if invalid and outcome.category in {"quota", "rate_limit", "transient_network"}:
        outcome = SessionOutcome("unknown", session_id=observed_session,
                                 message="Malformed CLI stream cannot authorize automatic retry")
    if provider == "codex" and terminal is None and pending is not None and exit_code == 0:
        outcome = SessionOutcome("unknown", session_id=observed_session,
                                 message="Codex error event without a failed turn or unsuccessful exit")
    return outcome


def _proc_info(pid: int) -> dict | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
                "pgid": int(fields[2]), "proc_start_ticks": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def _processes() -> list[dict]:
    return [info for entry in Path("/proc").iterdir() if entry.name.isdigit()
            if (info := _proc_info(int(entry.name))) is not None and info["state"] != "Z"]


def _terminate_group(process: subprocess.Popen, pgid: int) -> bool:
    """Bounded cleanup of our group plus descendants observed before termination.

    This is process supervision, not OS containment. A service cgroup is required
    to contain a deliberately detached descendant that already became orphaned.
    """
    snapshot = _processes()
    descendants = {process.pid}
    changed = True
    while changed:
        before = len(descendants)
        descendants.update(p["pid"] for p in snapshot if p["ppid"] in descendants)
        changed = before != len(descendants)
    targets = {p["pid"]: p["proc_start_ticks"] for p in snapshot
               if p["pid"] in descendants or p["pgid"] == pgid}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        for pid, ticks in targets.items():
            current = _proc_info(pid)
            if current and current["proc_start_ticks"] == ticks and current["state"] != "Z":
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            process.poll()
            alive = [p for p in _processes() if p["pgid"] == pgid
                     or targets.get(p["pid"]) == p["proc_start_ticks"]]
            if not alive:
                process.wait(timeout=1)
                return True
            time.sleep(0.02)
    return False


def run_session(provider: str, worktree: Path, prompt: str, session_id: str | None, *,
                timeout_seconds: float = 300, output_dir: Path,
                on_spawn: Callable[[dict], None] | None = None,
                executable: str | None = None) -> SessionOutcome:
    """Run once and return; never sleep for quota reset or retry a session here."""
    if provider not in {"codex", "claude"}:
        raise ValueError("provider must be codex or claude")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if session_id is not None and not _valid_session_id(session_id):
        raise ValueError("Invalid explicit session ID")
    argv = [executable or provider]
    if provider == "codex":
        argv += ["exec"]
        if session_id is not None:
            argv += ["resume", session_id]
        argv += ["--json", "-"]
    else:
        argv += ["-p", "--output-format", "stream-json", "--verbose"]
        if session_id is not None:
            argv += ["--resume", session_id]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    out_fd, out_name = tempfile.mkstemp(prefix="stdout-", suffix=".jsonl", dir=output_dir)
    err_fd, err_name = tempfile.mkstemp(prefix="stderr-", suffix=".log", dir=output_dir)
    metadata = {"stdout_path": out_name, "stderr_path": err_name}
    process = None
    failure = None
    with os.fdopen(out_fd, "wb") as stdout, os.fdopen(err_fd, "wb") as stderr, tempfile.TemporaryFile() as stdin:
        stdin.write(prompt.encode("utf-8"))
        stdin.seek(0)
        try:
            process = subprocess.Popen(argv, cwd=worktree, shell=False, stdin=stdin,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            identity = _proc_info(process.pid)
            if identity is None:
                raise RuntimeError("Cannot identify spawned CLI process")
            spawn_record = {key: identity[key] for key in ("pid", "pgid", "proc_start_ticks")}
            spawn_record["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if on_spawn is not None:
                on_spawn(spawn_record)
            process.wait(timeout=timeout_seconds)
            if any(p["pgid"] == process.pid for p in _processes()):
                stopped = _terminate_group(process, process.pid)
                failure = SessionOutcome("reconciliation", session_id=session_id,
                                         message=f"CLI exited with live descendants; stopped={stopped}")
        except subprocess.TimeoutExpired:
            stopped = _terminate_group(process, process.pid)
            failure = SessionOutcome("reconciliation", session_id=session_id,
                                     message=f"CLI timed out; process group stopped={stopped}")
        except (OSError, RuntimeError) as exc:
            if process is not None:
                stopped = _terminate_group(process, process.pid)
                failure = SessionOutcome("reconciliation", session_id=session_id,
                                         message=f"CLI supervision failed ({type(exc).__name__}); stopped={stopped}")
            else:
                category = "permission" if isinstance(exc, PermissionError) else "code_error"
                failure = SessionOutcome(category, session_id=session_id,
                                         message=f"CLI could not start ({type(exc).__name__})")
        except BaseException:
            if process is not None:
                _terminate_group(process, process.pid)
            raise
        finally:
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    metadata["exit_code"] = None if process is None else process.returncode
    observed = _read_outcome(provider, Path(out_name), session_id,
                             process.returncode if process is not None else -1)
    outcome = failure or observed
    if failure is not None and observed.category != "reconciliation":
        outcome.session_id = observed.session_id
    outcome.result = {**(outcome.result or {}), **metadata}
    return outcome
