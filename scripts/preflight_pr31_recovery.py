"""Read-only PR31 recovery preflight against private state copies.

It probes release imports/startup, Claude help/version, and user systemd state.
It never starts a model or controls an operating service.
"""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import pwd
import re
import sqlite3
import subprocess


REQUIRED_CLI_OPTIONS = ('--print', '--restricted', '--safe-mode', '--tools', '--disallowedTools',
                        '--strict-mcp-config', '--mcp-config', '--disable-slash-commands',
                        '--no-chrome', '--permission-mode', '--input-format', '--output-format',
                        '--verbose', '--no-session-persistence', '--model', '--effort',
                        '--max-budget-usd', '--settings')
REQUIRED_ENV = ('AI_COMPANY_STATE', 'AI_COMPANY_AUTOMATION_CONFIG',
                'AI_COMPANY_TRANSLATION_CONFIG', 'AI_COMPANY_EXECUTION_CATALOG',
                'AI_COMPANY_SHARED_CALL_LEDGER', 'AI_COMPANY_REQUIRE_SHARED_CALLS',
                'AI_COMPANY_TRANSLATION_CLI_PATH', 'AI_COMPANY_TRANSLATION_CLI_SHA256')


def file_sha(path):
    with Path(path).open('rb') as source:
        digest = sha256()
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def release_code_sha(release, runtime_package):
    """Hash source and the package Python actually imports from this release."""
    release = Path(release).resolve(strict=True)
    runtime_package = Path(runtime_package).resolve(strict=True)
    source_package = release / 'src/ai_company'
    if (not runtime_package.is_relative_to(release) or runtime_package.name != 'ai_company'
            or source_package.resolve(strict=True) != source_package):
        raise ValueError('runtime package is outside the fixed release')
    files = set((release / 'src/ai_company').rglob('*.py'))
    files.update(runtime_package.rglob('*.py'))
    files = sorted(files | {release / 'pyproject.toml'})
    if len(files) < 2 or any(not path.is_file() or path.is_symlink() for path in files):
        raise ValueError('release package source is incomplete')
    value = sha256()
    for path in files:
        value.update(str(path.relative_to(release)).encode() + b'\0'
                     + bytes.fromhex(file_sha(path)))
    return value.hexdigest()


def env_file(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) or name in result:
            raise ValueError('invalid or duplicate private environment key')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError('invalid quoted private environment value')
            value = value[1:-1]
        result[name] = value
    return result


def worker_environment(env, worker_path):
    user = pwd.getpwuid(os.getuid()).pw_name
    return {'HOME': str(Path.home()), 'USER': user, 'LOGNAME': user, 'LANG': 'C.UTF-8',
            'PATH': worker_path, 'LANGSMITH_TRACING': 'false',
            'LANGCHAIN_TRACING_V2': 'false', 'PYTHONDONTWRITEBYTECODE': '1', **env}


def sqlite_readonly(path, query):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('copied database integrity failed')
        return db.execute(query).fetchall()


def probe_cli(path, expected_sha, *, environment=None, run=subprocess.run):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return ('BLOCKED', 'service_cli_missing_or_unexecutable')
    if file_sha(path) != expected_sha:
        return ('BLOCKED', 'service_cli_digest_changed')
    try:
        version = run([str(path), '--version'], env=environment, capture_output=True, text=True,
                      timeout=10, check=False)
        help_result = run([str(path), '--help'], env=environment, capture_output=True, text=True,
                          timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ('BLOCKED', 'service_cli_probe_failed')
    if version.returncode or version.stdout.strip() != '2.1.270 (Claude Code)':
        return ('WAITING', 'service_cli_version_unverified')
    if help_result.returncode or any(option not in help_result.stdout for option in REQUIRED_CLI_OPTIONS):
        return ('WAITING', 'service_cli_options_unverified')
    if file_sha(path) != expected_sha:
        return ('BLOCKED', 'service_cli_changed_during_probe')
    return ('READY', 'service_cli_verified_without_model_call')


def probe_release(release, runtime_package, state_copy, ledger_copy, env, worker_path, *, run=subprocess.run):
    """Invoke the installed worker's explicit no-call/no-write startup probe."""
    executable = Path(release) / '.venv/bin/python'
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return ('BLOCKED', 'release_python_missing')
    try:
        clean_env = worker_environment(env, worker_path)
        clean_env['AI_COMPANY_STATE'] = str(Path(state_copy).resolve(strict=True))
        clean_env['AI_COMPANY_SHARED_CALL_LEDGER'] = str(Path(ledger_copy).resolve(strict=True))
        version = run([str(executable), '-c',
                       'import sys; print(".".join(map(str, sys.version_info[:2])))'],
                      env=clean_env, cwd=release, capture_output=True, text=True, timeout=10, check=False)
        if version.returncode or version.stdout.strip() != '3.13':
            return ('BLOCKED', 'release_python_not_3_13')
        origin = run([str(executable), '-c',
                      'import ai_company; print(ai_company.__file__)'],
                     env=clean_env, cwd=release, capture_output=True, text=True, timeout=10, check=False)
        if (origin.returncode or Path(origin.stdout.strip()).resolve()
                != (Path(runtime_package) / '__init__.py').resolve()):
            return ('BLOCKED', 'release_import_origin_changed')
        entrypoint = run([str(executable), '-m', 'ai_company.service_worker', '--help'],
                         env=clean_env, cwd=release, capture_output=True, text=True, timeout=10, check=False)
        if entrypoint.returncode or '--preflight' not in entrypoint.stdout:
            return ('BLOCKED', 'release_has_no_readonly_startup_probe')
        waiting = []
        for component in ('automation', 'translation'):
            command = [str(executable), '-m', 'ai_company.service_worker', component,
                       '--state-dir', clean_env['AI_COMPANY_STATE'], '--config',
                       env['AI_COMPANY_AUTOMATION_CONFIG' if component == 'automation'
                           else 'AI_COMPANY_TRANSLATION_CONFIG'], '--shared-call-ledger',
                       clean_env['AI_COMPANY_SHARED_CALL_LEDGER'], '--preflight']
            if component == 'automation':
                command.extend(('--execution-catalog', env['AI_COMPANY_EXECUTION_CATALOG']))
            else:
                command.append('--execute-translations')
            result = run(command, env=clean_env, cwd=release, capture_output=True, text=True,
                         timeout=20, check=False)
            if result.returncode:
                return ('BLOCKED', component + '_release_startup_probe_failed')
            try:
                status = json.loads(result.stdout)['status']
            except (ValueError, KeyError, TypeError):
                return ('BLOCKED', component + '_release_startup_probe_unreadable')
            if status not in ('ready', 'waiting'):
                return ('BLOCKED', component + '_release_startup_probe_invalid')
            if status == 'waiting':
                waiting.append(component)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ('BLOCKED', 'release_startup_probe_failed')
    if waiting:
        return ('WAITING', '_and_'.join(waiting) + '_release_call_readiness_waiting')
    return ('READY', 'both_workers_started_in_no_call_preflight')


def probe_ownership(*, run=subprocess.run):
    """Verify the supervised units have distinct owners; do not acquire locks."""
    try:
        result = run(['systemctl', '--user', 'show', 'ai-company-automation.service',
                      'ai-company-translation.service', 'ai-company-quota.timer', '-p', 'Id', '-p', 'MainPID',
                      '-p', 'ActiveState'], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            return ('BLOCKED', 'worker_unit_state_unreadable')
        units = {}
        for section in result.stdout.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in section.splitlines() if '=' in line)
            if fields.get('Id'):
                units[fields['Id']] = fields
        names = ('ai-company-automation.service', 'ai-company-translation.service')
        if set(units) != set((*names, 'ai-company-quota.timer')):
            return ('BLOCKED', 'worker_unit_set_changed')
        if units['ai-company-quota.timer'].get('ActiveState') != 'active':
            return ('BLOCKED', 'legacy_timer_schedule_inactive')
        pids = []
        for name in names:
            fields = units[name]
            pid = int(fields['MainPID'])
            if pid < 0 or fields['ActiveState'] == 'active' and pid == 0:
                return ('BLOCKED', 'worker_main_pid_inconsistent')
            if pid:
                pids.append(pid)
        if len(pids) != len(set(pids)):
            return ('BLOCKED', 'worker_main_pid_shared')
        if (units[names[0]]['ActiveState'] == 'active'
                and units[names[1]]['ActiveState'] == 'inactive'):
            return ('WAITING', 'pm_active_translation_stopped_timer_active')
        if len(pids) < 2:
            return ('WAITING', 'one_or_more_supervised_workers_inactive')
        return ('READY', 'supervised_workers_have_distinct_main_pids')
    except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
        return ('BLOCKED', 'worker_unit_state_invalid')


def probe_manager_import_env(*, run=subprocess.run):
    try:
        result = run(['systemctl', '--user', 'show-environment'], capture_output=True,
                     text=True, timeout=10, check=False)
        if result.returncode:
            return ('BLOCKED', 'manager_environment_unreadable')
        names = {line.partition('=')[0] for line in result.stdout.splitlines() if '=' in line}
        if names & {'PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'PYTHONSAFEPATH', 'VIRTUAL_ENV'}:
            return ('BLOCKED', 'manager_python_import_environment_present')
        return ('READY', 'manager_python_import_environment_clear')
    except (OSError, subprocess.TimeoutExpired):
        return ('BLOCKED', 'manager_environment_unreadable')


def installed_guard(plan, *, run=subprocess.run):
    """A source file is insufficient: compare the installed and loaded unit."""
    guard = Path(plan['guard_install_path'])
    unit = Path(plan['installed_quota_service'])
    if not guard.exists() or not unit.exists():
        return ('WAITING', 'guard_install_pending')
    if file_sha(guard) != plan['guard_sha256']:
        return ('BLOCKED', 'installed_guard_digest_changed')
    if file_sha(unit) != plan['quota_service_sha256']:
        return ('WAITING', 'guard_unit_install_pending')
    try:
        result = run(['systemctl', '--user', 'show', 'ai-company-quota.service',
                      '-p', 'FragmentPath', '-p', 'ExecStart', '-p', 'LoadState'],
                     capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            return ('BLOCKED', 'loaded_guard_unit_unreadable')
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        actual = re.search(r'argv\[\]=([^;]*)\s*;', fields.get('ExecStart', ''))
        if (fields.get('LoadState') != 'loaded'
                or Path(fields.get('FragmentPath', '')).resolve() != unit.resolve()
                or not actual or actual.group(1).strip() != plan['guard_loaded_argv']):
            return ('WAITING', 'guard_unit_reload_pending')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ('BLOCKED', 'loaded_guard_unit_invalid')
    return ('READY', 'installed_guard_and_loaded_unit_verified')


def inspect(plan, *, run=subprocess.run):
    checks = {}
    def record(name, state, reason):
        checks[name] = {'state': state, 'reason': reason}

    try:
        env = env_file(plan['worker_env'])
        if not isinstance(plan['worker_path'], str) or not plan['worker_path'].startswith('/'):
            raise ValueError('fixed worker PATH is missing')
        if any(not env.get(key) for key in REQUIRED_ENV) or env['AI_COMPANY_REQUIRE_SHARED_CALLS'] != '1':
            raise ValueError('missing shared-call or service CLI environment')
        if any(key in env for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE',
                                      'PYTHONSAFEPATH', 'VIRTUAL_ENV', 'PATH',
                                      'PYTHONDONTWRITEBYTECODE')):
            raise ValueError('private environment changes the worker import path')
        if (env['AI_COMPANY_TRANSLATION_CLI_PATH'] != plan['claude_cli']
                or env['AI_COMPANY_TRANSLATION_CLI_SHA256'] != plan['claude_sha256']
                or env['AI_COMPANY_SHARED_CALL_LEDGER'] != plan['operating_ledger_path']):
            raise ValueError('private environment differs from the fixed proposal')
        if (Path(plan['state_copy']).resolve() == Path(env['AI_COMPANY_STATE']).resolve()
                or (Path(plan['state_copy']) / 'sessions/sessions.sqlite').resolve()
                   == (Path(env['AI_COMPANY_STATE']) / 'sessions/sessions.sqlite').resolve()
                or Path(plan['shared_ledger_copy']).resolve() == Path(env['AI_COMPANY_SHARED_CALL_LEDGER']).resolve()
                or Path(plan['legacy_queue_copy']).resolve() == Path(plan['legacy_queue_live_path']).resolve()
                or any(path.is_symlink() for path in (
                    Path(plan['state_copy']) / 'sessions/sessions.sqlite',
                    Path(plan['shared_ledger_copy']), Path(plan['legacy_queue_copy'])))):
            raise ValueError('preflight copy points to operating state')
        if Path(env['AI_COMPANY_EXECUTION_CATALOG']).resolve() != Path(plan['catalog']).resolve():
            raise ValueError('execution catalog differs from the fixed proposal')
        for name, key in (('automation_config', 'AI_COMPANY_AUTOMATION_CONFIG'),
                          ('translation_config', 'AI_COMPANY_TRANSLATION_CONFIG'),
                          ('catalog', 'AI_COMPANY_EXECUTION_CATALOG')):
            if file_sha(env[key]) != plan[name + '_sha256']:
                raise ValueError('private configuration changed')
        record('environment', 'READY', 'required_keys_config_digests_and_isolated_state_checked')
    except (KeyError, OSError, ValueError):
        record('environment', 'BLOCKED', 'private_environment_missing_or_changed')
        return {'state': 'BLOCKED', 'checks': checks, 'model_calls': 0, 'operating_writes': 0}

    record('claude_cli', *probe_cli(plan['claude_cli'], plan['claude_sha256'],
                                    environment=worker_environment(env, plan['worker_path']), run=run))
    record('manager_import_env', *probe_manager_import_env(run=run))
    record('unit_ownership', *probe_ownership(run=run))
    for name in ('candidate', 'rollback'):
        try:
            if file_sha(Path(plan[name + '_release']) / 'uv.lock') != plan[name + '_uv_lock_sha256']:
                raise ValueError('dependency lock changed')
            record(name + '_dependencies', 'READY', 'frozen_dependency_lock_verified')
        except (KeyError, OSError, ValueError):
            record(name + '_dependencies', 'BLOCKED', 'dependency_lock_missing_or_changed')
        try:
            if release_code_sha(plan[name + '_release'], plan[name + '_package_path']) != plan[name + '_code_sha256']:
                raise ValueError('release code changed')
            release_ok = True
        except (KeyError, OSError, ValueError):
            release_ok = False
        if not release_ok:
            record(name + '_release', 'BLOCKED', 'release_code_missing_or_changed')
        else:
            record(name + '_release', *probe_release(plan[name + '_release'], plan[name + '_package_path'], plan['state_copy'],
                                                     plan['shared_ledger_copy'], env, plan['worker_path'], run=run))
            if release_code_sha(plan[name + '_release'], plan[name + '_package_path']) != plan[name + '_code_sha256']:
                record(name + '_release', 'BLOCKED', 'release_code_changed_during_probe')
    try:
        jobs = sqlite_readonly(Path(plan['state_copy']) / 'sessions/sessions.sqlite',
                               'SELECT count(*) FROM session_jobs')[0][0]
        record('management_copy', 'READY', 'integrity_verified_jobs_' + str(jobs))
    except (OSError, sqlite3.Error, ValueError, IndexError):
        record('management_copy', 'BLOCKED', 'copied_management_database_invalid')
    try:
        from_guard = Path(plan['guard_source'])
        unit = Path(plan['quota_service']).read_text()
        timer = Path(plan['timer_source']).read_bytes()
        installed_timer = Path(plan['installed_timer']).read_bytes()
        commands = [line.strip() for line in unit.splitlines()
                    if line.strip().startswith(('ExecStart=', 'ExecStartPre=', 'ExecStartPost='))]
        if (file_sha(from_guard) != plan['guard_sha256']
                or file_sha(plan['quota_service']) != plan['quota_service_sha256']
                or commands != [plan['guard_execstart']]
                or timer != installed_timer):
            raise ValueError('legacy timer could invoke an unaccounted worker')
        record('quota_guard', *installed_guard(plan, run=run))
    except (KeyError, OSError, ValueError):
        record('quota_guard', 'BLOCKED', 'quota_guard_or_unit_changed')
    try:
        count = sqlite_readonly(plan['legacy_queue_copy'], 'SELECT count(*) FROM session_jobs')[0][0]
        record('legacy_queue', 'READY' if count == 0 else 'WAITING',
               'empty_no_legacy_calls' if count == 0 else 'retained_jobs_need_reviewed_resume_path')
    except (OSError, sqlite3.Error, ValueError, IndexError):
        record('legacy_queue', 'BLOCKED', 'legacy_queue_copy_missing_or_corrupt')
    try:
        rows = sqlite_readonly(plan['shared_ledger_copy'],
                               "SELECT value FROM shared_meta WHERE key='schema_version'")
        if rows != [('2',)]:
            raise ValueError('shared ledger schema differs')
        accounts = sqlite_readonly(plan['shared_ledger_copy'],
            'SELECT group_id,state,resume_at,reason,calls,runtime_seconds,cost_usd,cost_unknown FROM accounts')
        active = sqlite_readonly(plan['shared_ledger_copy'],
                                 "SELECT count(*) FROM reservations WHERE state NOT IN ('SETTLED','CANCELLED')")[0][0]
        if not accounts or active:
            raise ValueError('shared ledger has no accounts or active reservations')
        groups = {}
        for group, *values in accounts:
            state, resume = values[:2]
            if (state not in ('AVAILABLE', 'COOLDOWN', 'DISABLED', 'UNKNOWN')
                    or state == 'COOLDOWN' and (not isinstance(resume, (int, float))
                                                or not math.isfinite(resume) or resume <= 0)):
                raise ValueError('shared account state invalid')
            if group in groups and groups[group] != values:
                raise ValueError('shared account aliases differ')
            groups[group] = values
        unknown = sum(values[0] == 'UNKNOWN' for values in groups.values())
        record('shared_ledger', 'WAITING' if unknown else 'READY',
               'unknown_account_retained' if unknown else 'schema_and_balances_readable')
    except (OSError, sqlite3.Error, ValueError, IndexError):
        record('shared_ledger', 'BLOCKED', 'shared_ledger_missing_or_invalid')
    if checks['rollback_release']['state'] != 'BLOCKED':
        record('safe_stop_recovery', 'READY', 'rollback_release_has_readonly_startup_probe')
    elif (checks['rollback_release']['state'] == 'BLOCKED'
            and checks['unit_ownership']['reason'] == 'pm_active_translation_stopped_timer_active'
            and checks['quota_guard']['state'] != 'BLOCKED'
            and checks['legacy_queue']['state'] != 'BLOCKED'
            and checks['shared_ledger']['state'] != 'BLOCKED'):
        record('safe_stop_recovery', 'READY' if checks['quota_guard']['state'] == 'READY' else 'WAITING',
               'pm_running_translation_stopped_guarded_timer_no_model_calls'
               if checks['quota_guard']['state'] == 'READY' else 'pm_running_translation_stopped_guard_install_pending')
    else:
        record('safe_stop_recovery', 'BLOCKED', 'safe_stop_recovery_conditions_unproved')
    accepted_alternatives = set()
    if checks['safe_stop_recovery']['state'] != 'BLOCKED':
        accepted_alternatives.add('rollback_release')
    if checks['safe_stop_recovery']['state'] == 'READY' and (
            checks['unit_ownership']['reason'] == 'pm_active_translation_stopped_timer_active'):
        accepted_alternatives.add('unit_ownership')
    effective = {check['state'] for name, check in checks.items() if name not in accepted_alternatives}
    return {'state': 'BLOCKED' if 'BLOCKED' in effective else 'WAITING' if 'WAITING' in effective else 'READY',
            'checks': checks, 'model_calls': 0, 'operating_writes': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path, help='private fixed paths and digests JSON')
    args = parser.parse_args(argv)
    report = inspect(json.loads(args.plan.read_text()))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return {'READY': 0, 'WAITING': 1, 'BLOCKED': 2}[report['state']]


if __name__ == '__main__':
    raise SystemExit(main())
