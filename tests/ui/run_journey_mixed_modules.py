"""Authenticated mixed-shell browser regression on a loopback-only temporary API.

Only public asset selection/failure is injected. API auth, CSRF, Host, Origin,
SQLite reads and project/run binding are the actual product implementations.
"""
import argparse
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from ai_company import management_diagrams, password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHandler, ManagementHTTPServer
from run_journey_workspace import business_state
from run_workspace_graph import seed_graph

BASELINE = "75227480d27d3eb1cd57fa43a07a0ab986be3eb5"


class MixedHandler(ManagementHandler):
    def do_POST(self):
        path = urlsplit(self.path).path
        self.server.write_paths.append(path)
        if path not in ("/api/login", "/api/password"):
            self._json(409, {"error": {"code": "test_write_blocked", "message": "Read-only mixed-module test"}})
            return
        super().do_POST()

    def _static(self, path):
        if path == "/__mixed__/configure":
            args = parse_qs(urlsplit(self.path).query)
            self.server.asset_mode = args.get("mode", ["old"])[0]
            self.server.failed_assets = set(args.get("fail", []))
            self.server.old_app = args.get("oldapp", ["0"])[0] == "1"
            self._json(200, {"mode": self.server.asset_mode, "failed_assets": sorted(self.server.failed_assets), "old_app": self.server.old_app})
            return
        if path == "/__mixed__/evidence":
            self._json(200, {"writes": self.server.write_paths, "asset_failures": self.server.asset_failures})
            return
        name = path.lstrip("/") or "index.html"
        if name in self.server.failed_assets:
            self.server.asset_failures.append(name)
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        if self.server.asset_mode == "old" or self.server.old_app and name == "app.js":
            body = self.server.baseline_files.get(name)
            if body is None:
                self._json(404, {"error": {"code": "not_found"}})
                return
            self.send_response(200)
            self._headers(mimetypes.guess_type(name)[0] or "application/octet-stream", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        super()._static(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    archive = subprocess.check_output(["git", "archive", BASELINE, "src/ai_company/web"], cwd=repository)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        baseline_files = {member.name.removeprefix("src/ai_company/web/"): bundle.extractfile(member).read()
                          for member in bundle.getmembers() if member.isfile()}
    with tempfile.TemporaryDirectory(prefix="ai-company-mixed-api-") as directory:
        state = Path(directory)
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state)
        try:
            password_auth.initialize(store.db)
            management_diagrams.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
            fixtures = seed_graph(store, state)
            draft = store.create_project({"name": "미전송 초안 · 격리 검사", "goal": "미전송 입력은 읽기 검사에서 전송되지 않습니다."})
            fixtures["draft_project_id"] = draft["id"]
            before = business_state(store)
            if args.seed_only:
                print(json.dumps({"status": "fixture prepared; browser not run", "tables": len(before), "baseline_assets": len(baseline_files), "draft_project": bool(fixtures["draft_project_id"])}))
                return 0
        finally:
            store.close()
        server = ManagementHTTPServer(("127.0.0.1", 0), state, password_login=True, web_root=repository / "src/ai_company/web")
        server.RequestHandlerClass = MixedHandler
        server.baseline_files = baseline_files
        server.asset_mode = "old"
        server.old_app = False
        server.failed_assets = set()
        server.asset_failures = []
        server.write_paths = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        result = None
        try:
            result = subprocess.run(["node", str(repository / "tests/ui/journey_mixed_modules.cjs")], cwd=repository, timeout=480, check=False,
                env={**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_PASSWORD": password, "GRAPH_FIXTURES": json.dumps(fixtures)})
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            store = ManagementStore(state)
            try:
                after = business_state(store)
            finally:
                store.close()
            changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
            assert not changed, changed
            output = Path(os.environ.get("UI_OUTPUT", "/tmp/ai-company-journey-mixed")); output.mkdir(parents=True, exist_ok=True)
            (output / "journey-mixed-database.json").write_text(json.dumps({"status": "PASS", "scope": "actual temporary API; authentication tables excluded",
                "tables": list(before), "unchanged_sha256": hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "browser_returncode": result.returncode if result else None, "write_paths": server.write_paths,
                "models": "not called", "production": "not accessed"}, ensure_ascii=False, indent=2))
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
