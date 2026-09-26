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

from flask import jsonify, request

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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


def _origin_allowed(value, allowed_hosts):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    if "*" in allowed_hosts:
        return True
    return _hostname(parsed.netloc) in allowed_hosts


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
            # 'null' = opaque origin: privacy browsers/extensions strip the
            # value, and it names no site we could distrust. The JSON-only
            # APIs (preflight kills cross-site JSON) and the Host guard
            # above already cover the real threats, so treat it as absent.
            if source and source.lower() != "null" \
                    and not _origin_allowed(source, allowed):
                return jsonify({
                    "error": "Forbidden — mutating requests from another "
                             "site are blocked (cross-site request shield).",
                    "hint": f"request Origin was: {origin!r}, "
                            f"Referer was: {referer!r}"}), 403
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
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
