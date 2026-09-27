"""Private process relay: native settings before/after one CLI invocation.

Run as a file in an isolated environment. Full settings and credentials never
enter captured stdout or the observation record. The queue owns termination.
"""
import hashlib
import json
import os
from pathlib import Path
from contextlib import ExitStack
import signal
import subprocess
import sys
import time


def main():
    # The PR review runner blocks these signals while spawning this relay.
    # Clear the inherited mask before spawning the native Claude CLI.
    signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    config = json.loads(Path(sys.argv[1]).read_text())
    expected = config.get('expected_applied', {'model': 'claude-opus-5', 'effort': 'xhigh', 'ultracode': True})
    if (not isinstance(expected, dict) or set(expected) != {'model', 'effort', 'ultracode'}
            or not isinstance(expected['model'], str) or not expected['model']
            or expected['effort'] != 'xhigh' or expected['ultracode'] is not True):
        raise RuntimeError('invalid expected Claude configuration')
    native = Path(config['cli_executable'])
    if hashlib.sha256(native.read_bytes()).hexdigest() != config['cli_sha256']:
        raise RuntimeError('Claude executable changed before launch')
    incoming = []
    for line in sys.stdin:
        if len(line) > 2_000_000 or len(incoming) >= 2:
            raise RuntimeError('invalid controlled CLI input')
        incoming.append(json.loads(line))
    if (len(incoming) != 2 or incoming[0].get('type') != 'control_request'
            or incoming[0].get('request', {}).get('subtype') != 'initialize'
            or incoming[1].get('type') != 'user'):
        raise RuntimeError('controlled CLI requires initialize and one user message')
    prompt_sha256 = config['binding'].get('prompt_sha256')
    if prompt_sha256:
        message = incoming[1].get('message', {})
        prompt = message.get('content') if isinstance(message, dict) else None
        if not isinstance(prompt, str) or hashlib.sha256(prompt.encode()).hexdigest() != prompt_sha256:
            raise RuntimeError('controlled CLI prompt differs from the bound review input')
    before = config['binding']['nonce'] + '-before'
    after = config['binding']['nonce'] + '-after'
    facts_path = Path(config['facts_path'])
    events_path = Path(config['events_path']) if config.get('events_path') else None
    if events_path is not None and events_path.parent != facts_path.parent:
        raise RuntimeError('live event journal must remain in this attempt directory')
    fd = os.open(facts_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with ExitStack() as stack:
        facts = stack.enter_context(os.fdopen(fd, 'w'))
        journal = None
        if events_path is not None:
            journal_fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            journal = stack.enter_context(os.fdopen(journal_fd, 'w'))
        def record(value):
            facts.write(json.dumps({'at': time.time(), **value}) + '\n')
            facts.flush()
            os.fsync(facts.fileno())
        def emit(event):
            line = json.dumps(event)
            if journal is not None:
                journal.write(line + '\n')
                journal.flush()
                if event.get('type') in ('assistant', 'user', 'result', 'control_response') or event.get('subtype') == 'task_notification':
                    os.fsync(journal.fileno())
            print(line, flush=True)
        record({'state': 'starting', 'binding': config['binding']})
        child = subprocess.Popen([str(native), *sys.argv[2:], *config['extra_args']],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                                 text=True, bufsize=1, env=config['environment'])
        def send(event):
            child.stdin.write(json.dumps(event) + '\n')
            child.stdin.flush()
        sent = closed = False
        queried_before = queried_after = False
        active_tasks = set()
        background_tasks = []
        send(incoming[0])
        try:
            while line := child.stdout.readline(2_000_001):
                if len(line) > 2_000_000:
                    raise RuntimeError('native CLI event exceeds limit')
                event = json.loads(line)
                response = event.get('response', {})
                ident = response.get('request_id')
                if event.get('type') == 'control_response':
                    if ident in (before, after):
                        payload = response.get('response', {})
                        applied = payload.get('applied', {})
                        if not isinstance(applied, dict):
                            applied = {}
                        safe = {'applied': {k: applied[k] for k in ('model', 'effort', 'ultracode') if k in applied},
                                'has_errors': bool(payload.get('errors')) or response.get('subtype') != 'success'}
                        record({'state': 'applied', 'request_id': ident, **safe})
                        event = {'type': 'control_response', 'response': {'request_id': ident,
                                 'subtype': response.get('subtype'), 'response': safe}}
                    else:
                        # Initialize also contains account details. Only its
                        # control acknowledgement is needed by this contract.
                        event = {'type': 'control_response', 'response': {'request_id': ident,
                                 'subtype': response.get('subtype'), 'response': {}}}
                emit(event)
                if event.get('type') == 'system':
                    if event.get('subtype') == 'task_started' and event.get('task_id'):
                        active_tasks.add(event['task_id'])
                    elif event.get('subtype') == 'task_notification' and event.get('task_id'):
                        active_tasks.discard(event['task_id'])
                    elif event.get('subtype') == 'background_tasks_changed' and isinstance(event.get('tasks'), list):
                        background_tasks = event['tasks']
                if event.get('type') == 'control_response' and ident == incoming[0]['request_id'] and not queried_before:
                    queried_before = True
                    send({'type': 'control_request', 'request_id': before, 'request': {'subtype': 'get_settings'}})
                elif event.get('type') == 'control_response' and ident == before and not sent:
                    safe = event['response']['response']
                    if safe['applied'] == expected and not safe['has_errors']:
                        # Intent precedes delivery. After a crash the record
                        # never incorrectly claims that no model call occurred.
                        record({'state': 'prompt_delivery_started'})
                        send(incoming[1])
                        if prompt_sha256:
                            record({'state': 'prompt_delivered', 'prompt_sha256': prompt_sha256})
                        sent = True
                    else:
                        record({'state': 'configuration_refused'})
                        child.stdin.close()
                        closed = True
                elif event.get('type') == 'result' and not queried_after and not active_tasks and not background_tasks:
                    queried_after = True
                    send({'type': 'control_request', 'request_id': after, 'request': {'subtype': 'get_settings'}})
                elif event.get('type') == 'control_response' and ident == after:
                    child.stdin.close()
                    closed = True
        finally:
            if not closed:
                child.stdin.close()
            child.stdout.close()
        code = child.wait()
        record({'state': 'closed', 'exit_code': code})
        return code


if __name__ == '__main__':
    raise SystemExit(main())
