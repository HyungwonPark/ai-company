"""Real authenticated read API and immutable before/after packaged UI comparison."""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
import zipfile
from ai_company import management_diagrams, password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
from run_journey_workspace import business_state, seed_status_cases
from run_workspace_graph import seed_graph

BASELINE = "dec455e63458ef98ac34d8e423463f7d5e30fdad"


def main():
    repo = Path(__file__).resolve().parents[2]
    output = Path(os.environ.get("UI_OUTPUT", "/tmp/ai-company-usability"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="journey-usability-") as directory:
        root = Path(directory)
        baseline = subprocess.check_output(["git", "show", BASELINE + ":docs/previews/journey/AI-Company-journey-preview.zip"], cwd=repo)
        with zipfile.ZipFile(io.BytesIO(baseline)) as archive:
            html = next(name for name in archive.namelist() if name.endswith(".html"))
            (root / "before.html").write_bytes(archive.read(html))
        subprocess.run(["python3", "scripts/package_journey_preview.py", str(root / "after.html")], cwd=repo, check=True)
        state = root / "state"
        password = secrets.token_urlsafe(24)
        store = ManagementStore(state)
        try:
            password_auth.initialize(store.db)
            management_diagrams.initialize(store.db)
            with store.db:
                password_auth.create_user(store.db, "edward", password, time.time())
            fixtures = seed_graph(store, state, long_names=True)
            seed_status_cases(store, fixtures)
            before = business_state(store)
        finally:
            store.close()
        server = ManagementHTTPServer(("127.0.0.1", 0), state, password_login=True, web_root=repo / "src/ai_company/web")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        result = None
        try:
            result = subprocess.run(["node", "tests/ui/journey_usability.cjs"], cwd=repo, timeout=480, check=False, env={**os.environ,
                "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_PASSWORD": password, "GRAPH_FIXTURES": json.dumps(fixtures),
                "USABILITY_BEFORE_HTML": str(root / "before.html"), "USABILITY_AFTER_HTML": str(root / "after.html")})
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            store = ManagementStore(state)
            try:
                after = business_state(store)
            finally:
                store.close()
            changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
            assert not changed, changed
            (output / "journey-usability-database.json").write_text(json.dumps({"status": "PASS", "tables": list(before),
                "unchanged_sha256": hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                "browser_returncode": result.returncode if result else None, "baseline": BASELINE,
                "scope": "temporary real API; auth tables excluded; execution facts synthetic; no models or production"}, ensure_ascii=False, indent=2))
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
