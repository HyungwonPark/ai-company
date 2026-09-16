"""Installer failure/recovery in real temporary files/SQLite, with fake systemd."""
import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install_control_plane_workers.py'
spec = importlib.util.spec_from_file_location('worker_installer', SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WorkerInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.proposal = self.home / 'proposal'; self.proposal.mkdir()
        self.release = self.home / 'release'
        (self.release / '.venv/bin').mkdir(parents=True)
        (self.release / '.venv/bin/python').write_text('fixture')
        self.alias = self.home / 'current'
        self.runtime = self.home / 'runtime'
        self.unit_root = self.home / '.config/systemd/user'
        self.units = ['ai-company-automation.service', 'ai-company-translation.service']
        files = {}
        for name in self.units + ['automation.json', 'translation.json', 'worker.env']:
            (self.proposal / name).write_text('fixture ' + name)
            files[name] = hashlib.sha256((self.proposal / name).read_bytes()).hexdigest()
        self.state = self.home / 'state'; (self.state / 'sessions').mkdir(parents=True)
        self.database = self.state / 'sessions/sessions.sqlite'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript('CREATE TABLE credential_groups(provider TEXT PRIMARY KEY, credential_ref TEXT, group_id TEXT);'
                'CREATE TABLE quota_groups(group_id TEXT PRIMARY KEY,state TEXT,resume_at REAL,reason TEXT);')
        (self.proposal / 'manifest.json').write_text(json.dumps(dict(release=str(self.release),
            release_alias=str(self.alias), runtime=str(self.runtime), state=str(self.state), files=files)))
        self.active = set(); self.enabled = set(); self.fail = None; self.calls = []

    def command(self, *args):
        self.calls.append(args)
        if args[0].endswith('/claude'):
            return '2.1.270 (Claude Code)'
        if 'show' in args:
            unit = args[3]
            if 'ActiveState' in args:
                return 'active' if unit in self.active else 'inactive'
            return 'loaded' if (self.unit_root / unit).exists() else 'not-found'
        if 'disable' in args:
            if self.fail == 'stop':
                self.fail = None
                raise RuntimeError('simulated stop failure')
            self.active.clear(); self.enabled.clear()
        if 'daemon-reload' in args and self.fail == 'reload':
            self.fail = None
            raise RuntimeError('simulated reload failure')
        if 'enable' in args:
            self.active.add(self.units[0]); self.enabled.add(self.units[0])
            if self.fail == 'enable':
                self.fail = None
                raise RuntimeError('simulated failure after first worker starts')
            self.active.update(self.units); self.enabled.update(self.units)
        return ''

    def invoke(self, mode=None):
        argv = [str(SCRIPT), '--proposal', str(self.proposal)] + ([mode] if mode else [])
        def run(args, **kwargs):
            self.command(*args)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(Path, 'home', return_value=self.home), patch.object(installer, 'command', self.command), \
             patch.object(installer.subprocess, 'run', side_effect=run), \
             patch.object(sys, 'argv', argv), patch('builtins.print'):
            installer.main()

    def set_cooldown(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE quota_groups SET state='COOLDOWN',resume_at=9999999999,reason='real saved quota'")

    def assert_cooldown_preserved(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT state,resume_at,reason FROM quota_groups').fetchall(),
                [('COOLDOWN', 9999999999, 'real saved quota')])
            self.assertEqual(db.execute('SELECT count(*) FROM credential_groups').fetchone()[0], 1)

    def test_failed_install_can_retry_without_resetting_saved_quota(self):
        self.fail = 'reload'
        with self.assertRaises(RuntimeError):
            self.invoke('--install')
        self.set_cooldown()
        self.invoke('--install')
        self.assertEqual(self.active, set(self.units))
        self.assert_cooldown_preserved()

    def test_failed_start_can_rollback_with_missing_files_then_reinstall(self):
        self.fail = 'enable'
        with self.assertRaises(RuntimeError):
            self.invoke('--install')
        self.assertFalse(self.active)
        # Covers a previously interrupted/manual cleanup and the old broken receipt.
        (self.runtime / 'worker.env').unlink(missing_ok=True)
        self.set_cooldown()
        self.invoke('--rollback')
        self.invoke('--rollback')
        self.assertFalse(self.alias.exists()); self.assertFalse(self.runtime.exists())
        self.assertFalse(self.enabled)
        self.invoke('--install')
        self.assertEqual(self.active, set(self.units)); self.assert_cooldown_preserved()

    def test_unrelated_existing_or_changed_file_is_never_overwritten(self):
        self.runtime.mkdir(); foreign = self.runtime / 'worker.env'; foreign.write_text('unrelated')
        with self.assertRaises((AssertionError, RuntimeError)):
            self.invoke('--install')
        self.assertEqual(foreign.read_text(), 'unrelated')
        self.assertFalse((self.proposal / 'installed.json').exists())
        foreign.unlink(); self.runtime.rmdir()
        self.fail = 'reload'
        with self.assertRaises(RuntimeError): self.invoke('--install')
        foreign.write_text('operator changed this')
        for operation in ['--install', '--rollback']:
            with self.assertRaises((AssertionError, RuntimeError)): self.invoke(operation)
        self.assertEqual(foreign.read_text(), 'operator changed this')

    def test_rollback_stop_failure_keeps_files_for_safe_retry(self):
        self.invoke('--install')
        self.fail = 'stop'
        with self.assertRaises(RuntimeError): self.invoke('--rollback')
        self.assertTrue(self.alias.is_symlink()); self.assertTrue((self.runtime / 'worker.env').is_file())
        self.invoke('--rollback')
        self.assertFalse(self.active); self.assertFalse(self.runtime.exists())

    def test_old_receipt_with_removed_files_is_recoverable(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO credential_groups VALUES ('claude','pilot-local-claude','pilot-local-claude')")
            db.execute("INSERT INTO quota_groups VALUES ('pilot-local-claude','COOLDOWN',9999999999,'real saved quota')")
        (self.proposal / 'installed.json').write_text(json.dumps(dict(
            manifest_sha256=hashlib.sha256((self.proposal/'manifest.json').read_bytes()).hexdigest(),
            credential_mapping='pilot-local-claude')))
        self.invoke('--rollback')
        self.invoke('--install')
        self.assertEqual(self.active, set(self.units)); self.assert_cooldown_preserved()

    def test_killed_installer_releases_lock_and_can_resume_after_registration(self):
        source = '''import importlib.util, pathlib, sys, time
spec=importlib.util.spec_from_file_location('installer', sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
root=pathlib.Path(sys.argv[2]);pathlib.Path.home=classmethod(lambda cls: root)
def command(*args):
 if args[0].endswith('/claude'): return '2.1.270 (Claude Code)'
 if 'show' in args: return 'not-found'
 if 'daemon-reload' in args:
  (root/'interruption-ready').write_text('ready');time.sleep(60)
 return ''
module.command=command
sys.argv=['installer','--proposal',str(root/'proposal'),'--install']
module.main()
'''
        child = subprocess.Popen([sys.executable, '-c', source, str(SCRIPT), str(self.home)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not (self.home/'interruption-ready').exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.home/'interruption-ready').exists())
            with self.assertRaises(BlockingIOError): self.invoke('--install')
            child.kill(); child.wait(timeout=5)
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)
        self.set_cooldown()
        self.invoke('--install')
        self.assertEqual(self.active, set(self.units)); self.assert_cooldown_preserved()

    def test_failure_during_file_publication_has_no_partial_target_and_recovers(self):
        real = installer.publish
        def fail(proposal, target, content, **kwargs):
            if target.name == 'automation.json':
                raise OSError('simulated publication failure')
            return real(proposal, target, content, **kwargs)
        with patch.object(installer, 'publish', side_effect=fail):
            with self.assertRaises(OSError): self.invoke('--install')
        self.assertFalse((self.runtime/'automation.json').exists())
        self.invoke('--install')
        self.assertEqual(self.active, set(self.units))
