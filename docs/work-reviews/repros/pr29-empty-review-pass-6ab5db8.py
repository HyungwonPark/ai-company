"""R29-1 관찰용 재현: 현재 잘못된 PASS를 단언합니다. 모델/네트워크 호출 없음.
사용법: python repro.py /검수할/저장소/scripts/review_pr_with_claude.py
수정 후에는 근거 없는 결과가 PASS가 아님을 단언하는 회귀로 전환하세요.
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("review_runner", Path(sys.argv[1]))
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
nonce = "empty-review"
def control(suffix):
    return {"type": "control_response", "response": {
        "request_id": nonce + suffix,
        "response": {"applied": review.APPLIED, "has_errors": False}}}
events = [
    control("-before"),
    {"type": "assistant", "message": {"model": review.MODEL, "content": []}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "판정: PASS", "stop_reason": "end_turn",
     "terminal_reason": "completed", "queued_turn_count": 0,
     "permission_denials": [],
     "subagent_stats": {"spawned": 0, "completed": 0, "killed": {}, "refused": {}}},
    control("-after"),
]
result = review.summarize(events, nonce, 0)
assert result["review_passed"] and result["tools_used"] == []
print({key: result[key] for key in
       ("review_passed", "completion_verified", "tools_used", "verdict")})
