"""Opt-in supervised workers with kernel ownership and observable liveness.

No installation or model execution occurs on import or a status read.
"""
import argparse
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
from threading import Event, Lock, Thread
import time

from ai_company.contracts import digest
from ai_company.storage import controller_lock

COMPONENTS = ('automation', 'translation')
HEARTBEAT_SECONDS = 5
STALE_SECONDS = 20
_UNSET = object()


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.heartbeat-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def runtime_status(root, *, clock=time.time):
    result = {}
    for component in COMPONENTS:
        path = Path(root) / 'worker-status' / (component + '.json')
        try:
            record = json.loads(path.read_text())
            age = clock() - record['heartbeat_at']
            state = record.get('state') if 0 <= age <= STALE_SECONDS else 'stale'
            if component == 'translation' and state not in ('starting', 'busy', 'idle'):
                record['call_readiness'] = {'state': 'blocked', 'reasons': ['service_not_running']}
            result[component] = {**record, 'state': state, 'age_seconds': max(0, age),
                                 'source': 'worker_heartbeat', 'process_probe': 'not_performed'}
        except (OSError, ValueError, KeyError, TypeError):
            result[component] = {'state': 'unavailable', 'source': 'worker_heartbeat'}
            if component == 'translation':
                result[component]['call_readiness'] = {'state': 'blocked', 'reasons': ['service_not_running']}
    return result


def public_configuration(component, config):
    """Requested settings only. Never export credentials or claim observed execution."""
    if hasattr(config, 'model_dump'):
        config = config.model_dump(mode='json')
    if component == 'translation':
        return {'source': 'requested_configuration', 'agents': [
            {key: config.get(key) for key in ('provider', 'model', 'reasoning_effort')}
        ]}
    return {'source': 'requested_configuration', 'mode': config.get('mode'),
            'allowed_paths': list(config.get('allowed_paths', [])),
            'agents': [{key: agent.get(key) for key in
                        ('agent_id', 'provider', 'model', 'reasoning_effort', 'roles')}
                       for agent in config.get('agents', [])]}


class WorkerHeartbeat:
    def __init__(self, root, component, configuration_digest, *, configuration=None, clock=time.time, interval=HEARTBEAT_SECONDS):
        if component not in COMPONENTS:
            raise ValueError('unknown worker component')
        self.path = Path(root) / 'worker-status' / (component + '.json')
        self.clock, self.interval = clock, interval
        self.record = dict(component=component, pid=os.getpid(),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            configuration_digest=configuration_digest, state='starting', started_at=clock(),
            last_completed_at=None, passes=0, error=None)
        if component == 'translation':
            self.record['call_readiness'] = {'state': 'checking', 'reasons': []}
        if configuration is not None:
            self.record['configuration'] = public_configuration(component, configuration)
        self.mutex, self.done = Lock(), Event()
        self.thread = None

    def update(self, **changes):
        with self.mutex:
            self.record.update(changes, heartbeat_at=self.clock())
            _atomic_json(self.path, self.record)

    def start(self):
        self.update()
        def beat():
            while not self.done.wait(self.interval):
                self.update()
        self.thread = Thread(target=beat, daemon=True)
        self.thread.start()

    def close(self, state='stopped'):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=self.interval + 1)
        self.update(state=state)


@contextmanager
def worker_ownership(root, component):
    if component not in COMPONENTS:
        raise ValueError('unknown worker component')
    # The OS releases flock on SIGKILL and reboot. PID files cannot steal ownership.
    with controller_lock(Path(root) / 'worker-locks' / component):
        yield


def serve(root, component, configuration_digest, tick, *, configuration=None, poll_seconds=5, stop=None, clock=time.time):
    if not 0 < poll_seconds <= 60:
        raise ValueError('poll interval must be positive and at most 60 seconds')
    stop = stop or Event()
    with worker_ownership(root, component):
        heartbeat = WorkerHeartbeat(root, component, configuration_digest, configuration=configuration, clock=clock)
        heartbeat.start()
        final = 'stopped'
        try:
            while not stop.is_set():
                heartbeat.update(state='busy')
                result = tick()
                changes = dict(state='idle', last_completed_at=clock(), passes=heartbeat.record['passes'] + 1)
                if component == 'translation' and isinstance(result, dict) and 'call_readiness' in result:
                    changes['call_readiness'] = result['call_readiness']
                heartbeat.update(**changes)
                stop.wait(poll_seconds)
        except BaseException as exc:
            final = 'error'
            heartbeat.update(error=type(exc).__name__)
            raise
        finally:
            heartbeat.close(final)


def translation_tick(root, config, shared_call_ledger=None):
    from ai_company.adapters.translation_cli import TranslationCLI
    from ai_company.collaboration import document_sources
    from ai_company.management import ManagementStore
    from ai_company.translation_worker import run_once
    from ai_company.translations import TranslationStore
    from ai_company.shared_calls import SharedCallLedger
    management = ManagementStore(root)
    shared = SharedCallLedger(shared_call_ledger) if shared_call_ledger else None
    try:
        store = TranslationStore(management.db, shared_calls=shared,
                                 queue_id=str(Path(root).resolve() / 'translation') if shared else None)
        for project in management.list_projects():
            if project['source'] == 'fixture':
                continue
            store.sync(project['id'], document_sources(management.overview(project['id'])), config)
        adapter = TranslationCLI(Path(root) / 'translation-runtime')
        checks = {}
        def readiness(value):
            key = digest(value)
            if key not in checks:
                checks[key] = adapter.readiness(value)
            return checks[key]
        result = run_once(store, adapter, adapter_readiness=readiness)
        return {'call_readiness': translation_call_readiness(store, adapter, config, result,
                                                              cli_verdict=readiness(config))}
    finally:
        if shared:
            shared.close()
        management.close()


def translation_call_readiness(store, adapter, config, result=None, *, cli_verdict=None, quota_verdict=_UNSET):
    """Public, credential-free diagnosis; process liveness is the heartbeat itself."""
    ready, reason = cli_verdict if cli_verdict is not None else adapter.readiness(config)
    reasons = [] if ready else [reason or 'translation_cli_unverified']
    quota = store._quota(config) if quota_verdict is _UNSET else quota_verdict
    if quota:
        if quota[2] == 'shared_credential_unavailable':
            reasons.append('account_confirmation_required')
        elif quota[0] == 'waiting_quota':
            reasons.append('shared_account_quota')
        else:
            # A wrong account mapping is an integrity error, not a quota wait.
            raise RuntimeError('translation account registration is inconsistent')
    if result and result.get('status') == 'running' and result.get('reason') == 'adapter_termination_unconfirmed':
        reasons.append('execution_termination_unconfirmed')
    uncertain = {'execution_termination_unconfirmed', 'shared_execution_requires_reconciliation',
                 'shared_result_requires_reconciliation', 'shared_result_requires_local_reconciliation',
                 'shared_reservation_missing'}
    for row in store.db.execute('SELECT document FROM translation_jobs'):
        job = json.loads(row[0])
        if job['status'] == 'running' or (job['status'] == 'blocked' and job.get('reason') in uncertain):
            reasons.append('execution_termination_unconfirmed')
            break
    if 'execution_termination_unconfirmed' in reasons:
        state = 'blocked'
    else:
        state = 'waiting' if reasons else 'ready'
    return {'state': state, 'reasons': list(dict.fromkeys(reasons))}


def preflight(root, component, config_path, catalog_path, shared_path, execute_translations):
    """Read-only startup checks against an isolated state copy; never claim work."""
    invalid_reason = 'management_db_invalid'
    try:
        root = Path(root).resolve(strict=True)
        with closing(sqlite3.connect((root / 'sessions' / 'sessions.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('management database integrity failed')
            db.execute('SELECT 1 FROM translation_jobs LIMIT 1').fetchone()
            invalid_reason = 'worker_config_invalid'
            config = json.loads(Path(config_path).read_text())
            if shared_path is None:
                return {'component': component, 'status': 'blocked', 'reasons': ['shared_call_ledger_missing']}
            invalid_reason = 'shared_call_ledger_invalid'
            with closing(sqlite3.connect(Path(shared_path).resolve(strict=True).as_uri() + '?mode=ro',
                                         uri=True)) as shared_db:
                shared_db.execute('PRAGMA query_only=ON')
                if (shared_db.execute('PRAGMA integrity_check').fetchone() != ('ok',)
                        or shared_db.execute("SELECT value FROM shared_meta WHERE key='schema_version'").fetchone() != ('2',)):
                    raise ValueError('shared call ledger integrity failed')
                if component == 'automation':
                    from ai_company.automation_contracts import AutomationConfig
                    from ai_company.execution_specs import ExecutionCatalog
                    invalid_reason = 'worker_config_invalid'
                    AutomationConfig.model_validate(config)
                    if catalog_path is not None:
                        invalid_reason = 'execution_catalog_invalid'
                        ExecutionCatalog.load(catalog_path)
                    return {'component': component, 'status': 'ready', 'reasons': []}
                from ai_company.adapters.translation_cli import TranslationCLI
                from ai_company.translations import TranslationStore, configuration
                invalid_reason = 'worker_config_invalid'
                config = configuration(config)
                if not execute_translations:
                    return {'component': component, 'status': 'blocked',
                            'reasons': ['translation_execution_disabled']}
                invalid_reason = 'shared_call_ledger_invalid'
                row = shared_db.execute("SELECT group_id,state,resume_at,reason,calls,runtime_seconds,cost_usd,cost_unknown "
                    "FROM accounts WHERE provider=? AND credential_ref=?",
                    (config['provider'], config['credential_ref'])).fetchone()
                aliases = shared_db.execute("SELECT state,resume_at,reason,calls,runtime_seconds,cost_usd,cost_unknown "
                    "FROM accounts WHERE group_id=?", (config['quota_group'],)).fetchall()
                if (not row or row[0] != config['quota_group'] or row[1] not in
                        ('AVAILABLE', 'COOLDOWN', 'DISABLED', 'UNKNOWN')
                        or not aliases or any(alias != row[1:] for alias in aliases)):
                    raise ValueError('shared account mapping is inconsistent')
                quota = (('blocked', None, 'shared_credential_unavailable') if row[1] in ('UNKNOWN', 'DISABLED')
                    else ('waiting_quota', row[2], 'shared_account_quota') if row[1] == 'COOLDOWN'
                         and (row[2] is None or row[2] > time.time()) else None)
                store = TranslationStore(db)
                invalid_reason = 'translation_state_invalid'
                readiness = translation_call_readiness(store, TranslationCLI(root / 'translation-runtime'),
                                                       config, quota_verdict=quota)
                return {'component': component, 'status': readiness['state'], 'reasons': readiness['reasons']}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, RuntimeError):
        return {'component': component, 'status': 'blocked', 'reasons': [invalid_reason]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=COMPONENTS)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--execution-catalog', type=Path, help='trusted local project execution catalog; automation only')
    parser.add_argument('--shared-call-ledger', type=Path,
                        help='existing reviewed common account/host reservation database')
    parser.add_argument('--poll-seconds', type=float, default=5)
    parser.add_argument('--execute-translations', action='store_true')
    parser.add_argument('--preflight', action='store_true', help='read-only startup check; never claim or call a model')
    args = parser.parse_args(argv)
    if args.component != 'automation' and args.execution_catalog is not None:
        parser.error('execution-catalog applies only to the automation worker')
    if args.preflight:
        result = preflight(args.state_dir, args.component, args.config, args.execution_catalog,
                           args.shared_call_ledger, args.execute_translations)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result['status'] == 'blocked' else 0
    # Require existing reviewed state and explicit installed local configuration.
    root = args.state_dir.resolve(strict=True)
    if not (root / 'sessions' / 'sessions.sqlite').is_file():
        parser.error('existing management state is required')
    config = json.loads(args.config.read_text())
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    worker = None
    try:
        if args.component == 'automation':
            from ai_company.automation import Automation
            from ai_company.automation_contracts import AutomationConfig
            config = AutomationConfig.model_validate(config)
            from ai_company.execution_specs import ExecutionCatalog
            catalog = ExecutionCatalog.load(args.execution_catalog) if args.execution_catalog is not None else None
            worker = Automation(root, config, execution_catalog=catalog,
                                shared_calls=args.shared_call_ledger)
            tick = worker.run_once
        else:
            from ai_company.translations import configuration
            config = configuration(config)
            if not args.execute_translations:
                parser.error('explicit lightweight translation execution flag required')
            tick = lambda: translation_tick(root, config, args.shared_call_ledger)
        serve(root, args.component, digest(config), tick, configuration=config, poll_seconds=args.poll_seconds, stop=stop)
    finally:
        if worker:
            worker.close()


if __name__ == '__main__':
    raise SystemExit(main())
