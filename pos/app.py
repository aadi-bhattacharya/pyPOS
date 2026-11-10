import csv
import html
import io
import secrets
import sqlite3
from datetime import datetime
from urllib.parse import urlencode
from pathlib import Path

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session

from pos.db import connect, init_db
from pos import catalog, reports, sales, exchanges, backups, gdrive
from pos import customers as customers_mod
from pos import parked as parked_mod
from pos import cash_sessions
from pos import settings as settings_mod
from pos import auth
from pos import loyalty
from pos import catalog_import
from pos import operations
from pos import staff
from pos import stock_policy
from pos import thermal_print
from pos.demo import seed_demo_data
from pos.security import install_security, issue_csrf_token
from pos.auth_pages import _login_page, _staff_setup_form, _staff_login_form

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


# Login markup and form builders live in pos.auth_pages.


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
    app.config["STAFF_BOOT_NONCE"] = secrets.token_urlsafe(24)

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

    def _login_branding():
        conn = connect(app.config.get("DB_PATH"))
        try:
            return settings_mod.get_settings(conn)
        finally:
            conn.close()

    # On first launch staff enrollment is mandatory. Existing catalogue and
    # financial records remain intact; no default PIN is created.
    @app.before_request
    def _staff_guard():
        if request.path.startswith("/static/"):
            return None
        conn = connect(app.config.get("DB_PATH"))
        try:
            enabled = staff.configured(conn)
            g.staff_enabled = enabled
            if not enabled:
                # An unconfigured installation must enroll an administrator
                # before opening a live till. Static assets and enrollment
                # stay accessible, business APIs do not.
                if request.path == "/":
                    return redirect("/staff-setup")
                if request.path.startswith("/api/") and request.path not in (
                        "/api/staff/status", "/api/settings/auth"):
                    return jsonify({"error":"Set up the first sales login", "code":"staff_setup_required"}), 403
                return None
            actor = staff.get_user(conn, session.get("staff_user_id")) if session.get("staff_user_id") else None
            g.staff_actor = actor if (actor and actor["active"] and
                session.get("staff_boot_nonce") == app.config["STAFF_BOOT_NONCE"]) else None
        finally:
            conn.close()
        if request.path == "/":
            return None if g.staff_actor else redirect("/staff-login")
        if not request.path.startswith("/api/"):
            return None
        if request.path in ("/api/staff/status", "/api/settings/auth"):
            return None
        if not g.staff_actor:
            return jsonify({"error":"Staff sign-in required", "code":"staff_login_required"}),401
        # All owner/security endpoints stay owner-only. A staff capability
        # toggle cannot grant Settings auth, backups, staff editing or demo reset.
        endpoint = request.endpoint or ""
        owner_only = (endpoint.startswith(("api_staff_", "api_backup_", "api_backups_", "api_gdrive_"))
                      or endpoint in ("api_settings_put", "api_settings_password", "api_demo",
                                      "api_thermal_print_test", "api_thermal_print_info"))
        if owner_only and g.staff_actor["role"] != "admin":
            return jsonify({"error": "Owner/administrator access required",
                            "code": "staff_forbidden"}), 403

        # Read gates protect financial exports and reports even when someone
        # calls the HTTP API directly instead of clicking a hidden control.
        if request.method in ("GET", "HEAD"):
            read_permission = None
            if endpoint.startswith("api_export_"):
                read_permission = "export_data"
            elif endpoint == "api_reports":
                read_permission = "view_reports"
            elif endpoint in ("api_sessions_list", "api_session_report"):
                read_permission = "view_reports"
            elif endpoint in ("api_purchase_detail", "api_purchases", "api_suppliers"):
                read_permission = "manage_purchases"
            elif endpoint in ("api_stocktakes", "api_stocktake_detail"):
                read_permission = "stocktakes"
            if read_permission and not staff.can(g.staff_actor, read_permission):
                return jsonify({"error": "Permission required: " + read_permission,
                                "code": "staff_forbidden"}), 403
            return None

        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        # Each mutating endpoint is mapped explicitly; any newly added API
        # starts denied for non-admins until it is reviewed and mapped.
        grants = {
            "api_groups_create": "add_products", "api_brands_create": "add_products",
            "api_categories_create": "add_products", "api_products_create": "add_products",
            "api_groups_update": "edit_products", "api_group_classification": "edit_products",
            "api_products_update": "edit_products", "api_attr_set": "edit_products",
            "api_attr_del": "edit_products",
            "api_groups_delete": "delete_products", "api_products_delete": "delete_products",
            "api_supplier_create": "manage_purchases", "api_purchases_receive": "manage_purchases",
            "api_supplier_payment": "manage_purchases", "api_stocktake_start": "stocktakes",
            "api_stocktake_count": "stocktakes", "api_stocktake_post": "stocktakes",
            "api_checkout": "checkout", "api_parked_create": "checkout",
            "api_thermal_print_sale": "checkout",
            "api_thermal_print_report": "view_reports",
            "api_parked_delete": "checkout", "api_sale_refund_preview": "refund_sales",
            "api_sale_refund": "refund_sales", "api_sale_void": "void_sales",
            "api_sale_exchange_preview": "refund_sales",
            "api_sale_exchange": "refund_sales",
            "api_customers_create": "manage_customers", "api_customer_update": "manage_customers",
            "api_customer_delete": "delete_customers", "api_customer_credit": "manage_credit",
            "api_loyalty_adjust": "manage_loyalty", "api_loyalty_redeem": "manage_loyalty",
            "api_session_open": "open_day", "api_session_close": "close_day",
            "api_cash_movement": "cash_movements",
            "api_catalog_import_preview": "import_stock",
            "api_catalog_import_apply": "import_stock",
        }
        # An exchange is both a return and a new sale. Refund permission alone
        # must not let an operator bypass the checkout capability.
        if (endpoint in ("api_sale_exchange", "api_sale_exchange_preview")
                and g.staff_actor["role"] != "admin"
                and not staff.can(g.staff_actor, "checkout")):
            return jsonify({"error": "Permission required: checkout",
                            "code": "staff_forbidden"}), 403
        if endpoint == "api_stock":
            # Safe integer parsing and reason validation are also enforced in
            # api_stock. Reject malformed deltas there, never default to +.
            raw = (request.get_json(silent=True) or {}).get("delta")
            try:
                delta = stock_policy.parse_delta(raw)
            except catalog.InvalidInputError:
                delta = None  # api_stock will respond HTTP 400
            required = "reduce_stock" if delta is not None and delta < 0 else "receive_stock"
        else:
            required = grants.get(endpoint)
        if not (required and staff.can(g.staff_actor, required)) and g.staff_actor["role"] != "admin":
            return jsonify({"error": "Permission required: " + (required or "owner-only operation"),
                            "code": "staff_forbidden"}), 403
        if endpoint in ("api_catalog_import_preview", "api_catalog_import_apply"):
            if not staff.can(g.staff_actor, "import_merge"):
                try:
                    stock_policy.cashier_import_mode(
                        request.form.get("mode", "skip"),
                        request.form.get("allow_zero_prices", "false").lower() == "true",
                        request.form.get("force_repeat", "false").lower() == "true")
                except stock_policy.InventoryForbidden as exc:
                    return jsonify({"error": str(exc), "code": "staff_forbidden"}), 403
        return None

    @app.after_request
    def _staff_audit(response):
        # Append successful staff API actions to a separate operational log.
        actor = getattr(g, "staff_actor", None)
        if actor and request.method in ("POST","PUT","PATCH","DELETE") and request.path.startswith("/api/") \
                and 200 <= response.status_code < 400:
            conn=connect(app.config.get("DB_PATH"))
            try:
                staff.audit(conn,actor,request.method,
                    request.path, response.status_code)
            except Exception:
                app.logger.exception("Staff audit write failed")
            finally:
                conn.close()
        return response

    @app.route("/staff-setup", methods=["GET", "POST"])
    def staff_setup():
        conn = connect(app.config.get("DB_PATH"))
        try:
            if staff.configured(conn):
                return redirect("/staff-login")
            # Initial enrollment cannot be performed from a remote/LAN client.
            if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
                return fail(403, "First-run staff enrollment is local-only")
            stored = settings_mod.get_all_settings(conn).get("admin_password") or ""
            error = ""
            if request.method == "POST":
                password = str(request.form.get("admin_password") or "")
                if auth.is_configured(stored):
                    valid = auth.verify_password(password, stored)
                else:
                    valid = len(password) >= auth.MIN_PASSWORD_LEN
                if not valid:
                    error = '<div class="login-error" role="alert">Check the administrator password. If this is a new setup, create a password of at least 4 characters.</div>'
                    auth.throttle_failed_login()
                else:
                    try:
                        if not auth.is_configured(stored):
                            settings_mod.save_settings(conn, {"admin_password": auth.hash_password(password)})
                        user = staff.add_user(conn, request.form.get("username"),
                           request.form.get("display_name"), "admin", request.form.get("pin"))
                        conn.commit()
                        session.clear()
                        session["staff_user_id"] = user["id"]
                        session["staff_boot_nonce"] = app.config["STAFF_BOOT_NONCE"]
                        return redirect("/#/register")
                    except (staff.StaffError, ValueError) as exc:
                        error = '<div class="login-error" role="alert">' + html.escape(str(exc)) + '</div>'
            return _login_page(error=error,
                body=_staff_setup_form(
                    existing_password=auth.is_configured(stored),
                    username=request.form.get("username") if request.method == "POST" else "",
                    display_name=request.form.get("display_name") if request.method == "POST" else ""),
                branding=_login_branding(), mode="setup")
        finally:
            conn.close()

    @app.route("/staff-login", methods=["GET","POST"])
    def staff_login():
        conn=connect(app.config.get("DB_PATH"))
        try:
            if not staff.configured(conn):
                return redirect("/")
            error=""
            if request.method=="POST":
                actor=staff.verify_login(conn,request.form.get("username"),request.form.get("pin"))
                if actor:
                    session.clear()
                    session["staff_user_id"] = actor["id"]
                    session["staff_boot_nonce"] = app.config["STAFF_BOOT_NONCE"]
                    return redirect("/#/register")
                auth.throttle_failed_login()
                error='<div class="login-error" role="alert">Incorrect username or PIN. Check your details and try again.</div>'
        finally:
            conn.close()
        return _login_page(error=error,
            body=_staff_login_form(request.form.get("username") if request.method == "POST" else ""),
            branding=_login_branding(), mode="staff")

    @app.get("/staff-logout")
    def staff_logout():
        # Switching user also clears Settings unlock and session CSRF state.
        session.clear()
        return redirect("/staff-login")

    @app.get("/api/staff/status")
    def api_staff_status():
        conn=connect(app.config.get("DB_PATH"))
        try:
            return jsonify({"enabled":staff.configured(conn),
                "operator":getattr(g,"staff_actor",None)})
        finally:
            conn.close()

    @app.get("/api/staff/users")
    def api_staff_list():
        if not getattr(g,"staff_actor",None) or g.staff_actor["role"]!="admin":
            return jsonify({"error":"Administrator access required"}),403
        return jsonify(staff.list_users(get_db()))

    def _require_owner_settings_unlock():
        # Permission changes are a second-factor owner operation, not merely
        # something anyone with an unlocked register may perform.
        if not getattr(g, "staff_actor", None) or g.staff_actor["role"] != "admin":
            return jsonify({"error": "Owner account required", "code": "staff_forbidden"}), 403
        if auth.is_configured(_admin_password()) and not auth.is_authenticated():
            return jsonify({"error": "Unlock administrator Settings before changing staff access",
                            "code": "settings_auth_required"}), 401
        return None

    @app.post("/api/staff/users")
    def api_staff_create():
        conn=get_db()
        if staff.configured(conn):
            denied = _require_owner_settings_unlock()
            if denied is not None:
                return denied
        elif not auth.is_configured(_admin_password()) or not auth.is_authenticated():
            return jsonify({"error":"Set an admin password and sign in to Settings before enabling staff PINs"}),403
        data=body()
        return jsonify(staff.add_user(conn,data.get("username"),data.get("display_name"),
            data.get("role"),data.get("pin"),data.get("permissions"))),201

    @app.patch("/api/staff/users/<int:uid>")
    def api_staff_toggle(uid):
        denied = _require_owner_settings_unlock()
        if denied is not None:
            return denied
        return jsonify(staff.set_active(get_db(),uid,body().get("active")))

    @app.get("/api/staff/permissions")
    def api_staff_permissions_schema():
        if not getattr(g, "staff_actor", None) or g.staff_actor["role"] != "admin":
            return jsonify({"error": "Owner access required"}), 403
        return jsonify({"groups": staff.PERMISSION_GROUPS,
                        "defaults": {key: key in staff.DEFAULT_STAFF for key in staff.PERMISSIONS},
                        "supervisor": {key: key in staff.DEFAULT_SUPERVISOR for key in staff.PERMISSIONS}})

    @app.put("/api/staff/users/<int:uid>/permissions")
    def api_staff_permissions_update(uid):
        denied = _require_owner_settings_unlock()
        if denied is not None:
            return denied
        return jsonify(staff.set_permissions(get_db(), uid, body().get("permissions")))

    @app.get("/api/staff/audit")
    def api_staff_audit():
        if not getattr(g,"staff_actor",None) or g.staff_actor["role"]!="admin":
            return jsonify({"error":"Administrator access required"}),403
        return jsonify(staff.list_audit(get_db()))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        stored = _admin_password()
        branding = _login_branding()
        if not auth.is_configured(stored):
            return _login_page(body="""
            <p class="login-hint">No admin password is set yet, so Settings
            are currently open. Open the POS, go to Settings and set one to
            lock this area.</p>
            <a class="btn block" href="/">Back to Register</a>""",
                next=_safe_next(), branding=branding)

        if request.method == "POST":
            password = request.form.get("password") or ""
            if auth.verify_password(password, stored):
                auth.login_session()
                return redirect(_safe_next())
            auth.throttle_failed_login()
            return _login_page(
                error='<div class="login-error" role="alert">Wrong password.</div>',
                next=_safe_next(), branding=branding)

        if auth.is_authenticated():
            return redirect(_safe_next())
        return _login_page(next=_safe_next(), branding=branding)

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
            html = html.replace("{{ csrf_token }}", issue_csrf_token())
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
    app.register_error_handler(operations.OperationError, lambda e: fail(400, e))
    app.register_error_handler(staff.StaffError, lambda e: fail(400, e))
    app.register_error_handler(ValueError, lambda e: fail(400, f"Invalid value: {e}"))

    def _redirect_uri():
        base = request.host_url.rstrip("/")
        return f"{base}/gdrive/callback"

    # ---------------- pages ----------------
    if assets_ok:
        @app.get("/")
        def index():
            return render_template("index.html", csrf_token=issue_csrf_token())

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

    @app.patch("/api/groups/<int:gid>/classification")
    def api_group_classification(gid):
        data = body()
        def optional_id(value):
            return int(value) if value not in (None, "") else None
        catalog.classify_group(get_db(), gid,
            brand_id=optional_id(data.get("brand_id")),
            category_id=optional_id(data.get("category_id")))
        return jsonify({"ok": True})

    @app.get("/api/brands")
    def api_brands():
        return jsonify(catalog.list_brands(get_db()))

    @app.post("/api/brands")
    def api_brands_create():
        new_id = catalog.create_label(get_db(), "brands", body().get("name"))
        return jsonify({"id": new_id}), 201

    @app.get("/api/categories")
    def api_categories():
        return jsonify(catalog.list_categories(get_db()))

    @app.post("/api/categories")
    def api_categories_create():
        data = body()
        new_id = catalog.create_label(get_db(), "categories", data.get("name"),
            parent_id=int(data["parent_id"]) if data.get("parent_id") else None)
        return jsonify({"id": new_id}), 201

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
        if not staff.can(g.staff_actor, "view_cost"):
            for row in rows:
                row["cost_price"] = None
        return jsonify(rows)

    @app.get("/api/lookup")
    def api_lookup():
        code = (request.args.get("code") or "").strip()
        if not code:
            return fail(400, "Missing ?code=")
        row = catalog.find_by_code(get_db(), code)
        if row is None:
            return fail(404, f"No product with SKU/barcode '{code}'")
        if not staff.can(g.staff_actor, "view_cost"):
            if "matches" in row:
                for match in row["matches"]:
                    match["cost_price"] = None
            else:
                row["cost_price"] = None
        return jsonify(row)

    @app.get("/api/products/<int:pid>")
    def api_product_get(pid):
        row = catalog.get_product(get_db(), pid)
        if row is None:
            return fail(404, f"No product with id {pid}")
        if not staff.can(g.staff_actor, "view_cost"):
            row["cost_price"] = None
        return jsonify(row)

    @app.post("/api/products")
    def api_products_create():
        data = body()
        cashier = not staff.can(g.staff_actor, "edit_products")
        if cashier:
            try:
                stock_policy.cashier_new_product(data)
            except stock_policy.InventoryForbidden as exc:
                return jsonify({"error": str(exc), "code": "staff_forbidden"}), 403
        pid = catalog.add_product_to_group(
            get_db(),
            group_id=data.get("group_id"),
            SKU=data.get("SKU"),
            barcode=data.get("barcode"),
            price=data.get("price"),
            quantity=data.get("quantity", 0),
            # Cashiers may enter selling prices, not inventory costs.
            cost_price=0 if (cashier or not staff.can(g.staff_actor, "view_cost")) else data.get("cost_price"),
            tax_rate=data.get("tax_rate", 0),
            hsn_code=data.get("hsn_code", ""),
            unit_code=data.get("unit_code", "NOS"),
            attributes=data.get("attributes", {}),
            audit_actor=g.staff_actor,
        )
        # Include the new identifier in the staff audit log as the generic
        # endpoint audit only records the route, not the created product.
        conn = get_db()
        staff.audit(conn, g.staff_actor, "CREATE", f"/api/products/{pid}", 201)
        conn.commit()
        created = catalog.get_product(conn, pid)
        if not staff.can(g.staff_actor, "view_cost"):
            created["cost_price"] = None
        return jsonify(created), 201

    @app.patch("/api/products/<int:pid>")
    def api_products_update(pid):
        fields = {}
        for key in ("SKU", "barcode", "price", "cost_price", "tax_rate", "group_id", "hsn_code", "unit_code", "reorder_point"):
            if key in body():
                fields[key] = body()[key]
        if "cost_price" in fields and not staff.can(g.staff_actor, "view_cost"):
            return jsonify({"error": "Viewing and editing costs requires permission", "code": "staff_forbidden"}), 403
        catalog.update_product(get_db(), pid, **fields,
                               verified_gst=body().get("verified_gst") is True,
                               verified_price=body().get("verified_price") is True)
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
        data = body()
        actor = g.staff_actor
        allow_reduction = staff.can(actor, "reduce_stock")
        try:
            delta, reason, kind = stock_policy.authorize_adjustment(
                data.get("delta"), data.get("reason", ""), data.get("kind", "ADJUSTMENT"),
                actor, cashier_can_reduce=allow_reduction)
        except stock_policy.InventoryForbidden as exc:
            return jsonify({"error":str(exc), "code":"staff_forbidden"}),403
        new_qty = catalog.adjust_stock(get_db(), pid, delta, reason=reason, kind=kind)
        return jsonify({"id": pid, "quantity": new_qty})

    # ---------------- inventory ----------------
    @app.get("/api/inventory/overview")
    def api_inventory_overview():
        threshold = settings_mod.get_settings(get_db()).get("low_stock_threshold") or "0"
        summary = catalog.stock_overview(get_db(), int(float(threshold)))
        if not staff.can(g.staff_actor, "view_cost"):
            summary.pop("missing_cost", None)
        return jsonify(summary)

    @app.get("/api/inventory/movements")
    def api_inventory_movements():
        return jsonify(catalog.stock_history(get_db(),
            product_id=request.args.get("product_id", type=int),
            limit=request.args.get("limit", default=100, type=int),
            offset=request.args.get("offset", default=0, type=int)))

    # ---------------- additive catalogue import ----------------
    def _catalog_import_payload():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return None, None, None, None, (jsonify({"error": "Choose a CSV or XLSX file"}), 400)
        mode = request.form.get("mode", "skip")
        allow_zero = request.form.get("allow_zero_prices", "false").lower() == "true"
        # Read at most the configured limit plus one byte. The service also
        # checks ZIP inflation limits before XML parsing.
        blob = upload.stream.read(catalog_import.MAX_UPLOAD + 1)
        items, issues = catalog_import.parse(blob, upload.filename)
        return items, issues, mode, allow_zero, None

    @app.post("/api/catalog/import/preview")
    def api_catalog_import_preview():
        items, issues, mode, allow_zero, error = _catalog_import_payload()
        if error is not None:
            return error
        return jsonify(catalog_import.preview(get_db(), items, issues, mode, allow_zero,
            cashier_safe=not staff.can(g.staff_actor, "import_merge")))

    @app.post("/api/catalog/import/apply")
    def api_catalog_import_apply():
        items, issues, mode, allow_zero, error = _catalog_import_payload()
        if error is not None:
            return error
        summary = catalog_import.apply(get_db(), items, issues, mode, allow_zero,
            force_repeat=request.form.get("force_repeat") == "true",
            repeat_reason=request.form.get("repeat_reason", ""),
            cashier_safe=not staff.can(g.staff_actor, "import_merge"),
            audit_actor=g.staff_actor)
        return jsonify(summary)

    # ---------------- retail operations: suppliers, receiving, stocktakes ----------------
    @app.get("/api/suppliers")
    def api_suppliers():
        return jsonify(operations.list_suppliers(get_db()))

    @app.post("/api/suppliers")
    def api_supplier_create():
        data = body()
        return jsonify(operations.new_supplier(get_db(), data.get("name"),
            phone=data.get("phone", ""), gstin=data.get("gstin", ""),
            address=data.get("address", ""))), 201

    @app.get("/api/purchases")
    def api_purchases():
        return jsonify(operations.list_purchases(get_db()))

    @app.post("/api/purchases")
    def api_purchases_receive():
        data = body()
        return jsonify(operations.receive_purchase(get_db(), data.get("supplier_id"),
            data.get("supplier_invoice"), data.get("lines"), data.get("note", ""),
            data.get("invoice_gst"), bool(data.get("update_catalog_cost")))), 201

    @app.get("/api/purchases/<int:pid>")
    def api_purchase_detail(pid):
        return jsonify(operations.purchase_detail(get_db(), pid))

    @app.post("/api/purchases/<int:pid>/payments")
    def api_supplier_payment(pid):
        data=body()
        return jsonify(operations.pay_supplier(get_db(),pid,data.get("amount"),
            data.get("method"),data.get("reference", ""),data.get("request_id"))), 201

    @app.get("/api/stocktakes")
    def api_stocktakes():
        return jsonify(operations.list_stocktakes(get_db()))

    @app.post("/api/stocktakes")
    def api_stocktake_start():
        return jsonify(operations.start_stocktake(get_db(),body().get("note", ""))),201

    @app.get("/api/stocktakes/<int:sid>")
    def api_stocktake_detail(sid):
        return jsonify(operations.get_stocktake(get_db(),sid))

    @app.put("/api/stocktakes/<int:sid>/count/<int:pid>")
    def api_stocktake_count(sid,pid):
        return jsonify(operations.count_stock(get_db(),sid,pid,body().get("counted")))

    @app.post("/api/stocktakes/<int:sid>/post")
    def api_stocktake_post(sid):
        return jsonify(operations.post_stocktake(get_db(), sid,
            adjustment_reason=body().get("reason", ""), actor=g.staff_actor))

    # ---------------- UPI payment display (does not verify a transfer) ----------------
    @app.get("/api/upi/reference")
    def api_upi_reference():
        return jsonify({"reference": "POS" + secrets.token_hex(7).upper()})

    @app.get("/api/upi/qr.svg")
    def api_upi_qr():
        from pos.upi import payment_svg
        opts = settings_mod.get_settings(get_db())
        try:
            svg = payment_svg(opts.get("store_upi_id"), opts.get("store_name"),
                request.args.get("amount"), request.args.get("ref"))
        except (ValueError, RuntimeError) as e:
            return fail(400, e)
        return Response(svg, mimetype="image/svg+xml",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options":"nosniff"})

    # ---------------- sales ----------------
    def require_open_day():
        if cash_sessions.current_session(get_db()) is None:
            return jsonify({"error": "Open the day in Cash Drawer before recording monetary transactions", "code": "day_closed"}), 409
        return None

    @app.post("/api/sales")
    def api_checkout():
        denied = require_open_day()
        if denied is not None:
            return denied
        data = body()
        discount = data.get("discount") or {}
        discount_restricted = not staff.can(g.staff_actor, "discount_override")
        receipt = sales.checkout(
            get_db(),
            items=data.get("items"),
            payments=data.get("payments"),
            discount_mode=discount.get("mode"),
            discount_value=discount.get("value"),
            customer_id=data.get("customer_id"),
            use_credit=bool(data.get("use_credit")),
            supply_type=data.get("supply_type", "intra"),
            place_of_supply=data.get("place_of_supply", ""),
            request_id=data.get("request_id"),
            salesperson=(data.get("salesperson") if g.staff_actor["role"] == "admin" else
                         g.staff_actor["display_name"]),
            require_open_session=True,
            max_discount_percent=(settings_mod.get_settings(get_db())["cashier_max_discount_percent"] if discount_restricted else None),
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
            search=request.args.get("q") or None,
        )
        return jsonify(rows)

    @app.get("/api/sales/<int:sid>")
    def api_sale_get(sid):
        return jsonify(sales.get_sale_detail(get_db(), sid))

    @app.post("/api/sales/<int:sid>/refund/preview")
    def api_sale_refund_preview(sid):
        data = body()
        return jsonify(sales.preview_refund(get_db(), sid, lines=data.get("lines"),
                                          to_credit=bool(data.get("to_credit")),
                                          credit_customer_id=data.get("credit_customer_id")))

    @app.post("/api/sales/<int:sid>/refund")
    def api_sale_refund(sid):
        denied = require_open_day()
        if denied is not None:
            return denied
        data = body()
        lines = data.get("lines")
        to_credit = bool(data.get("to_credit"))
        return jsonify(sales.refund_sale(get_db(), sid, lines=lines,
                                         to_credit=to_credit,
                                         reason=data.get("reason", ""),
                                         request_id=data.get("request_id"),
                                         credit_customer_id=data.get("credit_customer_id"),
                                         require_open_session=True))

    @app.post("/api/sales/<int:sid>/exchange/preview")
    def api_sale_exchange_preview(sid):
        data = body()
        return jsonify(exchanges.preview_exchange(
            get_db(), sid, returns=data.get("returns"),
            replacements=data.get("replacements"), customer_id=data.get("customer_id"),
            supply_type=data.get("supply_type", "intra"),
            place_of_supply=data.get("place_of_supply", "")))

    @app.post("/api/sales/<int:sid>/exchange")
    def api_sale_exchange(sid):
        denied = require_open_day()
        if denied is not None:
            return denied
        data = body()
        return jsonify(exchanges.exchange_sale(
            get_db(), sid, returns=data.get("returns"),
            replacements=data.get("replacements"), customer_id=data.get("customer_id"),
            payments=data.get("payments"), reason=data.get("reason", ""),
            request_id=data.get("request_id"),
            supply_type=data.get("supply_type", "intra"),
            place_of_supply=data.get("place_of_supply", ""),
            salesperson=g.staff_actor["display_name"],
            require_open_session=True,
            accept_forfeit=data.get("accept_forfeit", False))), 201

    @app.post("/api/sales/<int:sid>/void")
    def api_sale_void(sid):
        denied = require_open_day()
        if denied is not None:
            return denied
        return jsonify(sales.void_sale(get_db(), sid, require_open_session=True))

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

    # ---------------- direct thermal printing ----------------
    # Print only invoices fetched from the server's own database. Never accept
    # arbitrary ESC/POS or user-provided receipt HTML from the browser.
    @app.get("/api/printing/info")
    def api_thermal_print_info():
        try:
            default = thermal_print.default_printer()
        except (OSError, thermal_print.PrinterError):
            default = ""
        return jsonify({"platform": __import__("sys").platform, "default_printer": default})

    @app.post("/api/printing/test")
    def api_thermal_print_test():
        cfg = settings_mod.get_settings(get_db())
        if cfg.get("receipt_print_mode") != "thermal":
            return fail(400, "Enable thermal printing and save your printer settings first")
        try:
            printer = thermal_print.send_image(thermal_print.render_test(cfg), cfg)
        except thermal_print.PrinterError as e:
            return fail(503, e)
        return jsonify({"status": "spooled", "printer": printer})

    @app.post("/api/printing/sales/<int:sid>")
    def api_thermal_print_sale(sid):
        payload = body()
        manual = payload.get("manual", False)
        if type(manual) is not bool:
            return fail(400, "manual must be a boolean")
        try:
            result = thermal_print.print_recorded_sale(get_db(), sid, manual=manual)
        except thermal_print.PrinterError as e:
            # Sale/refund/exchange was already committed. A printer failure must
            # never be surfaced as a failed financial transaction.
            return fail(503, e)
        return jsonify(result)

    @app.post("/api/printing/reports/<int:sid>")
    def api_thermal_print_report(sid):
        cfg = settings_mod.get_settings(get_db())
        if cfg.get("receipt_print_mode") != "thermal":
            return fail(400, "Enable thermal printing first")
        try:
            report = cash_sessions.session_report(get_db(), sid)
            printer = thermal_print.send_image(
                thermal_print.render_z_report(report, cfg), cfg)
        except thermal_print.PrinterError as e:
            return fail(503, e)
        return jsonify({"status": "spooled", "printer": printer})

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
        init_db(app.config["DB_PATH"])
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
        init_db(app.config["DB_PATH"])
        return jsonify({"ok": True})

    # ---------------- Google Drive (native OAuth) ----------------
    def _gdrive_result_page(ok, detail=""):
        detail = html.escape(str(detail or ""))
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
        state = secrets.token_urlsafe(32)
        session["gdrive_oauth_state"] = state
        url = gdrive.build_auth_url(client_id, _redirect_uri(), state=state)
        return jsonify({"auth_url": url})

    @app.get("/gdrive/callback")
    def api_gdrive_callback():
        expected_state = session.pop("gdrive_oauth_state", None)
        received_state = request.args.get("state") or ""
        if not expected_state or not secrets.compare_digest(expected_state, received_state):
            return _gdrive_result_page(False, "Google Drive connection rejected: invalid OAuth state"), 400
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
    @app.get("/api/customers/overview")
    def api_customers_overview():
        row = get_db().execute('''
            SELECT COUNT(*) AS total_customers,
                   COALESCE(SUM(loyalty_points), 0) AS outstanding_points,
                   COALESCE(SUM(CASE WHEN loyalty_points > 0 THEN 1 ELSE 0 END), 0) AS point_holders,
                   COALESCE(SUM(store_credit_cents), 0) AS outstanding_credit_paise
            FROM customers
        ''').fetchone()
        return jsonify(dict(row))

    @app.get("/api/customers")
    def api_customers_list():
        return jsonify(customers_mod.list_customers(
            get_db(), query=request.args.get("q") or None,
            limit=min(500, max(1, request.args.get("limit", type=int) or 500)),
            balance_filter=request.args.get("filter") or "all"))

    @app.post("/api/customers")
    def api_customers_create():
        data = body()
        cust = customers_mod.create_customer(
            get_db(),
            name=data.get("name"),
            phone=data.get("phone"),
            email=data.get("email"),
            notes=data.get("notes"),
            gstin_number=data.get("gstin", ""),
            billing_address=data.get("billing_address", ""))
        return jsonify(cust), 201

    @app.get("/api/customers/<int:cid>")
    def api_customer_get(cid):
        conn = get_db()
        cust = customers_mod.get_customer(conn, cid)
        cust["credit_events"] = customers_mod.credit_events(conn, cid)
        cust["loyalty_events"] = loyalty.events(conn, cid)
        cust["recent_sales"] = customers_mod.recent_sales(conn, cid)
        return jsonify(cust)

    @app.patch("/api/customers/<int:cid>")
    def api_customer_update(cid):
        data = body()
        return jsonify(customers_mod.update_customer(
            get_db(), cid,
            name=data.get("name"), phone=data.get("phone"),
            email=data.get("email"), notes=data.get("notes"),
            gstin_number=data.get("gstin"),
            billing_address=data.get("billing_address")))

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

    # All loyalty balance edits and conversions require a configured admin
    # password AND an authenticated session; first-run bootstrap cannot bypass.
    def _loyalty_admin():
        # Explicit owner-granted permission replaces the global shared
        # Settings password for non-owner staff loyalty actions.
        if g.staff_actor["role"] != "admin" and staff.can(g.staff_actor, "manage_loyalty"):
            return None
        if not auth.is_configured(_admin_password()):
            return jsonify({"error": "Set an admin password in Settings before managing loyalty points"}), 403
        if not auth.is_authenticated():
            return jsonify({"error": "Admin login required", "code": "auth_required"}), 401
        return None

    @app.post("/api/customers/<int:cid>/loyalty/adjust")
    def api_loyalty_adjust(cid):
        denial = _loyalty_admin()
        if denial is not None:
            return denial
        data = body()
        result = loyalty.adjust(get_db(), cid,
                                delta=data.get("delta_points"),
                                set_to=data.get("set_points"),
                                reason=data.get("reason", ""))
        return jsonify({"id": cid, "loyalty_points": result})

    @app.post("/api/customers/<int:cid>/loyalty/redeem")
    def api_loyalty_redeem(cid):
        denial = _loyalty_admin()
        if denial is not None:
            return denial
        return jsonify(loyalty.redeem(get_db(), cid, body().get("points")))

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
            customer_id=data.get("customer_id"),
            salesperson=data.get("salesperson", ""))
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
        # Closing is committed before backup IO. If the disk/cloud is down,
        # never tell the cashier the day is still open or attempt to close it
        # twice; report an explicit, recoverable backup warning instead.
        result = cash_sessions.close_session(
            get_db(), counted_cash=data.get("counted_cash"),
            note=data.get("note"))
        cfg = settings_mod.get_all_settings(get_db())
        if cfg.get("backup_day_end_enabled") == "true":
            try:
                result["backup"] = backups.run_backup_cycle(
                    cfg, source_path=db_path(), reason="day_end",
                    upload_gdrive=False)
            except Exception as exc:
                app.logger.exception("Day closed but automatic snapshot failed")
                result["backup_warning"] = (
                    "Day closed successfully, but the backup failed: " + str(exc)[:180])
        return jsonify(result)

    @app.post("/api/cash-sessions/movements")
    def api_cash_movement():
        data = body()
        return jsonify(cash_sessions.record_cash_movement(get_db(),
            data.get("kind"), data.get("amount"), data.get("reason"),
            data.get("reference", ""))), 201

    @app.get("/api/cash-sessions/<int:sid>/report")
    def api_session_report(sid):
        return jsonify(cash_sessions.session_report(get_db(), sid))

    # ---------------- exports ----------------
    def _csv_response(rows, header, filename):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        # Prevent spreadsheet formula injection via user-controlled names,
        # addresses and SKU fields. Preserve typed numeric cells (including
        # negative monetary amounts) as numbers.
        for row in rows:
            writer.writerow(["'" + cell if isinstance(cell, str) and
                cell.lstrip().startswith(("=", "+", "-", "@")) else cell
                for cell in row])
        data = "\ufeff" + buf.getvalue()  # BOM so Excel opens UTF-8 cleanly
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"})

    @app.get("/api/export/upi.csv")
    def api_export_upi():
        from_date = request.args.get("from", "").strip()
        to_date = request.args.get("to", "").strip()
        params = []
        where = ["p.method='UPI'"]
        if from_date:
            where.append("date(s.timestamp) >= date(?)")
            params.append(from_date)
        if to_date:
            where.append("date(s.timestamp) <= date(?)")
            params.append(to_date)
        query = """SELECT s.id, s.invoice_number, s.timestamp, s.status,
                p.amount, p.reference, p.upi_utr
                FROM payments p JOIN sales s ON s.id=p.sale_id
                WHERE """ + " AND ".join(where) + " ORDER BY s.id DESC, p.id DESC"
        rows = [tuple(r) for r in get_db().execute(query, params)]
        return _csv_response(rows,
            ["Sale ID", "Invoice", "Timestamp", "Sale status", "Recorded UPI amount",
             "POS reference (not bank UTR)", "Bank UTR entered by cashier"],
            "pyPOS-UPI-Reconciliation.csv")

    @app.get("/api/export/loyalty.csv")
    def api_export_loyalty():
        denial = _loyalty_admin()
        if denial is not None:
            return denial
        rows = get_db().execute('''SELECT le.id, le.timestamp, le.customer_id,
            COALESCE(c.name, ''), COALESCE(c.phone, ''), le.event_type,
            le.delta_points, le.balance_after, le.reason,
            COALESCE(le.sale_id, '')
            FROM loyalty_events le
            LEFT JOIN customers c ON c.id = le.customer_id
            ORDER BY le.id DESC''').fetchall()
        return _csv_response([tuple(row) for row in rows],
            ["event_id", "timestamp_utc", "customer_id", "customer_name",
             "phone", "event_type", "delta_points", "balance_after",
             "reason", "linked_sale_id"], "pypos-loyalty-ledger.csv")

    @app.get("/api/export/refunds.csv")
    def api_export_refunds():
        return _csv_response(sales.refund_export_rows(get_db()),
            ["refund_receipt_id", "timestamp_utc", "original_sale_id",
             "original_invoice_number", "customer_id", "reason",
             "subtotal", "discount", "recorded_gst", "total_refunded",
             "cash", "card", "upi", "store_credit", "settlement_type",
             "exchange_offset", "exchange_forfeited", "replacement_invoice",
             "refund_number"],
            "pypos-refunds.csv")

    @app.get("/api/export/sales.csv")
    def api_export_sales():
        from_ts, to_ts = sales.parse_utc_bounds(
            request.args.get("from_date"), request.args.get("to_date"))
        cur = get_db().cursor()
        cur.execute(
            """SELECT s.id, COALESCE(s.invoice_number, ''), s.timestamp,
                      s.status, COALESCE(s.parent_sale_id, ''),
                      s.subtotal, s.discount_total, s.tax_total, s.total, s.salesperson,
                      CASE WHEN s.status = 'REFUNDED' THEN -s.total
                           WHEN s.status = 'VOID' THEN 0 ELSE s.total END,
                      COALESCE((SELECT SUM(quantity) FROM sale_items si
                                WHERE si.sale_id = s.id), 0),
                      COALESCE((SELECT GROUP_CONCAT(DISTINCT method) FROM payments p
                                WHERE p.sale_id = s.id), ''),
                      COALESCE(s.refund_number, '')
               FROM sales s
               WHERE (? IS NULL OR s.timestamp >= ?)
                 AND (? IS NULL OR s.timestamp <= ?)
               ORDER BY s.id DESC""",
            (from_ts, from_ts, to_ts, to_ts))
        return _csv_response(
            [tuple(r) for r in cur.fetchall()],
            ["receipt", "invoice_number", "timestamp_utc", "status",
             "parent_sale", "subtotal", "discount", "tax", "total",
             "salesperson", "signed_net_effect", "items", "payment_methods", "refund_number"],
            "pypos-sales.csv")

    @app.get("/api/export/sale-lines.csv")
    def api_export_sale_lines():
        """Recorded item-level GST data, without reconstructing missing older records.

        Legacy rows can have empty HSN or default recorded tax cents. Such
        omissions remain explicit rather than being replaced with estimates.
        """
        from_ts, to_ts = sales.parse_utc_bounds(
            request.args.get("from_date"), request.args.get("to_date"))
        cur = get_db().cursor()
        cur.execute(
            """SELECT s.id AS receipt, COALESCE(s.invoice_number, '') AS invoice_number,
                      s.timestamp, s.status, COALESCE(s.parent_sale_id, '') AS parent_sale,
                      COALESCE(si.sku_at_sale, '') AS sku, 
                      COALESCE(si.description_at_sale, '') AS description,
                      COALESCE(si.hsn_at_sale, '') AS hsn,
                      COALESCE(si.unit_at_sale, '') AS uqc,
                      si.quantity, si.unit_price, si.tax_rate_at_sale,
                      si.discount_cents, si.tax_cents,
                      COALESCE(s.refund_number, '') AS refund_number
               FROM sales s JOIN sale_items si ON si.sale_id = s.id
               WHERE (? IS NULL OR s.timestamp >= ?)
                 AND (? IS NULL OR s.timestamp <= ?)
               ORDER BY s.id DESC, si.id ASC""",
            (from_ts, from_ts, to_ts, to_ts))
        rows = []
        for r in cur.fetchall():
            rows.append((r["receipt"], r["invoice_number"], r["timestamp"],
                         r["status"], r["parent_sale"], r["sku"], r["description"],
                         r["hsn"], r["uqc"], r["quantity"], r["unit_price"],
                         r["tax_rate_at_sale"],
                         round(int(r["discount_cents"] or 0) / 100, 2),
                         round(int(r["tax_cents"] or 0) / 100, 2),
                         r["refund_number"]))
        return _csv_response(rows, ["receipt", "invoice_number", "timestamp_utc",
                    "status", "parent_sale", "sku_at_sale", "description_at_sale",
                    "hsn_sac_at_sale", "unit_code_at_sale", "quantity", "unit_price",
                    "tax_percent_at_sale", "recorded_discount", "recorded_tax", "refund_number"],
                    "pypos-sale-lines.csv")

    @app.get("/api/export/inventory.csv")
    def api_export_inventory():
        products = catalog.list_products(get_db())
        rows = []
        for p in products:
            attrs = "; ".join(f"{a['name']}={a['value']}" for a in p["attributes"])
            stock_value = round((p["cost_price"] or 0) * p["quantity"], 2)
            rows.append([p["SKU"], p["group_name"] or "", attrs, p["barcode"] or "",
                         p["quantity"], p["price"], p["cost_price"], p["tax_rate"], p["hsn_code"], p["unit_code"],
                         stock_value])
        return _csv_response(
            rows,
            ["sku", "product", "variants", "barcode", "stock", "price",
             "cost", "tax_percent", "hsn_sac", "unit_code", "stock_value_at_cost"],
            "pypos-inventory.csv")

    return app
