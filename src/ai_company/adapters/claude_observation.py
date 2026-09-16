"""Bounded local CLI telemetry, distinct from model-generated report content."""
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import threading
import time

from ai_company.runtime import ExecutionBlocked
from ai_company.contracts import digest


CLAUDE_VERSION = '2.1.270'
CLAUDE_SHA256 = '7bf9f33acc124df9abccf6f2366397a82a740378d535fa12d426fa77fdbc9946'
SOCAT_SHA256 = '8bce608454b1f027e42ad6d9e49935dfad44605da7f7f138b06a0f36b38d6025'
FIELDS = frozenset(('session.id', 'app.version', 'event.name', 'event.timestamp', 'event.sequence',
                    'model', 'effort', 'prompt.id', 'request_id', 'client_request_id', 'query_source',
                    'cost_usd', 'input_tokens', 'output_tokens', 'duration_ms', 'workflow.run_id'))


def persisted_attempt(db, job):
    row = db.execute("""SELECT occurred_at,document FROM session_events WHERE job_id=?
        AND json_extract(document,'$.attempt_count')=? AND json_extract(document,'$.status')='RUNNING'
        ORDER BY sequence LIMIT 1""", (job['job_id'], job['attempt_count'])).fetchone()
    if not row:
        raise ExecutionBlocked('Claude observation has no persisted attempt')
    claimed = json.loads(row[1])
    expected = claimed['checkpoint']['expected_report']
    binding = {key: expected[key] for key in ('execution_id', 'generation', 'role', 'task_digest', 'policy_digest')}
    binding.update(job_id=claimed['job_id'], attempt=claimed['attempt_count'], task_id=claimed['task_id'],
                   worktree=claimed['worktree'], previous_session_id=claimed['session_id'],
                   input_snapshot_digest=digest(claimed['repository_snapshot']))
    binding['nonce'] = digest(binding)[:32]
    return binding, row[0]


def _attributes(items):
    if not isinstance(items, list) or len(items) > 256:
        raise ValueError('invalid telemetry attributes')
    result = {}
    for item in items:
        if not isinstance(item, dict) or item.get('key') not in FIELDS:
            continue
        value = item.get('value')
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError('invalid telemetry scalar')
        kind, scalar = next(iter(value.items()))
        if kind not in ('stringValue', 'intValue', 'doubleValue', 'boolValue'):
            raise ValueError('invalid telemetry scalar')
        if (not isinstance(scalar, (str, int, float, bool)) or len(str(scalar)) > 512
                or isinstance(scalar, float) and not math.isfinite(scalar)):
            raise ValueError('oversized telemetry scalar')
        if item['key'] in result and result[item['key']] != scalar:
            raise ValueError('conflicting telemetry attribute')
        result[item['key']] = scalar
    return result


class ClaudeTelemetry:
    """One random loopback endpoint per attempt; no raw body or secret export."""
    def __init__(self):
        self.records = []
        self.errors = []
        self.bytes_received = 0
        self.closed = False
        token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def do_POST(self):
                if self.path != '/' + token + '/v1/logs':
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2_000_000 or owner.bytes_received + length > 8_000_000:
                        raise ValueError('telemetry size limit')
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError('incomplete telemetry body')
                    owner.bytes_received += length
                    owner.accept(json.loads(raw), time.time())
                except (ValueError, TypeError, KeyError, OSError):
                    if len(owner.errors) < 10:
                        owner.errors.append('invalid native telemetry batch')
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/{token}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
        self.thread.start()

    def accept(self, batch, received_at):
        if not isinstance(batch, dict) or not isinstance(batch.get('resourceLogs'), list):
            raise ValueError('invalid telemetry batch')
        for resource in batch['resourceLogs']:
            if (not isinstance(resource, dict) or not isinstance(resource.get('resource', {}), dict)
                    or not isinstance(resource.get('scopeLogs', []), list)):
                raise ValueError('invalid telemetry resource')
            common = _attributes(resource.get('resource', {}).get('attributes', []))
            for scope in resource.get('scopeLogs', []):
                if not isinstance(scope, dict) or not isinstance(scope.get('logRecords', []), list):
                    raise ValueError('invalid telemetry scope')
                for record in scope.get('logRecords', []):
                    if not isinstance(record, dict):
                        raise ValueError('invalid telemetry record')
                    attrs = _attributes(record.get('attributes', []))
                    if any(key in common and common[key] != value for key, value in attrs.items()):
                        raise ValueError('conflicting telemetry resource')
                    attrs = {**common, **attrs}
                    if attrs.get('event.name') not in ('api_request', 'api_error'):
                        continue
                    if len(self.records) >= 256:
                        raise ValueError('too many telemetry requests')
                    self.records.append({'received_at': received_at, 'attributes': attrs})

    def environment(self):
        return {'CLAUDE_CODE_ENABLE_TELEMETRY': '1', 'OTEL_LOGS_EXPORTER': 'otlp',
                'OTEL_METRICS_EXPORTER': 'none', 'OTEL_TRACES_EXPORTER': 'none',
                'OTEL_EXPORTER_OTLP_PROTOCOL': 'http/json', 'OTEL_EXPORTER_OTLP_ENDPOINT': self.endpoint,
                'OTEL_LOGS_EXPORT_INTERVAL': '250', 'OTEL_METRICS_INCLUDE_SESSION_ID': 'true',
                'OTEL_METRICS_INCLUDE_VERSION': 'true', 'OTEL_METRICS_INCLUDE_ACCOUNT_UUID': 'false',
                'OTEL_METRICS_INCLUDE_USER_EMAIL': 'false', 'OTEL_LOG_USER_PROMPTS': '0',
                'OTEL_LOG_TOOL_DETAILS': '0', 'OTEL_LOG_TOOL_CONTENT': '0', 'OTEL_LOG_MODEL_RESPONSES': '0'}

    def close(self):
        if not self.closed:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.closed = not self.thread.is_alive()


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate_claude_observation(evidence, *, binding, session_id, started_at, ended_at, writable_paths):
    """Validate the new contract only; never reinterpret legacy CLI evidence."""
    def require(condition, message):
        if not condition:
            raise ExecutionBlocked(message)
    require(isinstance(evidence, dict), 'Claude CLI observation is missing')
    require(evidence.get('source') == 'claude_cli_request_v1' and evidence.get('scope') == 'cli_request_configuration'
            and evidence.get('binding') == binding and evidence.get('session_id') == session_id
            and evidence.get('cli_version') == CLAUDE_VERSION and evidence.get('cli_sha256') == CLAUDE_SHA256
            and evidence.get('backend_model_verified') is False, 'Claude observation identity or runtime differs')
    begin, end = evidence.get('started_at'), evidence.get('ended_at')
    require(_number(begin) and _number(end) and started_at <= begin <= end <= ended_at,
            'Claude observation is outside the persisted attempt')
    require(evidence.get('collector_closed') is True and evidence.get('cgroup_stopped') is True
            and evidence.get('telemetry_errors') == [], 'Claude observation or process termination is incomplete')
    applied = evidence.get('applied')
    require(isinstance(applied, list) and len(applied) == 2,
            'Claude applied configuration is incomplete')
    ids = set()
    for index, record in enumerate(applied):
        require(isinstance(record, dict), 'invalid Claude applied configuration')
        request_id = record.get('request_id')
        require(request_id == binding['nonce'] + ('-before' if index == 0 else '-after') and request_id not in ids,
                'Claude applied response belongs to another request')
        ids.add(request_id)
        require(record.get('applied') == {'model': 'claude-opus-5', 'effort': 'xhigh', 'ultracode': True}
                and record.get('has_errors') is False, 'Claude applied model, effort or Ultracode differs')
    expected_tools = {'mcp__company_files__read_file'}
    if writable_paths:
        expected_tools.add('mcp__company_files__write_file')
    tools = evidence.get('native_tools')
    require(isinstance(tools, list) and all(isinstance(tool, str) for tool in tools) and len(tools) == len(set(tools))
            and expected_tools.issubset(tools) and set(tools) <= expected_tools | {'StructuredOutput'}
            and evidence.get('mcp_servers') == [{'name': 'company_files', 'status': 'connected'}]
            and not evidence.get('mcp_server_errors'), 'Claude file tool catalog differs from the assigned scope')
    broker = evidence.get('broker')
    require(isinstance(broker, dict) and broker.get('writable_paths') == list(writable_paths)
            and broker.get('binding') == binding and broker.get('closed') is True
            and broker.get('invalid') is False, 'Claude file broker scope or termination differs')
    requests = evidence.get('api_requests')
    require(isinstance(requests, list) and 1 <= len(requests) <= 256, 'Claude API request evidence is missing')
    seen = {}
    main_count = 0
    for request in requests:
        require(isinstance(request, dict) and isinstance(request.get('attributes'), dict), 'invalid Claude API observation')
        attrs = request['attributes']
        received = request.get('received_at')
        require(_number(received) and begin <= received <= end and attrs.get('session.id') == session_id
                and attrs.get('app.version') == CLAUDE_VERSION, 'Claude API observation belongs to another execution')
        try:
            timestamp = datetime.fromisoformat(attrs['event.timestamp'].replace('Z', '+00:00'))
            require(timestamp.tzinfo is not None and begin <= timestamp.timestamp() <= end,
                    'Claude API observation has a stale timestamp')
        except (ValueError, KeyError, AttributeError):
            raise ExecutionBlocked('Claude API observation has no valid timestamp') from None
        require(attrs.get('event.name') == 'api_request', 'Claude API request did not complete successfully')
        identity = attrs.get('request_id')
        require(isinstance(identity, str) and 1 <= len(identity) <= 200, 'Claude API request identity is missing')
        require(identity not in seen or seen[identity] == attrs, 'conflicting duplicate Claude API request')
        if identity in seen:
            continue
        seen[identity] = attrs
        if attrs.get('query_source') == 'generate_session_title':
            require(attrs.get('model') == 'claude-haiku-4-5-20251001', 'unrecognized Claude auxiliary model')
            continue
        require(attrs.get('query_source') == 'sdk' and attrs.get('model') == 'claude-opus-5'
                and attrs.get('effort') == 'xhigh' and not attrs.get('workflow.run_id'),
                'Claude task request model, effort or tool workflow differs')
        main_count += 1
    require(main_count > 0, 'Claude task has no observed model request')


def validate_claude_result(result, **scope):
    validate_claude_observation(result.get('configuration_evidence'), **scope)
    if (result.get('observed_models') not in (None, [], ['claude-opus-5'])
            or result.get('observed_efforts') not in (None, [], ['xhigh'])
            or result.get('observed_ultracode') not in (None, [], [True])):
        raise ExecutionBlocked('Claude native stream conflicts with the observed CLI request configuration')
