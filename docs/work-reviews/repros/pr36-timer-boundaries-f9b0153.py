"""PR #36의 재개 경계 두 건을 모델 호출 없이 재현합니다.

사용법: python3 timer_boundaries.py /path/to/ai-company
입력은 PR #36 f9b0153의 저장소 루트이며, src와
tests/test_claude_quota_checkpoint.py를 가져올 수 있어야 합니다.
Python 표준 라이브러리와 해당 저장소의 검사 보조 함수를 사용합니다.
런타임 장부·시도 기록은 TemporaryDirectory 안에서만 생성합니다.
Git·GitHub·Claude CLI 조회는 합성 값이며 실제 CLI를 실행하지 않습니다.
마지막 검사는 모델 실행 함수의 입구에서 예외로 중단합니다.
"""

import sys, tempfile, json
from pathlib import Path
from unittest.mock import patch
if len(sys.argv) != 2:
    raise SystemExit('사용법: python3 timer_boundaries.py /path/to/ai-company')
source_root = Path(sys.argv[1]).resolve()
if not (source_root / 'tests/test_claude_quota_checkpoint.py').is_file():
    raise SystemExit('PR #36의 tests/test_claude_quota_checkpoint.py가 필요합니다.')
sys.path[:0] = [str(source_root / 'src'), str(source_root)]
from tests.test_claude_quota_checkpoint import QuotaCheckpointTests, tick, review
from ai_company.shared_calls import SharedCallLedger

original_inspect = tick.inspect
captured = []
def inspect_archive(manifest, runner, ledger, *, now):
    result = original_inspect(manifest, runner, ledger, now=now)
    if result['state'] == 'READY' and not captured:
        root = Path(manifest['attempt_dir'])
        archived = root.with_name(root.name + '-quota-repro')
        root.rename(archived)
        after = original_inspect(manifest, runner, ledger, now=now)
        captured.append({'before_archive': result['state'], 'after_archive_no_canonical': after,
                         'ledger_owner': ledger.reservation('r')['owner'], 'archive': str(archived)})
        archived.rename(root)
    return result
with patch.object(tick, 'inspect', side_effect=inspect_archive):
    QuotaCheckpointTests('test_restart_tick_waits_for_reset_and_exact_settlement').test_restart_tick_waits_for_reset_and_exact_settlement()
print('ARCHIVE_OWNER:', json.dumps(captured[0]))

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    ledger=SharedCallLedger.initialize(root/'ledger.sqlite', [('claude','review','group','AVAILABLE',None,None,0,0,0,0)])
    fresh=root/'pr-35-new-head'
    entered=False
    with review.reserve_attempt(fresh, False, resume_quota=True, quota_binding={
            'pr_number':35,'pr_url':'url','head':'b'*40,'base':'c'*40,'diff_sha256':'d'*64},
            ledger=ledger, quota_group='group') as previous:
        entered=True
        print('RESUME_WITH_NO_PREDECESSOR:', json.dumps({'entered':entered,'previous':previous,'fresh_directory_created':fresh.exists()}))
    ledger.close()

class BoundaryReached(Exception): pass
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp); cli=root/'claude'; cli.write_text('synthetic cli, never executed')
    ledger=SharedCallLedger.initialize(root/'ledger.sqlite', [('claude','review','group','AVAILABLE',None,None,0,0,0,0)])
    ledger.close()
    new_head='b'*40; base='c'*40
    pr={'number':35,'url':'https://example.test/pull/35','state':'OPEN','headRefOid':new_head,
        'statusCheckRollup':[{'name':f'unit (Python {v})','workflowName':'CI','conclusion':'SUCCESS'} for v in ['3.11','3.12']]}
    def command(*args):
        if args==('git','rev-parse','HEAD'): return new_head
        if args==('git','status','--porcelain'): return ''
        if args==('git','rev-parse','--show-toplevel'): return str(root)
        if args[0]=='gh': return json.dumps(pr)
        if args==(str(cli),'--version'): return '2.1.283'
        raise AssertionError(args)
    observed={}
    def invoke(cmd, input_text, directory):
        config=json.loads((directory/'config.json').read_text())
        observed.update({'invocation_boundary_reached':True,'head':config['binding']['head'],
                         'attempt_directory':directory.name, 'contains_previous_checkpoint':'이전 체크포인트:' in input_text})
        raise BoundaryReached
    argv=['runner.py','35','--resume-quota','--shared-call-ledger',str(root/'ledger.sqlite'),
          '--credential-ref','review','--quota-group','group','--adoption-receipt',str(root/'receipt.json')]
    with patch.object(sys,'argv',argv), patch.object(review,'command',side_effect=command), \
         patch.object(review,'complete_pr_patch',return_value=('diff --git a/a.py b/a.py\n+x\n',base,base)), \
         patch.object(review,'shared_adoption_verified',return_value=True), \
         patch.object(review.shutil,'which',return_value=str(cli)), patch.object(Path,'home',return_value=root), \
         patch.object(review,'invoke',side_effect=invoke):
        try: review.main()
        except BoundaryReached: pass
    print('RUNNER_WITH_CHANGED_CHECKOUT:',json.dumps(observed))
