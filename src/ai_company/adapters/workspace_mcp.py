"""Stdio MCP transport for the file-only sandbox. No HTTP or shell tool."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from ai_company.adapters.workspace_files import FileToolError, MAX_BYTES, WorkspaceFiles


def tool_list(writable):
    result = [{'name': 'read_file', 'description': 'Read a UTF-8 repository file through the isolated file tool.',
               'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                               'required': ['path'], 'additionalProperties': False}}]
    if writable:
        result.append({'name': 'write_file', 'description': 'Write a UTF-8 file assigned to this role. Other paths are refused.',
                       'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'text': {'type': 'string'}},
                                       'required': ['path', 'text'], 'additionalProperties': False}})
    return result


class FileServer:
    def __init__(self, files, audit):
        self.files, self.audit = files, audit
        self.initialized = self.ready = False
        self.sequence = 0

    def record(self, data):
        self.sequence += 1
        self.audit.write(json.dumps({'sequence': self.sequence, 'at': time.time(), **data}, ensure_ascii=False) + '\n')
        self.audit.flush()
        os.fsync(self.audit.fileno())

    def respond(self, request):
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
        ident = request.get('id')
        method = request.get('method')
        if 'id' not in request:
            if method == 'notifications/initialized' and self.initialized:
                self.ready = True
            return None
        reply = {'jsonrpc': '2.0', 'id': ident}
        def error(code, message):
            return {**reply, 'error': {'code': code, 'message': message}}
        if isinstance(ident, bool) or not isinstance(ident, (str, int)) or len(str(ident)) > 200:
            return {**error(-32600, 'Invalid request ID'), 'id': None}
        params = request.get('params', {})
        if not isinstance(params, dict):
            return error(-32602, 'Invalid parameters')
        if method == 'initialize' and not self.initialized:
            self.initialized = True
            version = params.get('protocolVersion')
            if version not in ('2025-03-26', '2025-06-18', '2025-11-25'):
                version = '2025-11-25'
            result = {'protocolVersion': version, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'ai-company-workspace-files', 'version': '1'}}
        elif method == 'ping':
            result = {}
        elif not self.ready:
            return error(-32002, 'Server is not initialized')
        elif method == 'tools/list':
            result = {'tools': tool_list(bool(self.files.writable_paths))}
        elif method == 'tools/call':
            name, args = params.get('name'), params.get('arguments', {})
            expected = {'path'} if name == 'read_file' else {'path', 'text'}
            if not isinstance(name, str) or name not in {tool['name'] for tool in tool_list(bool(self.files.writable_paths))}:
                return error(-32602, 'Unknown file tool')
            if not isinstance(args, dict) or set(args) != expected:
                return error(-32602, 'Invalid file tool arguments')
            # No content in host audit: correlate the native MCP request with
            # file digests, start/end facts and a single fixed tool policy.
            facts = {'request_id': ident, 'tool': name,
                     'arguments_sha256': hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()}
            self.record({**facts, 'state': 'started'})
            try:
                value = self.files.call('read' if name == 'read_file' else 'write', **args)
                self.record({**facts, 'state': 'completed', 'path': value['path'], 'sha256': value['sha256']})
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}],
                          'isError': False}
            except (FileToolError, UnicodeError) as exc:
                self.record({**facts, 'state': 'failed', 'reason': str(exc)[:200]})
                result = {'content': [{'type': 'text', 'text': str(exc)[:200]}], 'isError': True}
        else:
            return error(-32601, 'Method not found')
        return {**reply, 'result': result}

    def serve(self, source, destination):
        while line := source.readline(MAX_BYTES * 6 + 4097):
            if len(line) > MAX_BYTES * 6 + 4096:
                # Terminate this transport on an oversized message; never
                # parse a truncated suffix as a second request.
                raise FileToolError('MCP request exceeds byte limit')
            try:
                request = json.loads(line)
            except ValueError:
                reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}
            else:
                reply = self.respond(request)
            if reply is not None:
                destination.write(json.dumps(reply, ensure_ascii=False) + '\n')
                destination.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worktree', type=Path, required=True)
    parser.add_argument('--write-path', action='append', default=[])
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--binding', type=json.loads)
    args = parser.parse_args()
    files = WorkspaceFiles(args.worktree, tuple(args.write_path))
    files.verify_runtime()
    audit_path = args.audit.resolve()
    if audit_path.is_relative_to(files.worktree):
        raise FileToolError('host audit must be outside the model workspace')
    fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as audit:
        server = FileServer(files, audit)
        server.record({'state': 'ready', 'worktree': str(files.worktree), 'writable_paths': files.writable_paths,
                       'binding': args.binding})
        try:
            server.serve(sys.stdin, sys.stdout)
        finally:
            server.record({'state': 'closed'})


if __name__ == '__main__':
    main()
