"""Isolated v2 PM API/browser flow with fixture model sessions and real writes."""

import json
from hashlib import sha256
import os
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from ai_company import password_auth
from ai_company.skill_catalog import _bundle_hash
from ai_company.adapters.session_cli import SessionOutcome
from ai_company.management_server import ManagementHTTPServer

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
from tests.test_automation import CoordinatorTests


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        control = self.server
        if self.headers.get("X-Fixture-Token") != control.token:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                raise ValueError("large fixture command")
            value = json.loads(self.rfile.read(length))
            with control.lock:
                if self.path == "/bind":
                    control.target = value["project_id"]
                    result = {"bound": control.target}
                elif self.path == "/step":
                    if not control.target:
                        raise ValueError("bind a browser-created project first")
                    control.h.worker.run_once()
                    result = control.snapshot()
                elif self.path == "/restart":
                    control.h.worker.close()
                    control.h.worker = control.h.open()
                    result = control.snapshot()
                elif self.path == "/assign":
                    control.h.worker.reconcile()
                    result = control.snapshot()
                else:
                    raise ValueError("unknown fixture command")
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
        except (KeyError, ValueError, AssertionError) as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(400)
        except Exception as exc:
            body = json.dumps({"error": type(exc).__name__ + ": " + str(exc)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ControlServer(HTTPServer):
    def __init__(self, h):
        self.h = h
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.target = None
        self.review_revised = False
        super().__init__(("127.0.0.1", 0), ControlHandler)
        self.timeout = 0.5

    def snapshot(self):
        overview = self.h.worker.store.overview(self.target)
        return {"project_id": self.target,
                "requests": [{"id": r["request_id"], "state": r["state"]} for r in overview["pm_requests"]],
                "plans": [{"id": p["id"], "digest": p["digest"], "status": p["status"],
                           "review": (p.get("review") or {}).get("verdict"),
                           "repair_attempt": p.get("repair_attempt")}
                          for p in overview["plans"]],
                "runs": [{"id": r["id"], "plan_id": r["plan_id"], "roles": sorted(r.get("roles", {}))}
                         for r in overview["runs"]],
                "role_tasks": [t["id"] for t in overview["tasks"] if t["id"].startswith("role-")],
                "review_revised": self.review_revised}


def main():
    h = CoordinatorTests()
    h.setUp()
    skill_root = h.root / "browser-reviewed-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("---\nname: 화면 점검\n---\n키보드와 모바일 버튼을 확인합니다.\n")
    (skill_root / "LICENSE").write_text("MIT License\n")
    source = "https://github.com/example/skills/blob/" + "a" * 40 + "/screen/SKILL.md"
    hashes = {name: sha256((skill_root / name).read_bytes()).hexdigest() for name in ("SKILL.md", "LICENSE")}
    h.worker.close()
    h.config = h.config.model_copy(update={"skill_catalog": ({
        "root": str(skill_root), "skill_id": "screen", "source_url": source, "source_ref": "a" * 40,
        "expected_sha256": _bundle_hash(source, "a" * 40, hashes), "license_path": "LICENSE",
        "license_id": "MIT", "redistribution": "internal_only", "providers": ("codex", "claude"),
        "runners": ("session_cli",), "capabilities": ("accessibility",)},)})
    h.plan["roles"][0]["required_capabilities"] = ["accessibility"]
    h.worker = h.open()
    password = secrets.token_urlsafe(24)
    with h.worker.store.db:
        password_auth.initialize(h.worker.store.db)
        password_auth.create_user(h.worker.store.db, "edward", password, time.time())
    controller = ControlServer(h)
    original = h.execute
    asked = False

    def model_fixture(agent, state, provider, worktree, prompt, session_id, **kwargs):
        nonlocal asked
        outcome = original(agent, state, provider, worktree, prompt, session_id, **kwargs)
        context = state["specification"]["plan"]
        if context.get("project_id") != controller.target:
            return outcome
        report = outcome.result["structured_output"]
        if state["stage"] == "pm" and not asked:
            asked = True
            report.update(verdict="BLOCK", plan=None, summary="첫 버전에서 맡길 대표 업무를 알려주세요.")
            report["requirements_feedback"] = {
                "version": 2, "revision": context["request_revision"],
                "goal_digest": context["goal_digest"],
                "problem": "첫 업무가 없으면 완료 조건을 정할 수 없습니다.",
                "users_and_flow": "마스터가 목표를 정하고 PM과 범위를 결정합니다.",
                "scope": ["첫 검증 프로젝트"], "exclusions": [], "assumptions": [],
                "questions": [{"id": "Q-1", "prompt": "첫 버전의 대표 업무는 무엇인가요?",
                               "reason": "완료 조건과 담당 범위가 달라집니다.",
                               "options": ["작은 함수 개발", "여러 프로젝트 운영"],
                               "recommendation": "작은 함수 개발부터 확인하는 안을 권합니다.",
                               "status": "open"}], "findings": [],
                "requirements": [{"id": "R-1", "source": "master goal",
                                  "acceptance": "대표 업무가 결정됩니다.",
                                  "verification": "저장된 마스터 답변을 확인합니다.",
                                  "role_keys": ["impl"]}]}
        elif state["stage"] == "pm" and not context.get("source_plan_id"):
            followup = controller.h.worker.store.overview(controller.target)["pm_requests"][-1]
            if (followup.get("conversation_context") or {}).get("previous_requirements_feedback"):
                report["plan"]["requirements_review"]["questions"] = [{
                    "id": "Q-1", "prompt": "첫 버전의 대표 업무는 무엇인가요?",
                    "reason": "완료 조건과 담당 범위가 달라집니다.", "status": "answered",
                    "resolution": "작은 함수 개발", "answer_message_id": followup["request_id"]}]
        elif state["stage"] == "reviewer" and state["specification"]["execution_scope"] == "plan_review" and not controller.review_revised:
            controller.review_revised = True
            report.update(verdict="REVISE", revision_route="technical", summary="검증 방법을 더 구체화하세요.", findings=[{
                "finding_id": "F-TEST", "detail": "빈 입력의 예상 결과를 명시하세요.",
                "evidence": "검사 역할의 완료 조건에는 빈 입력 사례가 없습니다."}])
        elif state["stage"] == "pm" and context.get("source_plan_id"):
            report["plan"]["requirements_review"]["questions"] = \
                context["previous_plan"]["requirements_review"]["questions"]
            report["plan"]["requirements_review"]["requirements"][0]["verification"] = "빈 입력과 모든 분류를 검사합니다."
        return outcome

    h.execute = model_fixture
    application = ManagementHTTPServer(("127.0.0.1", 0), h.root / "state", password_login=True,
                                       web_root=REPOSITORY / "src" / "ai_company" / "web")
    api_thread = threading.Thread(target=application.serve_forever, daemon=True)
    api_thread.start()
    browser = None
    try:
        browser = subprocess.Popen(["node", "tests/ui/pm_v2_flow.cjs"], cwd=REPOSITORY,
                                   env={**os.environ, "BASE_URL": f"http://127.0.0.1:{application.server_port}",
                                        "CONTROL_URL": f"http://127.0.0.1:{controller.server_port}",
                                        "CONTROL_TOKEN": controller.token, "TEST_PASSWORD": password})
        deadline = time.monotonic() + 420
        while browser.poll() is None and time.monotonic() < deadline:
            controller.handle_request()  # Fixture worker and its SQLite connections stay on this thread.
        if browser.poll() is None:
            browser.kill()
            raise TimeoutError("isolated PM browser flow exceeded 420 seconds")
        return browser.returncode
    finally:
        if browser is not None and browser.poll() is None:
            browser.kill()
            browser.wait(timeout=5)
        application.shutdown()
        application.server_close()
        controller.server_close()
        api_thread.join(timeout=5)
        h.doCleanups()


if __name__ == "__main__":
    raise SystemExit(main())
