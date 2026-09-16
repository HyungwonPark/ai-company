"""Origin-bound HTTP adapter on loopback or an explicit private proxy interface."""

import hashlib
import hmac
import ipaddress
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from ai_company.management import ManagementError, ManagementStore
from ai_company import password_auth


MAX_BODY = 65536
SESSION_SECONDS = 8 * 3600


def read_token(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Login token file must be owned by this user with mode 0600")
        token = handle.read(4097).strip()
    if not 32 <= len(token) <= 4096 or any(c.isspace() for c in token):
        raise ValueError("Login token must be 32–4096 non-whitespace characters")
    return token


class ManagementHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root, token_file=None, *, password_login=False, web_root=None, public_origin=None, private_bind=False, clock=time.time):
        if bool(token_file) == bool(password_login):
            raise ValueError("Choose exactly one of token_file or password_login")
        parsed = None
        if public_origin is not None:
            if any(c.isspace() or ord(c) < 32 for c in public_origin) or any(c in public_origin for c in "?#"):
                raise ValueError("public_origin must be an exact HTTP(S) origin")
            parsed = urlsplit(public_origin)
            if (parsed.scheme not in ("http", "https") or parsed.path or parsed.username is not None
                    or parsed.password is not None or not parsed.hostname):
                raise ValueError("public_origin must be an exact HTTP(S) origin without credentials or a path")
            if parsed.port == 0:
                raise ValueError("public_origin port must be positive")
            if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
                raise ValueError("Public origin requires HTTPS")
        if private_bind and (parsed is None or parsed.scheme != "https"):
            raise ValueError("Private interface binding requires an explicit HTTPS public_origin")
        if address[0] not in ("127.0.0.1", "localhost"):
            try:
                interface = ipaddress.IPv4Address(address[0])
            except ipaddress.AddressValueError as exc:
                raise ValueError("Management server requires a literal private IPv4 interface") from exc
            networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            if not private_bind or not any(interface in ipaddress.IPv4Network(n) for n in networks):
                raise ValueError("Management server requires loopback or an explicitly permitted RFC1918 interface")
        self.root = Path(root).resolve()
        self.password_login = password_login
        self.login_token = read_token(token_file) if token_file else None
        self.web_root = Path(web_root).resolve() if web_root else None
        self.clock = clock
        self.login_failures = {}
        self.auth_lock = threading.Lock()
        with_store = ManagementStore(self.root, clock=clock)
        try:
            password_auth.initialize(with_store.db)
            users = with_store.db.execute("SELECT COUNT(*) FROM console_users").fetchone()[0]
            if password_login and not users:
                raise ValueError("Create a master account with manage create-user before serving")
            if not password_login and users:
                raise ValueError("Password accounts exist; token login is disabled for this state")
        finally:
            with_store.close()
        super().__init__(address, ManagementHandler)
        self.origin = public_origin or "http://" + address[0] + ":" + str(self.server_port)
        parsed = parsed or urlsplit(self.origin)
        self.authority = parsed.netloc
        self.secure_cookie = parsed.scheme == "https"


class ManagementHandler(BaseHTTPRequestHandler):
    server_version = "AICompanyControl/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # Paths, query strings and login payloads must not enter shared logs.
        pass

    def _json(self, status, data, cookie=None):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self._headers("application/json; charset=utf-8", len(body))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _headers(self, content_type, length):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")

    def _cookie(self, value, max_age=SESSION_SECONDS):
        return "ai_company_session=" + value + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + str(max_age) + ("; Secure" if self.server.secure_cookie else "")

    def _session(self, store):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            token = jar["ai_company_session"].value
        except (KeyError, cookies.CookieError):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        if self.server.password_login:
            return password_auth.session(store.db, hashed, self.server.clock())
        row = store.db.execute("SELECT csrf,expires_at FROM management_auth WHERE token_hash=?", (hashed,)).fetchone()
        if row and row[1] > self.server.clock():
            return {"hash": hashed, "csrf_token": row[0]}
        return None

    def _body(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            raise ManagementError("invalid_content", "Use application/json with Content-Length", 415)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            raise ManagementError("invalid_length", "One valid Content-Length is required", 400)
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY:
            raise ManagementError("body_too_large", "JSON body must be 1–65536 bytes", 413)
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete body")
            value = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
            if not isinstance(value, dict):
                raise ValueError("Object required")
            return value
        except (ValueError, UnicodeError) as exc:
            raise ManagementError("invalid_json", "A valid JSON object is required", 400) from exc

    def _login(self, store, value):
        if self.server.password_login:
            self._password_action(store, value)
            return
        if set(value) != {"token"} or not isinstance(value["token"], str) or len(value["token"]) > 4096:
            raise ManagementError("invalid_login", "Token is required", 400)
        identity = self.client_address[0]
        with self.server.auth_lock:
            failures, until = self.server.login_failures.get(identity, (0, 0))
            if until > self.server.clock():
                raise ManagementError("rate_limited", "Wait before trying login again", 429)
            if not hmac.compare_digest(value["token"].encode(), self.server.login_token.encode()):
                failures += 1
                self.server.login_failures[identity] = (failures, self.server.clock() + min(300, 2 ** min(failures, 8)))
                raise ManagementError("unauthorized", "Invalid login token", 401)
            self.server.login_failures.pop(identity, None)
        session = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        old = self._session(store)
        with store.db:
            store.db.execute("DELETE FROM management_auth WHERE expires_at<=?", (self.server.clock(),))
            if old:
                store.db.execute("DELETE FROM management_auth WHERE token_hash=?", (old["hash"],))
            store.db.execute("INSERT INTO management_auth VALUES (?,?,?)", (hashlib.sha256(session.encode()).hexdigest(), csrf, self.server.clock() + SESSION_SECONDS))
        self._json(200, {"authenticated": True, "csrf_token": csrf}, self._cookie(session))

    def _password_action(self, store, value, current=None):
        # Bound memory/CPU use: only one scrypt operation can be in flight per server.
        if not self.server.auth_lock.acquire(blocking=False):
            raise ManagementError("rate_limited", "Another login is being processed; try again", 429)
        try:
            if current:
                token, result = password_auth.change_password(store.db, value, current, self.server.clock())
            else:
                token, result = password_auth.login(store.db, value, self.server.clock(), self._session(store))
        finally:
            self.server.auth_lock.release()
        self._json(200, password_auth.public_session(result), self._cookie(token))

    def _static(self, path):
        if not self.server.web_root:
            raise ManagementError("not_found", "Static console is not configured", 404)
        relative = unquote(path).lstrip("/") or "index.html"
        if any(part in ("", ".", "..") or part.startswith(".") for part in relative.split("/")) or "\\" in relative or "\x00" in relative:
            raise ManagementError("invalid_path", "Invalid asset path", 400)
        target = (self.server.web_root / relative).resolve()
        if not target.is_relative_to(self.server.web_root) or not target.is_file() or target.suffix not in (".html", ".js", ".css", ".svg", ".png", ".ico", ".webmanifest", ".woff2"):
            raise ManagementError("not_found", "Asset not found", 404)
        if target.stat().st_size > 4 * 1024 * 1024:
            raise ManagementError("asset_too_large", "Asset too large", 413)
        body = target.read_bytes()
        self.send_response(200)
        self._headers(mimetypes.guess_type(target.name)[0] or "application/octet-stream", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def _dispatch(self, write):
        store = None
        try:
            if self.headers.get_all("Host", []) != [self.server.authority]:
                raise ManagementError("invalid_host", "Host does not match the configured origin", 403)
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or "%" in parsed.path or "\\" in parsed.path or ".." in parsed.path:
                raise ManagementError("invalid_path", "Invalid request path", 400)
            path = parsed.path
            if write and (self.headers.get_all("Origin", []) != [self.server.origin] or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                raise ManagementError("invalid_origin", "Same-origin writes are required", 403)
            if not path.startswith("/api/") and not write:
                self._static(path)
                return
            store = ManagementStore(self.server.root, clock=self.server.clock)
            session = self._session(store)
            if path == "/api/session" and not write:
                result = (password_auth.public_session(session) if self.server.password_login else
                          {"authenticated": bool(session), **({"csrf_token": session["csrf_token"]} if session else {})})
                self._json(200, result)
                return
            if path == "/api/login" and write:
                self._login(store, self._body())
                return
            if not session:
                raise ManagementError("unauthorized", "Login required", 401)
            if write and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf_token"]):
                raise ManagementError("invalid_csrf", "Valid session CSRF token required", 403)
            value = self._body() if write else None
            if path == "/api/logout" and write:
                if value:
                    raise ManagementError("invalid_body", "Logout body must be empty", 400)
                with store.db:
                    table = "console_sessions" if self.server.password_login else "management_auth"
                    store.db.execute(f"DELETE FROM {table} WHERE token_hash=?", (session["hash"],))
                self._json(200, {"authenticated": False}, self._cookie("", 0))
                return
            if path == "/api/password" and write and self.server.password_login:
                self._password_action(store, value, session)
                return
            if session.get("password_change_required"):
                raise ManagementError("password_change_required", "Change the temporary password before using the workspace", 403)
            if path == "/api/projects":
                principal = "password:" + session["username"] if self.server.password_login else "legacy-token-master"
                result = ({"project": store.create_project(value, principal=principal)} if write else
                          {"projects": store.list_projects(summary=True)})
                self._json(201 if write else 200, result)
                return
            confirmation = re.fullmatch(r"/api/projects/([0-9a-f]{32})/plans/([0-9a-f]{32})/confirm", path)
            if confirmation:
                if not write:
                    raise ManagementError("method_not_allowed", "Method not allowed", 405)
                self._json(200, store.confirm_plan(confirmation[1], confirmation[2], value))
                return
            match = re.fullmatch(r"/api/projects/([0-9a-f]{32})/(overview|messages|harness|events|approvals/([0-9a-f]{32})/decisions)", path)
            if not match:
                raise ManagementError("not_found", "Endpoint not found", 404)
            project_id, endpoint, approval_id = match.groups()
            if endpoint == "overview" and not write:
                result = store.overview(project_id)
            elif endpoint == "events" and not write:
                query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
                after = query.get("after", ["0"])
                if set(query) - {"after"} or len(after) != 1 or not re.fullmatch(r"\d{1,18}", after[0]):
                    raise ManagementError("invalid_cursor", "Cursor must be a nonnegative integer", 400)
                result = store.events(project_id, int(after[0]))
            elif endpoint == "messages" and write:
                result = {"message": store.post_message(project_id, value)}
            elif endpoint == "harness" and write:
                result = {"harness": store.save_harness(project_id, value)}
            elif approval_id and write:
                result = {"approval": store.decide(project_id, approval_id, value)}
            else:
                raise ManagementError("method_not_allowed", "Method not allowed", 405)
            self._json(200, result)
        except ManagementError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except (ValidationError, ValueError):
            self._json(400, {"error": {"code": "invalid_input", "message": "Input does not match the API contract"}})
        except Exception:
            self._json(500, {"error": {"code": "internal_error", "message": "Request failed; state remains on the server"}})
        finally:
            if store:
                store.close()


def serve(root, token_file=None, *, password_login=False, host="127.0.0.1", port=8765, web_root=None, public_origin=None, private_bind=False):
    server = ManagementHTTPServer((host, port), root, token_file, password_login=password_login, web_root=web_root or Path(__file__).parent / "web", public_origin=public_origin, private_bind=private_bind)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
