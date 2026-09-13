"""Run or resume the offline simulation. Live adapters are intentionally unavailable."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from ai_company.adapters.fake import FakeAdapter
from ai_company.contracts import Task
from ai_company.harness import Registry
from ai_company.runtime import ExecutionBlocked
from ai_company.workflows import run_task


def main() -> int:
    parser = argparse.ArgumentParser(description="ai-company harness and loop simulator")
    parser.add_argument("command", choices=["demo"])
    parser.add_argument("--task", type=Path, required=True, help="immutable task JSON")
    parser.add_argument("--state-dir", type=Path, default=Path(".ai-company"))
    parser.add_argument("--reject-first", type=int, default=1, help="number of simulated review rejections")
    args = parser.parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
