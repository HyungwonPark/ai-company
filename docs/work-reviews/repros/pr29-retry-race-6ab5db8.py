"""R29-2 관찰용 재현: 모델/네트워크 호출 없이 동시 예약 충돌을 확인합니다.\n사용법: python repro.py /검수할/저장소/scripts/review_pr_with_claude.py\n현재 종료 코드 0은 결함 재현 성공이며 제품 합격을 뜻하지 않습니다.\n"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('review', Path(sys.argv[1]))
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)/'pr-29-abc'
    directory.mkdir()
    (directory/'facts.jsonl').write_text(json.dumps({'state':'closed'})+'\n')
    both_read_old_facts = threading.Barrier(2)
    first_reservation_active = threading.Event()
    original_read = Path.read_text
    original_rename = Path.rename
    outcomes = []

    def read(path, *args, **kwargs):
        content = original_read(path, *args, **kwargs)
        if path == directory/'facts.jsonl':
            both_read_old_facts.wait(timeout=5)
        return content

    def rename(path, target):
        if threading.current_thread().name == 'second':
            assert first_reservation_active.wait(timeout=5)
        return original_rename(path, target)

    def retry():
        name = threading.current_thread().name
        try:
            review.reserve_attempt(directory, True)
            # Real runner starts the relay here; record its irreversible call intent.
            if name == 'first':
                (directory/'facts.jsonl').write_text(json.dumps({'state':'prompt_delivery_started'})+'\n')
                (directory/'active-first').write_text('active')
                first_reservation_active.set()
            outcomes.append((name, 'reservation granted'))
        except Exception as exc:
            outcomes.append((name, type(exc).__name__, str(exc)))

    with patch.object(Path, 'read_text', read), patch.object(Path, 'rename', rename):
        first = threading.Thread(target=retry, name='first')
        second = threading.Thread(target=retry, name='second')
        first.start(); second.start()
        first.join(timeout=8); second.join(timeout=8)
    archived_live_attempt = [p for p in directory.parent.glob('pr-29-abc-unstarted-*') if (p/'active-first').exists()]
    assert len(outcomes) == 2 and all(row[1]=='reservation granted' for row in outcomes), outcomes
    assert len(archived_live_attempt) == 1
    assert 'prompt_delivery_started' in (archived_live_attempt[0]/'facts.jsonl').read_text()
    print('CONFIRMED:', outcomes)
    print('Second retry archived an already-started first attempt as unstarted and obtained the same-head slot.')
