import csv
import html
import io
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session

from pos.db import connect, migrate as migrate_db
from pos import catalog, reports, sales, backups, gdrive
from pos import customers as customers_mod
from pos import parked as parked_mod
from pos import cash_sessions
from pos import settings as settings_mod
from pos import auth
from pos.demo import seed_demo_data
from pos.security import install_security

NOT_FOUND_ERRORS = (
    catalog.GroupNotFoundError,
    catalog.ProductNotFoundError,
    catalog.AttributeNotFoundError,
    sales.SaleNotFoundError,
    customers_mod.CustomerNotFoundError,
    parked_mod.ParkedNotFoundError,
    cash_sessions.SessionNotFoundError,
)
CONFLICT_ERRORS = (
    catalog.DuplicateSKUError,
    catalog.DuplicateBarcodeError,
    catalog.ProductHasSalesError,
    catalog.GroupNotEmptyError,
    sales.AlreadyProcessedError,
    customers_mod.DuplicatePhoneError,
    cash_sessions.SessionAlreadyOpenError,
)


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pyPOS — Settings login</title>
<link rel="stylesheet" href="/static/css/app.css">
<style>
  body {{ display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  .login-card {{ width: 320px; padding: 28px 30px; }}
  .login-brand {{ text-align: center; margin-bottom: 18px; }}
  .login-brand .brand-mark {{ display: inline-grid; place-items: center;
    width: 40px; height: 40px; border-radius: 9px; background: #2F66C4;
    color: #fff; font-weight: 800; font-size: 20px; }}
  .login-brand h1 {{ font-size: 15px; margin: 8px 0 2px; }}
  .login-brand p {{ margin: 0; color: var(--muted); font-size: 12px; }}
  .login-error {{ color: var(--danger); font-size: 12.5px; font-weight: 600;
    margin-bottom: 10px; }}
  .login-hint {{ color: var(--muted); font-size: 12.5px; }}
</style></head>
<body>
  <div class="card login-card">
    <div class="login-brand">
      <span class="brand-mark">P</span>
      <h1>pyPOS</h1>
      <p>Settings access</p>
    </div>
    {error}
    {body}
  </div>
</body></html>"""


def _login_page(error="", body=None, next="/#/settings", escaped_next=None):
    if body is None:
        body = """
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{next}">
      <div class="field"><label for="pw">Admin password</label>
        <input class="input" type="password" id="pw" name="password"
               autofocus autocomplete="current-password" required></div>
      <button class="btn primary block" type="submit">Log in</button>
    </form>""".format(
            next=html.escape(escaped_next if escaped_next is not None else next))
    return _LOGIN_PAGE.format(error=error, body=body)


def _assets_on_disk() -> bool:
    """False when running from a zipapp — templates/static live inside the
    archive and must be served through importlib.resources."""
    import pos as pkg
    return Path(pkg.__file__).parent.is_dir()


_MIME_OVERRIDES = {
    ".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml",
    ".css": "text/css", ".js": "text/javascript", ".html": "text/html",
}


def _ensure_session_secret(app):
    """Stable per-install Flask session key, persisted in settings so login
    sessions survive server restarts."""
    conn = connect(app.config.get("DB_PATH"))
    try:
        stored = settings_mod.get_all_settings(conn).get("session_secret") or ""
        if not stored:
            stored = secrets.token_hex(32)
            settings_mod.save_settings(conn, {"session_secret": stored})
    finally:
        conn.close()
    app.secret_key = stored
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def create_app(db_path=None, extra_hosts=()):
    assets_ok = _assets_on_disk()
    app = Flask(
        __name__,
        static_folder="static" if assets_ok else None,
        template_folder="templates" if assets_ok else None)
    app.config["DB_PATH"] = str(db_path) if db_path else None
    app.jinja_env.auto_reload = False
    install_security(app, extra_hosts)
    _ensure_session_secret(app)

    def _admin_password():
        conn = connect(app.config.get("DB_PATH"))
        try:
            return settings_mod.get_all_settings(conn).get("admin_password") or ""
        finally:
            conn.close()

    def _safe_next():
        # Always return an absolute-path Location so the browser can't end
        # up re-resolving a bare fragment ("#/settings") back onto /login,
        # which previously produced an endless redirect loop.
        nxt = request.values.get("next") or "/#/settings"
        if nxt.startswith("/") and not nxt.startswith("//"):
            return nxt
        if nxt.startswith("#"):
            return "/" + nxt
        return "/#/settings"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        stored = _admin_password()
        if not auth.is_configured(stored):
            return _login_page(body="""
            <p class="login-hint">No admin password is set yet, so Settings
            are currently open. Open pyPOS, go to Settings and set one to
            lock this area.</p>
            <a class="btn block" href="/">Back to pyPOS</a>""",
                next=_safe_next())

        if request.method == "POST":
            password = request.form.get("password") or ""
            if auth.verify_password(password, stored):
                auth.login_session()
                return redirect(_safe_next())
            auth.throttle_failed_login()
            return _login_page(
                error='<div class="login-error">Wrong password.</div>',
                next=_safe_next())

        if auth.is_authenticated():
            return redirect(_safe_next())
        return _login_page(next=_safe_next())

    @app.get("/logout")
    def logout():
        auth.logout_session()
        return redirect("/")

    @app.get("/api/settings/auth")
    def api_settings_auth():
        return jsonify({
            "configured": auth.is_configured(_admin_password()),
            "authenticated": auth.is_authenticated(),
        })

    @app.post("/api/settings/password")
    def api_settings_password():
        data = request.get_json(silent=True) or {}
        stored = _admin_password()
        configured = auth.is_configured(stored)
        if configured and not auth.is_authenticated():
            return jsonify({"error": "Login required",
                            "code": "auth_required"}), 401
        if configured and not auth.verify_password(
                str(data.get("current_password") or ""), stored):
            auth.throttle_failed_login()
            return jsonify({"error": "Current password is wrong"}), 400
        new = str(data.get("new_password") or "")
        if configured and new == "":
            # Removing the password: authenticated user only (checked above).
            from pos.settings import clear_setting
            conn = connect(app.config.get("DB_PATH"))
            try:
                clear_setting(conn, "admin_password")
            finally:
                conn.close()
            auth.logout_session()
            return jsonify({"ok": True, "removed": True})
        if len(new) < auth.MIN_PASSWORD_LEN:
            return jsonify({"error": "Password must be at least "
                            f"{auth.MIN_PASSWORD_LEN} characters"}), 400
        conn = connect(app.config.get("DB_PATH"))
        try:
            settings_mod.save_settings(
                conn, {"admin_password": auth.hash_password(new)})
        finally:
            conn.close()
        auth.login_session()  # setting it counts as being logged in
        return jsonify({"ok": True})

    if not assets_ok:
        # ---- zipapp mode: serve bundled UI straight from the archive ----
        from flask import abort
        from importlib import resources
        import mimetypes

        @app.get("/static/<path:filename>")
        def zipped_static(filename):
            rel = Path("static") / filename
            try:
                data = resources.files("pos").joinpath(
                    *rel.parts).read_bytes()
            except (FileNotFoundError, NotADirectoryError, ValueError):
                abort(404)
            mime = (_MIME_OVERRIDES.get(rel.suffix.lower())
                    or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream")
            return Response(data, mimetype=mime)

        @app.get("/")
        def index():
            html = resources.files("pos").joinpath(
                "templates", "index.html").read_text(encoding="utf-8")
            return Response(html, mimetype="text/html")

    def get_db():
        if "db" not in g:
            g.db = connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def fail(status, message):
        return jsonify({"error": str(message)}), status

    for cls in NOT_FOUND_ERRORS:
        app.register_error_handler(cls, lambda e: fail(404, e))
    for cls in CONFLICT_ERRORS:
        app.register_error_handler(cls, lambda e: fail(409, e))
    app.register_error_handler(catalog.CatalogError, lambda e: fail(400, e))
    app.register_error_handler(sales.SalesError, lambda e: fail(400, e))
    app.register_error_handler(customers_mod.CustomersError, lambda e: fail(400, e))
    app.register_error_handler(parked_mod.ParkedError, lambda e: fail(400, e))
    app.register_error_handler(cash_sessions.SessionError, lambda e: fail(400, e))
    app.register_error_handler(backups.BackupError, lambda e: fail(400, e))
    app.register_error_handler(gdrive.GoogleDriveError, lambda e: fail(400, e))
    app.register_error_handler(ValueError, lambda e: fail(400, f"Invalid value: {e}"))

    def _redirect_uri():
        base = request.host_url.rstrip("/")
        return f"{base}/gdrive/callback"

    # ---------------- pages ----------------
    if assets_ok:
        @app.get("/")
        def index():
            return render_template("index.html")

    # ---------------- groups ----------------
    @app.get("/api/groups")
    def api_groups():
        return jsonify(get_db() and catalog.list_groups(get_db()))

    @app.post("/api/groups")
    def api_groups_create():
        gid = catalog.create_product_group(get_db(), body().get("name"))
        return jsonify({"id": gid, "name": body().get("name", "").strip()}), 201

    @app.patch("/api/groups/<int:gid>")
    def api_groups_update(gid):
        catalog.rename_product_group(get_db(), gid, body().get("name"))
        return jsonify({"id": gid, "name": body().get("name", "").strip()})

    @app.delete("/api/groups/<int:gid>")
    def api_groups_delete(gid):
        catalog.delete_product_group(get_db(), gid)
        return jsonify({"ok": True})

    # ---------------- products ----------------
    @app.get("/api/products")
    def api_products():
        q = request.args.get("q") or None
        group_id = request.args.get("group_id", type=int)
        low = request.args.get("low_stock")
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", type=int) or 0
        threshold = None
        if low is not None:
            threshold = int(float(settings_mod.get_settings(get_db())["low_stock_threshold"] or 5))
        rows = catalog.list_products(
            get_db(), query=q, group_id=group_id,
            low_stock_threshold=threshold, limit=limit, offset=offset,
        )
        return jsonify(rows)

    @app.get("/api/lookup")
    def api_lookup():
        code = (request.args.get("code") or "").strip()
        if not code:
            return fail(400, "Missing ?code=")
        row = catalog.find_by_code(get_db(), code)
        if row is None:
            return fail(404, f"No product with SKU/barcode '{code}'")
        return jsonify(row)

    @app.get("/api/products/<int:pid>")
    def api_product_get(pid):
        row = catalog.get_product(get_db(), pid)
        if row is None:
            return fail(404, f"No product with id {pid}")
        return jsonify(row)

    @app.post("/api/products")
    def api_products_create():
        data = body()
        pid = catalog.add_product_to_group(
            get_db(),
            group_id=data.get("group_id"),
            SKU=data.get("SKU"),
            barcode=data.get("barcode"),
            price=data.get("price"),
            quantity=data.get("quantity", 0),
            cost_price=data.get("cost_price"),
            tax_rate=data.get("tax_rate", 0),
        )
        return jsonify(catalog.get_product(get_db(), pid)), 201

    @app.patch("/api/products/<int:pid>")
    def api_products_update(pid):
        fields = {}
        for key in ("SKU", "barcode", "price", "cost_price", "tax_rate", "group_id"):
            if key in body():
                fields[key] = body()[key]
        catalog.update_product(get_db(), pid, **fields)
        return jsonify(catalog.get_product(get_db(), pid))

    @app.delete("/api/products/<int:pid>")
    def api_products_delete(pid):
        catalog.delete_product(get_db(), pid)
        return jsonify({"ok": True})

    @app.post("/api/products/<int:pid>/attributes")
    def api_attr_set(pid):
        data = body()
        catalog.set_product_attribute(get_db(), pid, data.get("name"), data.get("value"))
        return jsonify(catalog.get_product(get_db(), pid))

    @app.delete("/api/products/<int:pid>/attributes/<path:name>")
    def api_attr_del(pid, name):
        catalog.remove_product_attribute(get_db(), pid, name)
        return jsonify(catalog.get_product(get_db(), pid))

    @app.post("/api/products/<int:pid>/stock")
    def api_stock(pid):
        delta = body().get("delta")
        new_qty = catalog.adjust_stock(get_db(), pid, delta)
        return jsonify({"id": pid, "quantity": new_qty})

    # ---------------- sales ----------------
    @app.post("/api/sales")
    def api_checkout():
        data = body()
        discount = data.get("discount") or {}
        receipt = sales.checkout(
            get_db(),
            items=data.get("items"),
            payments=data.get("payments"),
            discount_mode=discount.get("mode"),
            discount_value=discount.get("value"),
            customer_id=data.get("customer_id"),
            use_credit=bool(data.get("use_credit")),
        )
        return jsonify(receipt), 201

    @app.get("/api/sales")
    def api_sales_list():
        from_ts, to_ts = sales.parse_utc_bounds(
            request.args.get("from_date"), request.args.get("to_date")
        )
        rows = sales.list_sales(
            get_db(), from_ts=from_ts, to_ts=to_ts,
            status=request.args.get("status") or None,
            limit=request.args.get("limit", type=int) or 500,
            offset=request.args.get("offset", type=int) or 0,
        )
        return jsonify(rows)

    @app.get("/api/sales/<int:sid>")
    def api_sale_get(sid):
        return jsonify(sales.get_sale_detail(get_db(), sid))

    @app.post("/api/sales/<int:sid>/refund")
    def api_sale_refund(sid):
        data = body()
        lines = data.get("lines")
        to_credit = bool(data.get("to_credit"))
        return jsonify(sales.refund_sale(get_db(), sid, lines=lines,
                                         to_credit=to_credit))

    @app.post("/api/sales/<int:sid>/void")
    def api_sale_void(sid):
        return jsonify(sales.void_sale(get_db(), sid))

    # ---------------- reports ----------------
    @app.get("/api/reports/summary")
    def api_reports():
        days = request.args.get("days", default=14, type=int)
        threshold = float(settings_mod.get_settings(get_db())["low_stock_threshold"] or 5)
        return jsonify(reports.summary(get_db(), days=days, low_stock_threshold=threshold))

    # ---------------- settings ----------------
    @app.get("/api/settings")
    def api_settings_get():
        return jsonify(settings_mod.get_settings(get_db()))

    @app.put("/api/settings")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_settings_put():
        updated = settings_mod.save_settings(get_db(), body())
        return jsonify(updated)

    # ---------------- demo ----------------
    @app.post("/api/demo-data")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_demo():
        result = seed_demo_data(get_db())
        return jsonify(result), 201

    def db_path():
        return app.config.get("DB_PATH")

    # ---------------- backups ----------------
    @app.get("/api/backups/status")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backup_status():
        return jsonify(backups.scheduler_status(
            lambda: settings_mod.get_all_settings(get_db()),
            source_path=db_path()))

    @app.get("/api/backups")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backups_list():
        return jsonify(backups.list_backups(backups._backup_dir(db_path())))

    @app.post("/api/backups")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backups_create():
        result = backups.run_backup_cycle(
            settings_mod.get_all_settings(get_db()), source_path=db_path())
        return jsonify(result), 201

    @app.get("/api/backups/<path:name>/download")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backup_download(name):
        path = backups._backup_path(name, backups._backup_dir(db_path()))
        return send_file(path, as_attachment=True, download_name=name)

    @app.post("/api/backups/<path:name>/restore")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backup_restore(name):
        backups.restore_backup(name, live_path=db_path())
        migrate_db(app.config["DB_PATH"])
        return jsonify({"ok": True})

    @app.delete("/api/backups/<path:name>")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backup_delete(name):
        backups.delete_backup(name, backups._backup_dir(db_path()))
        return jsonify({"ok": True})

    @app.post("/api/backups/upload-restore")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_backup_upload():
        f = request.files.get("file")
        if f is None:
            return fail(400, "No file uploaded")
        if not f.filename.lower().endswith((".db", ".sqlite", ".sqlite3")):
            return fail(400, "Upload a .db / .sqlite file")
        backups.save_upload_and_restore(f, live_path=db_path())
        migrate_db(app.config["DB_PATH"])
        return jsonify({"ok": True})

    # ---------------- Google Drive (native OAuth) ----------------
    def _gdrive_result_page(ok, detail=""):
        color = "#067647" if ok else "#d92d20"
        icon = "✓" if ok else "✗"
        heading = "Google Drive connected" if ok else "Google Drive connection failed"
        return Response(
            f"""<!DOCTYPE html><html><head><meta charset="utf-8">
            <title>pyPOS — Google Drive</title>
            <style>body{{font-family:system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0;background:#f4f5f7}}
            .box{{text-align:center;padding:36px 44px;border:1px solid #d5d9e0;border-radius:8px;background:#fff}}
            .mark{{font-size:34px;color:{color}}} h1{{font-size:16px;margin:.4em 0}} p{{color:#49505c;font-size:13px;max-width:420px}}</style></head>
            <body><div class="box"><div class="mark">{icon}</div><h1>{heading}</h1>
            <p>{detail or 'You can close this window and return to pyPOS.'}</p>
            <script>window.opener && setTimeout(() => window.close(), 1200);</script>
            </div></body></html>""",
            mimetype="text/html")

    @app.post("/api/backups/gdrive/connect")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_gdrive_connect():
        data = body()
        client_id = (data.get("client_id") or "").strip()
        client_secret = (data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return fail(400, "Both Client ID and Client Secret are required")
        settings_mod.save_settings(get_db(), {
            "gdrive_client_id": client_id,
            "gdrive_client_secret": client_secret,
        })
        url = gdrive.build_auth_url(client_id, _redirect_uri())
        return jsonify({"auth_url": url})

    @app.get("/gdrive/callback")
    def api_gdrive_callback():
        error = request.args.get("error")
        code = request.args.get("code")
        if error:
            return _gdrive_result_page(False, f"Google returned: {error}")
        try:
            conn = get_db()
            all_settings = settings_mod.get_all_settings(conn)
            result = gdrive.exchange_code(
                code, all_settings["gdrive_client_id"],
                all_settings["gdrive_client_secret"], _redirect_uri())
            settings_mod.save_settings(conn, {
                "gdrive_refresh_token": result["refresh_token"],
            })
        except gdrive.GoogleDriveError as e:
            return _gdrive_result_page(False, str(e))
        return _gdrive_result_page(True)

    @app.post("/api/backups/gdrive/disconnect")
    @auth.require_auth(lambda: auth.is_configured(_admin_password()))
    def api_gdrive_disconnect():
        conn = get_db()
        all_settings = settings_mod.get_all_settings(conn)
        token = all_settings.get("gdrive_refresh_token")
        if token:
            gdrive.revoke_token(token)
        settings_mod.clear_setting(conn, "gdrive_refresh_token")
        return jsonify({"ok": True})

    # ---------------- customers & store credit ----------------
    @app.get("/api/customers")
    def api_customers_list():
        return jsonify(customers_mod.list_customers(
            get_db(), query=request.args.get("q") or None,
            limit=request.args.get("limit", type=int) or 500))

    @app.post("/api/customers")
    def api_customers_create():
        data = body()
        cust = customers_mod.create_customer(
            get_db(),
            name=data.get("name"),
            phone=data.get("phone"),
            email=data.get("email"),
            notes=data.get("notes"))
        return jsonify(cust), 201

    @app.get("/api/customers/<int:cid>")
    def api_customer_get(cid):
        conn = get_db()
        cust = customers_mod.get_customer(conn, cid)
        cust["credit_events"] = customers_mod.credit_events(conn, cid)
        cust["recent_sales"] = customers_mod.recent_sales(conn, cid)
        return jsonify(cust)

    @app.patch("/api/customers/<int:cid>")
    def api_customer_update(cid):
        data = body()
        return jsonify(customers_mod.update_customer(
            get_db(), cid,
            name=data.get("name"), phone=data.get("phone"),
            email=data.get("email"), notes=data.get("notes")))

    @app.delete("/api/customers/<int:cid>")
    def api_customer_delete(cid):
        customers_mod.delete_customer(get_db(), cid)
        return jsonify({"ok": True})

    @app.post("/api/customers/<int:cid>/credit")
    def api_customer_credit(cid):
        data = body()
        try:
            delta_cents = int(round(float(data.get("delta_cents", 0))))
        except (TypeError, ValueError):
            raise ValueError("delta_cents must be a number")
        balance = customers_mod.adjust_credit(
            get_db(), cid, delta_cents,
            reason=data.get("reason") or "")
        return jsonify({"id": cid, "store_credit_cents": balance,
                        "store_credit": round(balance / 100, 2)})

    # ---------------- parked orders ----------------
    @app.get("/api/parked")
    def api_parked_list():
        return jsonify(parked_mod.list_parked(get_db()))

    @app.post("/api/parked")
    def api_parked_create():
        data = body()
        discount = data.get("discount") or {}
        ticket = parked_mod.park_cart(
            get_db(),
            items=data.get("items"),
            label=data.get("label"),
            discount_mode=discount.get("mode"),
            discount_value=discount.get("value"),
            customer_id=data.get("customer_id"))
        return jsonify(ticket), 201

    @app.get("/api/parked/<int:pid>")
    def api_parked_get(pid):
        return jsonify(parked_mod.get_parked(get_db(), pid))

    @app.delete("/api/parked/<int:pid>")
    def api_parked_delete(pid):
        parked_mod.delete_parked(get_db(), pid)
        return jsonify({"ok": True})

    # ---------------- cash sessions (X / Z reports) ----------------
    @app.get("/api/cash-sessions/current")
    def api_session_current():
        conn = get_db()
        session = cash_sessions.current_session(conn)
        if session is None:
            return jsonify(None)
        return jsonify({
            **session,
            "report": cash_sessions.session_report(conn, session["id"]),
        })

    @app.get("/api/cash-sessions")
    def api_sessions_list():
        return jsonify(cash_sessions.list_sessions(get_db()))

    @app.post("/api/cash-sessions/open")
    def api_session_open():
        data = body()
        return jsonify(cash_sessions.open_session(
            get_db(), opening_float=data.get("opening_float") or 0)), 201

    @app.post("/api/cash-sessions/close")
    def api_session_close():
        data = body()
        return jsonify(cash_sessions.close_session(
            get_db(), counted_cash=data.get("counted_cash"),
            note=data.get("note")))

    @app.get("/api/cash-sessions/<int:sid>/report")
    def api_session_report(sid):
        return jsonify(cash_sessions.session_report(get_db(), sid))

    # ---------------- exports ----------------
    def _csv_response(rows, header, filename):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerows(rows)
        data = "\ufeff" + buf.getvalue()  # BOM so Excel opens UTF-8 cleanly
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"})

    @app.get("/api/export/sales.csv")
    def api_export_sales():
        from_ts, to_ts = sales.parse_utc_bounds(
            request.args.get("from_date"), request.args.get("to_date"))
        cur = get_db().cursor()
        cur.execute(
            ''' SELECT s.id, s.timestamp, s.status, s.subtotal, s.discount_total,
                       s.tax_total, s.total,
                       COALESCE((SELECT SUM(quantity) FROM sale_items si
                                 WHERE si.sale_id = s.id), 0),
                       COALESCE((SELECT GROUP_CONCAT(DISTINCT method) FROM payments p
                                 WHERE p.sale_id = s.id), '')
                FROM sales s
                WHERE (? IS NULL OR s.timestamp >= ?)
                  AND (? IS NULL OR s.timestamp <= ?)
                ORDER BY s.id DESC''',
            (from_ts, from_ts, to_ts, to_ts))
        rows = [tuple(r) for r in cur.fetchall()]
        return _csv_response(
            rows,
            ["receipt", "timestamp_utc", "status", "subtotal", "discount",
             "tax", "total", "items", "payment_methods"],
            "pypos-sales.csv")

    @app.get("/api/export/inventory.csv")
    def api_export_inventory():
        products = catalog.list_products(get_db())
        rows = []
        for p in products:
            attrs = "; ".join(f"{a['name']}={a['value']}" for a in p["attributes"])
            stock_value = round((p["cost_price"] or 0) * p["quantity"], 2)
            rows.append([p["SKU"], p["group_name"] or "", attrs, p["barcode"] or "",
                         p["quantity"], p["price"], p["cost_price"], p["tax_rate"],
                         stock_value])
        return _csv_response(
            rows,
            ["sku", "product", "variants", "barcode", "stock", "price",
             "cost", "tax_percent", "stock_value_at_cost"],
            "pypos-inventory.csv")

    return app
