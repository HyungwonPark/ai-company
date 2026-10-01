"""Isolated host proof: generated ticks, no model/operational queue or unit writes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


def command(*argv, check=True):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=check)


def wait_until(check, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check(): return
        time.sleep(.1)
    raise RuntimeError('bounded fixture wait expired')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / 'fixture.json').write_text(json.dumps({'mode': 'fixture', 'model_calls': 0}))
    helper = root / 'tick.py'
    helper.write_text('''import json,os,signal,sys,time
from pathlib import Path
from threading import Event
from ai_company.service_worker import serve
root=Path(sys.argv[1]);component=sys.argv[2];stop=Event()
def tick():
 path=root/(component+'.calls.json')
 calls=json.loads(path.read_text()) if path.exists() else []
 calls.append({'pid':os.getpid(),'at':time.time()});path.write_text(json.dumps(calls))
 (root/(component+'.ready')).write_text('fixture')
 if component=='automation' and len(calls)==1:
  until=time.monotonic()+25
  while not (root/'release').exists() and time.monotonic()<until:time.sleep(.05)
  if not (root/'release').exists():raise RuntimeError('fixture release timeout')
  os.kill(os.getpid(),signal.SIGKILL)
 stop.set()
serve(root,component,'fixture-only-no-models',tick,poll_seconds=.1,stop=stop)
''')
    units = {component: f'ai-company-supervision-proof-{uuid4().hex}.service'
             for component in ('automation', 'translation')}
    source = str(Path(__file__).resolve().parents[1] / 'src')
    environment = {**os.environ, 'PYTHONPATH': source}
    result = {'mode': 'fixture', 'model_calls': 0, 'units': units, 'host_reboot_performed': False}
    try:
        for component, unit in units.items():
            command('systemd-run', '--user', '--quiet', '--unit=' + unit, '--service-type=exec',
                    '--property=WorkingDirectory=' + str(root), '--property=Restart=on-failure',
                    '--property=RestartSec=1', '--property=RemainAfterExit=yes',
                    '--property=RuntimeMaxSec=60', '--property=KillMode=control-group',
                    '--property=TimeoutStopSec=5', '--property=UMask=0077',
                    '--property=NoNewPrivileges=yes', '--property=MemoryMax=256M', '--property=TasksMax=64',
                    '--setenv=PYTHONPATH=' + source, sys.executable, str(helper), str(root), component)
        wait_until(lambda: (root / 'automation.ready').exists() and (root / 'translation.ready').exists())
        duplicate = subprocess.run([sys.executable, str(helper), str(root), 'automation'],
                                   env=environment, capture_output=True, text=True, timeout=10)
        assert duplicate.returncode != 0 and 'another controller' in duplicate.stderr
        assert len(json.loads((root / 'automation.calls.json').read_text())) == 1
        assert len(json.loads((root / 'translation.calls.json').read_text())) == 1
        (root / 'release').write_text('fixture release')
        def restarted():
            path = root / 'worker-status' / 'automation.json'
            return path.exists() and json.loads(path.read_text()).get('state') == 'stopped'
        wait_until(restarted)
        calls = json.loads((root / 'automation.calls.json').read_text())
        assert len(calls) == 2 and calls[0]['pid'] != calls[1]['pid']
        state = command('systemctl', '--user', 'show', units['automation'], '--property=NRestarts,ActiveState,SubState').stdout
        assert 'NRestarts=1' in state and 'SubState=exited' in state, state
        result.update(status='PASS', duplicate_start_rejected=True, automatic_restarts=1,
                      independent_component_completed_while_peer_blocked=True,
                      old_process_pid=calls[0]['pid'], resumed_process_pid=calls[1]['pid'])
    finally:
        for unit in units.values():
            command('systemctl', '--user', 'stop', unit, check=False)
            command('systemctl', '--user', 'reset-failed', unit, check=False)
        stopped = {}
        for component, unit in units.items():
            state = command('systemctl', '--user', 'show', unit, '--property=LoadState,ActiveState,MainPID', check=False).stdout
            stopped[component] = 'MainPID=0' in state and ('ActiveState=inactive' in state or 'LoadState=not-found' in state)
        result['test_units_stopped'] = stopped
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
    assert all(result['test_units_stopped'].values())


if __name__ == '__main__': main()
