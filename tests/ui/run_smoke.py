"""Run the browser scenario against temporary fixture state only.

Requires Playwright on NODE_PATH and a sandbox-capable Chrome/Chromium runtime.
No persistent tokens, production queues, privileged browser flags, or deployment.
"""

import os
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading

from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer


def seed_plan(store, name):
    """No model or worker is called: only the real API persistence contract is exercised."""
    project = store.create_project({"name": name, "goal": "UI fixture: review and confirm a stored PM plan"})
    message = store.post_message(project["id"], {"content": "UI fixture: propose two independent roles"})
    request = store.get_pm_request(message["id"])
    store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
    plan = store.complete_pm_request(message["id"], {
        "summary": "브라우저 회귀용 모의 계획 · 실제 모델 호출 없음",
        "roles": [{"key": key, "name": title, "responsibility": f"{title} 책임", "goal": f"{title} 산출물",
                   "acceptance": ["격리된 테스트 통과"], "allowed_paths": [f"src/{key}/"], "depends_on": []}
                  for key, title in [("api", "API 역할"), ("web", "화면 역할")]],
        "completion_criteria": ["두 역할의 산출물 검수", "같은 후보 커밋의 검증"],
    }, evidence={"source": "fixture", "verification_level": "UI fixture only; no model execution"})
    return {"project_id": project["id"], "plan_id": plan["id"], "digest": plan["digest"]}


def main():
    repository = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="ai-company-ui-") as directory:
        root = Path(directory)
        token = secrets.token_urlsafe(48)
        token_path = root / "login-token"
        with os.fdopen(os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            handle.write(token)
        store = ManagementStore(root / "state")
        try:
            store.seed_demo()
            plans = [seed_plan(store, name) for name in ("UI plan confirmation fixture", "UI stale plan fixture")]
        finally:
            store.close()
        server = ManagementHTTPServer(
            ("127.0.0.1", 0), root / "state", token_path,
            web_root=repository / "src" / "ai_company" / "web",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_TOKEN": token, "PLAN_FIXTURES": json.dumps(plans)}
        try:
            result = subprocess.run(
                ["node", str(repository / "tests" / "ui" / "smoke.cjs")],
                env=env, cwd=repository, timeout=240, check=False,
            )
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
