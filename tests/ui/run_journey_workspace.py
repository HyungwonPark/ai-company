"""Journey browser review against a temporary real API; no worker/model calls.

Reuse the graph fixture and compare every business table before/after read-only
navigation. Authentication tables are deliberately outside that comparison.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time

from ai_company import password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
from run_workspace_graph import seed_graph


def business_state(store):
    ignored = {"management_auth", "console_users", "console_sessions", "console_login_limit", "sqlite_sequence"}
    tables = sorted(row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    return {table: sorted(tuple(row) for row in store.db.execute('SELECT * FROM "' + table + '"'))
            for table in tables if table not in ignored}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("journey_workspace.cjs", "journey_graph.cjs"), default="journey_workspace.cjs")
    parser.add_argument("--seed-only", action="store_true", help="validate fixture/invariant capture without starting a browser")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="ai-company-journey-ui-") as directory:
        state = Path(directory) / "state"
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state)
        try:
            password_auth.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
            fixtures = seed_graph(store, state)
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
