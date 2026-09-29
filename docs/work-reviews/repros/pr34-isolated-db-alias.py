"""Scratch probe: real recovery persistence, diagnosis stubbed, no model/ops access."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if len(sys.argv) != 2:
    raise SystemExit('Usage: python probe.py SOURCE_ROOT')
ROOT = Path(sys.argv[1]).resolve(strict=True)
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
source = ROOT / 'src/ai_company/pm_evidence_recovery.py'
tree = ast.parse(source.read_text(), filename=str(source))
# LangGraph is unavailable here; only the unused Automation/Dispatcher imports
# are removed. The diagnosis itself is stubbed exactly as in the product tests.
tree.body = [item for item in tree.body if not (
    isinstance(item, ast.ImportFrom)
    and item.module in {'ai_company.automation', 'ai_company.dispatcher'})]
spec = importlib.util.spec_from_file_location('ai_company.pm_evidence_recovery', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
exec(compile(tree, str(source), 'exec'), module.__dict__)

from ai_company.management import ManagementStore
from tests.test_pm_evidence_recovery import RecoveredPlanTests

suite = unittest.defaultTestLoader.loadTestsFromTestCase(RecoveredPlanTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    original = base / 'original' / 'E1'
    isolated = base / 'copy' / 'E1'
    isolated.mkdir(parents=True)
    store = ManagementStore(original)
    project = store.create_project({'name': 'scope probe', 'goal': 'test'})
    message = store.post_message(project['id'], {'content': 'plan'})
    request = store.get_pm_request(message['id'])
    request.pop('requirements_contract_version', None)
    request.update(state='blocked', reason='old evidence unavailable',
                   configuration_digest='c'*64, mode='fixture')
    with store.db:
        store.db.execute('UPDATE management_pm_requests SET document=? WHERE message_id=?',
                         (json.dumps(request), message['id']))
    store.close()
    # A nested symlink survives resolve() of the top-level state root.
    (isolated / 'sessions').symlink_to(original / 'sessions', target_is_directory=True)
    content = {'summary': 'Build and check', 'roles': [
        {'key': 'dev', 'name': 'Developer', 'responsibility': 'Build', 'goal': 'Implement',
         'acceptance': ['Implementation works'], 'allowed_paths': ['src/'], 'depends_on': []},
        {'key': 'test', 'name': 'Tester', 'responsibility': 'Check', 'goal': 'Verify',
         'acceptance': ['Tests pass'], 'allowed_paths': ['tests/'], 'depends_on': []}],
        'completion_criteria': ['Check passes']}
    diagnosis = module.Diagnosis(original, isolated, base/'shared.sqlite', base/'config',
        base/'codex', base/'manifest', message['id'], project['id'], 'pm-'+message['id'],
        'job', 'reservation', content, {'source': 'derived_settled_pm'},
        {'id': 'revision', 'validator_commit': 'a'*40})
    with patch.object(module, 'diagnose', return_value=diagnosis):
        applied = module.apply_to_isolated_state(diagnosis)
    store = ManagementStore(original)
    print(json.dumps({'probe': 'nested database directory symlink', 'roots_different': original != isolated,
        'same_database': (original/'sessions/sessions.sqlite').samefile(isolated/'sessions/sessions.sqlite'),
        'apply_isolated_result': applied,
        'plans_in_original': store.db.execute('SELECT count(*) FROM management_plans').fetchone()[0]}))
    store.close()
