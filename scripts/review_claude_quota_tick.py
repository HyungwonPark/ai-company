#!/usr/bin/env python3
"""One short, fail-closed PR review quota check; install only with reviewed manifest."""
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import secrets


TRUSTED_HASHES = {
    'runner.py': '259c9b287c6b2fa4a3771d97e5f88df58f4afbd762d3608ee30f25290a62a862',
    'claude_control.py': '9bd51e97e31335235dfd12dacac0b5eb96a0215a130b21b9c2eace37836c1106',
    'shared_calls.py': '0eac176791c85b24173a79204335a869883f2047f751b9f442e65d3253cbcaf9',
}


def trusted_hashes(trusted_dir):
    """Read the installed package's immutable file manifest when present."""
    manifest = Path(trusted_dir) / 'manifest.json'
    if not manifest.is_file():
        return dict(TRUSTED_HASHES)
    try:
        value = json.loads(manifest.read_text())
        files = value['files']
        result = {name: files[name] for name in TRUSTED_HASHES}
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit('installed reviewer manifest is invalid') from None
    if any(not isinstance(item, str) or len(item) != 64 for item in result.values()):
        raise SystemExit('installed reviewer manifest has invalid hashes')
    return result


def load_runner(path):
    spec = importlib.util.spec_from_file_location('trusted_review_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_attempt(root, runner, *, repo):
    if root.exists():
        return root
    return runner.latest_quota_archive(root, repo=repo)


def recover_unstarted(manifest, ledger):
    """Free only a reservation that provably never entered the executor."""
    root = Path(manifest['attempt_dir'])
    if not root.exists() or (root / 'receipt.json').exists():
        return False
    lock = os.open(root.with_name(root.name + '.lock'),
                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        rows = ledger.db.execute(
            "SELECT reservation_id,state,started_at,process_identity FROM reservations "
            "WHERE owner=? AND group_id=? AND state IN ('RESERVED','STARTED','UNKNOWN')",
            (str(root), manifest['quota_group'])).fetchall()
        if len(rows) > 1:
            return False
        if not rows:
            rows = ledger.db.execute(
                "SELECT reservation_id,state,started_at,process_identity FROM reservations "
                "WHERE owner=? AND group_id=? AND state='CANCELLED' LIMIT 1",
                (str(root), manifest['quota_group'])).fetchall()
        if (len(rows) != 1 or rows[0][1] not in ('RESERVED', 'CANCELLED')
                or rows[0][2] is not None or rows[0][3] is not None
                or any((root / name).exists() for name in ('events.jsonl', 'events.live.jsonl'))):
            return False
        facts = root / 'facts.jsonl'
        if facts.exists():
            try:
                observed = [json.loads(line) for line in facts.read_text().splitlines()]
            except (OSError, ValueError):
                return False
            if any(item.get('state') in ('prompt_delivery_started', 'prompt_delivered') for item in observed):
                return False
        if rows[0][1] == 'RESERVED':
            ledger.cancel_unstarted(rows[0][0], str(root),
                                    evidence='queue_unclaimed_no_guard_no_process')
        root.rename(root.with_name(root.name + '-unstarted-' + secrets.token_hex(4)))
        return True
    finally:
        os.close(lock)


def inspect(manifest, runner, ledger, *, now):
    root = Path(manifest['attempt_dir'])
    try:
        attempt = current_attempt(root, runner, repo=manifest['checkout'])
        if not attempt or not (attempt / 'receipt.json').is_file():
            return {'state': 'NEEDS_RECONCILIATION', 'reason': 'attempt_receipt_missing'}
        receipt = runner.effective_receipt(attempt)
        if (receipt.get('pr') != manifest['pr'] or receipt.get('head') != manifest['head']
                or receipt.get('base') != manifest['base']
                or receipt.get('diff_sha256') != manifest['diff_sha256']):
            raise ValueError('review binding changed')
        reservation_id = receipt['shared_reservation_id']
        row = ledger.reservation(reservation_id)
        if (attempt != root and (attempt.parent != root.parent
                or not attempt.name.startswith(root.name + '-quota-'))):
            raise ValueError('archived review is outside the pinned attempt')
        if (not row or row['owner'] != str(root) or row['group_id'] != manifest['quota_group']):
            raise ValueError('shared reservation differs from attempt')
        if receipt.get('completion_verified'):
            if not runner.completed_review_verified(attempt, receipt, row,
                                                    repo=manifest['checkout']):
                raise ValueError('completed review evidence is inconsistent')
            return {'state': 'DONE', 'attempt': str(attempt), 'reservation_id': reservation_id}
        if row['state'] != 'SETTLED' or row['event_id'] != receipt.get('shared_settlement_event'):
            raise ValueError('previous review is not settled')
        result = json.loads(row['result'])
        reset = result.get('reset_at')
        if (result.get('category') != 'quota' or not isinstance(reset, (int, float))
                or isinstance(reset, bool) or not math.isfinite(reset) or reset <= 0):
            raise ValueError('previous result is not a verified quota wait')
        account = ledger.account('claude', manifest['credential_ref'], manifest['quota_group'])
        if account['state'] in ('UNKNOWN', 'DISABLED'):
            raise ValueError('shared account restriction needs reconciliation')
        if (account['state'] == 'COOLDOWN' and (account['resume_at'] is None
                or not math.isfinite(account['resume_at']))):
            raise ValueError('shared account cooldown is incomplete')
        if account['state'] == 'COOLDOWN' and account['resume_at'] > now:
            next_check = max(reset, account['resume_at'])
        else:
            next_check = reset
        bound = runner.quota_resume_verified(attempt, pr_number=manifest['pr'],
                   pr_url=manifest['pr_url'], head=manifest['head'], base=manifest['base'],
                   diff_sha256=manifest['diff_sha256'], ledger=ledger,
                   quota_group=manifest['quota_group'], now=max(now, next_check))
        if not bound:
            raise ValueError('terminal quota proof or review binding is incomplete')
        checkpoint = runner.previous_checkpoint(attempt, head=manifest['head'],
                      base=manifest['base'], diff_sha256=manifest['diff_sha256'],
                      files=runner.patch_files((attempt / 'pr.diff').read_text()),
                      repo=manifest['checkout'])
        if checkpoint['remaining_files'] == [] and not checkpoint['findings']:
            # A quota result is never final even when all file reads were seen.
            pass
        common = {'attempt': str(attempt), 'reservation_id': reservation_id,
                  'reset_at': reset, 'checkpoint_sha256': runner.hashlib.sha256(
                       json.dumps(checkpoint, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        if now < next_check:
            return {'state': 'WAITING_QUOTA', 'next_check_at': next_check, **common}
        return {'state': 'READY', 'next_check_at': now, **common}
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
        return {'state': 'NEEDS_RECONCILIATION', 'reason': type(exc).__name__}


def resume_command(runner_file, manifest, status):
    return [sys.executable, '-I', '-B', str(runner_file), str(manifest['pr']), '--resume-quota',
            '--shared-call-ledger', manifest['ledger'], '--credential-ref', manifest['credential_ref'],
            '--quota-group', manifest['quota_group'], '--adoption-receipt', manifest['adoption_receipt'],
            '--expected-head', manifest['head'], '--expected-base', manifest['base'],
            '--expected-diff-sha256', manifest['diff_sha256'], '--expected-pr-url', manifest['pr_url'],
            '--expected-attempt-dir', manifest['attempt_dir'],
            '--expected-previous-attempt', status['attempt'],
            '--expected-previous-reservation-id', status['reservation_id']]


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: review_claude_quota_tick.py MANIFEST')
    manifest_path = Path(sys.argv[1]).resolve()
    if manifest_path.stat().st_mode & 0o077:
        raise SystemExit('review manifest must be private')
    manifest = json.loads(manifest_path.read_text())
    required = {'pr', 'pr_url', 'head', 'base', 'diff_sha256', 'attempt_dir',
                'runner_file', 'checkout', 'ledger', 'credential_ref', 'quota_group', 'adoption_receipt'}
    if set(manifest) != required or not isinstance(manifest['pr'], int) or manifest['pr'] < 1:
        raise SystemExit('incomplete review resume manifest')
    runner_file = Path(manifest['runner_file']).resolve()
    trusted_dir = Path.home() / '.local/libexec/ai-company-pr-review'
    if runner_file != (trusted_dir / 'runner.py').resolve():
        raise SystemExit('resume requires the installed trusted runner')
    expected_hashes = trusted_hashes(trusted_dir)
    for name, expected in expected_hashes.items():
        source = trusted_dir / name
        if (source.is_symlink() or not source.is_file()
                or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
            raise SystemExit('trusted reviewer code differs from the reviewed package')
    adoption = json.loads(Path(manifest['adoption_receipt']).read_text())
    if (adoption.get('review_runner_sha256') != expected_hashes['runner.py']
            or adoption.get('review_relay_sha256') != expected_hashes['claude_control.py']
            or adoption.get('shared_calls_sha256') != expected_hashes['shared_calls.py']):
        raise SystemExit('shared caller adoption receipt differs from the reviewed package')
    runner = load_runner(runner_file)
    if not runner.shared_adoption_verified(Path(manifest['adoption_receipt']), Path(manifest['ledger'])):
        raise SystemExit('installed reviewer and shared ledger adoption are not verified')
    lock = os.open(manifest_path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        ledger = runner.SharedCallLedger(Path(manifest['ledger']))
        try:
            recover_unstarted(manifest, ledger)
            status = inspect(manifest, runner, ledger, now=time.time())
        finally:
            ledger.close()
        state_file = manifest_path.with_suffix('.state.json')
        if status['state'] == 'READY':
            running = status
            runner.replace_private(state_file, {**status, 'state': 'RUNNING', 'at': time.time()})
            argv = resume_command(runner_file, manifest, status)
            # The runner owns its 30-minute child timeout and process-group
            # cleanup. The systemd unit owns the outer cgroup timeout.
            subprocess.run(argv, cwd=manifest['checkout'], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ledger = runner.SharedCallLedger(Path(manifest['ledger']))
            try:
                status = inspect(manifest, runner, ledger, now=time.time())
            finally:
                ledger.close()
            if status['state'] == 'READY' and status.get('attempt') == running['attempt']:
                status = {'state': 'NEEDS_RECONCILIATION',
                          'reason': 'runner_returned_without_new_attempt'}
        if not state_file.exists() or json.loads(state_file.read_text()).get('state') != status['state']:
            runner.replace_private(state_file, {**status, 'at': time.time()})
        return 0 if status['state'] in ('DONE', 'WAITING_QUOTA') else 1
    finally:
        os.close(lock)


if __name__ == '__main__':
    raise SystemExit(main())
