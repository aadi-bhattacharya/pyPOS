"""Admin-password support for the Settings area.

Stupid simple on purpose: one shared password, PBKDF2-HMAC-SHA256 hashed
(never stored in plaintext), Flask session cookie for the web UI. The CLI
bypasses all of this — it is already on the machine.
"""
import hashlib
import hmac
import secrets
import time
from functools import wraps

from flask import jsonify, session

PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LEN = 4
_FAILED_SLEEP = 0.6  # seconds; slows online guessing without any lockout state


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification; tolerant of malformed stored values."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def is_configured(stored: str) -> bool:
    return bool(stored and stored.startswith("pbkdf2_sha256$"))


def login_session():
    session["settings_auth"] = True


def logout_session():
    session.clear()


def is_authenticated() -> bool:
    return session.get("settings_auth") is True


def require_auth(configured_fn=None):
    """API guard: 401 JSON when the Settings area is locked.

    When `configured_fn` is given and returns False (no admin password set
    yet), the endpoint stays open — first-run bootstrap mode.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if configured_fn is not None and not configured_fn():
                return fn(*args, **kwargs)
            if not is_authenticated():
                time.sleep(_FAILED_SLEEP)  # tiny brake; see module docstring
                return jsonify({"error": "Login required",
                                "code": "auth_required"}), 401
            return fn(*args, **kwargs)
        return wrapper
    return deco


def throttle_failed_login():
    time.sleep(_FAILED_SLEEP)
