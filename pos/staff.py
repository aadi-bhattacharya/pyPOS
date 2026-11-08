"""Individual POS operators and server-enforced, owner-controlled capabilities.

Older databases store cashier/manager in staff_users.role (a CHECK constraint).
We deliberately retain those values on disk to avoid rebuilding a table with
existing audit FKs, but expose one unified 'staff' role on the API. Legacy
operators inherit their previous privileges until the owner customises them.
"""
import hashlib
import hmac
import json
import re
import secrets

ITERATIONS = 420_000

# The API never accepts arbitrary keys or untyped values.
PERMISSION_GROUPS = {
    "Register and cash drawer": {
        "checkout": "Process sales and park carts",
        "open_day": "Start the till day",
        "close_day": "Count and close the till",
        "cash_movements": "Record cash paid in or out (sensitive)",
        "discount_override": "Override the standard staff discount limit (sensitive)",
        "refund_sales": "Process customer refunds (sensitive)",
        "void_sales": "Void existing sales (sensitive)",
    },
    "Catalogue and stock": {
        "add_products": "Create products, variants and groups",
        "edit_products": "Edit existing products, prices, barcodes and groups",
        "delete_products": "Delete products and groups (sensitive)",
        "receive_stock": "Receive additional stock",
        "reduce_stock": "Record stock shortages or damage (sensitive)",
        "import_stock": "Import new products and additive stock",
        "import_merge": "Merge imports into existing product details (sensitive)",
        "stocktakes": "Perform and post stocktakes (sensitive)",
        "view_cost": "View purchase costs and valuation",
    },
    "Customers and operations": {
        "manage_customers": "Create and update customer details",
        "delete_customers": "Delete customer records (sensitive)",
        "manage_credit": "Adjust customer store credit (sensitive)",
        "manage_loyalty": "Adjust or redeem loyalty points (sensitive)",
        "manage_purchases": "Suppliers, purchases and supplier payments (sensitive)",
        "view_reports": "View sales reports and cash-session history",
        "export_data": "Download CSV sales, inventory and customer data (sensitive)",
    },
}
PERMISSIONS = frozenset(p for group in PERMISSION_GROUPS.values() for p in group)
DEFAULT_STAFF = frozenset({"checkout", "open_day", "close_day", "add_products",
    "receive_stock", "import_stock", "manage_customers", "view_reports"})
# Preserves capabilities of historical 'manager' accounts, without giving
# access to owner-only staff administration, backups or Settings.
DEFAULT_SUPERVISOR = PERMISSIONS - frozenset({"manage_loyalty"})


class StaffError(ValueError):
    pass


def configured(conn):
    return conn.execute("SELECT 1 FROM staff_users LIMIT 1").fetchone() is not None


def _hash(pin):
    salt = secrets.token_bytes(16)
    secret = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${secret.hex()}"


def _verify(pin, hashed):
    try:
        _, n, salt, expected = hashed.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), int(n))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def _legacy_default(db_role):
    return DEFAULT_SUPERVISOR if db_role == "manager" else DEFAULT_STAFF


def _normalized_permissions(row):
    if row["role"] == "admin":
        return {key: True for key in PERMISSIONS}
    try:
        stored = row["permissions_json"]
    except (KeyError, IndexError):
        stored = None
    if stored is not None:
        # A present but corrupt permissions record fails closed. Only an
        # *absent* record (a truly legacy operator) inherits a role preset.
        try:
            result = json.loads(stored)
            if isinstance(result, dict):
                return {k: result.get(k, False) is True for k in PERMISSIONS}
        except (ValueError, TypeError):
            pass
        return {k: False for k in PERMISSIONS}
    allowed = _legacy_default(row["role"])
    return {k: k in allowed for k in PERMISSIONS}


def _public(row):
    data = {key: row[key] for key in ("id", "username", "display_name", "role", "active") if key in row.keys()}
    data["role"] = "admin" if row["role"] == "admin" else "staff"
    data["permissions"] = _normalized_permissions(row)
    if "created_at" in row.keys():
        data["created_at"] = row["created_at"]
    return data


def add_user(conn, username, display_name, role, pin, permissions=None):
    username = str(username or "").strip().lower()
    display_name = str(display_name or "").strip()
    role = str(role or "staff").strip().lower()
    pin = str(pin or "")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}", username):
        raise StaffError("Username must be 3–32 letters, digits, dots, dashes or underscores")
    if not display_name or len(display_name) > 100:
        raise StaffError("Enter a display name (up to 100 characters)")
    if role not in ("admin", "staff", "cashier", "manager"):
        raise StaffError("Invalid staff role")
    if not re.fullmatch(r"\d{6,12}", pin):
        raise StaffError("PIN must contain 6–12 digits")
    if not configured(conn) and role != "admin":
        raise StaffError("The first staff member must be an administrator")
    if role == "admin" and permissions is not None:
        raise StaffError("Administrator permissions cannot be overridden")
    # Store 'staff' as the pre-existing cashier role to honour the legacy
    # CHECK constraint without risk to existing FKs or business records.
    stored_role = "cashier" if role == "staff" else role
    if permissions is not None:
        normalized = validate_permissions(permissions)
    else:
        normalized = {p: p in _legacy_default(stored_role) for p in PERMISSIONS}
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("""INSERT INTO staff_users(username,display_name,role,pin_hash)
            VALUES(?,?,?,?)""", (username, display_name, stored_role, _hash(pin)))
        if stored_role != "admin":
            conn.execute("INSERT INTO staff_permissions(staff_id,permissions_json) VALUES(?,?)",
                (cur.lastrowid, json.dumps(normalized, sort_keys=True)))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "UNIQUE" in str(exc):
            raise StaffError("Username already exists") from exc
        raise
    return get_user(conn, cur.lastrowid)


def verify_login(conn, username, pin):
    row = conn.execute("SELECT id,pin_hash FROM staff_users WHERE username=? COLLATE NOCASE AND active=1",
        (str(username or "").strip(),)).fetchone()
    if not row or not _verify(str(pin or ""), row["pin_hash"]):
        return None
    return get_user(conn, row["id"])


def get_user(conn, user_id):
    row = conn.execute("""SELECT u.id,u.username,u.display_name,u.role,u.active,
        p.permissions_json FROM staff_users u LEFT JOIN staff_permissions p ON p.staff_id=u.id
        WHERE u.id=?""", (user_id,)).fetchone()
    return _public(row) if row else None


def list_users(conn):
    return [_public(r) for r in conn.execute("""SELECT u.id,u.username,u.display_name,u.role,u.active,
        u.created_at,p.permissions_json FROM staff_users u
        LEFT JOIN staff_permissions p ON p.staff_id=u.id ORDER BY u.id""")]


def can(actor, permission):
    return bool(actor and (actor.get("role") == "admin" or
        actor.get("permissions", {}).get(permission) is True))


def validate_permissions(payload):
    if not isinstance(payload, dict) or set(payload) != PERMISSIONS:
        raise StaffError("Supply every defined permission toggle (no unknown or missing permissions)")
    if any(type(value) is not bool for value in payload.values()):
        raise StaffError("Permission values must be true or false")
    return {key: payload[key] for key in sorted(PERMISSIONS)}


def set_permissions(conn, user_id, permissions):
    normalized = validate_permissions(permissions)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id,role FROM staff_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise StaffError("Staff member not found")
        if row["role"] == "admin":
            raise StaffError("Owner/administrator permissions cannot be edited")
        conn.execute("""INSERT INTO staff_permissions(staff_id,permissions_json) VALUES(?,?)
            ON CONFLICT(staff_id) DO UPDATE SET permissions_json=excluded.permissions_json""",
            (user_id, json.dumps(normalized, sort_keys=True)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_user(conn, user_id)


def set_active(conn, user_id, active):
    if type(active) is not bool:
        raise StaffError("Active status must be true or false")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id,role,active FROM staff_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise StaffError("Staff member not found")
        if not active and row["role"] == "admin" and row["active"]:
            n = conn.execute("SELECT COUNT(*) FROM staff_users WHERE role='admin' AND active=1").fetchone()[0]
            if n <= 1:
                raise StaffError("Cannot disable the last active administrator")
        conn.execute("UPDATE staff_users SET active=? WHERE id=?", (int(active), user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_user(conn, user_id)


def audit(conn, actor, method, action, status):
    conn.execute("""INSERT INTO staff_audit_events
        (staff_id,username_at_event,method,action,response_status)
        VALUES(?,?,?,?,?)""", (actor["id"], actor["username"], method, action, status))


def list_audit(conn, limit=200):
    return [dict(r) for r in conn.execute("SELECT * FROM staff_audit_events ORDER BY id DESC LIMIT ?",
        (max(1, min(1000, int(limit))),))]
