import hashlib
from pathlib import Path
import secrets
import tempfile
import threading
import unittest

from ai_company import password_auth as auth
from ai_company.management import ManagementStore
from ai_company.management_server import ManagementHTTPServer
import test_management_server as management_tests


class PasswordHTTPTests(unittest.TestCase):
    request = management_tests.ManagementHTTPTests.request
    tearDown = management_tests.ManagementHTTPTests.tearDown

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000
        self.password = "temporary-fixture-password"
        self.new_password = "new-pass10"
        store = ManagementStore(self.root / "state")
        auth.initialize(store.db)
        with store.db:
            auth.create_user(store.db, "edward", self.password, self.now)
        store.close()
        self.cookie = self.csrf = None
        self.start()

    def start(self):
        self.server = ManagementHTTPServer(("127.0.0.1", 0), self.root / "state",
            password_login=True, public_origin="https://hyungwon.cloud", clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def restart(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.start()

    def login(self, password=None):
        status, result, headers = self.request("POST", "/api/login", {
            "username": "edward", "password": password or self.password})
        self.assertEqual(status, 200, result)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = result["csrf_token"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(flag, headers["Set-Cookie"])
        return result

    def change(self, **headers):
        return self.request("POST", "/api/password", {
            "current_password": self.password, "new_password": self.new_password}, headers=headers)

    def test_temporary_session_cannot_read_or_confirm_and_survives_restart(self):
        self.assertEqual(self.request("GET", "/api/session")[1], {"authenticated": False, "login_method": "password"})
        self.assertTrue(self.login()["password_change_required"])
        for method, path, body in (("GET", "/api/projects", None),
                ("POST", "/api/projects", {"name": "P", "goal": "G"}),
                ("POST", "/api/projects/" + "a"*32 + "/plans/" + "b"*32 + "/confirm", {})):
            status, result, _ = self.request(method, path, body)
            self.assertEqual((status, result["error"]["code"]), (403, "password_change_required"))
        self.restart()
        self.assertTrue(self.request("GET", "/api/session")[1]["password_change_required"])
        self.assertEqual(self.request("GET", "/api/projects")[0], 403)
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/projects")[0], 401)

    def test_change_rotates_all_sessions_and_persists_password(self):
        self.login(); first_cookie, first_csrf = self.cookie, self.csrf
        self.cookie = self.csrf = None
        self.login(); second_cookie = self.cookie
        status, result, headers = self.change()
        self.assertEqual(status, 200, result)
        self.assertFalse(result["password_change_required"])
        new_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotIn(new_cookie, (first_cookie, second_cookie))
        self.assertNotEqual(result["csrf_token"], self.csrf)
        for cookie in (first_cookie, second_cookie):
            self.assertEqual(self.request("GET", "/api/projects", headers={"Cookie": cookie})[0], 401)
        self.assertEqual(self.change(Cookie=first_cookie, **{"X-CSRF-Token": first_csrf})[0], 401)
        self.cookie, self.csrf = new_cookie, result["csrf_token"]
        self.restart()
        self.assertEqual(self.request("GET", "/api/projects")[0], 200)
        self.now += auth.TEMPORARY_SECONDS + 1
        self.assertFalse(self.login(self.new_password)["password_change_required"])
        self.assertEqual(self.request("POST", "/api/login", {"username": "edward", "password": self.password})[0], 401)
        store = ManagementStore(self.root / "state")
        try:
            user = store.db.execute("SELECT * FROM console_users").fetchone()
            self.assertNotIn(self.password, str(tuple(user)))
            self.assertNotIn(self.new_password, str(tuple(user)))
            self.assertEqual(tuple(user)[2:], (2, 0, None))
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM management_runs").fetchone()[0], 0)
        finally:
            store.close()

    def test_change_requires_origin_csrf_current_password_and_new_value(self):
        self.login()
        for headers in ({"Origin": "https://evil.example"}, {"Host": "talenta-edward.life"},
                        {"X-CSRF-Token": "wrong"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.change(**headers)[0], 403)
        for new, code in (("123456789", "weak_password"), (self.password, "password_reused")):
            response = self.request("POST", "/api/password", {"current_password": self.password, "new_password": new})
            self.assertEqual(response[1]["error"]["code"], code)
        response = self.request("POST", "/api/password", {"current_password": "wrong", "new_password": self.new_password})
        self.assertEqual(response[1]["error"]["code"], "incorrect_password")
        self.assertEqual(self.change()[0], 429)
        self.now += 3
        self.assertEqual(self.change()[0], 200)

    def test_expiry_and_persistent_throttle_do_not_reveal_accounts(self):
        bad = {"username": "missing", "password": "wrong"}
        unknown = self.request("POST", "/api/login", bad)
        self.assertEqual(unknown[0], 401)
        self.restart()
        self.assertEqual(self.request("POST", "/api/login", bad)[0], 429)
        self.now += 3
        known = self.request("POST", "/api/login", {**bad, "username": "edward"})
        self.assertEqual(unknown[:2], known[:2])
        self.now += 5
        self.login()
        self.now += auth.TEMPORARY_SECONDS
        self.assertFalse(self.request("GET", "/api/session")[1]["authenticated"])
        self.assertEqual(self.request("POST", "/api/login", {"username": "edward", "password": self.password})[0], 401)

    def test_legacy_tokens_and_cookies_cannot_bypass_password_auth(self):
        token = secrets.token_urlsafe(32)
        store = ManagementStore(self.root / "state")
        with store.db:
            store.db.execute("INSERT INTO management_auth VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), "csrf", self.now+500))
        store.close()
        self.cookie, self.csrf = "ai_company_session=" + token, "csrf"
        self.assertEqual(self.request("GET", "/api/projects")[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"token": token})[0], 400)
        token_file = self.root / "token"
        token_file.write_text(token); token_file.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "token login is disabled"):
            ManagementHTTPServer(("127.0.0.1", 0), self.root / "state", token_file)

    def test_account_issue_never_overwrites_account_or_file(self):
        destination = self.root / "temporary-password"
        with self.assertRaisesRegex(ValueError, "already exists"):
            auth.issue_user(self.root / "state", "edward", destination)
        self.assertFalse(destination.exists())
        destination.write_text("preserve")
        with self.assertRaises(FileExistsError):
            auth.issue_user(self.root / "state", "another", destination)
        self.assertEqual(destination.read_text(), "preserve")
        store = ManagementStore(self.root / "state")
        self.assertIsNone(store.db.execute("SELECT 1 FROM console_users WHERE username='another'").fetchone())
        store.close()
        destination.unlink()
        auth.issue_user(self.root / "state", "another", destination)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(destination.read_text().strip()), 24)

    def test_parallel_kdf_is_rejected_without_changing_credentials(self):
        with self.server.auth_lock:
            self.assertEqual(self.request("POST", "/api/login", {"username": "edward", "password": self.password})[0], 429)
        self.assertTrue(self.login()["password_change_required"])
