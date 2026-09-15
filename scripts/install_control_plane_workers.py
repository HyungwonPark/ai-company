"""Review/install two new user workers; default is a read-only preflight.

Use an operator-owned proposal directory containing manifest.json and its files.
Rollback removes only matching installed files. Execution/usage facts are retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--install', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    proposal = args.proposal.resolve(strict=True)
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

    if args.rollback:
        installed = json.loads(receipt.read_text())
        assert installed['manifest_sha256'] == sha(proposal / 'manifest.json')
        assert alias.is_symlink() and alias.resolve() == release
        for name in names:
            target = unit_root / name if name in units else runtime / name
            assert sha(target) == manifest['files'][name], f'installed file changed: {name}'
        # Wait for bounded current executions to end; never clear queue/usage data.
        command('systemctl', '--user', 'disable', '--now', *units)
        for name in units:
            (unit_root / name).unlink()
        alias.unlink()
        for name in names - set(units):
            (runtime / name).unlink()
        runtime.rmdir()
        command('systemctl', '--user', 'daemon-reload')
        print(json.dumps({'status': 'STOPPED', 'execution_facts_retained': True,
                          'credential_mapping_retained': True}))
        return

    assert not receipt.exists(), 'proposal already installed'
    for path in [alias, runtime, *[unit_root / name for name in units]]:
        assert not path.exists() and not path.is_symlink(), f'existing path: {path}'
    for name in units:
        assert command('systemctl', '--user', 'show', name, '-p', 'LoadState', '--value') == 'not-found'
    assert (release / '.venv/bin/python').is_file()
    assert command('/home/edward/.local/bin/claude', '--version') == '2.1.270 (Claude Code)'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT 1 FROM credential_groups WHERE provider=?', ('claude',)).fetchone() is None
        assert db.execute('SELECT 1 FROM quota_groups WHERE group_id=?', (group,)).fetchone() is None
    if not args.install:
        print(json.dumps({'status': 'PREPARED_ONLY', 'units': units, 'model_calls': 0}))
        return

    # Create only new paths; never overwrite another operator's configuration.
    runtime.mkdir(parents=True, mode=0o700)
    unit_root.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(release, target_is_directory=True)
    created = []
    try:
        for name in sorted(names):
            target = unit_root / name if name in units else runtime / name
            with target.open('xb') as output:
                output.write((proposal / name).read_bytes())
            target.chmod(0o600)
            created.append(target)
        with sqlite3.connect(database) as db:
            db.execute('BEGIN IMMEDIATE')
            # Unique constraints also reject a registration racing the preflight.
            db.execute('INSERT INTO credential_groups VALUES (?,?,?)', ('claude', group, group))
            db.execute("INSERT INTO quota_groups VALUES (?,'AVAILABLE',NULL,NULL)", (group,))
        receipt.write_text(json.dumps({'manifest_sha256': sha(proposal / 'manifest.json'),
                                       'credential_mapping': group}) + '\n')
        receipt.chmod(0o600)
        command('systemctl', '--user', 'daemon-reload')
        command('systemctl', '--user', 'enable', '--now', *units)
    except BaseException:
        # Even failed starts may have saved real execution facts. Preserve them.
        subprocess.run(['systemctl', '--user', 'disable', '--now', *units], check=False)
        for target in created:
            if target.exists() and sha(target) == manifest['files'][target.name]:
                target.unlink()
        if alias.is_symlink() and alias.resolve() == release:
            alias.unlink()
        if runtime.exists() and not list(runtime.iterdir()):
            runtime.rmdir()
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
        raise
    print(json.dumps({'status': 'STARTED', 'units': units, 'execution_facts_retained': True}))


if __name__ == '__main__':
    main()
