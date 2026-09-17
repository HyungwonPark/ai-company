"""Serve real read APIs backed exclusively by an ephemeral graph fixture database.

No model, worker, credential lookup or production state is involved. --export
writes the sanitized overview instead of starting a browser/server.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from ai_company import password_auth
from ai_company.dispatcher import Dispatcher
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer


def seed_graph(store, root):
    project = store.create_project({"name": "역할 상태 확인 · 그래프 예시", "goal": "역할별로 무엇을 하는지 한눈에 보고 싶어요."})
    pid = project["id"]
    message = store.post_message(pid, {"content": "개발과 검사를 나누고 전달한 결과와 대기 이유를 보여 주세요."})
    request = store.get_pm_request(message["id"])
    store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
    plan = store.complete_pm_request(message["id"], {
        "summary": "함수 개발과 독립 검사를 나눕니다. 예시 기록이며 실제 모델을 실행하지 않았습니다.",
        "roles": [{"key": key, "name": name, "responsibility": duty, "goal": duty,
                   "acceptance": ["빈 입력과 모든 상태 확인"], "allowed_paths": [path], "depends_on": []}
                  for key, name, duty, path in [("dev", "개발", "역할 상태를 집계합니다.", "src/ai_company/pilot_status.py"),
                                                ("tests", "검사", "별도 테스트로 결과를 확인합니다.", "tests/test_pilot_status.py")]],
        "completion_criteria": ["격리 검사", "같은 후보의 독립 검수와 Astra 최종 검수"],
    }, evidence={"source": "fixture", "verification_level": "graph browser fixture; no models"})
    first = store.confirm_plan(pid, plan["id"], {"plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"],
                                               "idempotency_key": "graph-fixture-confirm"})["run"]
    second = {**copy.deepcopy(first), "id": "graph-fixture-new-run", "created_at": first["created_at"] + 10,
              "updated_at": first["created_at"] + 10, "state": "waiting"}
    dispatcher = Dispatcher(root)
    try:
        with dispatcher.db:
            dispatcher.db.execute("INSERT OR REPLACE INTO quota_groups VALUES (?,?,?,?)", ("fixture-codex", "AVAILABLE", None, None))
            dispatcher.db.execute("INSERT OR REPLACE INTO quota_groups VALUES (?,?,?,?)", ("fixture-claude", "COOLDOWN", time.time() + 900, "예시 공유 한도"))
        def task(run, key, status, *, scope="contribution", stage="developer", context=None):
            identity = "graph-fixture-" + ("old" if run["id"] == first["id"] else "new") + "-" + key
            state = {"task_id": identity, "created_at": time.time(), "status": status, "stage": stage,
                "reason": "공유 한도 대기 · 개발은 계속됩니다." if status.startswith("WAIT") else "예시로 저장한 작업 상태",
                "resume_at": time.time() + 900 if status.startswith("WAIT") else None,
                "specification": {"task": {"goal": "독립 검사" if key == "tests" else "역할 상태 집계", "repository": "fixture/graph"},
                    "worktree": "/fixture-only/isolated/" + identity, "execution_scope": scope,
                    "plan": {"plan_digest": plan["digest"], "project_id": pid, "revision": 0, **(context or {})},
                    "agents": [{"agent_id": "fixture-codex", "provider": "codex", "model": "gpt-6-astra", "reasoning_effort": "ultra", "quota_group": "fixture-codex"},
                               {"agent_id": "fixture-claude", "provider": "claude", "model": "claude-opus-5", "reasoning_effort": "xhigh", "quota_group": "fixture-claude"}]},
                "active": {"agent_id": "fixture-claude" if key == "tests" else "fixture-codex", "role": stage,
                           "execution_id": identity + ":2", "generation": 2, "previous_execution_id": identity + ":1"},
                "executions": [], "snapshot": {"head_commit": ("a" if run["id"] == first["id"] else "b") * 40}}
            with dispatcher.db:
                dispatcher._save(state, "submitted")
            store.link_task(pid, first["role_ids"].get(key, first["role_ids"]["dev"]), identity, title="독립 검사" if key == "tests" else "역할 상태 집계")
            return state

        for run in (first, second):
            old = run is first
            dev = task(run, "dev", "CONTRIBUTION_READY" if old else "RUNNING",
                       context={} if old else {"repair_findings": [{"detail": "알 수 없는 상태의 집계 근거를 보완해 주세요."}]})
            tests = task(run, "tests", "CONTRIBUTION_READY" if old else "WAITING_CAPACITY",
                         context={"dependency_artifacts": {"dev": {"candidate_sha": dev["snapshot"]["head_commit"], "clone": "/fixture-only/not-public"}}})
            for state in (dev, tests):
                with dispatcher.db:
                    dispatcher._save(state, "graph_advanced" if old else "ownership_transferred")
            run["roles"] = {"dev": {"task_id": dev["task_id"], "candidate_sha": dev["snapshot"]["head_commit"]},
                            "tests": {"task_id": tests["task_id"], "candidate_sha": tests["snapshot"]["head_commit"]}}
            integration = task(run, "integration", "MERGE_READY" if old else "WAITING_DEPENDENCY", scope="integration", stage="final" if old else "reviewer",
                               context={"contribution_artifacts": {key: {"candidate_sha": item["candidate_sha"]} for key, item in run["roles"].items()}})
            integration["verification"] = {"head_sha": integration["snapshot"]["head_commit"], "passed": True,
                                           "remote": {"source": "fixture", "conclusion": "success"}}
            with dispatcher.db:
                check = copy.deepcopy(integration); check.update(stage="check", status="READY")
                dispatcher._save(check, "submitted")
                review = copy.deepcopy(integration); review["active"]["role"] = "reviewer"
                dispatcher._save(review, "role_assigned")
                if old:
                    integration["executions"] = [{**integration["active"], "role": role, "execution_id": integration["task_id"] + ":" + role}
                                                 for role in ("reviewer", "final")]
                dispatcher._save(integration, "graph_advanced")
            run["integration"] = {"task_id": integration["task_id"]}
            if old:
                approval = store.request_approval(pid, {"title": "이전 예시 후보 수용", "action": "결과 수용만 기록합니다. 배포·병합은 아닙니다.",
                    "environment": "isolated-fixture", "artifact_sha": "a" * 40, "cost_usd": 0, "expires_at": time.time() + 86400,
                    "impact": "이전 예시 실행의 후보", "rollback": "승인을 미결로 유지", "verification": "fixture; 실제 모델·CI 실행 아님"})
                run.update(state="awaiting_approval", approval_id=approval["id"])
            with store.db:
                store.db.execute("INSERT OR REPLACE INTO management_runs VALUES (?,?,?)", (run["id"], pid, json.dumps(run)))
                store._event(pid, "fixture_graph_seeded", run["id"])
        # Fixture project metadata preserves these explicit rows while preventing writes
        # through executable project actions in browser tests.
        overview = store.overview(pid)
        project = store._project(pid)
        project.update(source="fixture", fixture_tasks=overview["tasks"], fixture_reports=overview["reports"])
        with store.db:
            store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), pid))
        other = store.create_project({"name": "다른 프로젝트 · 예시", "goal": "이전 프로젝트의 실행과 섞이지 않습니다."})
        with store.db:
            other.update(source="fixture", fixture_tasks=[], fixture_reports=[])
            store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(other), other["id"]))
        return {"project_id": pid, "plan_id": plan["id"], "run_ids": [first["id"], second["id"]], "other_project_id": other["id"]}
    finally:
        dispatcher.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, help="write sanitized fixture overview without starting a server/browser")
    parser.add_argument("--scenario", choices=("workspace_graph.cjs", "workspace_graph_independent.cjs", "diagram_view.cjs", "diagram_independent.cjs", "graph_edges_independent.cjs", "graph_edge_records.cjs"),
                        default="workspace_graph.cjs", help="run one browser review against a fresh temporary database")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="ai-company-graph-ui-") as directory:
        state = Path(directory) / "state"
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state)
        try:
            password_auth.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
            fixtures = seed_graph(store, state)
            if args.export:
                overview = store.overview(fixtures["project_id"])
                # The preview needs identifiers, graph facts and readable source documents;
                # credentials, local clone paths and operational root configuration are omitted.
                public = {"fixture": True, "fixtures": fixtures, "project": {k: overview["project"].get(k) for k in ("id", "name", "goal", "source")},
                          "workspace_graph": overview["workspace_graph"],
                          "plans": overview["plans"], "documents": overview["documents"], "approvals": overview["approvals"]}
                args.export.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
                return 0
        finally:
            store.close()
        scenario = repository / "tests" / "ui" / args.scenario
        if not scenario.exists():
            raise SystemExit("workspace_graph.cjs is not present; no browser started")
        server = ManagementHTTPServer(("127.0.0.1", 0), state, password_login=True,
                                      web_root=repository / "src" / "ai_company" / "web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.run(["node", str(scenario)], cwd=repository, timeout=600 if args.scenario == "graph_edge_records.cjs" else 300, check=False,
                env={**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_PASSWORD": password,
                     "GRAPH_FIXTURES": json.dumps(fixtures)}).returncode
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
