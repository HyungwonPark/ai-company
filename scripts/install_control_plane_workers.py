"""Review/install two new user workers; default is a read-only preflight.

Use an operator-owned proposal directory containing manifest.json and its files.
Rollback removes only matching installed files. Execution/usage facts are retained.
"""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def publish(proposal, target, content, *, replace=False):
    # Stage on the same filesystem, outside runtime/unit directories. A killed
    # writer cannot leave a partially written target that blocks recovery.
    descriptor, temporary = tempfile.mkstemp(prefix='.install-', dir=proposal)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, target)
        else:
            os.link(temporary, target)  # exclusive: never replace an existing file
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def operate(proposal, *, install=False, rollback=False):
    manifest = json.loads((proposal / 'manifest.json').read_text())
    units = ['ai-company-automation.service', 'ai-company-translation.service']
    names = set(units + ['automation.json', 'translation.json', 'worker.env'])
    assert set(manifest['files']) == names
    for name, expected in manifest['files'].items():
        assert sha(proposal / name) == expected, f'proposal changed: {name}'
    release = Path(manifest['release'])
    alias = Path(manifest['release_alias'])
    runtime = Path(manifest['runtime'])
    unit_root = Path.home() / '.config/systemd/user'
    receipt = proposal / 'installed.json'
    database = Path(manifest['state']) / 'sessions/sessions.sqlite'
    group = 'pilot-local-claude'
    journal = json.loads(receipt.read_text()) if receipt.exists() else None
    if journal is not None:
        assert journal['manifest_sha256'] == sha(proposal / 'manifest.json'), 'receipt belongs to another proposal'
        assert journal['credential_mapping'] == group

    def record(state, error=None):
        nonlocal journal
        value = journal or {'manifest_sha256': sha(proposal / 'manifest.json'),
                            'credential_mapping': group}
        value = {**value, 'state': state, 'error': error,
                 'events': [*value.get('events', []), {'state': state, 'at': time.time()}]}
        publish(proposal, receipt, (json.dumps(value) + '\n').encode(), replace=journal is not None)
        journal = value

    def validate_paths():
        # An existing receipt proves the intent of this exact installation,
        # including legacy receipts left behind by the old failed cleanup.
        for path in [alias, runtime, *[unit_root / name for name in units]]:
            if path.exists() or path.is_symlink():
                assert journal is not None, f'existing path: {path}'
        if alias.exists() or alias.is_symlink():
            assert alias.is_symlink() and alias.resolve() == release, 'release link changed'
        if runtime.exists() or runtime.is_symlink():
            assert runtime.is_dir() and not runtime.is_symlink(), 'runtime directory changed'
        for name in names:
            target = unit_root / name if name in units else runtime / name
            if target.exists() or target.is_symlink():
                assert target.is_file() and not target.is_symlink(), f'installed file type changed: {name}'
                assert sha(target) == manifest['files'][name], f'installed file changed: {name}'

    def validate_registration(db):
        credential = db.execute('SELECT credential_ref,group_id FROM credential_groups WHERE provider=?', ('claude',)).fetchone()
        quota = db.execute('SELECT state FROM quota_groups WHERE group_id=?', (group,)).fetchone()
        if credential is not None or quota is not None:
            assert journal is not None and credential == (group, group) and quota is not None, 'existing credential registration differs'
        return credential is not None

    def stop():
        validate_paths()
        loaded = [unit for unit in units if command('systemctl', '--user', 'show', unit,
                                                   '-p', 'LoadState', '--value') != 'not-found']
        if loaded:
            command('systemctl', '--user', 'disable', '--now', *loaded)
        for unit in units:
            state = command('systemctl', '--user', 'show', unit, '-p', 'ActiveState', '--value')
            assert state in ('inactive', 'failed'), f'worker termination unconfirmed: {unit}'

    validate_paths()
    if rollback:
        assert journal is not None, 'no owned installation to roll back'
        record('stopping')
        try:
            stop()
            # Missing files are valid interruption boundaries. Remove matching
            # files only, retain all execution/credential/cooldown records.
            for name in names:
                target = unit_root / name if name in units else runtime / name
                if target.exists():
                    assert sha(target) == manifest['files'][name]
                    target.unlink()
            if alias.is_symlink():
                alias.unlink()
            if runtime.exists() and not list(runtime.iterdir()):
                runtime.rmdir()
            command('systemctl', '--user', 'daemon-reload')
            record('rolled_back')
        except BaseException as exc:
            record('rollback_failed', type(exc).__name__)
            raise
        print(json.dumps({'status': 'STOPPED', 'execution_facts_retained': True,
                          'credential_mapping_retained': True}))
        return

    if journal is None:
        for name in units:
            assert command('systemctl', '--user', 'show', name, '-p', 'LoadState', '--value') == 'not-found'
    assert (release / '.venv/bin/python').is_file()
    assert command('/home/edward/.local/bin/claude', '--version') == '2.1.270 (Claude Code)'
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        validate_registration(db)
    if not install:
        print(json.dumps({'status': 'PREPARED_ONLY', 'units': units, 'model_calls': 0,
                          'installation_state': journal.get('state', 'interrupted') if journal else 'new'}))
        return

    # Record ownership before any side effect. Failed/interrupted attempts keep
    # this journal and their matching files so both retry and rollback work.
    record('installing')
    try:
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        unit_root.mkdir(parents=True, exist_ok=True)
        if not alias.is_symlink():
            alias.symlink_to(release, target_is_directory=True)
        for name in sorted(names):
            target = unit_root / name if name in units else runtime / name
            if not target.exists():
                publish(proposal, target, (proposal / name).read_bytes())
        validate_paths()
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not validate_registration(db):
                db.execute('INSERT INTO credential_groups VALUES (?,?,?)', ('claude', group, group))
                db.execute("INSERT INTO quota_groups VALUES (?,'AVAILABLE',NULL,NULL)", (group,))
        command('systemctl', '--user', 'daemon-reload')
        command('systemctl', '--user', 'enable', '--now', *units)
        record('installed')
    except BaseException as exc:
        record('failed', type(exc).__name__)
        try:
            stop()
        except BaseException as stop_error:
            record('stop_failed', type(stop_error).__name__)
        raise
    print(json.dumps({'status': 'STARTED', 'units': units, 'execution_facts_retained': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--install', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    proposal = args.proposal.resolve(strict=True)
    # Lock an existing file, so the default preflight makes no filesystem writes.
    with (proposal / 'manifest.json').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        operate(proposal, install=args.install, rollback=args.rollback)


if __name__ == '__main__':
    main()
