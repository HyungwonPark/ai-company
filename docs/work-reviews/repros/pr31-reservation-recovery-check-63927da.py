"""Read-only R31-2 proof. Usage: python pr31-reservation-recovery-check-63927da.py /path/to/source-export.

Imports the selected shared_calls.py and writes databases only under TemporaryDirectory.
Uses no provider calls, repository mutations, or operating database paths.
"""
import importlib.util
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    path = Path(sys.argv[1]) / 'src/ai_company/shared_calls.py'
    spec = importlib.util.spec_from_file_location('reviewed_shared_calls', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Ledger = module.SharedCallLedger
    checks = []
    proof = 'queue_unclaimed_no_guard_no_process'
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'calls.db'
        accounts = [
            ('codex', 'primary', 'a', 'AVAILABLE', None, None, 0, 0, 0, 0),
            ('codex', 'alias', 'a', 'AVAILABLE', None, None, 0, 0, 0, 0),
        ]
        ledger = Ledger.initialize(database, accounts, clock=lambda: 100)
        try:
            # Original defect: retrying the same cancelled ID could never start.
            ledger.reserve('original', 'owner', 'codex', 'primary', 'a')
            ledger.cancel_unstarted('original', 'owner', evidence=proof)
            assert ledger.reserve('original', 'owner', 'codex', 'primary', 'a')['state'] == 'RESERVED'
            ledger.started('original', 'owner', {'pid': 123})
            ledger.settle('original', 'owner', 'original-done',
                          {'category': 'success', 'duration_seconds': 1, 'total_cost_usd': 0}, terminated=True)
            checks.append('same cancelled ID reopens and starts')

            for reservation in ('r1', 'r2'):
                ledger.reserve(reservation, reservation, 'codex', 'primary', 'a')
                ledger.cancel_unstarted(reservation, reservation, evidence=proof)

            gate = threading.Barrier(2)

            def reserve(reservation):
                independent = Ledger(database, clock=lambda: 101)
                try:
                    gate.wait()
                    return independent.reserve(reservation, reservation, 'codex', 'primary', 'a')['state']
                except module.CapacityUnavailable:
                    return 'CAPACITY'
                finally:
                    independent.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve, ['r1', 'r2']))
            assert sorted(outcomes) == ['CAPACITY', 'RESERVED'], outcomes
            assert ledger.db.execute("SELECT count(*) FROM reservations WHERE state='RESERVED'").fetchone()[0] == 1
            checks.append('different cancelled IDs sharing account race: only one reopened')

            holder = 'r1' if outcomes[0] == 'RESERVED' else 'r2'
            ledger.cancel_unstarted(holder, holder, evidence=proof)
            ledger.db.execute("UPDATE accounts SET state='COOLDOWN',resume_at=200")
            try:
                ledger.reserve(holder, holder, 'codex', 'primary', 'a')
                raise AssertionError('cooldown bypass')
            except module.CapacityUnavailable:
                pass
            assert ledger.reservation(holder)['state'] == 'CANCELLED'
            checks.append('cooldown blocks reopening and preserves cancellation')

            ledger.db.execute("UPDATE accounts SET state='AVAILABLE',resume_at=NULL")
            gate = threading.Barrier(2)
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(reserve, [holder, holder]))
            assert outcomes == ['RESERVED', 'RESERVED'], outcomes
            count = ledger.db.execute("SELECT count(*) FROM reservations WHERE reservation_id LIKE ?",
                                      (holder + ':cancel:%',)).fetchone()[0]
            assert count == 2, count  # Two historical cancellations, only one archive per reopening.
            assert ledger.db.execute("SELECT count(*) FROM reservations WHERE state='RESERVED'").fetchone()[0] == 1
            checks.append('same owner and ID race: one reservation and one new cancellation archive')

            try:
                ledger.reserve(holder, 'other-owner', 'codex', 'primary', 'a')
                raise AssertionError('owner changed')
            except module.SharedCallError:
                pass
            ledger.started(holder, holder, {'pid': 124})
            try:
                ledger.cancel_unstarted(holder, holder, evidence=proof)
                raise AssertionError('started reservation cancelled')
            except module.SharedCallError:
                pass
            fact = {'category': 'success', 'duration_seconds': 2, 'total_cost_usd': 0}
            assert ledger.settle(holder, holder, 'done', fact, terminated=True)
            assert not ledger.settle(holder, holder, 'done', fact, terminated=True)
            assert ledger.account('codex', 'alias', 'a')['calls'] == 2
            assert ledger.db.execute('SELECT count(*) FROM settlement_events').fetchone()[0] == 2
            checks.append('owner preserved; started cancellation blocked; settlement once')

            ledger.reserve_host('h1', 'host')
            ledger.cancel_unstarted('h1', 'host', evidence=proof)
            ledger.reserve_host('h2', 'host')
            ledger.reserve_host('h3', 'host')
            try:
                ledger.reserve_host('h1', 'host')
                raise AssertionError('host capacity bypass')
            except module.CapacityUnavailable:
                pass
            ledger.cancel_unstarted('h3', 'host', evidence=proof)
            assert ledger.reserve_host('h1', 'host')['state'] == 'RESERVED'
            checks.append('host-only reopening rechecks two-slot capacity')
        finally:
            ledger.close()
    for check in checks:
        print('PASS:', check)


if __name__ == '__main__':
    main()
