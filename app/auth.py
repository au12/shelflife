"""Optional login: a form-based sign-in backed by a signed session cookie.

Turned on by setting AUTH_USER and AUTH_PASS. Sessions are signed with a random key kept in /config,
mixed with the configured credentials so changing the password signs everyone out.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from . import config

COOKIE = "shelflife_session"
SHORT_SESSION = 12 * 3600          # without "keep me signed in"
LONG_SESSION = 30 * 86400          # with it
MAX_FAILURES = 5
LOCKOUT = 300                      # seconds

_key: bytes | None = None
_failures: dict[str, list[float]] = {}


def enabled() -> bool:
    return bool(config.AUTH_USER and config.AUTH_PASS)


def _signing_key() -> bytes:
    global _key
    if _key is None:
        path = os.path.join(config.CONFIG_DIR, "session.key")
        try:
            with open(path, "rb") as f:
                secret = f.read()
        except FileNotFoundError:
            secret = b""
        if len(secret) < 32:
            secret = secrets.token_bytes(32)
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(secret)
            os.chmod(path, 0o600)
        cred = f"{config.AUTH_USER}\0{config.AUTH_PASS}".encode()
        _key = hmac.new(secret, cred, hashlib.sha256).digest()
    return _key


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(remember: bool) -> tuple[str, int]:
    ttl = LONG_SESSION if remember else SHORT_SESSION
    payload = _b64(json.dumps({"u": config.AUTH_USER, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}", ttl


def valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return False
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return False
    return data.get("u") == config.AUTH_USER and int(data.get("exp", 0)) > time.time()


def check_password(user: str, password: str) -> bool:
    # compare_digest on both so timing doesn't reveal which part was wrong
    u = hmac.compare_digest((user or "").encode(), config.AUTH_USER.encode())
    p = hmac.compare_digest((password or "").encode(), config.AUTH_PASS.encode())
    return u and p


def check_basic(header: str) -> bool:
    """Basic auth still works for scripts and curl."""
    if not header.lower().startswith("basic "):
        return False
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except ValueError:
        return False
    return check_password(user, pw)


def locked_for(ip: str) -> int:
    """Seconds until this address may try again (0 = not locked)."""
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < LOCKOUT]
    _failures[ip] = recent
    if len(recent) >= MAX_FAILURES:
        return int(LOCKOUT - (now - recent[0])) + 1
    return 0


def record_failure(ip: str):
    _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str):
    _failures.pop(ip, None)
