"""Read-only product source, synthetic SQLite evidence, no model/operation calls.

The evaluator's canary and ledger helpers are extracted unmodified with AST.
Earlier diagnose parsing is out of scope: retain its exact frozen-ledger and
one-E1-reservation guards, with a synthetic valid recovery for other checks.
This isolates whether adding a legitimate follow-up review can invalidate the
already verified first-call gate. It is NOT full diagnose integration coverage.
"""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types

if len(sys.argv) != 2:
    raise SystemExit('Usage: python probe.py SOURCE_ROOT')
ROOT = Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location('real_shared_calls', ROOT / 'src/ai_company/shared_calls.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

def extract(path, names, namespace):
    tree = ast.parse(path.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)

class RecoveryError(ValueError):
    pass

ns = dict(Path=Path, json=json, sqlite3=sqlite3, hashlib=hashlib,
          SharedCallLedger=shared.SharedCallLedger)
extract(ROOT / 'scripts/evaluate_pm_behavior.py',
        {'_owned_by_lineage', 'trial_reservations', 'canary_verified'}, ns)
extract(ROOT / 'src/ai_company/pm_evidence_recovery.py', {'_bound_ledger_facts'}, ns)

diagnose_tree = next(n for n in ast.parse((ROOT / 'src/ai_company/pm_evidence_recovery.py').read_text()).body
                     if isinstance(n, ast.FunctionDef) and n.name == 'diagnose')
frozen_guard = next(n for n in ast.walk(diagnose_tree) if isinstance(n, ast.If)
                    and any(isinstance(c, ast.Constant) and c.value == 'E1 or predecessor settlement differs from original shared ledger'
                            for c in ast.walk(n)))
count_guard = next(n for n in ast.walk(diagnose_tree) if isinstance(n, ast.If)
                   and any(isinstance(c, ast.Constant) and c.value == 'E1 has additional shared reservations'
                           for c in ast.walk(n)))
# Extract the narrowest matching guards, not an enclosing condition.
frozen_guard = min([n for n in ast.walk(diagnose_tree) if isinstance(n, ast.If)
                    and 'E1 or predecessor settlement differs from original shared ledger' in ast.unparse(n)],
                   key=lambda n: len(ast.unparse(n)))
count_guard = min([n for n in ast.walk(diagnose_tree) if isinstance(n, ast.If)
                   and 'E1 has additional shared reservations' in ast.unparse(n)],
                  key=lambda n: len(ast.unparse(n)))

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary); root = base / 'trial'; origin = root / 'E1'; prior = base / 'prior'
    dbpath = origin / 'sessions/sessions.sqlite'; dbpath.parent.mkdir(parents=True)
    db = sqlite3.connect(dbpath)
    for name, key in [('management_plans', 'id'), ('management_pm_requests', 'message_id'), ('flow_tasks', 'task_id')]:
        db.execute(f'CREATE TABLE {name}({key} TEXT PRIMARY KEY, document TEXT NOT NULL)')
    plan = {'id':'plan','project_id':'project','request_id':'request','status':'reviewing',
            'evidence': {'original_job_id':'job'}}
    request = {'request_id':'request','project_id':'project','state':'completed','plan_id':'plan'}
    for table, key, document in [('management_plans','plan',plan), ('management_pm_requests','request',request),
                                 ('flow_tasks','pm-request',{'executions':[]})]:
        db.execute(f'INSERT INTO {table} VALUES (?,?)',(key,json.dumps(document)))
    db.commit()
    ledger_path = base / 'shared.sqlite'
    ledger = shared.SharedCallLedger.initialize(ledger_path,[('codex','fixture','group','AVAILABLE',None,None,0,0,0,0)])
    def settle(reservation, owner):
        ledger.reserve(reservation, str(owner), 'codex','fixture','group')
        ledger.started(reservation,str(owner),{'kind':'test','pid':1})
        ledger.settle(reservation,str(owner),reservation+'-event',{'category':'success','duration_seconds':1},terminated=True)
    for i in range(6):
        settle('prior-'+str(i), prior / ('E'+str(i+1)))
    settle('original-pm',origin)
    frozen_ledger = sqlite3.connect(base / 'frozen.sqlite')
    ledger.db.backup(frozen_ledger)
    fact = ledger.reservation('original-pm')
    marker = {'case':'E1','state':'canary_pm_ready','model_calls':1,'plan_id':'plan',
              'last_reservation_id':'original-pm','last_event_id':fact['event_id'],
              'last_result_sha256':hashlib.sha256(fact['result'].encode()).hexdigest(),
              'recovery_revision_id':'revision','recovery_validator_commit':'a'*40,
              'recovery_config_path':'config','recovery_codex_home':'home','recovery_evidence_manifest':'manifest'}
    failures = []
    def checked_diagnose(*args, **kwargs):
        guard_ns = dict(ns, ledger=ledger.db, frozen_ledger=frozen_ledger, origin=origin, prior=prior,
                        original_facts=ns['_bound_ledger_facts'](ledger.db,origin,prior),RecoveryError=RecoveryError)
        for guard in (frozen_guard,count_guard):
            try:
                exec(compile(ast.Module(body=[guard],type_ignores=[]),'actual-diagnose-guard','exec'),guard_ns)
            except RecoveryError as error:
                failures.append(str(error)); raise
        return types.SimpleNamespace(job_id='job')
    fake_recovery = types.ModuleType('ai_company.pm_evidence_recovery')
    fake_recovery.diagnose = checked_diagnose
    fake_recovery.verify_saved_recovery = lambda *a, **k: {'revision_id':'revision','plan_id':'plan','reservation_id':'original-pm'}
    sys.modules['ai_company'] = types.ModuleType('ai_company')
    sys.modules['ai_company.pm_evidence_recovery'] = fake_recovery
    before = ns['canary_verified'](root,ledger_path,marker)
    settle('legitimate-plan-review',origin)
    plan['status']='proposed'
    db.execute('UPDATE management_plans SET document=? WHERE id=?',(json.dumps(plan),'plan')); db.commit()
    after = ns['canary_verified'](root,ledger_path,marker)
    print(json.dumps({'before_followup_review':before,'after_followup_review':after,
                      'failure':failures[-1], 'frozen_reservations':len(ns['_bound_ledger_facts'](frozen_ledger,origin,prior)[0]),
                      'current_reservations':len(ns['_bound_ledger_facts'](ledger.db,origin,prior)[0]),
                      'full_diagnose_integration':False,'model_calls':0},sort_keys=True))
    assert before is True and after is False
    # The separate exact-one guard also rejects, even if frozen-facts filtering is fixed.
    isolated = dict(ns,ledger=ledger.db,origin=origin,RecoveryError=RecoveryError)
    try:
        exec(compile(ast.Module(body=[count_guard],type_ignores=[]),'actual-diagnose-count-guard','exec'),isolated)
    except RecoveryError as error:
        print('second_independent_guard:', str(error))
    else:
        raise AssertionError('expected exact-one guard rejection')
    db.close(); ledger.close(); frozen_ledger.close()
