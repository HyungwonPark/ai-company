import io
import json
import tempfile
import unittest
from unittest.mock import Mock

from ai_company.adapters.workspace_files import FileToolError
from ai_company.adapters.workspace_mcp import FileServer, MAX_BYTES


class WorkspaceMCPTest(unittest.TestCase):
    def setUp(self):
        self.audit = tempfile.TemporaryFile(mode='w+')
        self.addCleanup(self.audit.close)
        self.files = Mock(writable_paths=('src/task.py',))
        self.server = FileServer(self.files, self.audit)

    def request(self, method, params=None):
        return self.server.respond({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})

    def ready(self):
        self.request('initialize', {'protocolVersion': '2025-11-25'})
        self.server.respond({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def test_lifecycle_and_readonly_catalog(self):
        self.assertIn('error', self.request('tools/list'))
        self.ready()
        self.assertEqual([t['name'] for t in self.request('tools/list')['result']['tools']], ['read_file', 'write_file'])
        self.files.writable_paths = ()
        self.assertEqual([t['name'] for t in self.request('tools/list')['result']['tools']], ['read_file'])
        self.assertIn('error', self.request('tools/call', {'name': 'write_file', 'arguments': {'path': 'src/task.py', 'text': 'x'}}))
        self.files.call.assert_not_called()

    def test_tools_cannot_select_commands_or_change_scope(self):
        self.ready()
        for name, args in [([], {}), ({}, {}), ('shell', {'argv': ['sh']}), ('write_file', {'path': 'src/task.py', 'text': 'x', 'worktree': '/'}),
                           ('read_file', {'path': 'src/task.py', 'text': 'x'})]:
            self.assertIn('error', self.request('tools/call', {'name': name, 'arguments': args}))
        self.files.call.assert_not_called()

    def test_tool_failure_stays_error_and_audit_has_no_contents(self):
        self.ready()
        self.files.call.side_effect = FileToolError('outside assignment')
        result = self.request('tools/call', {'name': 'write_file', 'arguments': {'path': 'outside', 'text': 'private-fixture-value'}})
        self.assertTrue(result['result']['isError'])
        self.audit.seek(0)
        text = self.audit.read()
        self.assertNotIn('private-fixture-value', text)
        self.assertEqual([json.loads(l)['state'] for l in text.splitlines()], ['started', 'failed'])

    def test_stdio_invalid_and_oversized_input_does_not_execute(self):
        output = io.StringIO()
        self.server.serve(io.StringIO('not-json\n[]\n'), output)
        self.assertEqual([json.loads(l)['error']['code'] for l in output.getvalue().splitlines()], [-32700, -32600])
        with self.assertRaises(FileToolError):
            self.server.serve(io.StringIO(' ' * (MAX_BYTES * 6 + 4097)), io.StringIO())
        self.files.call.assert_not_called()
