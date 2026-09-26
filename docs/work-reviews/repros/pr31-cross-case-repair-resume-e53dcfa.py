#!/usr/bin/env python3
"""R31-4b cross-case diagnostic. No model calls or product writes.

Usage: python repro_cross_case_repair_resume.py --source /path/to/read-export

Loads the candidate's unchanged function ASTs and real contract/ledger classes.
Automation/Dispatcher behavior is NOT exercised: their durable SQLite documents
and overview are synthetic. This is a focused runner-gate diagnostic, not an
end-to-end model or operating validation. Missing LangGraph is not stubbed.

Chronology: E1 plan -> review(REVISE) -> first repair -> review(PASS), then
the real case_budget_config pins E2 with max_repairs=1. E2 plan -> review(REVISE)
-> its first repair settles quota and recovery time passes. This is the second
started repair globally, while its within-case revision number is one.
"""

import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import types
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source / 'src'))
    from ai_company.automation_contracts import AutomationConfig
    from ai_company.contracts import digest
    from ai_company.shared_calls import SharedCallError, SharedCallLedger

    path = source / 'scripts/evaluate_pm_behavior.py'
    names = {'case_message', 'case_config', 'case_budget_config', 'global_usage',
             'resumable_second_repair', 'execution_usage', 'case_progress',
             'case_wait', 'stage_timeout', 'run_case'}
    tree = ast.parse(path.read_text())
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in body} != names:
        raise SystemExit('candidate runner function set differs from this diagnostic')
    evaluation = types.ModuleType('isolated_evaluation')
    evaluation.__dict__.update(globals())
    evaluation.__dict__.update(AutomationConfig=AutomationConfig, digest=digest,
        SharedCallLedger=SharedCallLedger, SharedCallError=SharedCallError,
        MAX_CALLS=24, MAX_SECONDS=1800, MAX_REPAIRS=2,
        CALL_CLEANUP_ALLOWANCE_SECONDS=60, Automation=Mock())
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), evaluation.__dict__)

    with tempfile.TemporaryDirectory(prefix='r31-cross-case-') as temp:
        root = Path(temp) / 'trial'
        root.mkdir()
        (root / 'cases.json').write_text(json.dumps({'common_project_goal': 'fixture'}))
        shared = Path(temp) / 'shared.db'
        ledger = SharedCallLedger.initialize(shared,
            [('codex', 'fixture', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)])
        config = AutomationConfig.model_validate({
            'repository': 'fixture/repo', 'source_clone': str(Path(temp) / 'source'),
            'base_sha': 'a' * 40, 'base_branch': 'main', 'allowed_paths': ['src/', 'tests/'],
            'checks': {'unit': {'argv': ['true']}},
            'agents': [{'agent_id': 'pm', 'provider': 'codex', 'requested_label': 'fixture',
                        'model': 'gpt-6-astra', 'reasoning_effort': 'ultra',
                        'roles': ['pm', 'developer', 'reviewer', 'final'],
                        'credential_ref': 'fixture', 'quota_group': 'group'}],
            'policy': {'candidates': {role: ['pm'] for role in ('pm', 'developer', 'reviewer', 'final')},
                       'max_executions': 24, 'max_runtime_seconds': 1800,
                       'max_repairs': 2, 'retry': {'execution_timeout_seconds': 60}},
            'ci': {'workflow_path': '.github/workflows/fixture.yml', 'workflow_digest': 'a' * 64,
                   'required_checks': ['fixture']},
            'mode': 'fixture', 'pm_timeout_seconds': 30, 'poll_seconds': 1})
        plan = {'id': 'source-E2', 'request_id': 'request-E2', 'project_id': 'project-E2',
                'request_revision': 1, 'goal_digest': 'goal-E2', 'status': 'needs_revision',
                'revision_action': 'automatic', 'auto_revision_attempt': 0}

        def settled_call(case, job, category='success'):
            owner = str(root / case)
            reservation = digest([owner, job, 1])
            ledger.reserve(reservation, owner, 'codex', 'fixture', 'group')
            ledger.started(reservation, owner, {'kind': 'synthetic-terminal-process'})
            ledger.settle(reservation, owner, 'event-' + job, {
                'category': category, 'duration_seconds': 1, 'total_cost_usd': 0,
                **({'reset_at': 1} if category == 'quota' else {})}, terminated=True)

        def write_repair(case, waiting):
            database = root / case / 'sessions' / 'sessions.sqlite'
            database.parent.mkdir(parents=True, exist_ok=True)
            task_id = ('pm-revise-' + digest([plan['id'], 1])[:48]
                       if waiting else 'pm-revise-prior-case')
            job_id, session = 'repair-' + case, 'session-' + case
            task = {'task_id': task_id, 'status': 'WAITING_PM' if waiting else 'PLAN_READY',
                    'resume_at': 0, 'usage': {'executions': 1, 'repairs': 0},
                    'executions': [] if waiting else [{'job_id': job_id}],
                    'active': {'job_id': job_id, 'session_id': session, 'accounted_attempts': 1} if waiting else None,
                    'specification': {'execution_scope': 'planning', 'plan': {
                        'source_plan_id': plan['id'] if waiting else 'source-E1',
                        'auto_revision_attempt': 1, 'project_id': plan['project_id'] if waiting else 'project-E1',
                        'request_revision': 1, 'goal_digest': plan['goal_digest'] if waiting else 'goal-E1'}}}
            job = {'job_id': job_id, 'task_id': task_id,
                   'status': 'WAITING_QUOTA' if waiting else 'COMPLETED',
                   'session_id': session, 'attempt_count': 1,
                   'last_category': 'quota' if waiting else 'success', 'resume_at': 0}
            with sqlite3.connect(database) as db:
                db.executescript('CREATE TABLE flow_tasks(task_id TEXT PRIMARY KEY, document TEXT NOT NULL);'
                                 'CREATE TABLE session_jobs(job_id TEXT PRIMARY KEY, document TEXT NOT NULL);')
                db.execute('INSERT INTO flow_tasks VALUES (?,?)', (task_id, json.dumps(task)))
                db.execute('INSERT INTO session_jobs VALUES (?,?)', (job_id, json.dumps(job)))
            return task, job

        # Completed E1: all four model calls count; its one started repair counts once.
        settled_call('E1', 'pm-E1')
        settled_call('E1', 'review-E1-initial')
        settled_call('E1', 'repair-E1')
        write_repair('E1', waiting=False)
        settled_call('E1', 'review-E1-final')

        # Pin E2 before its session database is created, using the REAL function.
        (root / 'E2').mkdir()
        effective, budget = evaluation.case_budget_config(root, 'E2', config, shared)
        assert budget['global_before'] == {'calls': 4, 'repairs': 1, 'seconds': 4}
        assert effective.policy.max_repairs == 1
        settled_call('E2', 'pm-E2')
        settled_call('E2', 'review-E2-initial')
        settled_call('E2', 'repair-E2', 'quota')
        task, job = write_repair('E2', waiting=True)
        ledger.close()

        store = Mock()
        store.list_projects.return_value = [{'id': 'project-E2', 'name': 'PM 행동 평가 E2'}]
        current = {'pm_requests': [{'request_id': 'request-E2', 'state': 'completed'}],
                   'plans': [plan], 'runs': []}
        proposed = {**current, 'plans': [plan, {
            'id': 'result-E2', 'request_id': 'request-E2', 'status': 'proposed'}]}
        # A fixed gate dispatches once, then can terminate this diagnostic normally.
        store.overview.side_effect = [current, proposed]
        worker = Mock(store=store)
        worker.dispatcher.tasks.return_value = [task]
        worker.dispatcher.queue.get.return_value = job
        # Only Automation is replaced. The persisted case budget is re-read and validated.
        with patch.object(evaluation, 'Automation', return_value=worker), patch.object(evaluation.time, 'sleep'):
            result = evaluation.run_case(root, {'id': 'E2', 'initial_message': 'fixture',
                'followup_message': 'fixture followup'}, config, shared)
        observed = {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'scope': 'real function AST + real SQLite/contracts/ledger; synthetic Automation state',
                    'e2_global_before': budget['global_before'], 'e2_pinned_caps': budget['caps'],
                    'global_usage_after_e2_quota': evaluation.global_usage(root, shared),
                    'execution_usage_after_e2_quota': evaluation.execution_usage(root, shared, 60),
                    'current_case_repair_number': 1, 'current_global_repair_number': 2,
                    'run_once_calls': worker.run_once.call_count, 'result': result}
        print(json.dumps(observed, ensure_ascii=False, indent=2))
        assert result['state'] == 'budget_wait' and worker.run_once.call_count == 0, (
            'The historical defect was not reproduced; inspect the candidate result.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
