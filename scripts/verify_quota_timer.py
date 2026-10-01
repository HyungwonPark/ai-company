"""Exercise the real user systemd timer with a generated Codex CLI fixture.

Run with the project's Python. No provider CLI, model or credentials are used.
The isolated smoke units are removed; the installed production timer is untouched.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from ai_company.contracts import Task
from ai_company.sessions import RetryPolicy, SessionQueue, SessionSpec


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, mode=0o700)  # Refuse to reuse an old fixture's evidence.
    worktree = root / "repo"
    worktree.mkdir()
    command(["git", "init", "-q", str(worktree)])
    for key, value in (("user.name", "Quota timer fixture"), ("user.email", "quota@example.invalid")):
        command(["git", "-C", str(worktree), "config", key, value])
    command(["git", "-C", str(worktree), "remote", "add", "origin", "https://github.com/fixture/quota.git"])
    (worktree / "fixture.txt").write_text("isolated timer fixture\n")
    command(["git", "-C", str(worktree), "add", "fixture.txt"])
    command(["git", "-C", str(worktree), "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"])
    head = command(["git", "-C", str(worktree), "rev-parse", "HEAD"]).stdout.strip()
    fake_bin = root / "bin"
    fake_bin.mkdir()
    fake_cli = fake_bin / "codex"
    fake_cli.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, pathlib, sys, time\n"
        f"root = pathlib.Path({str(root)!r})\n"
        "calls = root / 'calls.jsonl'\n"
        "old = calls.read_text().splitlines() if calls.exists() else []\n"
        "sys.stdin.read()\n"
        "with calls.open('a') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv[1:], 'pid': os.getpid(), 'worker_pid': os.getppid(), 'at': time.time()}) + '\\n')\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': 'quota-timer-session'}), flush=True)\n"
        "if not old:\n"
        "    assert sys.argv[1:] == ['exec', '--json', '-']\n"
        "    print(json.dumps({'type': 'turn.failed', 'error': {'message': \"You've hit your usage limit. Try again later.\"}}), flush=True)\n"
        "    sys.exit(1)\n"
        "assert len(old) == 1, 'duplicate session resume'\n"
        "assert sys.argv[1:] == ['exec', 'resume', 'quota-timer-session', '--json', '-']\n"
        "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}}), flush=True)\n"
    )
    fake_cli.chmod(0o700)
    state_dir = root / "state"
    task = Task(task_id="quota-timer-smoke", goal="Verify quota scheduling without calling a model",
                acceptance=("The timer resumes the saved session exactly once",), repository="fixture/quota",
                base_sha=head, allowed_paths=("fixture.txt",), required_checks=("fixture",))
    with SessionQueue(state_dir) as queue:
        submitted = queue.submit(SessionSpec(task=task, agent_id="codex-fixture", provider="codex",
                                             worktree=str(worktree), last_completed_stage="checkpointed",
                                             retry_policy=RetryPolicy(backoff_initial_seconds=10,
                                                                      backoff_max_seconds=20)))
    worker = [sys.executable, "-m", "ai_company.cli", "session", "worker", "--state-dir", str(state_dir)]
    fixture_env = os.environ.copy()
    fixture_env["PATH"] = str(fake_bin) + ":/usr/bin:/bin"
    first = json.loads(command(worker, env=fixture_env).stdout)
    assert first["status"] == "WAITING_QUOTA", first
    assert first["session_id"] == "quota-timer-session", first
    (root / "waiting.json").write_text(json.dumps(first, indent=2) + "\n")
    # The first worker has exited. A different process, launched by systemd, must
    # load the same SQLite row at/after resume_at and resume the saved session.
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "systemd/user"
    runtime.mkdir(parents=True, exist_ok=True)
    unit = "ai-company-quota-smoke-" + uuid4().hex[:12]
    service = runtime / (unit + ".service")
    timer = runtime / (unit + ".timer")
    def quote(value):
        return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'
    service.write_text("[Unit]\nDescription=ai-company isolated quota fixture\n[Service]\nType=oneshot\n"
                       + "ExecStart=" + " ".join(quote(part) for part in worker) + "\n"
                       + "Environment=" + quote("PATH=" + str(fake_bin) + ":/usr/bin:/bin") + "\n"
                       + "KillMode=control-group\nTimeoutStartSec=30\nUMask=0077\n")
    timer.write_text("[Unit]\nDescription=ai-company isolated quota timer fixture\n[Timer]\n"
                     + "OnActiveSec=1s\nOnUnitActiveSec=2s\nAccuracySec=100ms\nUnit=" + unit + ".service\n")
    try:
        command(["systemctl", "--user", "daemon-reload"])
        command(["systemctl", "--user", "start", unit + ".timer"])
        assert command(["systemctl", "--user", "is-active", unit + ".timer"]).stdout.strip() == "active"
        deadline = time.monotonic() + 45
        completed = None
        while time.monotonic() < deadline:
            with SessionQueue(state_dir) as queue:
                current = queue.get(submitted["job_id"])
            if current["status"] == "SESSION_COMPLETED":
                completed = current
                break
            if current["status"] not in ("WAITING_QUOTA", "RUNNING"):
                raise AssertionError(current)
            time.sleep(0.5)
        assert completed is not None, "timer did not resume the due job"
        # Additional ticks must observe completion without invoking the CLI again.
        time.sleep(6)
        calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
        assert len(calls) == 2, calls
        assert calls[0]["worker_pid"] != calls[1]["worker_pid"], calls
        assert calls[1]["at"] >= first["resume_at"], calls
        assert completed["retry_count"] == 1 and completed["attempt_count"] == 2, completed
        evidence = {"mode": "fixture CLI with real user systemd timer", "status": "passed",
                    "job_id": submitted["job_id"], "session_id": completed["session_id"],
                    "waiting_status": first["status"], "resume_at": first["resume_at"],
                    "final_status": completed["status"], "retry_count": completed["retry_count"],
                    "calls": calls, "timer_unit": unit + ".timer",
                    "timer_active_during_test": True, "fixture_units_removed_after_test": True}
        (root / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2), flush=True)
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit + ".timer", unit + ".service"],
                       capture_output=True, text=True)
        service.unlink(missing_ok=True)
        timer.unlink(missing_ok=True)
        command(["systemctl", "--user", "daemon-reload"])


if __name__ == "__main__":
    main()
