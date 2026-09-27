"""Read-only source review; all runtime records are temporary synthetic fixtures.

Usage: python3 repro_reconciliation_resume.py /path/to/ai-company
No Claude invocation, subprocess, network, source edit, or persistent ledger change.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

if len(sys.argv) != 2:
    raise SystemExit('usage: python3 repro_reconciliation_resume.py /path/to/ai-company')
SOURCE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(SOURCE / 'src'))
from ai_company.shared_calls import SharedCallLedger

def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

review = load_script('review_candidate', 'review_pr_with_claude.py')
tick = load_script('tick_candidate', 'review_claude_quota_tick.py')

def workflow_events():
    def result(index, cost):
        value = {'type': 'result', 'uuid': f'result-{index}', 'session_id': 'same-session',
                 'result_index': index, 'is_error': True, 'terminal_reason': 'api_error',
                 'api_error_status': 429, 'queued_turn_count': 0,
                 'subagent_stats': {'spawned': 0, 'completed': 0, 'failed': 0,
                                    'killed': {}, 'refused': {}},
                 'modelUsage': {'opus': {'inputTokens': index + 1, 'outputTokens': index + 2,
                                        'cacheReadInputTokens': index + 3,
                                        'cacheCreationInputTokens': index + 4,
                                        'thinkingTokens': index + 5, 'costUSD': cost}},
                 'total_cost_usd': cost}
        if index:
            value['origin'] = {'kind': 'task-notification'}
        return value
    return [{'type': 'system', 'subtype': 'task_started', 'task_id': 'task-1'},
            {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 200}},
            result(0, 10),
            {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': []},
            {'type': 'system', 'subtype': 'task_notification', 'task_id': 'task-1', 'status': 'completed'},
            result(1, 11)]

def settings_events(binding):
    safe = {'applied': review.APPLIED, 'has_errors': False}
    return [
        {'type': 'control_response', 'response': {
            'request_id': binding['nonce'] + '-before', 'response': safe}},
        {'type': 'assistant', 'message': {'model': review.MODEL, 'content': []}},
        {'type': 'control_response', 'response': {
            'request_id': binding['nonce'] + '-after', 'response': safe}},
    ]

sha = lambda value: hashlib.sha256(value).hexdigest()
def put(directory, name, value):
    (directory / name).write_text(json.dumps(value, sort_keys=True) + '\n')

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    head, base = 'a' * 40, 'b' * 40
    attempt = root / ('pr-35-' + head)
    attempt.mkdir()
    patch = 'diff --git a/a.py b/a.py\n+x\n'
    diff_sha = sha(patch.encode())
    prompt = f'--- PATCH {diff_sha} BEGIN ---\n{patch}\n--- PATCH END ---'
    binding = {'pr': 35, 'head': head, 'diff_sha256': diff_sha,
               'prompt_sha256': sha(prompt.encode()), 'nonce': 'n'}
    (attempt / 'pr.diff').write_text(patch)
    (attempt / 'prompt.txt').write_text(prompt)
    facts = [{'state': 'starting', 'binding': binding, 'at': 100},
             {'state': 'prompt_delivery_started'},
             {'state': 'prompt_delivered', 'prompt_sha256': binding['prompt_sha256']},
             {'state': 'closed', 'exit_code': 1, 'at': 150}]
    (attempt / 'facts.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in facts))
    rows = workflow_events()
    rows[2].update(is_error=False, terminal_reason='completed', stop_reason='end_turn')
    rows[2].pop('api_error_status')
    rows.insert(-1, {'type': 'assistant', 'is_api_error_message': True,
                    'error': 'rate_limit', 'message': {'model': '<synthetic>'}})
    events = settings_events(binding) + rows
    stream = ''.join(json.dumps(x) + '\n' for x in events)
    for name in ('events.jsonl', 'events.live.jsonl'):
        (attempt / name).write_text(stream)
    checkpoint = review.checkpoint_from_events(events, binding=binding, base=base,
        files=['a.py'], reservation_id='r', settlement_event=None,
        events_sha256=sha(stream.encode()), repo=root, legacy_model_check=True)
    put(attempt, 'checkpoint.json', checkpoint)
    receipt = {'pr': 35, 'url': 'https://example.test/pull/35', 'head': head,
               'base': base, 'diff_sha256': diff_sha,
               'prompt_sha256': binding['prompt_sha256'],
               'events_sha256': sha(stream.encode()),
               'checkpoint_sha256': sha((attempt / 'checkpoint.json').read_bytes()),
               'completion_verified': False, 'input_delivery_verified': True,
               'binding_unchanged_after_review': True, 'incomplete_reason': None,
               'shared_reservation_id': 'r', 'shared_settlement_event': None}
    put(attempt, 'receipt.json', receipt)
    interpretation = {'events_sha256': receipt['events_sha256'],
                      'checkpoint_sha256': receipt['checkpoint_sha256'],
                      'reservation_id': 'r', 'settlement_event': None}
    put(attempt, 'interpretation.json', interpretation)
    original_hashes = {name: sha((attempt / name).read_bytes()) for name in
                      ('receipt.json', 'events.jsonl', 'checkpoint.json', 'interpretation.json')}
    ledger = SharedCallLedger.initialize(root / 'shared.sqlite', [
        ('claude', 'review', 'group', 'AVAILABLE', None, None, 0, 0, 0, 0)], clock=lambda: 201)
    ledger.reserve('r', str(attempt), 'claude', 'review', 'group')
    ledger.started('r', str(attempt), {'kind': 'executor_invocation'})
    ledger.uncertain('r', str(attempt), 'terminal_or_children_unverified')
    event_id = 'e' * 64
    ledger.settle('r', str(attempt), event_id, {'category': 'quota',
        'reset_at': 200, 'duration_seconds': 50, 'total_cost_usd': 11}, terminated=True)
    ledger.reconcile_settled_quota('r', 'claude', 'review', 'group', event_id)
    put(attempt, 'reconciliation.json', {
        'source_receipt_sha256': original_hashes['receipt.json'],
        'source_events_sha256': original_hashes['events.jsonl'],
        'shared_reservation_id': 'r', 'shared_settlement_event': event_id,
        'category': 'quota', 'reset_at': 200})
    manifest = {'pr': 35, 'pr_url': receipt['url'], 'head': head, 'base': base,
                'diff_sha256': diff_sha, 'attempt_dir': str(attempt),
                'checkout': str(root), 'credential_ref': 'review', 'quota_group': 'group'}
    args = {'pr_number': 35, 'pr_url': receipt['url'], 'head': head,
            'base': base, 'diff_sha256': diff_sha, 'ledger': ledger,
            'quota_group': 'group', 'now': 201}
    print('previous_checkpoint_preserves_legacy:', review.previous_checkpoint(attempt,
        head=head, base=base, diff_sha256=diff_sha, files=['a.py'], repo=root) == checkpoint)
    print('input_delivery_verified:', review.input_delivery_verified(attempt, binding, 1))
    print('terminal_children_stopped:', review.terminal_children_stopped(events))
    print('reset:', review.rejected_quota_reset(events))
    print('reservation:', ledger.reservation('r')['state'])
    print('account:', ledger.account('claude', 'review', 'group')['state'])
    print('effective_settlement:', review.effective_receipt(attempt)['shared_settlement_event'])
    print('interpretation_settlement:', interpretation['settlement_event'])
    print('quota_resume_verified:', review.quota_resume_verified(attempt, **args))
    print('tick_inspect:', tick.inspect(manifest, review, ledger, now=201))
    print('original_records_unchanged:', all(sha((attempt / name).read_bytes()) == digest
        for name, digest in original_hashes.items()))
    # A diagnostic-only counterfactual in the temporary fixture isolates the failed condition.
    put(attempt, 'interpretation.json', {**interpretation, 'settlement_event': event_id})
    print('counterfactual_interpretation_rewrite_quota:', review.quota_resume_verified(attempt, **args))
    print('counterfactual_interpretation_rewrite_tick:', tick.inspect(manifest, review, ledger, now=201)['state'])
    ledger.close()
