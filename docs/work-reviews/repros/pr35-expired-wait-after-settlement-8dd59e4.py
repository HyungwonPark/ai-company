"""Synthetic PR35 boundary: a wait added after settlement remains blocked after expiry.

Run from source HEAD 8dd59e4: PYTHONPATH=src:. python /path/to/this_file.py
The original fixture/recovery/ledger code is used. Only service_alive observations
and the clock are mocked. No external model or operating records are accessed.
This does not establish that the actual E2 incident had a future resume_at.
"""
import json
import sqlite3
import time
from unittest.mock import patch

from tests.test_pm_timeout_recovery import TimeoutRecoveryTests
from ai_company.shared_calls import SharedCallLedger

fixture = TimeoutRecoveryTests(
    'test_incomplete_timeout_is_read_only_then_settled_once_for_same_session')
fixture.setUp()
try:
    now = time.time()
    with (patch('ai_company.timeout_recovery.service_alive', return_value=False),
          patch('ai_company.adapters.session_cli.service_alive', return_value=False)):
        plan = fixture.plan()
        try:
            fixture.apply(plan['digest'], fail_after='settlement')
        except RuntimeError as exc:
            if str(exc) != 'injected stop after settlement':
                raise
        with sqlite3.connect(fixture.ledger_path) as db:
            settlements_before = db.execute(
                'SELECT event_id,reservation_id,result,at FROM settlement_events ORDER BY event_id').fetchall()
        future = now + 3600
        with sqlite3.connect(fixture.ledger_path) as db:
            db.execute("UPDATE accounts SET resume_at=? WHERE group_id='group'", (future,))
        observations = []
        for instant in (now, future + 1):
            with patch('ai_company.timeout_recovery.time.time', return_value=instant):
                try:
                    outcome = fixture.apply(plan['digest'])
                except Exception as exc:
                    outcome = {'exception': type(exc).__name__, 'message': str(exc)}
                fresh = fixture.plan()
                observations.append({'wait_expired': instant > future,
                                     'apply': outcome,
                                     'fresh_diagnosis': fresh['decision'],
                                     'fresh_digest': fresh['digest']})
        with sqlite3.connect(fixture.db_path) as db:
            phase = db.execute('SELECT phase FROM timeout_recoveries').fetchone()[0]
        ledger = SharedCallLedger(fixture.ledger_path)
        try:
            account = ledger.account('codex', 'credential', 'group')
            settlements_after = ledger.db.execute(
                'SELECT event_id,reservation_id,result,at FROM settlement_events ORDER BY event_id').fetchall()
        finally:
            ledger.close()
        print(json.dumps({'observations': observations, 'phase': phase,
                          'original_plan_digest': plan['digest'],
                          'settlement_count': len(settlements_after),
                          'settlements_unchanged': settlements_before == settlements_after,
                          'account': account}, ensure_ascii=False, sort_keys=True))
finally:
    fixture.doCleanups()
