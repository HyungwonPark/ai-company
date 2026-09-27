"""PR35 recovery boundary reproduction, synthetic records only.

Run from the exact PR35 source checkout:
  PYTHONPATH=src:. python /path/to/repro_future_resume_at.py

Uses the submitted test fixture and original recovery/ledger code. The two
service_alive references are mocked because this review container has no user
systemd. No provider, model, production service or production DB is accessed.
The manually injected future resume_at is a boundary input, not evidence that
the operating E2 incident had this value or that a normal API sequence made it.
"""

import json
import sqlite3
import time
from unittest.mock import patch

from tests.test_pm_timeout_recovery import TimeoutRecoveryTests
from ai_company.shared_calls import SharedCallLedger


def run(case_name):
    fixture = TimeoutRecoveryTests(
        'test_incomplete_timeout_is_read_only_then_settled_once_for_same_session')
    fixture.setUp()
    try:
        with (patch('ai_company.timeout_recovery.service_alive', return_value=False),
              patch('ai_company.adapters.session_cli.service_alive', return_value=False)):
            original_plan = fixture.plan()
            if case_name == 'after_settlement':
                try:
                    fixture.apply(original_plan['digest'], fail_after='settlement')
                except RuntimeError as exc:
                    if str(exc) != 'injected stop after settlement':
                        raise
            future = time.time() + 3600
            with sqlite3.connect(fixture.ledger_path) as db:
                db.execute('UPDATE accounts SET resume_at=? WHERE group_id=?',
                           (future, 'group'))
                before = db.execute(
                    'SELECT state,resume_at,reason,calls FROM accounts WHERE group_id=?',
                    ('group',)).fetchone()
            with sqlite3.connect(fixture.baseline) as db:
                baseline = db.execute(
                    'SELECT state,resume_at,reason,calls FROM accounts WHERE group_id=?',
                    ('group',)).fetchone()
            plan = fixture.plan() if case_name == 'before_diagnosis' else original_plan
            result = fixture.apply(plan['digest'])
            ledger = SharedCallLedger(fixture.ledger_path)
            try:
                after = ledger.account('codex', 'credential', 'group')
                reservation = ledger.reserve('new-reservation-probe', 'synthetic-review',
                                             'codex', 'credential', 'group')
                probe_state = reservation['state']
                ledger.cancel_unstarted('new-reservation-probe', 'synthetic-review',
                    evidence='queue_unclaimed_no_guard_no_process')
            finally:
                ledger.close()
            print(json.dumps({
                'case': case_name,
                'scope': 'synthetic boundary; service_alive mocked; no model calls',
                'baseline': baseline,
                'current_before_recovery': before,
                'diagnosis': plan['decision'],
                'recovery_result': result['state'],
                'account_after': after,
                'new_unstarted_reservation': probe_state,
            }, ensure_ascii=False, sort_keys=True))
    finally:
        fixture.doCleanups()


for name in ('before_diagnosis', 'after_settlement'):
    run(name)
