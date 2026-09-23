"""Journey browser review against a temporary real API; no worker/model calls.

Reuse the graph fixture and compare every business table before/after read-only
navigation. Authentication tables are deliberately outside that comparison.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from ai_company import management_diagrams, password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
from run_workspace_graph import seed_graph


def business_state(store):
    ignored = {"management_auth", "console_users", "console_sessions", "console_login_limit", "sqlite_sequence"}
    tables = sorted(row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    return {table: sorted(tuple(row) for row in store.db.execute('SELECT * FROM "' + table + '"'))
            for table in tables if table not in ignored}



def seed_status_cases(store, fixtures):
    """Synthetic saved states through the real SQLite/overview/snapshot contract."""
    pid = fixtures["project_id"]
    overview = store.overview(pid)
    template = overview["runs"][-1]
    project = store._project(pid)
    tasks = project["fixture_tasks"]
    cases = [
        ("completed-operator", "waiting", ["MERGE_READY", "RECONCILIATION_REQUIRED"], "확인 필요", {"완료": 1, "확인 필요": 1}),
        ("operator-handoff", "waiting", ["NEEDS_RECONCILIATION", "NEEDS_CONTEXT_HANDOFF"], "확인 필요", {"확인 필요": 2}),
        ("rejected", "rejected", ["CONTRIBUTION_READY", "MERGE_READY"], "반려됨", {"완료": 2}),
        ("running", "running", ["RUNNING", "CHECK_RUNNING"], "작업 중", {"진행": 2}),
        ("scheduled", "waiting", ["WAITING_QUOTA", "WAITING_RETRY"], "대기", {"대기": 2}),
        ("unknown", "waiting", ["NEW_UNREGISTERED_STATE", "WAITING_NEW_UNREGISTERED_STATE"], "상태 미확인", {"미확인": 2}),
        ("eligibility", "waiting", ["NO_ELIGIBLE_AGENT", "HANDOFF_PENDING"], "확인 필요", {"확인 필요": 1, "진행": 1}),
        ("superseded", "waiting", ["SUPERSEDED", "READY"], "확인 필요", {"확인 필요": 1, "진행": 1}),
    ]
    output = []
    for index, (name, run_state, statuses, title, groups) in enumerate(cases):
        run = copy.deepcopy(template)
        run.update(id="journey-status-" + name, state=run_state,
                   created_at=template["created_at"] + 100 + index,
                   updated_at=template["updated_at"] + 100 + index,
                   reason="상태 투영 독립 검사 · 저장된 예시 기록")
        run.pop("integration", None)
        run.pop("approval_id", None)
        run["roles"] = {}
        for role, status in zip(("dev", "tests"), statuses):
            source = next(t for t in overview["tasks"] if t["id"] == template["roles"][role]["task_id"])
            task = copy.deepcopy(source)
            task.update(id=run["id"] + "-" + role, status=status,
                        wait_reason="예약 시각까지 대기합니다." if name == "scheduled" else "저장된 원문 상태와 실행 근거를 대조하세요.",
                        resume_at=2000000000 if name == "scheduled" else None)
            tasks.append(task)
            run["roles"][role] = {"task_id": task["id"], "status": status, "resume_at": task["resume_at"]}
        with store.db:
            store.db.execute("INSERT INTO management_runs VALUES (?,?,?)", (run["id"], pid, json.dumps(run)))
        output.append(dict(name=name, run_id=run["id"], state=run_state, statuses=statuses, title=title, groups=groups))
    with store.db:
        store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), pid))
    result = store.overview(pid)
    for case in output:
        snapshot = next(s for s in result["workspace_graph"]["snapshots"] if s["run_id"] == case["run_id"])
        nodes = [n for n in snapshot["nodes"] if n["kind"] == "role"]
        assert sorted(n["status"] for n in nodes) == sorted(case["statuses"]), case
        case["snapshot_id"] = snapshot["id"]
    fixtures["status_cases"] = output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("journey_workspace.cjs", "journey_graph.cjs", "journey_status.cjs"), default="journey_workspace.cjs")
    parser.add_argument("--seed-only", action="store_true", help="validate fixture/invariant capture without starting a browser")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="ai-company-journey-ui-") as directory:
        state = Path(directory) / "state"
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state)
        try:
            password_auth.initialize(store.db)
            # Match the HTTP server's startup schema before capturing invariants.
            # Keep this table in the comparison: navigation must create no diagrams.
            management_diagrams.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
            fixtures = seed_graph(store, state)
            if args.scenario == "journey_status.cjs":
                seed_status_cases(store, fixtures)
            before = business_state(store)
            if args.seed_only:
                print(json.dumps({"status": "fixture prepared; browser not run", "tables": list(before), "run_count": len(fixtures["run_ids"])}))
                return 0
        finally:
            store.close()
        server = ManagementHTTPServer(("127.0.0.1", 0), state, password_login=True,
                                      web_root=repository / "src" / "ai_company" / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        result = None
        try:
            result = subprocess.run(["node", str(repository / "tests" / "ui" / args.scenario)], cwd=repository,
                timeout=360, check=False, env={**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "TEST_PASSWORD": password, "GRAPH_FIXTURES": json.dumps(fixtures)})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            store = ManagementStore(state)
            try:
                after = business_state(store)
            finally:
                store.close()
            changed = sorted(table for table in before.keys() | after.keys() if before.get(table) != after.get(table))
            assert not changed, f"Read navigation changed business tables: {changed}"
            output = Path(os.environ.get("UI_OUTPUT", "/tmp/ai-company-journey-ui"))
            output.mkdir(parents=True, exist_ok=True)
            fingerprint = hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            (output / (Path(args.scenario).stem + "-database.json")).write_text(json.dumps({
                "status": "PASS", "scope": "temporary SQLite business tables; authentication excluded",
                "tables": list(before), "unchanged_sha256": fingerprint,
                "browser_returncode": result.returncode if result else None,
                "models": "not called", "production": "not accessed",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
