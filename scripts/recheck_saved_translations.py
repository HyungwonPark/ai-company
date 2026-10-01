"""저장된 번역과 원문 검토를 대조합니다. 기본 동작은 읽기 전용입니다."""
import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

from ai_company.translations import TranslationStore, initialize


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_for(database, reviews):
    database = Path(database).resolve(strict=True)
    if not isinstance(reviews, list) or not reviews or len({item['job_id'] for item in reviews}) != len(reviews):
        raise ValueError('명시적인 중복 없는 저장 결과 검토가 필요합니다')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        store = TranslationStore(connection)
        jobs = [store.inspect_saved(item['job_id'], item)[2] for item in reviews]
    for job in jobs:
        job.pop('fields')
    return dict(database=str(database), jobs=jobs, maximum_new_model_calls=0)


def apply_to_connection(connection, plan):
    store = TranslationStore(connection)
    # Re-read every original before changing any selection. Individual operations
    # are transactional and idempotent, so an interruption resumes committed ones.
    for expected in plan['jobs']:
        actual = store.inspect_saved(expected['job_id'], expected['review'])[2]
        actual.pop('fields')
        if actual != expected:
            raise ValueError('검토 이후 원문·저장 결과·의미 판정이 변경됐습니다')
    return [store.recheck_saved(item['job_id'], expected_original_digest=item['original_digest'], review=item['review'])
            for item in plan['jobs']]


def copy_and_apply(plan, destination):
    # O_EXCL forbids overwriting the source, a prior copy, or a symlink target.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    source = Path(plan['database']).resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination)) as connection:
            original.backup(connection)
            initialize(connection)
            return apply_to_connection(connection, plan)


def require_running_candidate(database, release):
    # Reuse the previous operator's tested process/cwd/database/source checks.
    path = Path(__file__).with_name('repair_recorded_translations.py')
    spec = importlib.util.spec_from_file_location('recorded_translation_operator', path)
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    return operator.require_running_candidate(database, release)


def apply_operating(plan, release):
    # Applying this path requires separate operating approval.
    database = Path(plan['database']).resolve(strict=True)
    pid = require_running_candidate(database, release)
    actual = plan_for(database, [item['review'] for item in plan['jobs']])
    if actual != plan or require_running_candidate(database, release) != pid:
        raise ValueError('검토 이후 가동 worker 또는 저장 결과가 변경됐습니다')
    with closing(sqlite3.connect(database)) as connection:
        # A reviewed candidate creates this table during startup; do not migrate
        # operating state from this one-off command or from an older worker.
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='translation_saved_rechecks'").fetchone():
            raise ValueError('새 재검사 버전이 가동 상태에 준비되지 않았습니다')
        return apply_to_connection(connection, plan)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--proposal', type=Path)
    parser.add_argument('--expected-proposal-sha256')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--copy-to', type=Path)
    action.add_argument('--apply', action='store_true')
    parser.add_argument('--candidate-release', type=Path)
    args = parser.parse_args(argv)
    if args.copy_to or args.apply:
        if not args.proposal or not args.expected_proposal_sha256 or file_sha(args.proposal) != args.expected_proposal_sha256:
            parser.error('고정된 제안 파일과 일치하는 SHA-256이 필요합니다')
        plan = json.loads(args.proposal.read_text())
        if args.apply and not args.candidate_release:
            parser.error('실제 적용에는 검증한 후보 release가 필요합니다')
        result = (copy_and_apply(plan, args.copy_to) if args.copy_to else apply_operating(plan, args.candidate_release))
        print(json.dumps(dict(operating_applied=bool(args.apply), new_model_calls=0,
            jobs=[{key: item[key] for key in ('job_id', 'status', 'result_job_id')} for item in result])))
        return
    if not args.database or not args.reviews or not args.output:
        parser.error('미리보기에는 DB·저장 결과 의미 검토·새 출력 파일이 필요합니다')
    plan = plan_for(args.database, json.loads(args.reviews.read_text()))
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(dict(operating_applied=False, proposal_sha256=file_sha(args.output), new_model_calls=0,
        completed=sum(item['status'] == 'completed' for item in plan['jobs']),
        rejected=sum(item['status'] == 'rejected' for item in plan['jobs']))))


if __name__ == '__main__':
    main()
