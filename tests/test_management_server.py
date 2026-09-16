import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_company import password_auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer, ManagementHandler, read_token


class ManagementHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = "secret-test-token-" + "a" * 32
        token_file = self.root / "login-token"
        token_file.write_text(self.token); token_file.chmod(0o600)
        self.web = self.root / "web"; self.web.mkdir()
        (self.web / "index.html").write_text("<html>Console</html>")
        (self.web / "escape.js").symlink_to(token_file)
        self.now = 1000
        self.server = ManagementHTTPServer(("127.0.0.1", 0), self.root / "state", token_file, web_root=self.web, clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.cookie = None; self.csrf = None

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, *, authenticated=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        base = {"Origin": self.server.origin, "Host": self.server.authority}
        if authenticated and self.cookie:
            base["Cookie"] = self.cookie
            base["X-CSRF-Token"] = self.csrf
        if body is not None:
            base["Content-Type"] = "application/json"
            body = json.dumps(body)
        base.update(headers or {})
        connection.request(method, path, body=body, headers=base)
        response = connection.getresponse()
        raw = response.read(); status = response.status; returned_headers = dict(response.getheaders())
        connection.close()
        result = json.loads(raw) if returned_headers.get("Content-Type", "").startswith("application/json") else raw.decode()
        return status, result, returned_headers

    def login(self):
        status, result, headers = self.request("POST", "/api/login", {"token": self.token})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = result["csrf_token"]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])

    def test_execution_catalog_and_version_registration_are_authenticated_and_confined(self):
        from test_execution_specs import catalog_config
        from ai_company.contracts import digest
        from ai_company.execution_specs import ExecutionCatalog
        config = catalog_config()
        self.server.execution_catalog = ExecutionCatalog({'project-main': config})
        self.assertEqual(self.request('GET', '/api/execution-catalog')[0], 401)
        self.login()
        status, catalog, _ = self.request('GET', '/api/execution-catalog')
        self.assertEqual(status, 200)
        self.assertEqual(catalog['entries'][0]['catalog_digest'], digest(config))
        self.assertNotIn('source_clone', json.dumps(catalog))
        self.assertNotIn('credential_ref', json.dumps(catalog))
        self.assertEqual(self.request('POST', '/api/execution-catalog', {})[0], 405)
        project = self.request('POST', '/api/projects', {'name': '새 프로젝트', 'goal': '자연어 목표'})[1]['project']
        path = '/api/projects/' + project['id'] + '/execution-specs'
        body = {'base_version': 0, 'idempotency_key': 'http-spec-1',
                'selection': {'catalog_id': 'project-main', 'catalog_digest': digest(config)}}
        self.assertEqual(self.request('POST', path, body, headers={'X-CSRF-Token': 'wrong'})[0], 403)
        first = self.request('POST', path, body)
        self.assertEqual(first[0], 201)
        self.assertEqual(self.request('POST', path, body)[1], first[1])
        self.assertEqual(len(self.request('GET', path)[1]['execution_specs']), 1)
        poisoned = {**body, 'idempotency_key': 'http-spec-2', 'base_version': 1,
                    'selection': {**body['selection'], 'checks': {'unit': {'argv': ['sh', '-c', 'anything']}}}}
        self.assertEqual(self.request('POST', path, poisoned)[0], 400)
        self.assertEqual(self.request('POST', path, {**body, 'idempotency_key': 'http-conflict'})[0], 409)
        with_store = ManagementStore(self.root / 'state')
        try:
            self.assertEqual(with_store.pm_requests(project['id']), [])
            self.assertEqual(with_store.run_records(project['id']), [])
            self.assertEqual(with_store.db.execute('SELECT COUNT(*) FROM session_jobs').fetchone()[0], 0)
        finally:
            with_store.close()

    def test_auth_origin_csrf_and_logout(self):
        self.assertEqual(self.request("GET", "/api/session")[1], {"authenticated": False})
        self.assertEqual(self.request("GET", "/api/projects")[0], 401)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"token": self.token}, headers={"Origin": "https://evil.example"})[0], 403)
        self.login()
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"}, headers={"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"}, headers={"Origin": "https://evil.example"})[0], 403)
        status, result, _ = self.request("POST", "/api/projects", {"name": "P", "goal": "G"})
        self.assertEqual(status, 201)
        pid = result["project"]["id"]
        self.assertEqual(self.request("POST", f"/api/projects/{pid}/messages", {"content": "PM wait"})[1]["message"]["status"], "awaiting_pm")
        self.assertEqual(self.request("GET", f"/api/projects/{pid}/overview")[0], 200)
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/projects")[0], 401)

    def test_token_file_permissions_static_path_and_host_limits(self):
        path = self.root / "bad-token"; path.write_text(self.token); path.chmod(0o644)
        with self.assertRaises(ValueError):
            read_token(path)
        self.assertEqual(self.request("GET", "/")[0], 200)
        for path in ("/../login-token", "/%2e%2e/login-token", "/escape.js", "/.git/config"):
            with self.subTest(path=path):
                self.assertGreaterEqual(self.request("GET", path)[0], 400)
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "evil.example"})[0], 403)

    def test_session_expiration_content_limits_and_login_throttle(self):
        self.assertEqual(self.request("POST", "/api/login", {"token": "wrong"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"token": self.token})[0], 429)
        self.now += 3; self.login()
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "a" * 70000})[0], 413)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G", "execute": True})[0], 400)
        self.now += 8 * 3600
        self.assertEqual(self.request("GET", "/api/projects")[0], 401)

    def test_public_origin_secure_cookie_and_exact_proxy_request_boundaries(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.server = ManagementHTTPServer(("127.0.0.1", 0), self.root / "state", self.root / "login-token",
            web_root=self.web, public_origin="https://hyungwon.cloud", clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        status, result, headers = self.request("POST", "/api/login", {"token": self.token})
        self.assertEqual(status, 200)
        self.assertIn("; Secure", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]; self.csrf = result["csrf_token"]
        for headers in ({"Host": "talenta-edward.life"}, {"Host": "127.0.0.1"},
                        {"Origin": "http://hyungwon.cloud"}, {"Origin": "https://evil.example"},
                        {"Origin": "https://hyungwon.cloud.evil.example"}, {"X-CSRF-Token": "wrong"},
                        {"Sec-Fetch-Site": "cross-site"},
                        {"Host": "evil.example", "X-Forwarded-Host": "hyungwon.cloud"},
                        {"Origin": "https://evil.example", "X-Forwarded-Proto": "https"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"}, headers=headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/projects")[1]["projects"], [])
        self.assertEqual(self.request("POST", "/api/projects", {"name": "P", "goal": "G"})[0], 201)
        self.assertEqual(len(self.request("GET", "/api/projects")[1]["projects"]), 1)

    def test_public_origin_and_interface_errors_rejected_before_state_creation(self):
        for origin in ("http://hyungwon.cloud", "https://hyungwon.cloud/", "https://hyungwon.cloud?",
                       "https://hyungwon.cloud#", "https://user@hyungwon.cloud", "https://@hyungwon.cloud",
                       "https://hyungwon.cloud:65536", "https://hyungwon.cloud:0", "https://hyungwon.cloud:wrong",
                       "https://hyungwon.cloud\n", "https://hyungwon.cloud/path", "//hyungwon.cloud"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ManagementHTTPServer(("127.0.0.1", 0), self.root / "invalid-state", self.root / "login-token", public_origin=origin)
        for host, private, origin in (("0.0.0.0", True, "https://hyungwon.cloud"),
                                     ("161.118.250.112", True, "https://hyungwon.cloud"),
                                     ("169.254.1.1", True, "https://hyungwon.cloud"),
                                     ("172.30.88.1", False, "https://hyungwon.cloud"),
                                     ("172.30.88.1", True, None), ("172.30.88.1", True, "http://127.0.0.1")):
            with self.subTest(host=host, private=private, origin=origin), self.assertRaises(ValueError):
                ManagementHTTPServer((host, 0), self.root / "invalid-state", self.root / "login-token",
                                     public_origin=origin, private_bind=private)
        self.assertFalse((self.root / "invalid-state").exists())

    def test_committed_creation_lost_http_response_retries_once_after_restart_and_login(self):
        self.login()
        intent = {"name": "새 프로젝트", "goal": "자연어 목표", "start_pm": True,
                  "idempotency_key": "same-creation-intent"}
        original_json = ManagementHandler._json
        def drop_creation_response(handler, status, data, cookie=None):
            if status == 201:
                handler.close_connection = True
                return
            return original_json(handler, status, data, cookie)
        with patch.object(ManagementHandler, "_json", drop_creation_response):
            with self.assertRaises(http.client.RemoteDisconnected):
                self.request("POST", "/api/projects", intent)
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.server = ManagementHTTPServer(("127.0.0.1", 0), self.root / "state", self.root / "login-token",
            web_root=self.web, clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.cookie = self.csrf = None
        self.login()
        original = self.request("GET", "/api/projects")[1]["projects"]
        self.assertEqual(len(original), 1)
        status, result, _ = self.request("POST", "/api/projects", intent)
        self.assertEqual((status, result["project"]["id"]), (201, original[0]["id"]))
        overview = self.request("GET", f"/api/projects/{original[0]['id']}/overview")[1]
        self.assertEqual(len(overview["messages"]), 1)
        self.assertEqual(len(overview["pm_requests"]), 1)
        self.assertEqual(overview["runs"], [])
        status, result, _ = self.request("POST", "/api/projects", {**intent, "goal": "changed"})
        self.assertEqual((status, result["error"]["code"]), (409, "idempotency_conflict"))
        self.assertEqual(self.request("POST", "/api/projects", intent, headers={"X-CSRF-Token": "wrong"})[0], 403)

    def test_creation_key_is_bound_to_authenticated_username_not_cookie_or_client_body(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        store = ManagementStore(self.root / "state")
        password_auth.initialize(store.db)
        with store.db:
            for username in ("edward", "another"):
                password_auth.create_user(store.db, username, "fixture-password-10", self.now)
            # Fixture accounts have already completed the mandatory first password change.
            store.db.execute("UPDATE console_users SET must_change=0,temporary_expires=NULL")
        store.close()
        self.server = ManagementHTTPServer(("127.0.0.1", 0), self.root / "state",
            password_login=True, clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        intent = {"name": "같은 이름", "goal": "같은 목표", "start_pm": True, "idempotency_key": "same-intent-key"}
        ids = []
        for username in ("edward", "another", "edward"):
            self.cookie = self.csrf = None
            status, result, headers = self.request("POST", "/api/login", {"username": username, "password": "fixture-password-10"})
            self.assertEqual(status, 200)
            self.cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.csrf = result["csrf_token"]
            status, result, _ = self.request("POST", "/api/projects", intent)
            self.assertEqual(status, 201)
            ids.append(result["project"]["id"])
        self.assertNotEqual(ids[0], ids[1])
        self.assertEqual(ids[0], ids[2])
        self.assertEqual(self.request("POST", "/api/projects", {**intent, "principal": "password:another"})[0], 400)
        self.assertEqual(len(self.request("GET", "/api/projects")[1]["projects"]), 2)
