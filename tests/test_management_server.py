import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from ai_company.management_server import ManagementHTTPServer, read_token


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
