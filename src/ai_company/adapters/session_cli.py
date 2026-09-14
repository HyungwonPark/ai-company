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
from uuid import uuid4
import shutil
import copy


@dataclass
class SessionOutcome:
    category: str
    session_id: str | None = None
    reset_at: float | None = None
    message: str = ""
    result: dict | None = None


def codex_output_schema(schema: dict) -> dict:
    """Codex's strict response format requires every declared property.

    Pydantic defaults affect local validation, but must not make fields optional
    on the provider wire. Preserve the caller's schema and nullable alternatives.
    """
    result = copy.deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            node.pop("default", None)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(result)
    return result


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


def service_alive(unit: str) -> bool:
    """Fail closed for an unqueryable unit; only inspect our isolated run units."""
    if not re.fullmatch(r"ai-company-run-[0-9a-f]{32}\.service", unit):
        return True
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit, "--property=LoadState,ActiveState,ControlGroup"],
                                capture_output=True, text=True, timeout=10)
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if fields.get("LoadState") == "not-found":
            return False
        if result.returncode or fields.get("ActiveState") not in {"inactive", "failed"}:
            return True
        group = fields.get("ControlGroup", "")
        if group:
            events = Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.events"
            if events.exists() and "populated 1" in events.read_text():
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return True


def stop_service(unit: str) -> bool:
    if not re.fullmatch(r"ai-company-run-[0-9a-f]{32}\.service", unit):
        return False
    try:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, timeout=15)
        return not service_alive(unit)
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_session(provider: str, worktree: Path, prompt: str, session_id: str | None, *,
                timeout_seconds: float = 300, output_dir: Path,
                on_spawn: Callable[[dict], None] | None = None,
                executable: str | None = None, model: str | None = None,
                reasoning_effort: str | None = None, ultracode_enabled: bool = False, output_schema: dict | None = None,
                permission: str | None = None, capture_configuration: bool = False, isolate_cgroup: bool = False, max_cost_usd: float | None = None) -> SessionOutcome:
    """Run once and return; never sleep for quota reset or retry a session here."""
    if provider not in {"codex", "claude"}:
        raise ValueError("provider must be codex or claude")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")
    if session_id is not None and not _valid_session_id(session_id):
        raise ValueError("Invalid explicit session ID")
    if ultracode_enabled and (provider != "claude" or reasoning_effort != "xhigh"):
        raise ValueError("Ultracode requires Claude with xhigh reasoning")
    started_at = time.time()
    started = time.monotonic()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    schema_path = None
    if output_schema is not None:
        fd, schema_path = tempfile.mkstemp(prefix="schema-", suffix=".json", dir=output_dir)
        with os.fdopen(fd, "w") as schema_file:
            json.dump(codex_output_schema(output_schema) if provider == "codex" else output_schema, schema_file)
    configuration_request = "configuration-" + uuid4().hex
    argv = [executable or provider]
    if provider == "codex":
        argv += ["exec"]
        if session_id is not None:
            argv += ["resume", session_id]
        argv += ["--json"]
        if model is not None:
            argv += ["--model", model]
        if reasoning_effort is not None:
            argv += ["-c", "model_reasoning_effort=" + json.dumps(reasoning_effort)]
        if permission is not None:
            if permission not in {"read-only", "workspace-write"}:
                raise ValueError("unsupported session permission")
            argv += ["-c", "sandbox_mode=" + json.dumps(permission)]
        if schema_path:
            argv += ["--output-schema", schema_path]
        argv += ["-"]
    else:
        argv += ["-p", "--output-format", "stream-json", "--verbose"]
        if capture_configuration:
            argv += ["--input-format", "stream-json"]
        if session_id is not None:
            argv += ["--resume", session_id]
        if model is not None:
            argv += ["--model", model]
        if reasoning_effort is not None:
            argv += ["--effort", "ultracode" if ultracode_enabled else reasoning_effort]
        if output_schema is not None:
            argv += ["--json-schema", json.dumps(output_schema)]
        if permission is not None:
            if permission not in {"read-only", "workspace-write"}:
                raise ValueError("unsupported session permission")
            argv += ["--permission-mode", "dontAsk", "--tools",
                     "Read,Grep,Glob,Workflow,Task,TaskOutput,TaskStop,Skill" if permission == "read-only"
                     else "Read,Grep,Glob,Edit,Write,Bash,Workflow,Task,TaskOutput,TaskStop,Skill"]
        if max_cost_usd is not None:
            argv += ["--max-budget-usd", str(max_cost_usd)]
    resolved_executable = shutil.which(argv[0]) or argv[0]
    unit = None
    if isolate_cgroup:
        unit = "ai-company-run-" + uuid4().hex + ".service"
        argv[0] = shutil.which(argv[0]) or argv[0]
        env_args = ["--setenv=PATH=" + os.environ.get("PATH", "/usr/bin:/bin")]
        for key in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_DISABLE_WORKFLOWS"):
            if key in os.environ:
                env_args.append("--setenv=" + key + "=" + os.environ[key])
        argv = ["systemd-run", "--user", "--quiet", "--wait", "--collect", "--pipe",
                "--service-type=exec", "--unit=" + unit, "--property=KillMode=control-group",
                "--property=TimeoutStopSec=5", "--property=UMask=0077",
                "--property=WorkingDirectory=" + str(Path(worktree).resolve()), *env_args, "--", *argv]
    out_fd, out_name = tempfile.mkstemp(prefix="stdout-", suffix=".jsonl", dir=output_dir)
    err_fd, err_name = tempfile.mkstemp(prefix="stderr-", suffix=".log", dir=output_dir)
    metadata = {"stdout_path": out_name, "stderr_path": err_name, "executable_path": resolved_executable}
    process = None
    failure = None
    with os.fdopen(out_fd, "wb") as stdout, os.fdopen(err_fd, "wb") as stderr, tempfile.TemporaryFile() as stdin:
        if provider == "claude" and capture_configuration:
            records = [{"type": "control_request", "request_id": configuration_request, "request": {"subtype": "initialize"}},
                       {"type": "user", "message": {"role": "user", "content": prompt},
                        "parent_tool_use_id": None, "session_id": session_id or ""}]
            stdin.write(("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8"))
        else:
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
            if unit:
                spawn_record["systemd_unit"] = unit
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
            if unit and not stop_service(unit):
                failure = SessionOutcome("reconciliation", session_id=session_id, message="CLI cgroup termination unconfirmed")
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
    if unit:
        metadata["systemd_unit"] = unit
        metadata["cgroup_stopped"] = not service_alive(unit)
    metadata["exit_code"] = None if process is None else process.returncode
    metadata["duration_seconds"] = time.monotonic() - started
    observed = _read_outcome(provider, Path(out_name), session_id,
                             process.returncode if process is not None else -1)
    outcome = failure or observed
    if failure is not None and observed.category != "reconciliation":
        outcome.session_id = observed.session_id
    outcome.result = {**(outcome.result or {}), **metadata}
    if model is not None:
        outcome.result["requested_model"] = model
        outcome.result["requested_effort"] = reasoning_effort
        outcome.result["requested_ultracode"] = ultracode_enabled
        observed_models, observed_efforts, auxiliary_models, observed_ultracode, payload = set(), set(), set(), set(), None
        with Path(out_name).open(errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if capture_configuration and provider == "claude":
                    from ai_company.adapters.configuration_evidence import claude_control_configuration
                    evidence = claude_control_configuration(event, configuration_request, model)
                    if evidence:
                        outcome.result["configuration_evidence"] = evidence
                if event.get("type") in {"system", "response.completed", "turn.completed"}:
                    if isinstance(event.get("ultracode"), bool):
                        observed_ultracode.add(event["ultracode"])
                    if isinstance(event.get("model"), str):
                        observed_models.add(event["model"])
                    if isinstance(event.get("reasoning_effort", event.get("effort")), str):
                        observed_efforts.add(event.get("reasoning_effort", event.get("effort")))
                if provider == "claude" and isinstance(event.get("message"), dict):
                    actual = event["message"].get("model")
                    if event.get("type") == "assistant" and isinstance(actual, str):
                        observed_models.add(actual)
                if provider == "claude" and event.get("type") == "result":
                    payload = event.get("structured_output", payload)
                    for actual in event.get("modelUsage", {}):
                        auxiliary_models.add(actual)
                item = event.get("item", {})
                if provider == "codex" and event.get("type") == "item.completed" and item.get("type") == "agent_message":
                    try:
                        payload = json.loads(item.get("text", ""))
                    except ValueError:
                        pass
        outcome.result.update(observed_models=sorted(observed_models), observed_efforts=sorted(observed_efforts),
                              auxiliary_models=sorted(auxiliary_models - observed_models),
                              observed_ultracode=sorted(observed_ultracode), structured_output=payload)
    if capture_configuration and provider == "codex" and outcome.session_id:
        from ai_company.adapters.configuration_evidence import codex_turn_configuration
        outcome.result["configuration_evidence"] = codex_turn_configuration(
            outcome.session_id, Path(worktree), started_at, time.time(),
            Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    return outcome
