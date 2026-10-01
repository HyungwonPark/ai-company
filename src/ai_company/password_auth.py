"""Local master accounts; temporary passwords never grant management access."""

import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import time

from ai_company.management import ManagementError, ManagementStore


SESSION_SECONDS = 8 * 3600
TEMPORARY_SECONDS = 24 * 3600


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS console_users(
            username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
            version INTEGER NOT NULL, must_change INTEGER NOT NULL, temporary_expires REAL);
        CREATE TABLE IF NOT EXISTS console_sessions(
            token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, version INTEGER NOT NULL,
            csrf TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS console_login_limit(
            id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER NOT NULL, retry_at REAL NOT NULL);
    """)


def password_hash(password, salt=None):
    # OWASP scrypt recommendation: N=2^17, r=8, p=1. Serialize HTTP KDFs.
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**17,
                            r=8, p=1, maxmem=192 * 1024**2, dklen=32).hex()
    return "scrypt-17-8-1$" + salt + "$" + digest


def matches(password, encoded):
    scheme, salt, _ = encoded.split("$")
    if scheme != "scrypt-17-8-1":
        raise ValueError("Unsupported password hash")
    return hmac.compare_digest(password_hash(password, salt), encoded)


def create_user(db, username, password, now):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,31}", username):
        raise ValueError("Username must be 3–32 lowercase letters, digits, underscores or hyphens")
    if db.execute("SELECT 1 FROM console_users WHERE username=?", (username,)).fetchone():
        raise ValueError("Account already exists; it will not be overwritten")
    validate_password(password)
    db.execute("INSERT INTO console_users VALUES (?,?,1,1,?)",
               (username, password_hash(password), now + TEMPORARY_SECONDS))


def issue_user(root, username, destination, *, clock=time.time):
    """Trusted CLI only. Exclusive private file, no password in argv or stdout."""
    store = ManagementStore(root)
    path = Path(destination)
    created = False
    try:
        initialize(store.db)
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            password = secrets.token_urlsafe(18)
            create_user(store.db, username, password, clock())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            with os.fdopen(fd, "w") as handle:
                handle.write(password + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    except BaseException:
        if created:
            path.unlink()
        raise
    finally:
        store.close()


def validate_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128 or not password.strip():
        raise ManagementError("weak_password", "Use a password of 10–128 characters", 400)


def session(db, hashed, now):
    row = db.execute("""SELECT s.csrf,u.username,u.version,u.must_change
        FROM console_sessions s JOIN console_users u ON s.username=u.username AND s.version=u.version
        WHERE s.token_hash=? AND s.expires_at>? AND
        (u.must_change=0 OR u.temporary_expires>?)""", (hashed, now, now)).fetchone()
    return ({"hash": hashed, "csrf_token": row[0], "username": row[1], "version": row[2],
             "password_change_required": bool(row[3])} if row else None)


def issue_session(db, username, version, now, old=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute("DELETE FROM console_sessions WHERE expires_at<=?", (now,))
    if old:
        db.execute("DELETE FROM console_sessions WHERE token_hash=?", (old["hash"],))
    db.execute("INSERT INTO console_sessions VALUES (?,?,?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), username, version, csrf, now + SESSION_SECONDS))
    return token, session(db, hashlib.sha256(token.encode()).hexdigest(), now)


def public_session(value):
    return {"authenticated": bool(value), "login_method": "password", **({
        "csrf_token": value["csrf_token"], "username": value["username"],
        "password_change_required": value["password_change_required"]} if value else {})}


def check_limit(db, now):
    row = db.execute("SELECT failures,retry_at FROM console_login_limit WHERE id=1").fetchone()
    if row and row[1] > now:
        raise ManagementError("rate_limited", "Wait before trying again", 429)
    return row[0] if row else 0


def failure(db, failures, now):
    db.execute("INSERT OR REPLACE INTO console_login_limit VALUES (1,?,?)",
               (failures + 1, now + min(300, 2**min(failures + 1, 8))))


def login(db, value, now, old=None):
    if (set(value) != {"username", "password"} or not isinstance(value["username"], str)
            or not 1 <= len(value["username"]) <= 32 or not isinstance(value["password"], str)
            or not 1 <= len(value["password"]) <= 128):
        raise ManagementError("invalid_login", "Username and password are required", 400)
    with db:
        db.execute("BEGIN IMMEDIATE")
        failures = check_limit(db, now)
        user = db.execute("SELECT password_hash,version,must_change,temporary_expires FROM console_users WHERE username=?",
                          (value["username"],)).fetchone()
        # Do the same expensive work for unknown accounts, without exposing their existence.
        encoded = user[0] if user else "scrypt-17-8-1$" + "00" * 16 + "$" + "00" * 32
        valid = matches(value["password"], encoded)
        if not user or not valid or (user[2] and user[3] <= now):
            failure(db, failures, now)
            result = None
        else:
            db.execute("DELETE FROM console_login_limit")
            result = issue_session(db, value["username"], user[1], now, old)
    if result is None:
        raise ManagementError("unauthorized", "Invalid or expired credentials", 401)
    return result


def change_password(db, value, current, now):
    if (set(value) != {"current_password", "new_password"} or not isinstance(value["current_password"], str)
            or not 1 <= len(value["current_password"]) <= 128):
        raise ManagementError("invalid_password_change", "Current and new passwords are required", 400)
    validate_password(value["new_password"])
    with db:
        db.execute("BEGIN IMMEDIATE")
        if not session(db, current["hash"], now):
            raise ManagementError("unauthorized", "Login required", 401)
        failures = check_limit(db, now)
        user = db.execute("SELECT password_hash,version FROM console_users WHERE username=?",
                          (current["username"],)).fetchone()
        if not matches(value["current_password"], user[0]):
            failure(db, failures, now)
            result = None
        else:
            if hmac.compare_digest(value["current_password"].encode(), value["new_password"].encode()):
                raise ManagementError("password_reused", "Choose a different password", 400)
            db.execute("UPDATE console_users SET password_hash=?,version=version+1,must_change=0,temporary_expires=NULL WHERE username=?",
                       (password_hash(value["new_password"]), current["username"]))
            db.execute("DELETE FROM console_sessions WHERE username=?", (current["username"],))
            db.execute("DELETE FROM console_login_limit")
            result = issue_session(db, current["username"], user[1] + 1, now)
    if result is None:
        raise ManagementError("incorrect_password", "Current password is incorrect", 400)
    return result
