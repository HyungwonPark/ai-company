"""Inspect and, only on an exact operator-selected attempt, prepare a stopped timeout for retry.

The original session outcome stays in an audit row.  Settlement happens before
the queue or account can run again; re-entry finishes the same durable steps.
"""

import hashlib
import json
import math
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from ai_company.sessions import execution_alive, repository_snapshot
from ai_company.adapters.session_cli import service_alive
from ai_company.shared_calls import SharedCallLedger, SharedCallError
from ai_company.storage import controller_lock


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _document(db, table, key, value):
    row = db.execute(f'SELECT document FROM {table} WHERE {key}=?', (value,)).fetchone()
    if row is None:
        raise ValueError(f'{table} record is missing')
    return json.loads(row[0]), row[0]


def _guard(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        return json.load(stream)


def _output_facts(path, root):
    path = Path(path)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('CLI output is absent or outside the case root')
    data = path.read_bytes()
    events = [json.loads(line) for line in data.splitlines()]
    types = [event.get('type') for event in events]
    sessions = sorted({event.get('thread_id') or event.get('session_id')
                       for event in events if event.get('type') == 'thread.started'})
    return {'sha256': _sha(data), 'bytes': len(data), 'turn_started': 'turn.started' in types,
            'turn_completed': 'turn.completed' in types, 'thread_started': 'thread.started' in types,
            'sessions': sessions}


def _ledger_history_preserved(current_path, baseline_path):
    """Check every old fact and reconstruct counters from later settlement events."""
    columns = ('provider,credential_ref,group_id,state,resume_at,reason,calls,'
               'runtime_seconds,cost_usd,cost_unknown')
    reservation_columns = ('reservation_id,owner,group_id,state,created_at,started_at,'
                           'process_identity,event_id,result,closed_at')
    with (closing(sqlite3.connect(f'file:{Path(baseline_path).resolve()}?mode=ro', uri=True)) as old,
          closing(sqlite3.connect(f'file:{Path(current_path).resolve()}?mode=ro', uri=True)) as now):
        old_accounts = {row[:2]: row for row in old.execute(f'SELECT {columns} FROM accounts')}
        accounts = {row[:2]: row for row in now.execute(f'SELECT {columns} FROM accounts')}
        if old_accounts.keys() != accounts.keys():
            return False
        original_reservations = old.execute(f'SELECT {reservation_columns} FROM reservations').fetchall()
        for row in original_reservations:
            if now.execute(f'SELECT {reservation_columns} FROM reservations WHERE reservation_id=?',
                           (row[0],)).fetchone() != row:
                return False
        original_events = dict((row[0], row) for row in old.execute(
            'SELECT event_id,reservation_id,result,at FROM settlement_events'))
        current_events = dict((row[0], row) for row in now.execute(
            'SELECT event_id,reservation_id,result,at FROM settlement_events'))
        if any(current_events.get(key) != row for key, row in original_events.items()):
            return False
        increments = {}
        for key, event in current_events.items():
            if key in original_events:
                continue
            reservation = now.execute(
                'SELECT group_id,state,event_id,result FROM reservations WHERE reservation_id=?',
                (event[1],)).fetchone()
            if (reservation is None or reservation[1:] != ('SETTLED', key, event[2])
                    or event[1] in {row[1] for row in original_events.values()}):
                return False
            result = json.loads(event[2])
            duration, cost = result.get('duration_seconds'), result.get('total_cost_usd')
            if (isinstance(duration, bool) or not isinstance(duration, (float, int))
                    or not math.isfinite(duration) or duration < 0
                    or cost is not None and (isinstance(cost, bool) or not isinstance(cost, (float, int))
                                             or not math.isfinite(cost) or cost < 0)):
                return False
            group = reservation[0]
            count, seconds, dollars, unknown = increments.get(group, (0, 0.0, 0.0, 0))
            increments[group] = (count + 1, seconds + duration, dollars + (cost or 0),
                                 max(unknown, int(cost is None)))
        for key, before in old_accounts.items():
            after = accounts[key]
            if after[2] != before[2]:
                return False
            count, seconds, dollars, unknown = increments.get(after[2], (0, 0, 0, 0))
            if (after[6] != before[6] + count
                    or not math.isclose(after[7], before[7] + seconds, rel_tol=0, abs_tol=1e-6)
                    or not math.isclose(after[8], before[8] + dollars, rel_tol=0, abs_tol=1e-6)
                    or after[9] != max(before[9], unknown)):
                return False
        return True


def _ledger_snapshot_digest(path):
    """Hash SQLite's logical snapshot, including WAL pages invisible to file hashing."""
    with closing(sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        rows = {}
        for table in ('shared_meta', 'accounts', 'reservations', 'settlement_events',
                      'legacy_review_imports'):
            rows[table] = sorted(db.execute(f'SELECT * FROM {table}').fetchall())
        return _sha(_canonical(rows))


def diagnose(case_root, ledger_path, baseline_path, job_id):
    """Read-only, fail-closed assessment; never treats elapsed time as termination proof."""
    root = Path(case_root).resolve()
    if os.path.samefile(ledger_path, baseline_path):
        raise ValueError('baseline ledger aliases the current ledger')
    path = root / 'sessions/sessions.sqlite'
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
        job, job_raw = _document(db, 'session_jobs', 'job_id', job_id)
        task, task_raw = _document(db, 'flow_tasks', 'task_id', job['task_id'])
        if not job['task_id'].startswith('pm-'):
            raise ValueError('only a PM request may use this recovery')
        request, request_raw = _document(db, 'management_pm_requests', 'message_id',
                                         job['task_id'].removeprefix('pm-'))
        previous_rows = db.execute("SELECT document FROM management_pm_requests WHERE project_id=? "
                                   "AND json_extract(document,'$.request_revision')=1",
                                   (request['project_id'],)).fetchall()
    cases_path = root.parent / 'cases.json'
    cases_bytes = cases_path.read_bytes()
    case = next(item for item in json.loads(cases_bytes)['cases'] if item['id'] == 'E2')
    previous = json.loads(previous_rows[0][0]) if len(previous_rows) == 1 else {}
    ledger = SharedCallLedger(ledger_path)
    try:
        from ai_company.contracts import digest
        reservation_id = digest([str(root), job_id, max(1, job['attempt_count'])])
        row = ledger.reservation(reservation_id)
        if row is None:
            raise ValueError('attempt has no shared reservation')
        group = row['group_id']
        accounts = ledger.db.execute('SELECT state,resume_at,reason FROM accounts WHERE group_id=?',
                                     (group,)).fetchall()
        other_live = ledger.db.execute("SELECT count(*) FROM reservations WHERE group_id=? AND reservation_id!=? "
                                       "AND state IN ('RESERVED','STARTED','UNKNOWN')",
                                       (group, reservation_id)).fetchone()[0]
    finally:
        ledger.close()
    if job['attempt_count'] == 0:
        guard_path = Path(job['repository_snapshot']['git_common_dir']) / 'ai-company-session-active.json'
        unstarted = (job['status'] == 'READY' and not job.get('process') and not job.get('result')
                     and row['state'] == 'RESERVED' and row['process_identity'] is None
                     and _guard(guard_path) is None
                     and repository_snapshot(Path(job['worktree'])) == job['repository_snapshot'])
        plan = {'schema_version': 1, 'job_id': job_id, 'reservation_id': reservation_id,
                'decision': 'existing_cancel_unstarted_contract' if unstarted else 'hold_unknown',
                'checks': {'queue_unclaimed': bool(unstarted)}, 'duration_seconds': 0}
        plan['digest'] = _sha(_canonical(plan))
        return plan
    with closing(sqlite3.connect(f'file:{Path(baseline_path).resolve()}?mode=ro', uri=True)) as baseline:
        old_accounts = baseline.execute('SELECT state,resume_at,reason FROM accounts WHERE group_id=?',
                                        (group,)).fetchall()
    preserved = _ledger_history_preserved(ledger_path, baseline_path)
    result = job.get('result') or {}
    stdout = _output_facts(result.get('stdout_path'), root)
    stderr = Path(result.get('stderr_path', ''))
    if not stderr.is_file() or not stderr.resolve().is_relative_to(root):
        raise ValueError('CLI stderr is absent or outside the case root')
    process = job.get('process') or {}
    guard_path = Path(job['repository_snapshot']['git_common_dir']) / 'ai-company-session-active.json'
    guard = _guard(guard_path)
    current_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    repo_same = repository_snapshot(Path(job['worktree'])) == job['repository_snapshot']
    stopped = (process.get('boot_id') == current_boot and bool(process.get('pid'))
               and bool(process.get('proc_start_ticks')) and result.get('cgroup_stopped') is True
               and isinstance(process.get('systemd_unit'), str)
               and process['systemd_unit'].startswith('ai-company-run-')
               and result.get('systemd_unit') == process['systemd_unit']
               and not service_alive(process['systemd_unit']) and not execution_alive(process))
    owned_guard = (guard is not None and guard.get('job_id') == job_id
                   and guard.get('queue_root') == str(root / 'sessions')
                   and guard.get('process') == process)
    tied = (task.get('active', {}).get('job_id') == job_id
            and task.get('active', {}).get('accounted_attempts') == job['attempt_count']
            and task.get('usage', {}).get('executions', 0) >= job['attempt_count']
            and task.get('usage', {}).get('runtime_seconds', 0) >= result.get('duration_seconds', float('inf'))
            and request.get('execution', {}).get('task_id') == job['task_id']
            and request.get('request_revision') == 2
            and previous.get('state') == 'answer_needed'
            and previous.get('content') == case['initial_message']
            and request.get('content') == case['followup_message']
            and job.get('session_id') and job.get('attempt_count') == 1
            and job.get('status') == 'NEEDS_RECONCILIATION'
            and job.get('reason') == 'non-retryable session outcome: reconciliation'
            and job.get('last_category') == 'reconciliation'
            and row['state'] == 'UNKNOWN' and row['owner'] == str(root)
            and isinstance(row['process_identity'], str)
            and json.loads(row['process_identity']).get('kind') == 'executor_invocation'
            and json.loads(row['process_identity']).get('job_id') == job_id
            and json.loads(row['process_identity']).get('attempt') == job['attempt_count']
            and row['event_id'] is None and row['result'] is None)
    safe = (tied and stopped and owned_guard and repo_same and stdout['thread_started']
            and stdout['sessions'] == [job['session_id']]
            and stdout['turn_started'] and not stdout['turn_completed']
            and result.get('termination_cause') == 'timeout'
            and result.get('effective_timeout_seconds') == job['specification']['retry_policy']['execution_timeout_seconds']
            and result.get('exit_code') == -15 and result.get('structured_output') is None
            and len(accounts) > 0 and all(a[0] == 'UNKNOWN' and
                a[2] == 'session termination or effects are uncertain' for a in accounts)
            and old_accounts and all(a[0] == 'AVAILABLE' for a in old_accounts)
            and not other_live and preserved and isinstance(result.get('duration_seconds'), (int, float))
            and not isinstance(result.get('duration_seconds'), bool)
            and math.isfinite(result['duration_seconds']) and result['duration_seconds'] > 0)
    plan = {'schema_version': 1, 'case_root': str(root), 'ledger_path': str(Path(ledger_path).resolve()),
            'baseline_logical_sha256': _ledger_snapshot_digest(baseline_path), 'job_id': job_id,
            'task_id': job['task_id'], 'request_id': request['request_id'],
            'request_revision': request['request_revision'], 'session_id': job['session_id'],
            'cases_sha256': _sha(cases_bytes), 'first_request_sha256':
                _sha(previous_rows[0][0].encode()) if len(previous_rows) == 1 else None,
            'attempt_count': job['attempt_count'], 'reservation_id': reservation_id,
            'group_id': group, 'job_sha256': _sha(job_raw.encode()),
            'task_sha256': _sha(task_raw.encode()), 'request_sha256': _sha(request_raw.encode()),
            'stdout_path': str(Path(result['stdout_path']).resolve()), 'stdout': stdout,
            'stderr_path': str(stderr.resolve()), 'stderr_sha256': _sha(stderr.read_bytes()),
            'repository_snapshot': job['repository_snapshot'],
            'process': process, 'guard_path': str(guard_path),
            'duration_seconds': result.get('duration_seconds'),
            'result_sha256': _sha(_canonical(result)),
            'decision': ('same_session_retry_preparable' if safe else
                         'saved_terminal_needs_contract_review' if tied and stopped and
                         stdout['turn_completed'] and stdout['sessions'] == [job['session_id']]
                         and repo_same else 'hold_unknown'),
            'checks': {'attempt_tied': bool(tied), 'terminated': bool(stopped),
                       'guard_owned': bool(owned_guard), 'repository_unchanged': bool(repo_same),
                       'timeout_cause_recorded': result.get('termination_cause') == 'timeout',
                       'terminal_complete': bool(stdout['turn_completed']),
                       'other_live_reservations': other_live, 'historical_ledger_preserved': preserved,
                       'baseline_available': bool(old_accounts)
                       and all(a[0] == 'AVAILABLE' for a in old_accounts)}}
    plan['digest'] = _sha(_canonical(plan))
    return plan


def apply_in_stopped_environment(case_root, ledger_path, baseline_path, job_id, expected_digest,
                                 *, fail_after=None):
    """Operator-only mutation. Keep workers stopped; use a private state copy for tests."""
    root = Path(case_root).resolve()
    shared = Path(ledger_path).resolve()
    db_path = root / 'sessions/sessions.sqlite'
    with (controller_lock(shared.with_name(shared.name + '-evaluation-runner')),
          controller_lock(root / 'timeout-recovery')):
        db = sqlite3.connect(db_path, isolation_level=None, timeout=10)
        try:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='timeout_recoveries'").fetchone()
            saved = (db.execute('SELECT plan,phase,prepared_hashes FROM timeout_recoveries WHERE job_id=?',
                                (job_id,)).fetchone() if exists else None)
            if saved:
                plan, phase, prepared_hashes = json.loads(saved[0]), saved[1], saved[2]
            else:
                plan = diagnose(root, shared, baseline_path, job_id)
                if plan['digest'] != expected_digest:
                    raise ValueError('recovery evidence changed')
                if plan['decision'] != 'same_session_retry_preparable':
                    raise ValueError('termination, output, account or repository evidence is incomplete')
                job, raw_job = _document(db, 'session_jobs', 'job_id', job_id)
                task, raw_task = _document(db, 'flow_tasks', 'task_id', job['task_id'])
                _, raw_request = _document(db, 'management_pm_requests', 'message_id',
                                           job['task_id'].removeprefix('pm-'))
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute('CREATE TABLE IF NOT EXISTS timeout_recoveries('
                               'job_id TEXT PRIMARY KEY, plan TEXT NOT NULL, original_job TEXT NOT NULL, '
                               'original_task TEXT NOT NULL, original_request TEXT NOT NULL, '
                               'prepared_hashes TEXT, phase TEXT NOT NULL)')
                    db.execute('INSERT INTO timeout_recoveries VALUES (?,?,?,?,?,?,?)',
                               (job_id, json.dumps(plan, ensure_ascii=False), raw_job, raw_task,
                                raw_request, None, 'pinned'))
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
                phase = 'pinned'
                prepared_hashes = None
            if (plan['digest'] != expected_digest or
                    plan['baseline_logical_sha256'] != _ledger_snapshot_digest(baseline_path)):
                raise ValueError('recovery evidence changed')
            if _sha(_canonical({k: v for k, v in plan.items() if k != 'digest'})) != plan['digest']:
                raise ValueError('stored recovery plan changed')
            if (plan['decision'] != 'same_session_retry_preparable' or plan['case_root'] != str(root)
                    or plan['ledger_path'] != str(shared)):
                raise ValueError('recovery target differs from the frozen plan')
            if (repository_snapshot(Path(plan['repository_snapshot']['worktree'])) != plan['repository_snapshot']
                    or execution_alive(plan['process']) or service_alive(plan['process']['systemd_unit'])):
                raise ValueError('repository or process state changed')
            if (_output_facts(plan['stdout_path'], root) != plan['stdout']
                    or _sha(Path(plan['stderr_path']).read_bytes()) != plan['stderr_sha256']):
                raise ValueError('saved CLI output changed')
            if _sha((root.parent / 'cases.json').read_bytes()) != plan['cases_sha256']:
                raise ValueError('evaluation cases changed')
            first = db.execute("SELECT document FROM management_pm_requests WHERE project_id=? "
                               "AND json_extract(document,'$.request_revision')=1",
                               (json.loads(db.execute('SELECT original_request FROM timeout_recoveries '
                                                      'WHERE job_id=?', (job_id,)).fetchone()[0])['project_id'],)).fetchall()
            if len(first) != 1 or _sha(first[0][0].encode()) != plan['first_request_sha256']:
                raise ValueError('original PM question changed')
            current_hashes = []
            for table, key, value in (('session_jobs', 'job_id', job_id),
                                      ('flow_tasks', 'task_id', plan['task_id']),
                                      ('management_pm_requests', 'message_id', plan['request_id'])):
                _, raw = _document(db, table, key, value)
                current_hashes.append(_sha(raw.encode()))
            expected_hashes = ([plan['job_sha256'], plan['task_sha256'], plan['request_sha256']]
                               if phase in ('pinned', 'settled') else
                               json.loads(prepared_hashes) if prepared_hashes else None)
            if current_hashes != expected_hashes:
                raise ValueError('queue, task or request changed since the frozen phase')
            if not _ledger_history_preserved(shared, baseline_path):
                raise ValueError('shared account history or cumulative usage changed')
            ledger = SharedCallLedger(shared)
            try:
                event_id = _sha(_canonical(['timeout-recovery', plan['digest'], plan['reservation_id']]))
                fact = {'category': 'reconciliation', 'duration_seconds': plan['duration_seconds'],
                        'total_cost_usd': None, 'recovery_kind': 'confirmed_incomplete_timeout',
                        'original_result_sha256': plan['result_sha256']}
                if phase != 'pinned':
                    settled = ledger.reservation(plan['reservation_id'])
                    if (settled is None or settled['state'] != 'SETTLED' or settled['event_id'] != event_id
                            or settled['result'] != json.dumps(fact, sort_keys=True, ensure_ascii=False)):
                        raise ValueError('recovered settlement changed')
                guard_now = _guard(plan['guard_path'])
                if phase in ('pinned', 'settled', 'prepared'):
                    if (guard_now is None or guard_now.get('job_id') != job_id
                            or guard_now.get('process') != plan['process']
                            or guard_now.get('queue_root') != str(root / 'sessions')):
                        raise ValueError('repository guard changed before release')
                elif phase == 'guard_release_pending' and guard_now is not None:
                    if (guard_now.get('job_id') != job_id or guard_now.get('process') != plan['process']
                            or guard_now.get('queue_root') != str(root / 'sessions')):
                        raise ValueError('another execution owns the repository guard')
                elif guard_now is not None:
                    raise ValueError('repository guard changed after release')
                if phase == 'pinned':
                    current = ledger.reservation(plan['reservation_id'])
                    if current['state'] not in ('UNKNOWN', 'SETTLED'):
                        raise ValueError('shared reservation is not the frozen attempt')
                    if current['state'] == 'UNKNOWN':
                        fresh = diagnose(root, shared, baseline_path, job_id)
                        if fresh['digest'] != plan['digest'] or fresh['decision'] != 'same_session_retry_preparable':
                            raise ValueError('recovery evidence changed before settlement')
                    ledger.settle(plan['reservation_id'], str(root), event_id, fact, terminated=True)
                    db.execute("UPDATE timeout_recoveries SET phase='settled' WHERE job_id=?", (job_id,))
                    phase = 'settled'
                    if fail_after == 'settlement': raise RuntimeError('injected stop after settlement')
                if phase == 'settled':
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        job, raw_job = _document(db, 'session_jobs', 'job_id', job_id)
                        task, raw_task = _document(db, 'flow_tasks', 'task_id', plan['task_id'])
                        request, raw_request = _document(db, 'management_pm_requests', 'message_id',
                                                         plan['request_id'])
                        if tuple(_sha(raw.encode()) for raw in (raw_job, raw_task, raw_request)) != (
                                plan['job_sha256'], plan['task_sha256'], plan['request_sha256']):
                            raise ValueError('queue facts changed since recovery was pinned')
                        now = time.time()
                        job.update(status='WAITING_RETRY', resume_at=now, lease_owner=None, lease_until=None,
                                   reason='operator-reviewed incomplete timeout; same session only')
                        task.update(status='WAITING_CAPACITY', resume_at=now,
                                    reason='same-session timeout recovery prepared')
                        request.update(state='running', reason='same-session timeout recovery prepared',
                                       execution={**request['execution'], 'status': 'WAITING_CAPACITY',
                                                  'resume_at': now})
                        db.execute('UPDATE session_jobs SET state=?,resume_at=?,lease_owner=NULL,'
                                   'lease_until=NULL,document=? WHERE job_id=?',
                                   ('WAITING_RETRY', now, json.dumps(job, ensure_ascii=False), job_id))
                        db.execute('INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)',
                                   (job_id, now, json.dumps(job, ensure_ascii=False)))
                        db.execute('UPDATE flow_tasks SET document=? WHERE task_id=?',
                                   (json.dumps(task, ensure_ascii=False), plan['task_id']))
                        db.execute('INSERT INTO flow_events(task_id,kind,at,document) VALUES (?,?,?,?)',
                                   (plan['task_id'], 'timeout_recovery_prepared', now,
                                    json.dumps(task, ensure_ascii=False)))
                        db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?',
                                   (json.dumps(request, ensure_ascii=False), plan['request_id']))
                        prepared_hashes = json.dumps([_sha(json.dumps(item, ensure_ascii=False).encode())
                                                      for item in (job, task, request)])
                        db.execute("UPDATE timeout_recoveries SET phase='prepared',prepared_hashes=? "
                                   'WHERE job_id=?', (prepared_hashes, job_id))
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
                    phase = 'prepared'
                    if fail_after == 'database': raise RuntimeError('injected stop after database')
                if phase == 'prepared':
                    db.execute("UPDATE timeout_recoveries SET phase='guard_release_pending' WHERE job_id=?",
                               (job_id,))
                    phase = 'guard_release_pending'
                    if fail_after == 'guard_intent': raise RuntimeError('injected stop after guard intent')
                if phase == 'guard_release_pending':
                    guard_path = Path(plan['guard_path'])
                    marker = _guard(guard_path)
                    if marker is not None:
                        if (marker.get('job_id') != job_id or marker.get('queue_root') != str(root / 'sessions')
                                or marker.get('process') != plan['process']):
                            raise ValueError('another execution owns the repository guard')
                        guard_path.unlink()
                    if fail_after == 'guard_unlink': raise RuntimeError('injected stop after guard unlink')
                    db.execute("UPDATE timeout_recoveries SET phase='guard_cleared' WHERE job_id=?", (job_id,))
                    phase = 'guard_cleared'
                    if fail_after == 'guard': raise RuntimeError('injected stop after guard')
                if phase == 'guard_cleared':
                    def reopen():
                        row = ledger.reservation(plan['reservation_id'])
                        if row['state'] != 'SETTLED' or row['event_id'] != event_id:
                            raise SharedCallError('attempt is not exactly settled')
                        if ledger.db.execute("SELECT 1 FROM reservations WHERE group_id=? AND state IN "
                                             "('RESERVED','STARTED','UNKNOWN')", (plan['group_id'],)).fetchone():
                            raise SharedCallError('another reservation still blocks this account')
                        accounts = ledger.db.execute('SELECT state,reason FROM accounts WHERE group_id=?',
                                                     (plan['group_id'],)).fetchall()
                        if accounts and all(row == ('AVAILABLE', None) for row in accounts):
                            return
                        if not accounts or any(row != ('UNKNOWN', 'reconciliation')
                                               for row in accounts):
                            raise SharedCallError('account has another block or cooldown')
                        ledger.db.execute("UPDATE accounts SET state='AVAILABLE',resume_at=NULL,reason=NULL "
                                          'WHERE group_id=?', (plan['group_id'],))
                    ledger._transaction(reopen)
                    if fail_after == 'account': raise RuntimeError('injected stop after account')
                    db.execute("UPDATE timeout_recoveries SET phase='done' WHERE job_id=?", (job_id,))
                    phase = 'done'
                return {'state': phase, 'plan_digest': plan['digest'], 'reservation_id': plan['reservation_id'],
                        'session_id': plan['session_id']}
            finally:
                ledger.close()
        finally:
            db.close()
