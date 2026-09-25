#!/usr/bin/env python3
"""Read-only Claude Code review of one checked-out PR head; never posts a verdict."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys


MODEL = 'claude-opus-5-5'
APPLIED = {'model': MODEL, 'effort': 'xhigh', 'ultracode': True}
CONTROL = Path(__file__).with_name('claude_control.py')
if not CONTROL.exists():
    CONTROL = Path(__file__).resolve().parents[1] / 'src/ai_company/adapters/claude_control.py'


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed (exit {result.returncode})')
    return result.stdout


def reviewable(pr, head):
    if pr.get('state') != 'OPEN' or pr.get('headRefOid') != head:
        raise RuntimeError('PR is closed or its remote head differs from this checkout')
    checks = pr.get('statusCheckRollup') or []
    outcome = lambda check: check.get('conclusion') or check.get('state')
    if not checks or not any(outcome(check) == 'SUCCESS' for check in checks):
        raise RuntimeError('PR has no successful CI check')
    if any(outcome(check) not in ('SUCCESS', 'SKIPPED', 'NEUTRAL') for check in checks):
        raise RuntimeError('PR CI is pending or failing')
    return [{'name': check.get('name'), 'conclusion': outcome(check)} for check in checks]


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        target.write(content)


def reserve_attempt(directory, retry_unstarted):
    if directory.exists():
        if not retry_unstarted:
            raise RuntimeError('this PR head already has a review attempt; inspect its receipt')
        facts_path = directory / 'facts.jsonl'
        try:
            facts = [json.loads(line) for line in facts_path.read_text().splitlines()]
        except (OSError, json.JSONDecodeError):
            raise RuntimeError('cannot prove the previous attempt did not call the model') from None
        if (not facts or facts[-1].get('state') != 'closed'
                or any(item.get('state') == 'prompt_delivery_started' for item in facts)):
            raise RuntimeError('previous model call is possible; retry refused')
        directory.rename(directory.with_name(directory.name + '-unstarted-' + secrets.token_hex(4)))
    directory.mkdir(mode=0o700, exist_ok=False)


def invoke(command_line, input_text, directory, timeout_seconds=1800):
    def interrupted(_number, _frame):
        for number in signals:
            signal.signal(number, signal.SIG_IGN)
        raise KeyboardInterrupt
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in signals}
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    incomplete_reason = None
    try:
        for number in signals:
            signal.signal(number, interrupted)
        process = subprocess.Popen(command_line, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True, cwd=directory)
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            stdout, stderr = process.communicate(input_text, timeout=timeout_seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            incomplete_reason = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'interrupted'
            for number in signals:
                signal.signal(number, signal.SIG_IGN)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired as final:
                    incomplete_reason = 'kill_timeout'
                    stdout = final.stdout or ''
                    stderr = final.stderr or ''
                    stdout = stdout.decode(errors='replace') if isinstance(stdout, bytes) else stdout
                    stderr = stderr.decode(errors='replace') if isinstance(stderr, bytes) else stderr
                    for pipe in (process.stdout, process.stderr):
                        if pipe:
                            pipe.close()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        for number in signals:
            signal.signal(number, signal.SIG_IGN)
        write_private(directory / 'events.jsonl', stdout)
        write_private(directory / 'stderr.txt', stderr)
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        for number, handler in previous.items():
            signal.signal(number, handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            incomplete_reason = incomplete_reason or 'malformed_stream'
    return process.returncode, events, incomplete_reason


def summarize(events, nonce, exit_code, incomplete_reason=None):
    settings = {}
    models = set()
    tools = []
    result = None
    for event in events:
        if event.get('type') == 'control_response':
            response = event.get('response', {})
            if response.get('request_id') in (nonce + '-before', nonce + '-after'):
                settings[response['request_id']] = response.get('response', {})
        if event.get('type') == 'assistant':
            message = event.get('message', {})
            if message.get('model'):
                models.add(message['model'])
            tools.extend(item.get('name') for item in message.get('content', [])
                         if isinstance(item, dict) and item.get('type') == 'tool_use')
        if event.get('type') == 'result':
            result = event
    applied = [settings.get(nonce + suffix, {}) for suffix in ('-before', '-after')]
    configuration_verified = (all(item.get('applied') == APPLIED and item.get('has_errors') is False
                                  for item in applied) and models == {MODEL})
    subagents = result.get('subagent_stats') if result else None
    subagents = subagents if isinstance(subagents, dict) else {}
    killed = subagents.get('killed')
    refused = subagents.get('refused')
    killed = killed if isinstance(killed, dict) else {'invalid': 1}
    refused = refused if isinstance(refused, dict) else {'invalid': 1}
    completion_verified = (configuration_verified and incomplete_reason is None and exit_code == 0
                           and result is not None and result.get('subtype') == 'success'
                           and not result.get('is_error') and result.get('stop_reason') == 'end_turn'
                           and result.get('terminal_reason') == 'completed'
                           and result.get('queued_turn_count') == 0
                           and subagents.get('spawned', 0) == subagents.get('completed', 0)
                           and subagents.get('failed', 0) == 0
                           and not any(killed.values()) and not any(refused.values()))
    report = result.get('result', '') if result else ''
    verdict_pattern = r'(?:\*\*)?판정:\s*(PASS|REVISE|INCOMPLETE)(?:\*\*)?'
    lines = report.strip().splitlines()
    first_verdict = re.fullmatch(verdict_pattern, lines[0].strip()) if lines else None
    verdicts = re.findall(r'^\s*' + verdict_pattern + r'\s*$', report, re.MULTILINE)
    verdict = first_verdict.group(1) if first_verdict and len(verdicts) == 1 else None
    return {'configuration_verified': configuration_verified, 'completion_verified': completion_verified,
            'review_passed': completion_verified and verdict == 'PASS', 'verdict': verdict,
            'incomplete_reason': incomplete_reason,
            'applied_before_after': [item.get('applied') for item in applied],
            'response_models': sorted(models), 'workflow_tool_used': 'Workflow' in tools,
            'tools_used': sorted(set(tools)), 'cost_usd_estimate': result.get('total_cost_usd') if result else None,
            'result': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pr', type=int)
    parser.add_argument('--retry-unstarted', action='store_true', help='Archive a closed attempt that never sent a prompt')
    args = parser.parse_args()
    if args.pr < 1:
        parser.error('PR must be positive')
    head = command('git', 'rev-parse', 'HEAD').strip()
    if command('git', 'status', '--porcelain').strip():
        raise RuntimeError('review checkout must be clean')
    pr = json.loads(command('gh', 'pr', 'view', str(args.pr), '--json',
                            'number,url,state,headRefOid,statusCheckRollup'))
    checks = reviewable(pr, head)
    cli_name = shutil.which('claude')
    if not cli_name:
        raise RuntimeError('Claude Code CLI is not installed')
    cli = Path(cli_name).resolve()
    version = command(str(cli), '--version').strip()
    match = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', version)
    if not match or tuple(map(int, match.groups())) < (2, 1, 280):
        raise RuntimeError('Claude Code 2.1.280 or newer is required for Opus 5.5')
    patch = command('gh', 'pr', 'diff', str(args.pr), '--patch')
    if not patch.strip():
        raise RuntimeError('PR diff is empty')
    if json.loads(command('gh', 'pr', 'view', str(args.pr), '--json', 'headRefOid'))['headRefOid'] != head:
        raise RuntimeError('PR head changed during preflight')
    if not CONTROL.is_file():
        raise RuntimeError('trusted Claude relay is missing')
    parent = Path.home() / '.local/state/ai-company/claude-pr-reviews'
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent.chmod(0o700)
    directory = parent / f'pr-{args.pr}-{head}'
    reserve_attempt(directory, args.retry_unstarted)
    write_private(directory / 'pr.diff', patch)
    nonce = secrets.token_hex(16)
    environment = {key: os.environ[key] for key in ('HOME', 'USER', 'PATH', 'LANG', 'TERM', 'XDG_RUNTIME_DIR')
                   if key in os.environ}
    environment['CLAUDE_CODE_MAX_RETRIES'] = '0'
    settings = {'enableWorkflows': True, 'ultracode': True, 'disableAllHooks': True,
                'enabledPlugins': {}, 'fallbackModel': [], 'switchModelsOnFlag': False}
    repo = Path(command('git', 'rev-parse', '--show-toplevel').strip()).resolve()
    config = {'cli_executable': str(cli), 'cli_sha256': hashlib.sha256(cli.read_bytes()).hexdigest(),
              'binding': {'nonce': nonce, 'pr': args.pr, 'head': head},
              'facts_path': str(directory / 'facts.jsonl'), 'environment': environment,
              'expected_applied': APPLIED,
              'extra_args': ['--restricted', '--strict-mcp-config', '--permission-mode', 'dontAsk',
                             '--tools', 'Read,Grep,Glob,Workflow,Task,TaskOutput,TaskStop',
                             '--add-dir', str(repo), '--settings', json.dumps(settings),
                             '--no-session-persistence']}
    write_private(directory / 'config.json', json.dumps(config))
    prompt = (f'PR #{args.pr}, 최종 HEAD {head}를 읽기 전용으로 독립 검수하세요. '
              f'전체 패치는 {directory / "pr.diff"}에 있습니다. 현재 저장소 코드와 대조하세요. '
              '정확성·권한·재시작·회귀를 우선하고 실제 결함만 파일과 근거로 보고하세요. '
              '검토하지 못한 파일과 도구 제한을 명시하세요. 부분 검토를 PASS라고 하지 마세요. '
              '테스트 실행이나 파일 수정은 하지 마세요. 보고서 첫 줄에 정확히 '
              '`판정: PASS`, `판정: REVISE`, `판정: INCOMPLETE` 중 하나만 한 번 쓰세요.')
    events = [
        {'type': 'control_request', 'request_id': 'init', 'request': {'subtype': 'initialize'}},
        {'type': 'user', 'message': {'role': 'user', 'content': prompt}},
    ]
    cmd = [sys.executable, '-I', '-B', str(CONTROL), str(directory / 'config.json'),
           '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
           '--model', MODEL, '--effort', 'ultracode']
    code, output, incomplete_reason = invoke(cmd, '\n'.join(json.dumps(event) for event in events) + '\n', directory)
    summary = summarize(output, nonce, code, incomplete_reason)
    write_private(directory / 'report.md', summary.pop('result') or
                  '검수 미완료: 모델 응답이나 결과가 없습니다. facts.jsonl과 events.jsonl을 확인하세요.\n')
    write_private(directory / 'receipt.json', json.dumps({
        'pr': args.pr, 'url': pr['url'], 'head': head, 'checks': checks,
        'diff_sha256': hashlib.sha256(patch.encode()).hexdigest(),
        'cli_version': version, 'cli_sha256': config['cli_sha256'],
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'relay_sha256': hashlib.sha256(CONTROL.read_bytes()).hexdigest(), **summary}, indent=2) + '\n')
    print(json.dumps({'receipt': str(directory / 'receipt.json'),
                      'report': str(directory / 'report.md'),
                      'configuration_verified': summary['configuration_verified'],
                      'completion_verified': summary['completion_verified'], 'verdict': summary['verdict'],
                      'workflow_tool_used': summary['workflow_tool_used']}))
    return 0 if summary['review_passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
