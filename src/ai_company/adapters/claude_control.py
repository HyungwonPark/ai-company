"""Private process relay: native settings before/after one CLI invocation.

Run as a file in an isolated environment. Full settings and credentials never
enter captured stdout or the observation record. The queue owns termination.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
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
    before = config['binding']['nonce'] + '-before'
    after = config['binding']['nonce'] + '-after'
    facts_path = Path(config['facts_path'])
    fd = os.open(facts_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as facts:
        def record(value):
            facts.write(json.dumps({'at': time.time(), **value}) + '\n')
            facts.flush()
            os.fsync(facts.fileno())
        record({'state': 'starting', 'binding': config['binding']})
        child = subprocess.Popen([str(native), *sys.argv[2:], *config['extra_args']],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                                 text=True, bufsize=1, env=config['environment'])
        def send(event):
            child.stdin.write(json.dumps(event) + '\n')
            child.stdin.flush()
        sent = closed = False
        queried_before = queried_after = False
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
                print(json.dumps(event), flush=True)
                if event.get('type') == 'control_response' and ident == incoming[0]['request_id'] and not queried_before:
                    queried_before = True
                    send({'type': 'control_request', 'request_id': before, 'request': {'subtype': 'get_settings'}})
                elif event.get('type') == 'control_response' and ident == before and not sent:
                    safe = event['response']['response']
                    if safe['applied'] == {'model': 'claude-opus-5', 'effort': 'xhigh', 'ultracode': True} and not safe['has_errors']:
                        # Intent precedes delivery. After a crash the record
                        # never incorrectly claims that no model call occurred.
                        record({'state': 'prompt_delivery_started'})
                        send(incoming[1])
                        sent = True
                    else:
                        record({'state': 'configuration_refused'})
                        child.stdin.close()
                        closed = True
                elif event.get('type') == 'result' and not queried_after:
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
