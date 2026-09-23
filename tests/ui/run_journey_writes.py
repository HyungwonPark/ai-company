"""Exercise real write APIs in an ephemeral store, with explicitly synthetic PM facts.

The browser calls this file's fixture command locally for PM/candidate records.
No extra HTTP endpoint, worker, model process, credential lookup or operational
state is used. The executable catalog is the existing unit-test fixture.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time

from ai_company import password_auth
from ai_company.collaboration import plan_document
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.execution_specs import ExecutionCatalog
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
from ai_company.translations import TranslationStore, segments

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "tests"))
from test_execution_specs import catalog_config  # Reuse a bounded nonoperational configuration.


def catalog():
    return ExecutionCatalog({"journey-fixture": catalog_config()})


def content():
    return {"summary": "Build and independently verify a small function", "roles": [
        {"key": key, "name": name, "responsibility": duty, "goal": goal,
         "acceptance": [acceptance], "allowed_paths": [file], "depends_on": []}
        for key, name, duty, goal, acceptance, file in (
            ("impl", "Implementation", "Implement the function", "Preserve existing data", "Do not deploy", "src/a.py"),
            ("test", "Tests", "Independent verification", "Verify the same candidate", "Inspect failure cases", "tests/a.py"))],
        "completion_criteria": ["No merge without approval"]}


def translated_plan(store, plan):
    document = plan_document(plan["project_id"], plan)
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO credential_groups VALUES ('codex','journey-fixture','journey-fixture')")
        store.db.execute("INSERT OR IGNORE INTO quota_groups VALUES ('journey-fixture','AVAILABLE',NULL,NULL)")
    translations = TranslationStore(store.db)
    translations.sync(plan["project_id"], [document], {"credential_ref": "journey-fixture", "quota_group": "journey-fixture"})
    job = translations.claim("journey-fixture-translator", adapter_ready=True)
    words = {"Build and independently verify a small function": "작은 함수를 개발하고 독립적으로 검증합니다",
             "Implementation": "개발", "Implement the function": "함수를 구현합니다", "Preserve existing data": "기존 데이터를 보존합니다",
             "Do not deploy": "배포하지 않습니다", "Tests": "검사", "Independent verification": "독립적으로 검증합니다",
             "Verify the same candidate": "같은 후보를 검증합니다", "Inspect failure cases": "실패 사례를 검사합니다",
             "No merge without approval": "승인 없이 병합하지 않습니다"}
    translations.finish(job["id"], job["lease_token"], {"category": "success",
        "fields": {key: words[value] for key, value in segments(document["fields"]).items()}})
    result = translations.get_result(job["id"])
    assert result["status"] == "completed", result
    return {"id": job["id"], "source_digest": document["source_digest"]}


def inject(state, action, project_id):
    # A marker created by main constrains this helper to this test's temporary root.
    assert (state / "journey-write-fixture-only").read_text() == "no production or model calls\n"
    # For expiry, record a valid request at an earlier fixture time; the real API
    # uses current time and must then reject it. No historical row is rewritten.
    store = ManagementStore(state, execution_catalog=catalog(),
                            clock=(lambda: time.time() - 120) if action == "expired-approval" else time.time)
    try:
        if action == "proposal":
            request = store.overview(project_id)["pm_requests"][-1]
            assert request["state"] == "pending", "Fixture completion applies only to the pending request"
            config = store.execution_config_for(project_id, request["execution_spec"]) if request.get("execution_spec") else catalog_config()
            store.save_pm_request({**request, "state": "running", "configuration_digest": digest(config), "mode": "fixture"}, expected_state="pending")
            body = content()
            if not request.get("execution_spec"):
                body["execution_spec_proposal"] = {"catalog_id": "journey-fixture", "catalog_digest": digest(config),
                    "allowed_paths": ["src/a.py", "tests/a.py"]}
            plan = store.complete_pm_request(request["request_id"], body,
                evidence={"source": "fixture", "verification_level": "synthetic PM proposal; no models or worker"})
            translation = translated_plan(store, plan) if request.get("execution_spec") else None
            return {"fixture_action": action, "plan": plan, "translation": translation}
        if action in ("approval", "expired-approval"):
            run = store.overview(project_id)["runs"][-1]
            expired = action == "expired-approval"
            approval = store.request_approval(project_id, {"title": "기한 지난 시험 후보" if expired else "같은 실행의 시험 후보",
                "action": "임시 DB에서 후보 수용만 기록합니다. 배포와 병합은 하지 않습니다.",
                "environment": "isolated-journey-fixture", "artifact_sha": "a" * 40, "cost_usd": 0,
                "expires_at": time.time() + (-60 if expired else 3600), "impact": "가상 시험 기록",
                "rollback": "임시 시험 DB 폐기", "verification": "synthetic candidate; actual development and CI are not claimed"})
            store.save_run({**run, "state": "awaiting_approval", "approval_id": approval["id"],
                "candidate_sha": approval["artifact_sha"], "updated_at": time.time()})
            return {"fixture_action": action, "run_id": run["id"], "approval": approval}
        raise ValueError("Unknown fixture action")
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-action", choices=("proposal", "approval", "expired-approval"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--project")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    if args.fixture_action:
        print(json.dumps(inject(args.state, args.fixture_action, args.project), ensure_ascii=False))
        return 0
    with tempfile.TemporaryDirectory(prefix="ai-company-journey-writes-") as directory:
        state = Path(directory) / "state"
        initializer = Dispatcher(state)
        initializer.close()
        (state / "journey-write-fixture-only").write_text("no production or model calls\n")
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state, execution_catalog=catalog())
        try:
            password_auth.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
        finally:
            store.close()
        if args.seed_only:
            store = ManagementStore(state, execution_catalog=catalog())
            try:
                project = store.create_project({"name": "fixture self-check", "goal": "작은 기능 검증", "start_pm": True})
                pid = project["id"]
                initial = inject(state, "proposal", pid)
                saved = store.register_execution_spec(pid, {"base_version": 0, "idempotency_key": "fixture-save-once",
                    "selection": initial["plan"]["content"]["execution_spec_proposal"]})
                store.post_message(pid, {"content": "등록한 명세로 새 계획"})
                fresh = inject(state, "proposal", pid)
                plan = fresh["plan"]
                confirmed = store.confirm_plan(pid, plan["id"], {"plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
                    "execution_spec_digest": saved["digest"], "idempotency_key": "fixture-confirm-once", "displayed_translation": fresh["translation"]})
                candidate = inject(state, "approval", pid)
                assert candidate["run_id"] == confirmed["run"]["id"]
                expired = inject(state, "expired-approval", pid)
                assert expired["approval"]["expires_at"] < time.time()
                assert store.db.execute("SELECT count(*) FROM session_jobs").fetchone()[0] == 0
                print(json.dumps({"status": "fixture contracts prepared; browser not run", "translation": "saved synthetic output accepted", "run_count": 1, "session_jobs": 0}))
                return 0
            finally:
                store.close()
        server = ManagementHTTPServer(("127.0.0.1", 0), state, password_login=True,
            execution_catalog=catalog(), web_root=REPOSITORY / "src" / "ai_company" / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(["node", str(REPOSITORY / "tests/ui/journey_writes.cjs")], cwd=REPOSITORY, timeout=360, check=False,
                env={**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_PASSWORD": password,
                     "JOURNEY_FIXTURE_STATE": str(state), "PYTHON_EXECUTABLE": sys.executable})
            store = ManagementStore(state, execution_catalog=catalog())
            try:
                assert store.db.execute("SELECT count(*) FROM session_jobs").fetchone()[0] == 0, "No worker/model sessions may be created"
                assert store.db.execute("SELECT count(*) FROM flow_tasks").fetchone()[0] == 0, "No dispatcher tasks may be executed"
            finally:
                store.close()
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
