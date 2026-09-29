"""No external calls. Exercise reviewer main with terminal quota lacking reset."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

if len(sys.argv) != 2:
    raise SystemExit('usage: python pr31-reviewer-unknown-quota-6530176.py /path/to/repo')
ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT / 'src'))
from ai_company.shared_calls import SharedCallLedger
spec = importlib.util.spec_from_file_location('review_audit', ROOT / 'scripts/review_pr_with_claude.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

def run(reset_present):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cli = root / 'claude'
        cli.write_text('fake-cli')
        relay = root / 'relay'
        relay.write_text('fake-relay')
        ledger_path = root / 'shared.db'
        ledger = SharedCallLedger.initialize(ledger_path, [('claude','review','account','AVAILABLE',None,None,0,0,0,0)])
        head = 'a'*40
        pr = {'url':'https://github.com/example/repo/pull/31','headRefOid':head}
        def command(*args):
            if args == ('git','rev-parse','HEAD'): return head
            if args == ('git','status','--porcelain'): return ''
            if args == ('git','rev-parse','--show-toplevel'): return str(root)
            if args[0] == 'gh': return json.dumps(pr)
            if args[-1] == '--version': return '2.1.280'
            raise AssertionError(args)
        info = {'status':'rejected'}
        if reset_present: info['resetsAt'] = 9_999_999_999
        events = [
            {'type':'rate_limit_event','rate_limit_info':info},
            {'type':'result','is_error':True,'terminal_reason':'api_error',
             'queued_turn_count':0,'duration_ms':1,'subagent_stats':
             {'spawned':0,'completed':0,'failed':0,'killed':{},'refused':{}}},
        ]
        argv=['review', '31', '--shared-call-ledger',str(ledger_path), '--credential-ref','review',
              '--quota-group','account','--adoption-receipt',str(root/'adoption.json')]
        with patch.object(sys,'argv',argv), patch.object(Path,'home',return_value=root), \
             patch.object(review,'shared_adoption_verified',return_value=True), \
             patch.object(review,'command',side_effect=command), \
             patch.object(review,'reviewable',return_value=[]), \
             patch.object(review.shutil,'which',return_value=str(cli)), \
             patch.object(review,'CONTROL',relay), \
             patch.object(review,'complete_pr_patch',return_value=('diff --git a/a b/a\n+x\n','b'*40,'b'*40)), \
             patch.object(review,'invoke',return_value=(1,events,None)), \
             patch.object(review,'input_delivery_verified',return_value=True), \
             patch.object(review,'binding_unchanged',return_value=True):
            assert review.main() == 1
        state = ledger.account('claude','review','account')
        print('reset_present=',reset_present,'state=',state['state'])
        if not reset_present:
            reservation = ledger.reserve('other-worker','other-queue','claude','review','account')
            print('other_queue_can_start=',reservation['state'])
            assert state['state'] == 'AVAILABLE'
            assert reservation['state'] == 'RESERVED'
        else:
            assert state['state'] == 'COOLDOWN'
        ledger.close()

run(True)
run(False)
