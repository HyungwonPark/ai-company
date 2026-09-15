"""Opt-in supervised workers with kernel ownership and observable liveness.

No installation or model execution occurs on import or a status read.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import tempfile
from threading import Event, Lock, Thread
import time

from ai_company.contracts import digest
from ai_company.storage import controller_lock

COMPONENTS = ('automation', 'translation')
HEARTBEAT_SECONDS = 5
STALE_SECONDS = 20


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
            result[component] = {**record, 'state': state, 'age_seconds': max(0, age),
                                 'source': 'worker_heartbeat', 'process_probe': 'not_performed'}
        except (OSError, ValueError, KeyError, TypeError):
            result[component] = {'state': 'unavailable', 'source': 'worker_heartbeat'}
    return result


class WorkerHeartbeat:
    def __init__(self, root, component, configuration_digest, *, clock=time.time, interval=HEARTBEAT_SECONDS):
        if component not in COMPONENTS:
            raise ValueError('unknown worker component')
        self.path = Path(root) / 'worker-status' / (component + '.json')
        self.clock, self.interval = clock, interval
        self.record = dict(component=component, pid=os.getpid(),
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            configuration_digest=configuration_digest, state='starting', started_at=clock(),
            last_completed_at=None, passes=0, error=None)
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


def serve(root, component, configuration_digest, tick, *, poll_seconds=5, stop=None, clock=time.time):
    if not 0 < poll_seconds <= 60:
        raise ValueError('poll interval must be positive and at most 60 seconds')
    stop = stop or Event()
    with worker_ownership(root, component):
        heartbeat = WorkerHeartbeat(root, component, configuration_digest, clock=clock)
        heartbeat.start()
        final = 'stopped'
        try:
            while not stop.is_set():
                heartbeat.update(state='busy')
                tick()
                heartbeat.update(state='idle', last_completed_at=clock(), passes=heartbeat.record['passes'] + 1)
                stop.wait(poll_seconds)
        except BaseException as exc:
            final = 'error'
            heartbeat.update(error=type(exc).__name__)
            raise
        finally:
            heartbeat.close(final)


def translation_tick(root, config):
    from ai_company.adapters.translation_cli import TranslationCLI
    from ai_company.collaboration import document_sources
    from ai_company.management import ManagementStore
    from ai_company.translation_worker import run_once
    from ai_company.translations import TranslationStore
    management = ManagementStore(root)
    try:
        store = TranslationStore(management.db)
        for project in management.list_projects():
            if project['source'] == 'fixture':
                continue
            store.sync(project['id'], document_sources(management.overview(project['id'])), config)
        return run_once(store, TranslationCLI(Path(root) / 'translation-runtime'))
    finally:
        management.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=COMPONENTS)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--poll-seconds', type=float, default=5)
    parser.add_argument('--execute-translations', action='store_true')
    args = parser.parse_args(argv)
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
            worker = Automation(root, config)
            tick = worker.run_once
        else:
            from ai_company.translations import configuration
            from ai_company.adapters.translation_cli import TranslationCLI
            config = configuration(config)
            if not args.execute_translations or not TranslationCLI(root / 'translation-runtime').ready(config):
                parser.error('explicit verified lightweight translation configuration and execution flag required')
            tick = lambda: translation_tick(root, config)
        serve(root, args.component, digest(config), tick, poll_seconds=args.poll_seconds, stop=stop)
    finally:
        if worker:
            worker.close()


if __name__ == '__main__':
    main()
