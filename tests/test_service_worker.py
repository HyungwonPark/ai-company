"""Real OS ownership and process-stop tests in fresh state; never call models."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from threading import Event
import unittest

from ai_company.contracts import digest
from ai_company.runtime import ExecutionBlocked
from ai_company.service_worker import WorkerHeartbeat, runtime_status, serve, worker_ownership
from ai_company.sessions import execution_alive


class ServiceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_components_have_independent_ownership_and_reject_duplicate(self):
        with worker_ownership(self.root, 'automation'):
            with self.assertRaises(ExecutionBlocked):
                with worker_ownership(self.root, 'automation'):
                    self.fail('duplicate worker acquired ownership')
            with worker_ownership(self.root, 'translation'):
                pass
        with worker_ownership(self.root, 'automation'):
            pass

    def test_heartbeat_reports_stale_and_error_without_claiming_a_process_probe(self):
        now = [100.0]
        heartbeat = WorkerHeartbeat(self.root, 'automation', 'config', clock=lambda: now[0])
        heartbeat.update(state='busy')
        self.assertEqual(runtime_status(self.root, clock=lambda: now[0])['automation']['state'], 'busy')
        now[0] += 21
        status = runtime_status(self.root, clock=lambda: now[0])
        self.assertEqual(status['automation']['state'], 'stale')
        self.assertEqual(status['automation']['process_probe'], 'not_performed')
        self.assertEqual(status['translation']['state'], 'unavailable')
        def fail(): raise RuntimeError('private argument must not enter heartbeat')
        with self.assertRaises(RuntimeError):
            serve(self.root, 'automation', 'config', fail, clock=lambda: now[0])
        record = runtime_status(self.root, clock=lambda: now[0])['automation']
        self.assertEqual(record['state'], 'error'); self.assertEqual(record['error'], 'RuntimeError')
        self.assertNotIn('private', json.dumps(record))

    def test_graceful_stop_finishes_current_pass_once(self):
        stop = Event(); calls = []
        def tick():
            calls.append('pass'); stop.set()
        serve(self.root, 'automation', 'config', tick, stop=stop, poll_seconds=.01)
        self.assertEqual(calls, ['pass'])
        record = runtime_status(self.root)['automation']
        self.assertEqual(record['state'], 'stopped'); self.assertEqual(record['passes'], 1)

    def test_sigkill_releases_lock_and_next_process_recovers_ownership(self):
        source = """from pathlib import Path
import sys,time
from ai_company.service_worker import worker_ownership
root=Path(sys.argv[1])
with worker_ownership(root,'automation'):
 (root/'ready').write_text('ready')
 time.sleep(60)
"""
        child = subprocess.Popen([sys.executable, '-c', source, str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.root / 'ready').exists())
            with self.assertRaises(ExecutionBlocked):
                with worker_ownership(self.root, 'automation'): pass
            child.send_signal(signal.SIGKILL); child.wait(timeout=5)
            with worker_ownership(self.root, 'automation'): pass
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)
        # A prior-boot identity cannot keep an execution slot alive after reboot.
        self.assertFalse(execution_alive({'boot_id': 'fixture-previous-boot', 'pgid': os.getpgrp()}))
