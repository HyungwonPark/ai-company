"""Read-only PR31 review repro: second repair waits then cannot resume.

Loads the candidate evaluator's exact function bodies using AST to avoid its
unavailable LangGraph import. SQLite ledger and usage aggregation are real;
the PM overview/worker are explicit fixtures. No model or operating calls.
"""
import ast
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock

if len(sys.argv) != 2:
    raise SystemExit('usage: python pr31-second-repair-resume-63927da.py /path/to/exact-63927da-source')
SOURCE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SOURCE / 'src'))
spec = importlib.util.spec_from_file_location('shared_calls', SOURCE / 'src/ai_company/shared_calls.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from ai_company.contracts import digest

names = {'case_message', 'global_usage', 'execution_usage', 'case_progress', 'case_wait', 'stage_timeout', 'run_case'}
tree = ast.parse((SOURCE / 'scripts/evaluate_pm_behavior.py').read_text())
namespace = dict(json=json, hashlib=hashlib, math=math, Path=Path, sqlite3=sqlite3,
                 time=time, digest=digest, SharedCallLedger=module.SharedCallLedger,
                 SharedCallError=module.SharedCallError, MAX_CALLS=24, MAX_SECONDS=1800, MAX_REPAIRS=2)
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
                        type_ignores=[]), str(SOURCE / 'scripts/evaluate_pm_behavior.py'), 'exec'), namespace)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary) / 'trial'
    case_root = root / 'E1'
    session_db = case_root / 'sessions/sessions.sqlite'
    session_db.parent.mkdir(parents=True)
    (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
    ledger_path = Path(temporary) / 'shared.db'
    ledger = module.SharedCallLedger.initialize(ledger_path, [
        ('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: 100)
    db = sqlite3.connect(session_db)
    db.execute('CREATE TABLE flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
    db.execute('CREATE TABLE session_jobs(job_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
    source_plan = 'source-plan-after-first-repair'
    second_task = 'pm-revise-' + digest([source_plan, 2])[:48]
    for number, task_id in ((1, 'pm-revise-first'), (2, second_task)):
        job_id = f'repair-job-{number}'
        reservation_id = digest([str(case_root), job_id, 1])
        ledger.reserve(reservation_id, str(case_root), 'codex', 'fixture', 'group')
        ledger.started(reservation_id, str(case_root), {'kind': 'fixture', 'job_id': job_id})
        ledger.settle(reservation_id, str(case_root), f'result-{number}', {
            'category': 'success' if number == 1 else 'quota',
            'duration_seconds': 1, 'total_cost_usd': 0,
            **({'reset_at': 110} if number == 2 else {})}, terminated=True)
        execution = {'job_id': job_id, 'session_id': f'fixture-session-{number}',
                     'accounted_attempts': 1}
        task = {'task_id': task_id, 'usage': {'repairs': 0, 'executions': 1},
                'executions': [execution] if number == 1 else [],
                'active': execution if number == 2 else None,
                'status': 'PLAN_READY' if number == 1 else 'WAITING_PM',
                'resume_at': 110 if number == 2 else None}
        db.execute('INSERT INTO flow_tasks VALUES (?,?)', (task_id, json.dumps(task)))
        db.execute('INSERT INTO session_jobs VALUES (?,?)', (job_id, json.dumps({
            'job_id': job_id, 'session_id': execution['session_id'], 'attempt_count': 1,
            'status': 'COMPLETED' if number == 1 else 'WAITING_QUOTA',
            'resume_at': 110 if number == 2 else None})))
    db.commit()
    db.close()
    ledger.close()
    recovered = module.SharedCallLedger(ledger_path, clock=lambda: 120)
    recovered.reserve('capacity-probe', 'probe-outside-evaluation', 'codex', 'fixture', 'group')
    recovered.cancel_unstarted('capacity-probe', 'probe-outside-evaluation',
                               evidence='queue_unclaimed_no_guard_no_process')
    recovered.close()
    overview = {'pm_requests': [{'request_id': 'request', 'state': 'completed'}],
                'plans': [{'request_id': 'request', 'status': 'needs_revision',
                           'revision_action': 'automatic', 'id': source_plan, 'auto_revision_attempt': 1}],
                'runs': []}
    store = Mock()
    store.list_projects.return_value = [{'id': 'project', 'name': 'PM 행동 평가 E1'}]
    store.overview.return_value = overview
    worker = Mock(store=store)
    worker.dispatcher.tasks.return_value = [{'task_id': second_task, 'status': 'WAITING_PM',
                                            'resume_at': 110, 'reason': 'quota'}]
    namespace['Automation'] = lambda *args, **kwargs: worker
    namespace['case_budget_config'] = lambda root, case_id, config, ledger_path: (config, {
        'effective_configuration_sha256': 'fixture',
        'caps': {'max_executions': 24, 'max_runtime_seconds': 1740, 'max_repairs': 2}})
    config = SimpleNamespace(pm_timeout_seconds=30, poll_seconds=1, policy=SimpleNamespace(
        max_runtime_seconds=1740, retry=SimpleNamespace(execution_timeout_seconds=60)))
    result = namespace['run_case'](root, {'id': 'E1', 'initial_message': 'fixture'}, config, ledger_path)
    assert namespace['global_usage'](root, ledger_path) == (2, 2)
    assert result['state'] == 'budget_wait'
    worker.run_once.assert_not_called()
    print(json.dumps({'diagnostic': 'CURRENT BUG: same second repair cannot resume after quota recovery',
                      'expected': 'Permit the existing second repair to resume; reject creation of a third repair',
                      'actual': result, 'model_ticks': worker.run_once.call_count,
                      'same_second_repair_task': second_task,
                      'quota_expired': time.time() > 110,
                      'shared_account_capacity_available': True,
                      'scope': 'candidate original evaluator + real SQLite; worker/overview fixtures'}, ensure_ascii=False))
