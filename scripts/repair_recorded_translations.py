"""미리보기가 기본인 번역 복구 도구. 승인된 후보 worker에서만 작업을 등록한다.

기존 실패와 원문을 보존한다. 재처리는 저장된 native 결과만 사용하며,
추가 번역은 원래 예산의 남은 횟수만 공유 한도 큐에서 실행한다.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from contextlib import closing

from ai_company import translations
from ai_company.adapters import translation_cli
from ai_company.contracts import digest


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def action_for(store, job_id):
    original = store._get(job_id)
    if (not original or original['status'] != 'failed'
            or original.get('execution_result', {}).get('cgroup_stopped') is not True
            or original['config'].get('parser_version') == translations.REPAIR_PARSER
            or original['config']['provider'] != 'claude'
            or original['config']['model'] != translation_cli.HAIKU
            or original['config']['model_version'] != '2.1.270'
            or original.get('reason') not in ('protected literal missing or duplicated',
                'translation_output_not_json', 'negation, condition, exception, or limit marker missing')):
        raise ValueError('확정된 원래 실패 작업만 복구할 수 있습니다')
    replay = None
    try:
        replay = translation_cli.TranslationCLI.replay(original)
    except ValueError as error:
        # A format failure may use the original remaining budget. Model, tool,
        # session and termination provenance failures must never become retries.
        if str(error) != 'translation_output_not_json':
            raise
    if replay is not None:
        try:
            translations.validate_fields({**original, 'config': {**original['config'],
                'parser_version': translations.REPAIR_PARSER}}, replay['fields'])
        except ValueError:
            replay = None
    if replay is None and (original['attempts'] >= original['config']['max_attempts']
            or original['spent_seconds'] + original['config']['timeout_seconds'] > original['config']['max_total_seconds']):
        raise ValueError('원래 실행 예산의 남은 횟수가 없습니다')
    raw = Path(original['execution_identity']['evidence_dir']) / 'stdout.log'
    return dict(job_id=job_id, expected_original_digest=digest(original),
        source_digest=original['source_digest'], raw_sha256=file_sha(raw),
        action='replay' if replay else 'bounded_retry', replay_digest=digest(replay),
        maximum_new_model_calls=0 if replay else original['config']['max_attempts']-original['attempts']), replay


def require_running_candidate(database, release):
    """Observe the live service, its actual cwd/state and installed Python files."""
    release = Path(release).resolve(strict=True)
    output = subprocess.check_output(['systemctl', '--user', 'show', 'ai-company-translation.service',
        '--property=ActiveState,MainPID'], text=True, timeout=10)
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    pid = int(values.get('MainPID', 0))
    if values.get('ActiveState') != 'active' or pid <= 0:
        raise ValueError('번역 worker가 실제로 가동 중이지 않습니다')
    if Path(f'/proc/{pid}/cwd').resolve(strict=True) != release:
        raise ValueError('번역 worker가 검토한 후보 release를 사용하지 않습니다')
    argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
    try:
        selected = Path(argv[argv.index('--state-dir')+1]).resolve(strict=True)
    except (ValueError, IndexError):
        raise ValueError('실제 worker의 상태 디렉터리를 확인하지 못했습니다') from None
    if selected / 'sessions' / 'sessions.sqlite' != Path(database).resolve(strict=True):
        raise ValueError('실제 worker와 복구 대상 DB가 다릅니다')
    for relative, module in [('translations.py', translations), ('adapters/translation_cli.py', translation_cli)]:
        matches = list((release / '.venv/lib').glob('python*/site-packages/ai_company/' + relative))
        if len(matches) != 1 or file_sha(matches[0]) != file_sha(module.__file__):
            raise ValueError('가동 worker에 검증한 복구 코드가 설치되지 않았습니다')
    return pid


def apply_plan(plan, *, candidate_release):
    database = Path(plan['database']).resolve(strict=True)
    pid = require_running_candidate(database, candidate_release)
    with closing(sqlite3.connect(database)) as connection:
        store = translations.TranslationStore(connection)
        prepared = [(action_for(store, row['job_id']), row) for row in plan['jobs']]
        if any(actual != approved for ((actual, _), approved) in prepared):
            raise ValueError('검토 이후 원본 결과나 복구 범위가 변경됐습니다')
        if require_running_candidate(database, candidate_release) != pid:
            raise ValueError('검토 중 worker가 교체되어 다시 대조해야 합니다')
        return [store.repair(row['job_id'], expected_original_digest=row['expected_original_digest'], replay=replay)
                for ((_, replay), row) in prepared]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--job', action='append', default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--proposal', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-proposal-sha256')
    parser.add_argument('--candidate-release', type=Path)
    args = parser.parse_args(argv)
    if args.apply:
        if not args.proposal or not args.expected_proposal_sha256 or not args.candidate_release:
            parser.error('적용에는 고정된 제안 파일·SHA-256·후보 release가 필요합니다')
        if file_sha(args.proposal) != args.expected_proposal_sha256:
            parser.error('제안 파일 SHA-256이 다릅니다')
        plan = json.loads(args.proposal.read_text())
        results = apply_plan(plan, candidate_release=args.candidate_release)
        print(json.dumps({'applied': True, 'jobs': [{'id': row['id'], 'status': row['status']} for row in results]}))
        return
    if not args.database or not args.job or not args.output:
        parser.error('미리보기에는 DB·명시적인 작업 ID·새 출력 파일이 필요합니다')
    database = args.database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        store = translations.TranslationStore(connection)
        plan = {'database': str(database), 'jobs': [action_for(store, identity)[0] for identity in dict.fromkeys(args.job)]}
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({'applied': False, 'proposal_sha256': file_sha(args.output),
        'replays': sum(row['action'] == 'replay' for row in plan['jobs']),
        'maximum_new_model_calls': sum(row['maximum_new_model_calls'] for row in plan['jobs'])}))


if __name__ == '__main__':
    main()
