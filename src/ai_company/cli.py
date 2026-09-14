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
    flow = commands.add_parser("flow", help="durable role allocation and development/review loop")
    flow_actions = flow.add_subparsers(dest="flow_action", required=True)
    for action in ("submit", "status", "worker", "context-handoff", "migrate-policy", "rollback-policy"):
        child = flow_actions.add_parser(action)
        child.add_argument("--state-dir", type=Path, default=Path(".ai-company/flows"))
        if action in ("submit", "migrate-policy"):
            child.add_argument("--spec", type=Path, required=True)
        if action in ("status", "context-handoff", "migrate-policy"):
            child.add_argument("--task-id", required=action != "status")
        if action == "rollback-policy":
            child.add_argument("--migration-id", required=True)
        if action in ("migrate-policy", "rollback-policy"):
            child.add_argument("--reason", required=True)
        if action == "worker":
            child.add_argument("--parallel", type=int, choices=(1, 2), default=1,
                               help="bounded independent flow workers; separate Git clones required")
    manage = commands.add_parser("manage", help="serve the authenticated project console")
    manage_actions = manage.add_subparsers(dest="manage_action", required=True)
    serve = manage_actions.add_parser("serve")
    serve.add_argument("--state-dir", type=Path, required=True)
    serve.add_argument("--token-file", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    automate = commands.add_parser("automate", help="coordinate confirmed PM plans using the existing Dispatcher")
    automate.add_argument("action", choices=("tick", "worker", "status"))
    automate.add_argument("--state-dir", type=Path, required=True)
    automate.add_argument("--config", type=Path, required=True, help="trusted local server configuration JSON")
    automate.add_argument("--max-seconds", type=float, default=1800,
                          help="bounded foreground worker lifetime; no timer or service is installed")
    args = parser.parse_args()
    if args.command == "automate":
        return automation_command(args)
    if args.command == "manage":
        from ai_company.management_server import serve
        try:
            serve(args.state_dir, args.token_file, host=args.host, port=args.port)
            return 0
        except (ExecutionBlocked, OSError, ValueError) as exc:
            print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
            return 2
    if args.command == "flow":
        return flow_command(args)
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


def automation_command(args) -> int:
    import time
    import math
    from ai_company.automation import Automation
    from ai_company.automation_contracts import AutomationConfig
    from ai_company.management import ManagementError
    worker = None
    try:
        if not math.isfinite(args.max_seconds) or not 0 < args.max_seconds <= 86400:
            raise ValueError("max-seconds must be positive and at most 86400")
        config = AutomationConfig.model_validate_json(args.config.read_text())
        worker = Automation(args.state_dir, config)
        if args.action == "status":
            result = {"pm_requests": worker.store.pm_requests(), "runs": worker.store.run_records()}
        elif args.action == "tick":
            result = worker.run_once()
        else:
            until = time.monotonic() + args.max_seconds
            while time.monotonic() < until:
                result = worker.run_once()
                print(json.dumps({"pm": [{"request_id": r["request_id"], "state": r["state"]} for r in result["pm_requests"]],
                                  "runs": [{"id": r["id"], "state": r["state"]} for r in result["runs"]]}), flush=True)
                time.sleep(min(config.poll_seconds, max(0, until - time.monotonic())))
            result = {"status": "worker_stopped", "reason": "bounded foreground lifetime reached; durable state retained"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ExecutionBlocked, ValidationError, ManagementError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if worker:
            worker.close()


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


def flow_command(args) -> int:
    from ai_company.dispatcher import Dispatcher
    from ai_company.flow_contracts import FlowSpec
    dispatcher = None
    try:
        dispatcher = Dispatcher(args.state_dir)
        if args.flow_action == "submit":
            result = dispatcher.submit(FlowSpec.model_validate_json(args.spec.read_text()))
        elif args.flow_action == "worker":
            if args.parallel == 1:
                result = dispatcher.run_once()
            else:
                from concurrent.futures import ThreadPoolExecutor
                # Each thread owns its SQLite connection. No daemon, timer or
                # background retry loop is introduced by a scheduling pass.
                def once(_):
                    worker = Dispatcher(args.state_dir)
                    try:
                        return worker.run_once()
                    finally:
                        worker.close()
                with ThreadPoolExecutor(max_workers=args.parallel) as workers:
                    result = list(workers.map(once, range(args.parallel)))
        elif args.flow_action == "context-handoff":
            result = dispatcher.context_handoff(args.task_id)
        elif args.flow_action == "migrate-policy":
            result = dispatcher.migrate_policy(args.task_id, FlowSpec.model_validate_json(args.spec.read_text()), args.reason)
        elif args.flow_action == "rollback-policy":
            result = dispatcher.rollback_policy(args.migration_id, args.reason)
        else:
            result = dispatcher.get(args.task_id) if args.task_id else dispatcher.tasks()
        keys = ("task_id", "status", "stage", "reason", "generation", "resume_at", "usage", "migration_id")
        def public(record):
            return {k: record[k] for k in keys if k in record}
        print(json.dumps([public(r) for r in result] if isinstance(result, list) else public(result),
                         ensure_ascii=False, indent=2))
        return 0
    except (ExecutionBlocked, ValidationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if dispatcher:
            dispatcher.close()


if __name__ == "__main__":
    raise SystemExit(main())
