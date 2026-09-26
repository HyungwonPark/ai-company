"""Current-code diagnostic, not a passing regression test. No external calls.
Pass a read-only checkout of PR31 6530176 as the only argument.
"""
from pathlib import Path
import importlib.util
import tempfile
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: python pr31-cancelled-reservation-6530176.py /path/to/repo')
source = Path(sys.argv[1]).resolve() / 'src/ai_company/shared_calls.py'
spec = importlib.util.spec_from_file_location('shared_calls_under_review', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as root:
    ledger = module.SharedCallLedger.initialize(Path(root) / 'calls.db', [
        ('codex', 'primary', 'account', 'AVAILABLE', None, None, 0, 0, 0, 0),
    ])
    first = ledger.reserve('unchanged-job-attempt-1', 'queue', 'codex', 'primary', 'account')
    assert first['state'] == 'RESERVED'
    # Dispatcher 846-851 does this after the queue reports BUSY/IDLE without claiming.
    ledger.cancel_unstarted('unchanged-job-attempt-1', 'queue',
        evidence='queue_unclaimed_no_guard_no_process')
    # Dispatcher 826 derives the same ID because attempt_count was never incremented.
    retry = ledger.reserve('unchanged-job-attempt-1', 'queue', 'codex', 'primary', 'account')
    assert retry['state'] == 'CANCELLED', retry
    try:
        ledger.started('unchanged-job-attempt-1', 'queue',
            {'kind': 'executor_invocation', 'job_id': 'job', 'attempt': 1})
    except module.SharedCallError as exc:
        print('CONFIRMED: unstarted cancellation -> reserve returns CANCELLED ->', exc)
    else:
        raise AssertionError('Expected actual current-code retry failure')
    ledger.close()
