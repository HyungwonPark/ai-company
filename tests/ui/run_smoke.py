"""Run the browser scenario against temporary fixture state only.

Requires Playwright on NODE_PATH and a sandbox-capable Chrome/Chromium runtime.
No persistent tokens, production queues, privileged browser flags, or deployment.
"""

import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading

from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer


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
        finally:
            store.close()
        server = ManagementHTTPServer(
            ("127.0.0.1", 0), root / "state", token_path,
            web_root=repository / "src" / "ai_company" / "web",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {**os.environ, "BASE_URL": f"http://127.0.0.1:{server.server_port}", "TEST_TOKEN": token}
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
