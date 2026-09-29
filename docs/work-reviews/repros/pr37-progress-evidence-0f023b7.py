#!/usr/bin/env python3
"""PR #37 0f023b7 중간 설정 증거의 잘못된 완료 인정 2건 재현.

합성 이벤트만 사용합니다. 실제 timeout 원문의 소급 판정이 아니며,
모델·네트워크·운영 장부·제품 파일을 호출하거나 변경하지 않습니다.
"""
import importlib.util
import json
from pathlib import Path
import sys


sys.dont_write_bytecode = True
SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "source"
sys.path.insert(0, str(SOURCE / "src"))
SPEC = importlib.util.spec_from_file_location(
    "pr37_progress_repro", SOURCE / "scripts/review_pr_with_claude.py"
)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)

BINDING = {"pr": 35, "head": "a" * 40, "diff_sha256": "b" * 64, "nonce": "n"}
CLAIM = {
    "head": BINDING["head"],
    "patch_sha256": BINDING["diff_sha256"],
    "reviewed_files": ["a.py"],
}


def settings(suffix, effort="xhigh"):
    return {"type": "control_response", "response": {
        "request_id": "n-" + suffix,
        "response": {"applied": {**REVIEW.APPLIED, "effort": effort},
                     "has_errors": False},
    }}


def progress(claim):
    return {"type": "assistant", "parent_tool_use_id": None,
            "session_id": "main", "message": {
                "model": REVIEW.MODEL,
                "content": [{"type": "text", "text":
                    "REVIEW_PROGRESS_JSON: " + json.dumps(claim)}],
            }}


READ = [
    {"type": "assistant", "session_id": "main", "message": {
        "model": REVIEW.MODEL, "content": [{"type": "tool_use", "id": "r1",
            "name": "Read", "input": {"file_path": "/repo/a.py"}}]}},
    {"type": "user", "session_id": "main", "message": {
        "content": [{"type": "tool_result", "tool_use_id": "r1",
                     "is_error": False}]}},
]


def show(name, events, explanation):
    checkpoint = REVIEW.checkpoint_from_events(
        events, binding=BINDING, base="c" * 40, files=["a.py"],
        reservation_id="synthetic-reservation", repo="/repo",
    )
    result = {
        "case": name, "synthetic_only": True, "explanation": explanation,
        "expected_reviewed_files": [],
        "current_reviewed_files": checkpoint["reviewed_files"],
        "current_scope_verified": checkpoint["scope_verified"],
        "current_global_configuration_verified":
            checkpoint["configuration_observed"]["verified"],
        "current_progress_verified":
            checkpoint["configuration_observed"].get("progress_verified", []),
    }
    print(json.dumps(result, ensure_ascii=False))
    return checkpoint["reviewed_files"] == ["a.py"]


first = show(
    "midrun_medium_bypassed_by_good_before_after",
    [settings("before"), *READ, progress(CLAIM),
     settings("progress-1", "medium"), settings("after")],
    "선언에 대응한 중간 설정은 medium인데 전후 설정 통과가 해당 선언을 완료로 인정합니다.",
)

long_marker = progress({**CLAIM, "requirements": ["x" * 10000]})
assert len(long_marker["message"]["content"][0]["text"]) > 10000
second = show(
    "oversized_marker_shifts_snapshot_binding",
    [settings("before"), *READ, long_marker, progress(CLAIM),
     settings("progress-1"), settings("progress-2", "medium")],
    "relay는 긴 선언에도 번호 1을 부여하므로 정상 선언의 설정은 번호 2입니다. "
    "parser는 긴 선언을 세지 않아 정상 선언을 번호 1의 지연 응답에 잘못 연결합니다. "
    "after 설정은 없습니다.",
)
raise SystemExit(0 if first and second else 1)
