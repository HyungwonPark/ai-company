import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters import claude_control
from ai_company.adapters.claude_files import _broker, run_claude_files
from ai_company.adapters.workspace_files import FileToolError
from ai_company.runtime import ExecutionBlocked


NATIVE = '''#!/usr/bin/env python3
import json,os,sys
for line in sys.stdin:
    event=json.loads(line)
    if event['type']=='user':
        open(os.environ['MARKER'],'w').write('delivered')
        print(json.dumps({'type':'result','session_id':'native-session','result':'done'}),flush=True)
        continue
    req=event['request_id']
    payload={'account':{'secret':'PRIVATE_ACCOUNT'}}
    if event['request']['subtype']=='get_settings':
        payload={'applied':{'model':'claude-opus-5','effort':os.environ['EFFORT'],'ultracode':True},
                 'settings':{'env':{'SECRET':'PRIVATE_ENV'}},'errors':[]}
    print(json.dumps({'type':'control_response','response':{'request_id':req,'subtype':'success','response':payload}}),flush=True)
'''


class ClaudeFileAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def relay(self, effort):
        directory = self.root / effort; directory.mkdir()
        native = directory / 'native'; native.write_text(NATIVE); native.chmod(0o700)
        config = {'cli_executable':str(native), 'cli_sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
            'binding':{'nonce':'attempt-1'}, 'facts_path':str(directory/'facts.jsonl'), 'extra_args':[],
            'environment':{'PATH':os.environ['PATH'], 'EFFORT':effort, 'MARKER':str(directory/'delivered')}}
        path = directory/'config.json'; path.write_text(json.dumps(config))
        records = [{'type':'control_request','request_id':'init','request':{'subtype':'initialize'}},
                   {'type':'user','message':{'role':'user','content':'task'}}]
        result = subprocess.run([sys.executable, '-I', '-B', claude_control.__file__, str(path)],
            input='\n'.join(json.dumps(r) for r in records)+'\n', capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = [json.loads(line) for line in (directory/'facts.jsonl').read_text().splitlines()]
        return directory, result, facts

    def test_native_settings_precede_delivery_and_sensitive_control_fields_are_removed(self):
        directory, result, facts = self.relay('xhigh')
        self.assertTrue((directory/'delivered').exists())
        self.assertEqual([r['state'] for r in facts], ['starting','applied','prompt_delivery_started','applied','closed'])
        self.assertEqual(facts[1]['request_id'], 'attempt-1-before')
        self.assertEqual(facts[3]['request_id'], 'attempt-1-after')
        self.assertNotIn('PRIVATE_', result.stdout + json.dumps(facts))

    def test_wrong_applied_effort_refuses_prompt_without_a_model_request(self):
        directory, result, facts = self.relay('high')
        self.assertFalse((directory/'delivered').exists())
        self.assertIn('configuration_refused', [r['state'] for r in facts])
        self.assertNotIn('prompt_delivery_started', [r['state'] for r in facts])
        self.assertNotIn('PRIVATE_', result.stdout)

    def test_runtime_preflight_refuses_before_native_launch_and_does_not_replay(self):
        repo = self.root/'repo'; repo.mkdir()
        binding = {'nonce':'a'*32, 'worktree':str(repo), 'previous_session_id':None}
        with (patch('ai_company.adapters.claude_files.WorkspaceFiles.verify_runtime', side_effect=FileToolError('different profile')),
                patch('ai_company.adapters.claude_files.run_session') as native):
            result = run_claude_files('claude', repo, 'task', None, binding=binding, output_dir=self.root/'logs')
            self.assertEqual(result.category, 'permission')
            self.assertEqual(result.result['total_cost_usd'], 0)
            native.assert_not_called()
            with self.assertRaises(FileExistsError):
                run_claude_files('claude', repo, 'task', None, binding=binding, output_dir=self.root/'logs')

    def test_model_workspace_cannot_contain_control_logs(self):
        binding = {'nonce':'a'*32, 'worktree':str(self.root), 'previous_session_id':None}
        with self.assertRaises(ExecutionBlocked):
            run_claude_files('claude', self.root, 'task', None, binding=binding, output_dir=self.root/'logs')

    def test_broker_missing_completion_wrong_scope_and_reordered_facts_are_invalid(self):
        binding = {'worktree':'/assigned'}
        ready = {'state':'ready', 'sequence':1, 'at':10, 'worktree':'/assigned', 'writable_paths':['src/code.py'], 'binding':binding}
        start = {'state':'started', 'sequence':2, 'at':11, 'request_id':1, 'tool':'write_file', 'arguments_sha256':'digest'}
        done = {**start, 'state':'completed', 'sequence':3, 'at':12}
        close = {'state':'closed', 'sequence':4, 'at':13}
        path = self.root/'audit.jsonl'
        def check(rows):
            path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
            return _broker(path, binding, ('src/code.py',), 9, 14)
        self.assertFalse(check([ready,start,done,close])['invalid'])
        self.assertTrue(check([ready,start,close])['invalid'])
        self.assertTrue(check([{**ready,'binding':{}},start,done,close])['invalid'])
        self.assertTrue(check([ready,done,start,close])['invalid'])
        self.assertTrue(check([ready,start,done,{**close,'at':15}])['invalid'])
        self.assertTrue(check([ready,{**start,'tool':'Bash'},done,close])['invalid'])
