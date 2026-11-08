"""Request-level isolation for pyPOS.

The register binds to 127.0.0.1 and treats everything beyond the local
machine as hostile. This module enforces that boundary:

1. Host allow-list   — blocks DNS-rebinding attacks (a malicious website
   resolving an evil domain to 127.0.0.1 cannot reach the API because the
   Host header won't match).
2. Origin checks     — mutating requests (POST/PATCH/PUT/DELETE) coming from
   another website ("drive-by" CSRF) carry an Origin/Referer we don't trust;
   they are refused. Plain tools (curl/scripts) send no Origin and keep working.
3. Security headers  — strict CSP (no external code can ever load), nosniff,
   framing denied, referrers stripped, API responses never cached.
"""

from urllib.parse import urlparse
import hmac
import secrets

from flask import jsonify, request, session

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def issue_csrf_token():
    """Generate a per-session token delivered only by our own served pages.

    It gives legitimate Windows/browser clients a way to authenticate requests
    where their browser emits the opaque ``Origin: null``. An opaque origin on
    its own is never enough to authorize a write.
    """
    token = session.get("pypos_csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["pypos_csrf_token"] = token
    return token


def _valid_csrf_token():
    presented = (request.headers.get("X-pyPOS-CSRF") or
                 request.form.get("_csrf") or "")
    expected = session.get("pypos_csrf_token")
    return (isinstance(presented, str) and isinstance(expected, str)
            and bool(expected) and hmac.compare_digest(expected, presented))


def _hostname(netloc):
    """'127.0.0.1:8420' -> '127.0.0.1'; '[::1]:8420' -> '::1'."""
    host = (netloc or "").split("/")[0]
    if host.startswith("["):
        return host.split("]")[0].lstrip("[")
    return host.split(":")[0]


def _is_allowed_host(host_header, extra_hosts=()):
    if not host_header:
        return False
    if "*" in extra_hosts:
        return True  # e.g. --lan: clients may address the machine however they like
    return _hostname(host_header) in LOOPBACK_HOSTS or \
        _hostname(host_header) in set(extra_hosts)


def _origin_allowed(value, allowed_hosts=None, target_origin=None):
    """Allow only the exact origin serving this POS, including its TCP port.

    A foreign localhost port and an opaque ``Origin: null`` are *not* trusted.
    LAN access is permitted by Host policy, but cross-origin mutations remain
    disallowed even when ``--lan`` is enabled.
    """
    try:
        parsed = urlparse(value)
        current = urlparse(target_origin or "")
    except ValueError:
        return False
    if (parsed.scheme not in ("http", "https") or not parsed.netloc or
            parsed.username is not None or parsed.password is not None):
        return False
    if parsed.scheme != current.scheme:
        return False
    if parsed.netloc.lower() == current.netloc.lower():
        return True
    # Windows shortcuts may open localhost while the desktop server resolves
    # 127.0.0.1 (or vice versa). Only alias the same TCP port on loopback.
    try:
        return (parsed.hostname in ("localhost", "127.0.0.1", "::1") and
                current.hostname in ("localhost", "127.0.0.1", "::1") and
                parsed.port == current.port)
    except ValueError:
        return False
    return False


def install_security(app, extra_hosts=()):
    """Attach the isolation middleware. extra_hosts: hosts allowed when the
    operator explicitly opts into LAN serving (--lan). The sentinel "*"
    allows any Host/Origin (the operator has already accepted LAN exposure)."""
    allowed = LOOPBACK_HOSTS | {h.lower() for h in extra_hosts}

    @app.before_request
    def _guard():
        host = request.headers.get("Host", "")
        if not _is_allowed_host(host, extra_hosts):
            # Wrong Host => DNS rebinding attempt or foreign access.
            return jsonify({
                "error": "Forbidden — this pyPOS server only accepts "
                         "loopback addresses. Open http://127.0.0.1:8420 "
                         "locally, or start the server with --lan to serve "
                         "other devices.",
                "hint": f"request Host was: {host!r}"}), 403

        if request.method in MUTATING_METHODS:
            origin = request.headers.get("Origin") or ""
            referer = request.headers.get("Referer") or ""
            source = origin or referer
            # Browsers sometimes send an opaque Origin on Windows. Only our
            # served UI can read the unpredictable, session-bound CSRF token.
            # Do not trust Origin:null by itself: file: and sandboxed pages
            # generate it too. Foreign explicit origins are *always* denied.
            opaque = origin.strip().lower() == "null"
            trusted = (_valid_csrf_token() if opaque else
                       not source or _origin_allowed(source, allowed, request.host_url))
            if not trusted:
                return jsonify({
                    "error": "Forbidden — mutating requests from another "
                             "site are blocked (cross-site request shield).",
                    "hint": (f"request Origin was: {origin!r}, Referer was: {referer!r}. "
                             "Open the POS using its http://127.0.0.1 address "
                             "and reload. Opaque-origin requests require a "
                             "valid token from the served pyPOS page.")}), 403
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' "
            "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        if request.path.startswith("/api/") or request.path in (
                "/", "/login", "/staff-login", "/staff-setup"):
            # These pages contain per-session tokens; never cache them.
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
