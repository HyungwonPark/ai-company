"""No-call recovery preflight and legacy timer fail-closed boundaries."""

from hashlib import sha256
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load('guard_legacy_session_timer')
preflight = load('preflight_pr31_recovery')


class LegacyGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sessions.sqlite'

    def queue(self, count):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE session_jobs(id TEXT PRIMARY KEY, status TEXT)')
            for index in range(count):
                db.execute('INSERT INTO session_jobs VALUES (?,?)', (str(index), 'WAITING_QUOTA'))

    def test_empty_queue_does_not_start_a_model(self):
        self.queue(0)
        before = self.path.read_bytes()
        self.assertEqual(guard.check(self.path), 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_waiting_jobs_stay_in_place_and_block_old_worker(self):
        self.queue(2)
        before = self.path.read_bytes()
        self.assertEqual(guard.check(self.path), 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_and_corrupt_queue_fail_closed(self):
        self.assertEqual(guard.check(self.path), 2)
        self.path.write_bytes(b'not sqlite')
        self.assertEqual(guard.check(self.path), 2)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / 'state-copy'
        (self.state / 'sessions').mkdir(parents=True)
        with closing(sqlite3.connect(self.state / 'sessions/sessions.sqlite')) as db, db:
            db.execute('CREATE TABLE session_jobs(id TEXT PRIMARY KEY)')
        self.legacy = root / 'legacy.sqlite'
        with closing(sqlite3.connect(self.legacy)) as db, db:
            db.execute('CREATE TABLE session_jobs(id TEXT PRIMARY KEY)')
        self.ledger = root / 'ledger.sqlite'
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('CREATE TABLE shared_meta(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO shared_meta VALUES ('schema_version','2')")
            db.execute('CREATE TABLE accounts(group_id TEXT,state TEXT,resume_at REAL,reason TEXT,'
                       'calls INTEGER,runtime_seconds REAL,cost_usd REAL,cost_unknown INTEGER)')
            db.execute("INSERT INTO accounts VALUES ('shared','UNKNOWN',NULL,'unverified',3,10,0,1)")
            db.execute('CREATE TABLE reservations(state TEXT)')
        self.claude = root / 'claude'
        self.claude.write_text('#!/bin/sh\nexit 0\n')
        self.claude.chmod(0o700)
        self.guard = SCRIPTS / 'guard_legacy_session_timer.py'
        self.installed_guard = root / 'installed-guard.py'
        self.installed_guard.write_bytes(self.guard.read_bytes())
        self.unit = root / 'ai-company-quota.service'
        self.guard_execstart = 'ExecStart=/usr/bin/python3 /private/ai-company-legacy-guard.py --database copy.sqlite'
        self.unit.write_text(self.guard_execstart + '\n')
        self.installed_unit = root / 'installed-quota.service'
        self.installed_unit.write_bytes(self.unit.read_bytes())
        self.guard_loaded_argv = '/usr/bin/python3 /private/ai-company-legacy-guard.py --database copy.sqlite'
        self.timer_source = root / 'source.timer'
        self.installed_timer = root / 'installed.timer'
        self.timer_source.write_text('OnUnitActiveSec=1min\n')
        self.installed_timer.write_bytes(self.timer_source.read_bytes())
        for name in ('automation', 'translation', 'catalog'):
            (root / (name + '.json')).write_text('{}')
        for key in ('candidate', 'rollback'):
            (root / key).mkdir()
            (root / key / 'uv.lock').write_text('frozen')
            (root / key / 'pyproject.toml').write_text('[project]\nname="ai-company"\n')
            package = root / key / 'src/ai_company'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
            (package / 'service_worker.py').write_text('entrypoint = "fixture"\n')
            python = root / key / '.venv/bin/python'
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\nexit 0\n')
            python.chmod(0o700)
        self.env = root / 'worker.env'
        self.env.write_text('\n'.join((
            'AI_COMPANY_STATE=' + str(root / 'live-state'),
            'AI_COMPANY_AUTOMATION_CONFIG=' + str(root / 'automation.json'),
            'AI_COMPANY_TRANSLATION_CONFIG=' + str(root / 'translation.json'),
            'AI_COMPANY_EXECUTION_CATALOG=' + str(root / 'catalog.json'),
            'AI_COMPANY_SHARED_CALL_LEDGER=' + str(root / 'operating-ledger.sqlite'),
            'AI_COMPANY_REQUIRE_SHARED_CALLS=1',
            'AI_COMPANY_TRANSLATION_CLI_PATH=' + str(self.claude),
            'AI_COMPANY_TRANSLATION_CLI_SHA256=' + preflight.file_sha(self.claude),
        )))
        self.plan = dict(worker_env=str(self.env), state_copy=str(self.state),
                         worker_path='/private/bin:/usr/bin:/bin',
                         candidate_release=str(root / 'candidate'), rollback_release=str(root / 'rollback'),
                         candidate_package_path=str(root / 'candidate/src/ai_company'),
                         rollback_package_path=str(root / 'rollback/src/ai_company'),
                         candidate_uv_lock_sha256=preflight.file_sha(root / 'candidate/uv.lock'),
                         rollback_uv_lock_sha256=preflight.file_sha(root / 'rollback/uv.lock'),
                         candidate_code_sha256=preflight.release_code_sha(root / 'candidate', root / 'candidate/src/ai_company'),
                         rollback_code_sha256=preflight.release_code_sha(root / 'rollback', root / 'rollback/src/ai_company'),
                         claude_cli=str(self.claude), claude_sha256=preflight.file_sha(self.claude),
                         catalog=str(root / 'catalog.json'), shared_ledger_copy=str(self.ledger),
                         operating_ledger_path=str(root / 'operating-ledger.sqlite'),
                         automation_config_sha256=preflight.file_sha(root / 'automation.json'),
                         translation_config_sha256=preflight.file_sha(root / 'translation.json'),
                         catalog_sha256=preflight.file_sha(root / 'catalog.json'),
                         legacy_queue_copy=str(self.legacy), guard_source=str(self.guard),
                         legacy_queue_live_path=str(root / 'live-legacy.sqlite'),
                         guard_sha256=preflight.file_sha(self.guard),
                         guard_install_path=str(self.installed_guard), guard_execstart=self.guard_execstart,
                         guard_loaded_argv=self.guard_loaded_argv, quota_service=str(self.unit),
                         installed_quota_service=str(self.installed_unit),
                         quota_service_sha256=preflight.file_sha(self.unit),
                         timer_source=str(self.timer_source), installed_timer=str(self.installed_timer))
        self.commands = []

    def fake_run(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[-1] == '--version':
            output = '2.1.270 (Claude Code)\n'
        elif argv[-1] == '--help':
            output = (' '.join(preflight.REQUIRED_CLI_OPTIONS) if argv[0] == str(self.claude)
                      else '--preflight')
        elif '-c' in argv:
            output = (str(Path(argv[0]).parents[2] / 'src/ai_company/__init__.py') + '\n'
                      if 'ai_company.__file__' in argv[-1] else '3.13\n')
        elif argv[:4] == ['systemctl', '--user', 'show', 'ai-company-quota.service']:
            output = (f'FragmentPath={self.installed_unit}\nLoadState=loaded\n'
                      f'ExecStart={{ path=/usr/bin/python3 ; argv[]={self.guard_loaded_argv} ; '
                      'ignore_errors=no ; }}\n')
        elif argv == ['systemctl', '--user', 'show-environment']:
            output = 'HOME=/private\nLANG=C.UTF-8\n'
        elif argv[:3] == ['systemctl', '--user', 'show']:
            output = ('Id=ai-company-automation.service\nMainPID=101\nActiveState=active\n\n'
                      'Id=ai-company-translation.service\nMainPID=102\nActiveState=active\n\n'
                      'Id=ai-company-quota.timer\nMainPID=0\nActiveState=active\n')
        elif '--preflight' in argv:
            output = json.dumps({'status': 'waiting', 'reasons': ['account_unknown']})
        else:
            raise AssertionError('unexpected subprocess')
        return subprocess.CompletedProcess(argv, 0, output, '')

    def test_copied_state_and_unknown_account_stay_waiting_without_calls(self):
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['state'], 'WAITING')
        self.assertEqual(report['checks']['candidate_release']['state'], 'WAITING')
        self.assertEqual(report['checks']['rollback_release']['state'], 'WAITING')
        self.assertEqual(report['checks']['shared_ledger']['reason'], 'unknown_account_retained')
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['operating_writes'], 0)
        self.assertEqual(sum('--preflight' in argv for argv in self.commands), 4)
        self.assertFalse(any('-p' in argv for argv in self.commands if argv[0] == str(self.claude)))

    def test_changed_cli_and_nonempty_legacy_queue_block_without_losing_jobs(self):
        with closing(sqlite3.connect(self.legacy)) as db, db:
            db.execute("INSERT INTO session_jobs VALUES ('waiting')")
        self.plan['claude_sha256'] = sha256(b'different').hexdigest()
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['state'], 'BLOCKED')
        self.assertEqual(report['checks']['environment']['state'], 'BLOCKED')
        self.plan['claude_sha256'] = preflight.file_sha(self.claude)
        self.claude.write_text('#!/bin/sh\nexit 1\n')
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['claude_cli']['reason'], 'service_cli_digest_changed')
        self.assertEqual(report['checks']['legacy_queue']['state'], 'WAITING')
        self.assertEqual(preflight.sqlite_readonly(self.legacy, 'SELECT count(*) FROM session_jobs'), [(1,)])

    def test_live_state_cannot_be_used_as_copy(self):
        text = self.env.read_text().replace(str(self.state.parent / 'live-state'), str(self.state))
        self.env.write_text(text)
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['environment']['state'], 'BLOCKED')
        self.assertEqual(self.commands, [])

    def test_unverified_cli_version_or_missing_required_option_waits(self):
        def version_changed(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv == [str(self.claude), '--version']:
                result.stdout = '2.1.283 (Claude Code)\n'
            return result
        self.assertEqual(preflight.probe_cli(self.claude, self.plan['claude_sha256'],
                                             run=version_changed)[1], 'service_cli_version_unverified')
        def option_missing(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv == [str(self.claude), '--help']:
                result.stdout = '--model --effort'
            return result
        self.assertEqual(preflight.probe_cli(self.claude, self.plan['claude_sha256'],
                                             run=option_missing)[1], 'service_cli_options_unverified')

    def test_duplicate_worker_owner_blocks(self):
        def duplicate(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv[:3] == ['systemctl', '--user', 'show']:
                result.stdout = result.stdout.replace('MainPID=102', 'MainPID=101')
            return result
        self.assertEqual(preflight.probe_ownership(run=duplicate),
                         ('BLOCKED', 'worker_main_pid_shared'))

    def test_old_rollback_unavailable_keeps_safe_stop_distinct(self):
        def stopped_translation(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv[:3] == ['systemctl', '--user', 'show']:
                result.stdout = result.stdout.replace('MainPID=102\nActiveState=active',
                                                      'MainPID=0\nActiveState=inactive')
            if argv[-1] == '--help' and argv[0].startswith(self.plan['rollback_release']):
                result.stdout = 'old worker without readonly probe'
            return result
        self.installed_guard.unlink()
        report = preflight.inspect(self.plan, run=stopped_translation)
        self.assertEqual(report['state'], 'WAITING')
        self.assertEqual(report['checks']['rollback_release']['state'], 'BLOCKED')
        self.assertEqual(report['checks']['safe_stop_recovery']['state'], 'WAITING')
        self.assertEqual(report['checks']['quota_guard']['reason'], 'guard_install_pending')

    def test_installed_file_without_loaded_guard_never_reports_ready(self):
        def stale_unit(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv[:4] == ['systemctl', '--user', 'show', 'ai-company-quota.service']:
                result.stdout = result.stdout.replace(self.guard_loaded_argv,
                                                      '/old/bin/ai-company session worker')
            return result
        failed = preflight.inspect(self.plan, run=stale_unit)
        self.assertEqual(failed['checks']['quota_guard'],
                         {'state': 'WAITING', 'reason': 'guard_unit_reload_pending'})
        fixed = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(fixed['checks']['quota_guard'],
                         {'state': 'READY', 'reason': 'installed_guard_and_loaded_unit_verified'})

    def test_verified_safe_stop_can_replace_unstartable_full_rollback(self):
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("UPDATE accounts SET state='AVAILABLE'")
        def safe_state(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv[:3] == ['systemctl', '--user', 'show'] and argv[3] != 'ai-company-quota.service':
                result.stdout = result.stdout.replace('MainPID=102\nActiveState=active',
                                                      'MainPID=0\nActiveState=inactive')
            if argv[-1] == '--help' and argv[0].startswith(self.plan['rollback_release']):
                result.stdout = 'old worker without readonly probe'
            if '--preflight' in argv:
                result.stdout = json.dumps({'status': 'ready', 'reasons': []})
            return result
        report = preflight.inspect(self.plan, run=safe_state)
        self.assertEqual(report['checks']['rollback_release']['state'], 'BLOCKED')
        self.assertEqual(report['checks']['safe_stop_recovery']['state'], 'READY')
        self.assertEqual(report['state'], 'READY')

    def test_changed_package_code_blocks_before_startup_probe(self):
        source = Path(self.plan['candidate_release']) / 'src/ai_company/service_worker.py'
        source.write_text('entrypoint = "changed"\n')
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['candidate_release'],
                         {'state': 'BLOCKED', 'reason': 'release_code_missing_or_changed'})
        self.assertFalse(any('--preflight' in argv and argv[0].startswith(self.plan['candidate_release'])
                             for argv in self.commands))

    def test_shared_alias_usage_disagreement_blocks(self):
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("INSERT INTO accounts VALUES ('shared','UNKNOWN',NULL,'unverified',4,10,0,1)")
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['shared_ledger'],
                         {'state': 'BLOCKED', 'reason': 'shared_ledger_missing_or_invalid'})

    def test_unknown_ledger_state_enum_blocks(self):
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("UPDATE accounts SET state='BOGUS'")
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['shared_ledger'],
                         {'state': 'BLOCKED', 'reason': 'shared_ledger_missing_or_invalid'})

    def test_runtime_package_symlink_outside_release_blocks(self):
        external = Path(self.tmp.name) / 'outside'
        external.mkdir()
        (external / '__init__.py').write_text('')
        package = Path(self.plan['candidate_package_path'])
        for file in package.iterdir():
            file.unlink()
        package.rmdir()
        package.symlink_to(external, target_is_directory=True)
        report = preflight.inspect(self.plan, run=self.fake_run)
        self.assertEqual(report['checks']['candidate_release']['reason'],
                         'release_code_missing_or_changed')

    def test_manager_pythonpath_blocks_actual_import_claim(self):
        def changed_manager(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if argv == ['systemctl', '--user', 'show-environment']:
                result.stdout += 'PYTHONPATH=/untrusted\n'
            return result
        report = preflight.inspect(self.plan, run=changed_manager)
        self.assertEqual(report['checks']['manager_import_env'],
                         {'state': 'BLOCKED', 'reason': 'manager_python_import_environment_present'})

    def test_runtime_import_origin_must_match_hashed_package(self):
        def substituted_import(argv, **kwargs):
            result = self.fake_run(argv, **kwargs)
            if '-c' in argv and 'ai_company.__file__' in argv[-1]:
                result.stdout = '/outside/ai_company/__init__.py\n'
            return result
        report = preflight.inspect(self.plan, run=substituted_import)
        self.assertEqual(report['checks']['candidate_release']['reason'], 'release_import_origin_changed')


if __name__ == '__main__':
    unittest.main()
