"""Offline development-loop simulation and durable CLI session scheduling."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from ai_company.adapters.fake import FakeAdapter
from ai_company.contracts import Task
from ai_company.harness import Registry
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import RetryPolicy, SessionQueue, SessionSpec
from ai_company.workflows import run_task


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-company harness, loop and session queue")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the offline development-loop simulation")
    demo.add_argument("--task", type=Path, required=True, help="immutable task JSON")
    demo.add_argument("--state-dir", type=Path, default=Path(".ai-company"))
    demo.add_argument("--reject-first", type=int, default=1, help="number of simulated review rejections")
    session = commands.add_parser("session", help="schedule or inspect real CLI sessions")
    actions = session.add_subparsers(dest="session_action", required=True)
    for action in ("submit", "status", "worker", "handoff"):
        child = actions.add_parser(action)
        child.add_argument("--state-dir", type=Path, default=Path(".ai-company/sessions"))
        if action == "submit":
            child.add_argument("--task", type=Path, required=True)
            child.add_argument("--agent", choices=("codex", "claude"), required=True)
            child.add_argument("--agent-id", help="stable adapter/role identity; defaults to provider")
            child.add_argument("--worktree", type=Path, required=True)
            child.add_argument("--session-id", help="explicit existing session ID; never --last")
            child.add_argument("--last-completed-stage", default="submitted")
            child.add_argument("--checkpoint", type=Path, help="handoff context JSON; do not include credentials")
            child.add_argument("--retry-policy", type=Path, help="immutable retry and execution limits JSON")
        elif action in ("status", "handoff"):
            child.add_argument("--job-id", required=action == "handoff")
    args = parser.parse_args()
    if args.command == "session":
        return session_command(args)
    if not 0 <= args.reject_first <= 20:
        parser.error("--reject-first must be between 0 and 20")
    try:
        task = Task.model_validate_json(args.task.read_text())
        registry = Registry()
        registry.register("developer", FakeAdapter("fake-developer", "developer"))
        registry.register("reviewer", FakeAdapter("fake-reviewer", "reviewer", args.reject_first))
        state = run_task(task, args.state_dir, registry)
    except (ExecutionBlocked, ValidationError, OSError) as exc:
        print(json.dumps({"mode": "simulation", "status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] == "DEMO_READY" else 2


def session_command(args) -> int:
    try:
        with SessionQueue(args.state_dir) as queue:
            if args.session_action == "submit":
                task = Task.model_validate_json(args.task.read_text())
                policy = (RetryPolicy.model_validate_json(args.retry_policy.read_text())
                          if args.retry_policy else RetryPolicy())
                spec = SessionSpec(task=task, provider=args.agent, agent_id=args.agent_id or args.agent,
                                   worktree=str(args.worktree), session_id=args.session_id,
                                   last_completed_stage=args.last_completed_stage,
                                   checkpoint=json.loads(args.checkpoint.read_text()) if args.checkpoint else {},
                                   retry_policy=policy)
                result = queue.submit(spec)
            elif args.session_action == "worker":
                result = queue.run_once()
            elif args.session_action == "handoff":
                result = queue.handoff(args.job_id)
            else:
                result = queue.get(args.job_id) if args.job_id else queue.list_jobs()
    except (ExecutionBlocked, ValidationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    keys = ("job_id", "task_id", "agent_id", "provider", "status", "reason", "session_id", "worktree",
            "head_commit", "last_completed_stage", "retry_count", "attempt_count", "resume_at", "reset_at",
            "lease_until", "last_category", "handoff_file", "handoff_count")
    def public(job):
        return {key: job[key] for key in keys if key in job}
    print(json.dumps([public(job) for job in result] if isinstance(result, list) else public(result),
                     ensure_ascii=False, indent=2))
    # The worker completed its bounded scheduling pass even if the task is blocked.
    # Persisted states, not service restarts, govern all future retry decisions.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
