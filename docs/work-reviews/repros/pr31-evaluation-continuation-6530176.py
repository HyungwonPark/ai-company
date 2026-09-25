#!/usr/bin/env python3
"""PR #31 read-only evaluation-continuation diagnostic.

Usage:
    python3 pr31-evaluation-continuation-6530176.py /path/to/repository

The diagnostic extracts the original run_case function and persisted-deadline
branch with ast. Automation, usage observations and clock are test doubles.
It does not import the product, run a model, access the network or modify the
repository. Only temporary fixture files are written. These are control-flow
reproductions, not end-to-end evaluations.
"""
import ast
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock


def unexpected_sleep(_seconds):
    raise AssertionError("The candidate continued; this reproduction no longer matches.")


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__)
    source = Path(argv[0]).resolve() / "scripts/evaluate_pm_behavior.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    run_case = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "run_case")
    product_main = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "main")
    deadline_branch = next(
        node for node in ast.walk(product_main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Attribute)
        and isinstance(node.test.func.value, ast.Name)
        and node.test.func.value.id == "started"
        and node.test.func.attr == "exists"
    )
    budget_assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Tuple)
                and [item.id for item in target.elts] ==
                    ["MAX_CALLS", "MAX_SECONDS", "MAX_REPAIRS"]
                for target in node.targets)
    )
    results = []
    with TemporaryDirectory(prefix="pr31-evaluation-repro-") as temporary:
        for label, now, usage in [
            ("automatic_repair_early_exit", 100.0, (2, 1)),
            ("elapsed_wait_consumes_budget", 2001.0, (0, 0)),
        ]:
            root = Path(temporary) / label
            root.mkdir()
            (root / "cases.json").write_text(
                json.dumps({"common_project_goal": "fixture"}), encoding="utf-8")
            started = root / "started-at.json"
            started.write_text(json.dumps({"at": 100.0}), encoding="utf-8")
            store = Mock()
            store.list_projects.return_value = [
                {"id": "project", "name": "PM 행동 평가 E4"}]
            store.overview.return_value = {
                "pm_requests": [{"state": "completed", "content": "fixture"}],
                "plans": [{"status": "needs_revision", "revision_action": "automatic"}],
                "runs": [],
            }
            worker = Mock(store=store)
            namespace = {
                "json": json,
                "time": SimpleNamespace(time=lambda: now, sleep=unexpected_sleep),
                "global_usage": lambda *_: usage,
                "case_config": lambda config, _case: config,
                "case_message": lambda case: case["initial_message"],
                "Automation": lambda *_args, **_kwargs: worker,
                "started": started,
            }
            # Execute unmodified candidate AST, with only external collaborators mocked.
            extract = ast.Module(
                body=[budget_assignment, run_case, deadline_branch], type_ignores=[])
            exec(compile(extract, str(source), "exec"), namespace)
            config = SimpleNamespace(
                policy=SimpleNamespace(
                    retry=SimpleNamespace(execution_timeout_seconds=30)),
                poll_seconds=1,
            )
            result = namespace["run_case"](
                root, {"id": "E4", "initial_message": "fixture"}, config,
                root / "unused-ledger.db", namespace["deadline"],
            )
            if label == "automatic_repair_early_exit":
                assert result["state"] == "observed", result
                assert result["plan_states"] == ["needs_revision"], result
                assert worker.run_once.call_count == 1
            else:
                assert now > namespace["deadline"]
                assert result["state"] == "budget_wait", result
                assert result["calls"] == result["repairs"] == 0, result
                assert worker.run_once.call_count == 0
            results.append({
                "proof": label,
                "result": result,
                "worker_ticks": worker.run_once.call_count,
                "observed_usage": {"calls": usage[0], "repairs": usage[1]},
                "persisted_start": 100.0,
                "deadline": namespace["deadline"],
                "clock": now,
                "model_calls": 0,
                "scope": "original candidate control flow; mocked worker and usage",
            })
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])

