from copy import deepcopy
from datetime import datetime, timezone
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

from ai_company.adapters.claude_observation import (
    CLAUDE_SHA256, CLAUDE_VERSION, ClaudeTelemetry, validate_claude_observation, validate_claude_result,
)
from ai_company.runtime import ExecutionBlocked


def observed(binding, sid, start, end, paths):
    return {'source': 'claude_cli_request_v1', 'scope': 'cli_request_configuration',
        'binding': deepcopy(binding), 'session_id': sid, 'cli_version': CLAUDE_VERSION,
        'cli_sha256': CLAUDE_SHA256, 'backend_model_verified': False,
        'started_at': start, 'ended_at': end, 'collector_closed': True, 'cgroup_stopped': True,
        'telemetry_errors': [], 'applied': [{'request_id': binding['nonce'] + suffix,
            'applied': {'model': 'claude-opus-5', 'effort': 'xhigh', 'ultracode': True}, 'has_errors': False}
            for suffix in ('-before', '-after')],
        'native_tools': ['mcp__company_files__read_file'] + (['mcp__company_files__write_file'] if paths else []),
        'mcp_servers': [{'name': 'company_files', 'status': 'connected'}], 'mcp_server_errors': [],
        'broker': {'binding': deepcopy(binding), 'writable_paths': list(paths), 'closed': True, 'invalid': False},
        'api_requests': [{'received_at': end, 'attributes': {'session.id': sid, 'app.version': CLAUDE_VERSION,
            'event.name': 'api_request', 'event.timestamp': datetime.fromtimestamp(start, timezone.utc).isoformat(),
            'request_id': 'request-1', 'query_source': 'sdk', 'model': 'claude-opus-5', 'effort': 'xhigh'}}]}


class ClaudeObservationTests(unittest.TestCase):
    def setUp(self):
        self.binding = {'nonce': 'a' * 32, 'job_id': 'job-1', 'attempt': 2, 'generation': 1,
                        'execution_id': 'execution', 'task_digest': 'task', 'policy_digest': 'policy'}
        self.evidence = observed(self.binding, 'session-1', 1001, 1002, ('src/code.py',))

    def validate(self, value):
        validate_claude_observation(value, binding=self.binding, session_id='session-1',
                                   started_at=1000, ended_at=1003, writable_paths=('src/code.py',))

    def test_local_configuration_and_auxiliary_requests_remain_distinct(self):
        e = self.evidence
        e['native_tools'].append('StructuredOutput')
        title = deepcopy(e['api_requests'][0])
        title['attributes'].update(request_id='title', query_source='generate_session_title', model='claude-haiku-4-5-20251001')
        title['attributes'].pop('effort')
        e['api_requests'].append(title)
        e['api_requests'].append(deepcopy(e['api_requests'][0]))
        before = deepcopy(e)
        self.validate(e)
        self.assertEqual(e, before)
        self.assertFalse(e['backend_model_verified'])

    def test_identity_settings_scope_and_termination_mismatches_are_rejected(self):
        mutations = [
            lambda e: e.update(source='model_self_report'), lambda e: e.update(scope='backend_attestation'),
            lambda e: e.update(session_id='another'), lambda e: e.update(cli_version='new'),
            lambda e: e.update(cli_sha256='0'*64), lambda e: e.update(backend_model_verified=True),
            lambda e: e.update(started_at=999), lambda e: e.update(ended_at=float('nan')),
            lambda e: e.update(collector_closed=False), lambda e: e.update(cgroup_stopped=False),
            lambda e: e.update(telemetry_errors=['lost batch']), lambda e: e.update(applied=[]),
            lambda e: e['applied'][0].update(request_id='old-before'),
            lambda e: e['applied'][1]['applied'].update(effort='high'),
            lambda e: e['applied'][0]['applied'].update(ultracode=False),
            lambda e: e['applied'][0].update(has_errors=True),
            lambda e: e['native_tools'].append('Bash'), lambda e: e['native_tools'].append(['bad']),
            lambda e: e['native_tools'].append(e['native_tools'][0]),
            lambda e: e.update(mcp_servers=[]), lambda e: e.update(mcp_server_errors=['unavailable']),
            lambda e: e['broker'].update(writable_paths=['src/']),
            lambda e: e['broker'].update(binding={}), lambda e: e['broker'].update(closed=False),
            lambda e: e['broker'].update(invalid=True), lambda e: e.update(api_requests=[]),
        ]
        for key in self.binding:
            mutations.append(lambda e, key=key: e['binding'].update({key: 'other'}))
        for mutate in mutations:
            e = deepcopy(self.evidence)
            mutate(e)
            with self.subTest(e=e), self.assertRaises(ExecutionBlocked):
                self.validate(e)

    def test_stale_wrong_model_failed_and_workflow_requests_are_rejected(self):
        changes = [{'session.id':'old'}, {'app.version':'unknown'}, {'event.timestamp':'1970-01-01T00:00:00Z'},
            {'event.timestamp':123}, {'event.name':'api_error'}, {'request_id':None},
            {'model':'claude-haiku-4-5-20251001'}, {'effort':'high'}, {'workflow.run_id':'unreviewed-child'},
            {'query_source':'unknown'}]
        for change in changes:
            e = deepcopy(self.evidence)
            e['api_requests'][0]['attributes'].update(change)
            with self.subTest(change=change), self.assertRaises(ExecutionBlocked):
                self.validate(e)
        e = deepcopy(self.evidence)
        changed = deepcopy(e['api_requests'][0]); changed['attributes']['effort'] = 'low'
        e['api_requests'].append(changed)
        with self.assertRaises(ExecutionBlocked): self.validate(e)

    def test_read_only_catalog_does_not_admit_write_tool(self):
        e = observed(self.binding, 'session-1', 1001, 1002, ())
        args = dict(binding=self.binding, session_id='session-1', started_at=1000, ended_at=1003, writable_paths=())
        validate_claude_observation(e, **args)
        e['native_tools'].append('mcp__company_files__write_file')
        with self.assertRaises(ExecutionBlocked): validate_claude_observation(e, **args)

    def test_conflicting_native_stream_metadata_is_not_hidden_by_settings(self):
        args = dict(binding=self.binding, session_id='session-1', started_at=1000, ended_at=1003, writable_paths=('src/code.py',))
        result = {'configuration_evidence':self.evidence, 'observed_models':['claude-opus-5']}
        validate_claude_result(result, **args)
        for key,value in [('observed_models',['other']), ('observed_efforts',['high']), ('observed_ultracode',[False])]:
            with self.subTest(key=key), self.assertRaises(ExecutionBlocked):
                validate_claude_result({**result,key:value}, **args)

    def test_collector_filters_sensitive_fields_and_survives_invalid_batches(self):
        collector = ClaudeTelemetry(); self.addCleanup(collector.close)
        url = collector.endpoint + '/v1/logs'
        batch = {'resourceLogs':[{'scopeLogs':[{'logRecords':[{'attributes':[
            {'key':'event.name','value':{'stringValue':'api_request'}},
            {'key':'model','value':{'stringValue':'claude-opus-5'}},
            {'key':'prompt','value':{'stringValue':'DO-NOT-STORE'}},
            {'key':'user.email','value':{'stringValue':'DO-NOT-STORE'}}]}]}]}]}
        def post(value):
            return urlopen(Request(url, data=json.dumps(value).encode(), headers={'Content-Type':'application/json'}), timeout=3)
        with post(batch) as response: self.assertEqual(response.status, 200)
        self.assertNotIn('DO-NOT-STORE', json.dumps(collector.records))
        for bad in [{'resourceLogs':[None]}, {'resourceLogs':[{'scopeLogs':[None]}]},
                    {'resourceLogs':[{'scopeLogs':[{'logRecords':[None]}]}]}]:
            with self.assertRaises(HTTPError): post(bad)
        with post(batch) as response: self.assertEqual(response.status, 200)
        self.assertEqual(len(collector.errors), 3)
        collector.close(); self.assertTrue(collector.closed)
        with self.assertRaises(OSError): post(batch)
