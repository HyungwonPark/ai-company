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
import json,os,signal,sys
if os.environ.get('SIGMASK_MARKER'):
    mask=signal.pthread_sigmask(signal.SIG_BLOCK, [])
    open(os.environ['SIGMASK_MARKER'],'w').write(str([s in mask for s in
        (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)]))
for line in sys.stdin:
    event=json.loads(line)
    if event['type']=='user':
        open(os.environ['MARKER'],'w').write('delivered')
        if os.environ.get('PROGRESS'):
            for item in [
                {'type':'system','subtype':'background_tasks_changed','tasks':['task-1']},
                {'type':'system','subtype':'task_started','task_id':'task-1'},
                {'type':'assistant','parent_tool_use_id':None,
                 'message':{'model':os.environ['MODEL'],'content':[{'type':'text',
                    'text':'REVIEW_PROGRESS_JSON: {}'}]}},
                {'type':'result','session_id':'native-session','result_index':0,'result':'first'}]:
                print(json.dumps(item),flush=True)
            continue
        if os.environ.get('WORKFLOW'):
            for item in [
                {'type':'system','subtype':'task_started','task_id':'task-1'},
                {'type':'result','session_id':'native-session','result_index':0,'result':'first'},
                {'type':'system','subtype':'background_tasks_changed','tasks':[]},
                {'type':'system','subtype':'task_notification','task_id':'task-1','status':'completed'},
                {'type':'result','session_id':'native-session','result_index':1,'origin':{'kind':'task-notification'},'result':'final'}]:
                print(json.dumps(item),flush=True)
            continue
        print(json.dumps({'type':'result','session_id':'native-session','result':'done'}),flush=True)
        continue
    req=event['request_id']
    payload={'account':{'secret':'PRIVATE_ACCOUNT'}}
    if event['request']['subtype']=='get_settings':
        payload={'applied':{'model':os.environ['MODEL'],'effort':os.environ['EFFORT'],'ultracode':True},
                 'settings':{'env':{'SECRET':'PRIVATE_ENV'}},'errors':[]}
    print(json.dumps({'type':'control_response','response':{'request_id':req,'subtype':'success','response':payload}}),flush=True)
    if req.endswith('-progress-1') and os.environ.get('PROGRESS'):
        for item in [{'type':'system','subtype':'task_notification','task_id':'task-1','status':'completed'},
                     {'type':'system','subtype':'background_tasks_changed','tasks':[]}]:
            print(json.dumps(item),flush=True)
'''


class ClaudeFileAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def relay(self, effort, model='claude-opus-5', blocked=False, bound_prompt=False,
              journal=False, workflow=False, progress=False):
        directory = self.root / effort; directory.mkdir()
        native = directory / 'native'; native.write_text(NATIVE); native.chmod(0o700)
        config = {'cli_executable':str(native), 'cli_sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
            'binding':{'nonce':'attempt-1'}, 'facts_path':str(directory/'facts.jsonl'), 'extra_args':[],
            'environment':{'PATH':os.environ['PATH'], 'EFFORT':effort, 'MODEL':model,
                           'MARKER':str(directory/'delivered')}}
        if bound_prompt:
            config['binding']['prompt_sha256'] = hashlib.sha256(b'task').hexdigest()
        if journal:
            config['events_path'] = str(directory/'events.live.jsonl')
        if workflow:
            config['environment']['WORKFLOW'] = '1'
        if progress:
            config['environment']['PROGRESS'] = '1'
        if model != 'claude-opus-5':
            config['expected_applied'] = {'model':model, 'effort':'xhigh', 'ultracode':True}
        if blocked:
            config['environment']['SIGMASK_MARKER'] = str(directory/'mask')
        path = directory/'config.json'; path.write_text(json.dumps(config))
        records = [{'type':'control_request','request_id':'init','request':{'subtype':'initialize'}},
                   {'type':'user','message':{'role':'user','content':'task'}}]
        command = [sys.executable, '-I', '-B', claude_control.__file__, str(path)]
        if blocked:
            blocker = directory/'blocker.py'
            blocker.write_text('import os,signal,sys\n'
                'signal.pthread_sigmask(signal.SIG_BLOCK,(signal.SIGINT,signal.SIGTERM,signal.SIGHUP))\n'
                'os.execv(sys.executable,[sys.executable,"-I","-B",sys.argv[1],sys.argv[2]])\n')
            command = [sys.executable, str(blocker), claude_control.__file__, str(path)]
        result = subprocess.run(command,
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

    def test_separate_pr_review_model_is_checked_before_prompt(self):
        directory, _, facts = self.relay('xhigh', 'claude-opus-5-5', bound_prompt=True)
        self.assertTrue((directory/'delivered').exists())
        self.assertEqual(facts[1]['applied']['model'], 'claude-opus-5-5')
        self.assertEqual(facts[3]['state'], 'prompt_delivered')
        self.assertEqual(facts[3]['prompt_sha256'], hashlib.sha256(b'task').hexdigest())

    def test_live_journal_matches_sanitized_stream_before_runner_receipt(self):
        directory, result, facts = self.relay('xhigh', journal=True)
        live = (directory/'events.live.jsonl').read_text()
        self.assertEqual(live, result.stdout)
        self.assertEqual(facts[-1]['state'], 'closed')
        self.assertNotIn('PRIVATE_', live)

    def test_after_settings_waits_for_workflow_notification_result(self):
        _, result, facts = self.relay('xhigh', workflow=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        last_result = max(index for index, item in enumerate(rows) if item.get('type') == 'result')
        after = next(index for index, item in enumerate(rows)
                     if item.get('type') == 'control_response'
                     and item.get('response', {}).get('request_id') == 'attempt-1-after')
        self.assertGreater(after, last_result)
        self.assertEqual(facts[-1]['state'], 'closed')

    def test_progress_settings_and_after_wait_for_workflow_completion(self):
        _, result, facts = self.relay('xhigh', progress=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        positions = {item['response']['request_id']: index for index, item in enumerate(rows)
                     if item.get('type') == 'control_response'}
        result_index = next(index for index, item in enumerate(rows) if item.get('type') == 'result')
        notification = next(index for index, item in enumerate(rows)
                            if item.get('subtype') == 'task_notification')
        self.assertLess(result_index, positions['attempt-1-progress-1'])
        self.assertLess(notification, positions['attempt-1-after'])
        self.assertEqual(facts[-1]['state'], 'closed')
        self.assertNotIn('PRIVATE_', result.stdout + json.dumps(facts))

    def test_relay_and_native_unblock_inherited_signals(self):
        directory, _, _ = self.relay('xhigh', blocked=True)
        self.assertEqual((directory/'mask').read_text(), '[False, False, False]')

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
