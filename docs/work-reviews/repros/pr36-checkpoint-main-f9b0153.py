#!/usr/bin/env python3
"""PR #36 checkpoint replay regression; no real models or server mutations.

Usage:
    python repro_checkpoint_main.py /path/to/ai-company --second-result pass
    python repro_checkpoint_main.py /path/to/ai-company --second-result quota

Exercises the supplied source's real runner.main(), reserve_attempt(), temporary
SharedCallLedger, checkpoint replay and tick.inspect(). Git/GitHub preflight and
Claude CLI output are synthetic. All writable runtime evidence stays inside a
TemporaryDirectory; the supplied repository is not modified. A zero exit means
the expected regression was reproduced, NOT that the product is passing.
"""
import argparse
import importlib.util, json, tempfile, time, sys, hashlib, contextlib, io, re
from pathlib import Path
from unittest.mock import patch
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('--second-result', choices=['pass','quota'], default='pass')
options=parser.parse_args()
source=options.source.resolve()
sys.path.insert(0,str(source/'src'))
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
r=load('reviewmain',source/'scripts/review_pr_with_claude.py')
t=load('reviewtick',source/'scripts/review_claude_quota_tick.py')
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary);repo=root/'repo';repo.mkdir();(repo/'a.py').write_text('a=1\n');(repo/'b.py').write_text('b=1\n')
 native=root/'claude';native.write_text('mock-only')
 head='a'*40;base='b'*40;url='https://example.test/pull/35'
 patch_text='diff --git a/a.py b/a.py\n+a=1\ndiff --git a/b.py b/b.py\n+b=1\n'
 ledgerfile=root/'calls.sqlite';r.SharedCallLedger.initialize(ledgerfile,[('claude','review','group','AVAILABLE',None,None,0,0,0,0)]).close()
 phases=iter(['a.py','b.py'])
 def command(*args):
  if args==('git','rev-parse','HEAD'):return head
  if args==('git','status','--porcelain'):return ''
  if args==('git','rev-parse','--show-toplevel'):return str(repo)
  if args==(str(native),'--version'):return '2.1.283'
  if args[:3]==('gh','pr','view'):return json.dumps({'url':url,'headRefOid':head})
  raise AssertionError(args)
 def invoke(cmd,input_text,directory):
  filename=next(phases);quota=filename=='a.py' or options.second_result=='quota';config=json.loads((directory/'config.json').read_text());b=config['binding'];now=time.time();code=1 if quota else 0
  before={'type':'control_response','response':{'request_id':b['nonce']+'-before','response':{'applied':r.APPLIED,'has_errors':False}}}
  after={'type':'control_response','response':{'request_id':b['nonce']+'-after','response':{'applied':r.APPLIED,'has_errors':False}}}
  claim={'head':head,'patch_sha256':b['diff_sha256'],'reviewed_files':[filename]}
  rows=[before,{'type':'assistant','message':{'model':r.MODEL,'content':[{'type':'tool_use','id':'read','name':'Read','input':{'file_path':str(repo/filename)}}]}},{'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'read','content':'ok'}]}},{'type':'assistant','message':{'model':r.MODEL,'content':[{'type':'text','text':'REVIEW_PROGRESS_JSON: '+json.dumps(claim)}]}}]
  terminal={'type':'result','session_id':filename,'is_error':quota,'queued_turn_count':0,'subagent_stats':{'spawned':0,'completed':0,'failed':0,'killed':{},'refused':{}},'total_cost_usd':1,'duration_ms':1000,'permission_denials':[]}
  if quota:
   rows += [{'type':'rate_limit_event','rate_limit_info':{'status':'rejected','resetsAt':now-1}}];terminal.update(terminal_reason='api_error',api_error_status=429)
  else:
   terminal.update(subtype='success',terminal_reason='completed',stop_reason='end_turn',result=f'판정: PASS\n대상 HEAD: {head}\n패치 SHA-256: {b["diff_sha256"]}\n검토 범위: a.py, b.py\n미검토: 없음')
  rows += [terminal,after]
  serialized=''.join(json.dumps(e)+'\n' for e in rows)
  (directory/'events.jsonl').write_text(serialized);(directory/'events.live.jsonl').write_text(serialized)
  facts=[{'state':'starting','binding':b,'at':now-3},{'state':'prompt_delivery_started'},{'state':'prompt_delivered','prompt_sha256':b['prompt_sha256']},{'state':'closed','exit_code':code,'at':now}]
  (directory/'facts.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in facts))
  return code,rows,None
 args=['review','35','--shared-call-ledger',str(ledgerfile),'--credential-ref','review','--quota-group','group','--adoption-receipt',str(root/'receipt')]
 with patch.object(r,'command',side_effect=command),patch.object(r,'shared_adoption_verified',return_value=True),patch.object(r,'reviewable',return_value=[]),patch.object(r.shutil,'which',return_value=str(native)),patch.object(r,'complete_pr_patch',return_value=(patch_text,base,base)),patch.object(r,'binding_unchanged',return_value=True),patch.object(r,'invoke',side_effect=invoke),patch.object(r.Path,'home',return_value=root):
  for extra in ([],['--resume-quota']):
   with patch.object(sys,'argv',args+extra),contextlib.redirect_stdout(io.StringIO()):code=r.main()
   print('main',extra or ['initial'],'return=',code)
 attempt=root/'.local/state/ai-company/claude-pr-reviews'/('pr-35-'+head)
 prompt=(attempt/'prompt.txt').read_text();saved=json.loads((attempt/'checkpoint.json').read_text());receipt=json.loads((attempt/'receipt.json').read_text());ledger=r.SharedCallLedger(ledgerfile)
 print('inherited phrase present:', '이전 체크포인트: ' in prompt)
 print('line-anchored inherited matches:',len(re.findall(r'(?m)^이전 체크포인트: (.+)$',prompt)))
 print('saved reviewed_files:',saved['reviewed_files'],'saved lineage count:',len(saved['lineage']))
 print('receipt completed:',receipt['completion_verified'],'review_passed:',receipt['review_passed'])
 manifest={'pr':35,'pr_url':url,'head':head,'base':base,'diff_sha256':hashlib.sha256(patch_text.encode()).hexdigest(),'attempt_dir':str(attempt),'checkout':str(repo),'credential_ref':'review','quota_group':'group'}
 state=t.inspect(manifest,r,ledger,now=time.time())
 print('tick after second main:',state)
 assert state['state']=='NEEDS_RECONCILIATION', 'Expected regression no longer reproduced'
 try:r.previous_checkpoint(attempt,head=head,base=base,diff_sha256=manifest['diff_sha256'],files=['a.py','b.py'],repo=repo)
 except RuntimeError as e:
  print('checkpoint replay:',type(e).__name__,str(e))
  assert str(e)=='saved checkpoint differs from bound event replay'
 else:
  raise AssertionError('Expected checkpoint replay mismatch no longer reproduced')
 ledger.close()
