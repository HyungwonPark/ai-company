"""Opt-in Claude file adapter. The persistent queue owns retries and termination.

Native settings, API requests and the file broker share one attempt binding.
This contract verifies local CLI configuration, not a backend model attestation.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

from ai_company.adapters.claude_observation import (
    CLAUDE_SHA256, CLAUDE_VERSION, SOCAT_SHA256, ClaudeTelemetry,
)
from ai_company.adapters.session_cli import run_session, SessionOutcome
from ai_company.adapters.workspace_files import FileToolError, WorkspaceFiles, task_path
from ai_company.runtime import ExecutionBlocked


def candidate_eligible(agent, spec, role):
    if (agent.provider, agent.model, agent.reasoning_effort, agent.ultracode_enabled) != ('claude', 'claude-opus-5', 'xhigh', True):
        return False
    if role != 'developer':
        return True
    # Contributions already have a runner-owned commit step. A legacy full
    # developer must not be granted Git or shell access to make this work.
    if spec.execution_scope != 'contribution':
        return False
    try:
        return bool(spec.task.allowed_paths) and all(
            task_path(path) == path and not any(ch in path for ch in '*?[]')
            and not (Path(spec.worktree) / path).is_dir() for path in spec.task.allowed_paths)
    except FileToolError:
        return False


def _write(path, value, *, executable=False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700 if executable else 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _records(path):
    if not path.exists() or path.stat().st_size > 8_000_000:
        raise ValueError('missing or oversized CLI record')
    result = []
    with path.open() as stream:
        for line in stream:
            if len(line) > 2_000_000 or len(result) >= 4096:
                raise ValueError('CLI record limit')
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError('invalid CLI record')
            result.append(item)
    return result


def _broker(path, binding, writable_paths, begin, end):
    result = {'binding': binding, 'writable_paths': list(writable_paths), 'closed': False, 'invalid': True}
    rows = _records(path)
    if (len(rows) < 2 or rows[0].get('state') != 'ready' or rows[-1].get('state') != 'closed'
            or rows[0].get('binding') != binding or rows[0].get('worktree') != binding['worktree']
            or rows[0].get('writable_paths') != list(writable_paths)):
        return result
    pending = None
    previous_at = begin
    for index, row in enumerate(rows):
        at = row.get('at')
        if (row.get('sequence') != index + 1 or isinstance(at, bool) or not isinstance(at, (int, float))
                or not math.isfinite(at) or not previous_at <= at <= end):
            return result
        previous_at = at
        if index in (0, len(rows) - 1):
            continue
        identity = tuple(row.get(key) for key in ('request_id', 'tool', 'arguments_sha256'))
        if row.get('state') == 'started' and pending is None:
            if row.get('tool') not in ('read_file', 'write_file') or (row['tool'] == 'write_file' and not writable_paths):
                return result
            pending = identity
        elif row.get('state') in ('completed', 'failed') and pending == identity:
            pending = None
        else:
            return result
    result.update(closed=True, invalid=pending is not None, calls=(len(rows) - 2) // 2,
                  sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def run_claude_files(provider, worktree, prompt, session_id, *, binding, writable_paths=(),
                     timeout_seconds=300, output_dir, on_spawn=None, model='claude-opus-5',
                     reasoning_effort='xhigh', ultracode_enabled=True, output_schema=None, max_cost_usd=None,
                     shared_call_controlled=False):
    if (provider, model, reasoning_effort, ultracode_enabled) != ('claude', 'claude-opus-5', 'xhigh', True):
        raise ExecutionBlocked('file adapter requires the reviewed Claude configuration')
    if (binding.get('worktree') != str(Path(worktree).resolve())
            or binding.get('previous_session_id') != session_id):
        raise ExecutionBlocked('file adapter differs from the persisted attempt')
    started_at, monotonic_start = time.time(), time.monotonic()
    directory = Path(output_dir).resolve() / ('claude-' + binding['nonce'])
    if directory.is_relative_to(Path(worktree).resolve()):
        raise ExecutionBlocked('CLI observation must be outside the model workspace')
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        WorkspaceFiles(Path(worktree), tuple(writable_paths)).verify_runtime()
        native = Path(shutil.which('claude') or '/missing-claude').resolve(strict=True)
        socat = Path(os.environ.get('AI_COMPANY_CLAUDE_SOCAT') or shutil.which('socat') or '/missing-socat').resolve(strict=True)
        if (hashlib.sha256(native.read_bytes()).hexdigest() != CLAUDE_SHA256
                or hashlib.sha256(socat.read_bytes()).hexdigest() != SOCAT_SHA256
                or (socat.parent / 'socat').resolve(strict=True) != socat):
            raise FileToolError('reviewed Claude or socat executable differs')
    except (OSError, FileToolError):
        # No process has been created and no prompt delivered at this boundary.
        return SessionOutcome('permission', session_id=session_id, message='reviewed Claude file runtime unavailable',
                              result={'total_cost_usd': 0, 'duration_seconds': time.monotonic() - monotonic_start,
                                      'prompt_delivered': False})
    cap = min(.5, max_cost_usd) if max_cost_usd is not None else .5
    if not math.isfinite(cap) or cap <= 0:
        raise ExecutionBlocked('Claude attempt budget is exhausted')
    collector = ClaudeTelemetry()
    try:
        environment = {'HOME': str(Path.home()), 'USER': os.environ.get('USER', ''), 'LOGNAME': os.environ.get('LOGNAME', ''),
                       'LANG': 'C.UTF-8', 'PATH': str(socat.parent) + ':/usr/bin:/bin',
                       'CLAUDE_CODE_MAX_RETRIES': '0', 'API_TIMEOUT_MS': '45000', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '4096',
                       **collector.environment()}
        package_root = str(Path(__file__).resolve().parents[2])
        mcp_args = ['-i', 'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', 'PYTHONPATH=' + package_root,
                    sys.executable, '-B', '-m', 'ai_company.adapters.workspace_mcp', '--worktree', str(worktree),
                    '--audit', str(directory / 'file-audit.jsonl'), '--binding', json.dumps(binding)]
        for path in writable_paths:
            mcp_args += ['--write-path', path]
        mcp = {'mcpServers': {'company_files': {'command': '/usr/bin/env', 'args': mcp_args}}}
        settings = {'disableAllHooks': True, 'enabledPlugins': {}, 'fallbackModel': [], 'ultracode': True,
                    'enableWorkflows': True, 'sandbox': {'enabled': True, 'failIfUnavailable': True,
                    'allowUnsandboxedCommands': False, 'network': {'allowedDomains': [], 'strictAllowlist': True}}}
        names = ['mcp__company_files__read_file'] + (['mcp__company_files__write_file'] if writable_paths else [])
        extra = ['--restricted', '--permission-mode', 'dontAsk', '--tools', '', '--allowedTools', ','.join(names),
                 '--max-turns', '12', '--strict-mcp-config', '--mcp-config', json.dumps(mcp),
                 '--disable-slash-commands', '--no-chrome', '--settings', json.dumps(settings)]
        config = {'cli_executable': str(native), 'cli_sha256': CLAUDE_SHA256, 'binding': binding,
                  'facts_path': str(directory / 'control.jsonl'), 'environment': environment, 'extra_args': extra}
        _write(directory / 'config.json', json.dumps(config))
        command = ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', sys.executable,
                   '-I', '-B', str(Path(__file__).with_name('claude_control.py')), str(directory / 'config.json')]
        _write(directory / 'relay', '#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n', executable=True)
        remaining = timeout_seconds - (time.monotonic() - monotonic_start)
        if remaining <= 0:
            raise ExecutionBlocked('Claude observation setup exhausted the attempt time')
        outcome = run_session(provider, worktree, prompt, session_id, timeout_seconds=remaining,
                              output_dir=directory, on_spawn=on_spawn, executable=str(directory / 'relay'),
                              model=model, reasoning_effort=reasoning_effort, ultracode_enabled=ultracode_enabled,
                              output_schema=output_schema, capture_configuration=True, isolate_cgroup=True,
                              max_cost_usd=cap, shared_call_controlled=shared_call_controlled)
    finally:
        collector.close()
    ended_at = time.time()
    result = outcome.result
    evidence = {'source': 'claude_cli_request_v1', 'scope': 'cli_request_configuration',
                'binding': binding, 'session_id': outcome.session_id, 'cli_version': CLAUDE_VERSION,
                'cli_sha256': CLAUDE_SHA256, 'backend_model_verified': False, 'started_at': started_at,
                'ended_at': ended_at, 'collector_closed': collector.closed, 'cgroup_stopped': result.get('cgroup_stopped'),
                'api_requests': collector.records, 'telemetry_errors': collector.errors}
    try:
        facts = _records(directory / 'control.jsonl')
        if (not facts or facts[0].get('binding') != binding or facts[-1].get('state') != 'closed'):
            raise ValueError('incomplete Claude control record')
        evidence['applied'] = [{k: row[k] for k in ('request_id', 'applied', 'has_errors')}
                               for row in facts if row.get('state') == 'applied']
        native_events = _records(Path(result['stdout_path']))
        init = [row for row in native_events if row.get('type') == 'system' and row.get('subtype') == 'init']
        if len(init) != 1 or init[0].get('session_id') != outcome.session_id:
            raise ValueError('missing bound native initialization')
        evidence.update({key: init[0].get(key, []) for key in ('mcp_servers', 'mcp_server_errors')})
        evidence['native_tools'] = init[0].get('tools')
        evidence['broker'] = _broker(directory / 'file-audit.jsonl', binding, writable_paths, started_at, ended_at)
    except (OSError, ValueError, KeyError, TypeError):
        evidence['telemetry_errors'].append('incomplete local CLI or file broker record')
    _write(directory / 'observation.json', json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    result.update(configuration_evidence=evidence, duration_seconds=time.monotonic() - monotonic_start,
                  claude_attempt_budget_usd=cap)
    return outcome
