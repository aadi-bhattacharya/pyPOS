"""Optional staff identities and PIN verification (no default credentials)."""
import hashlib
import hmac
import re
import secrets

ITERATIONS = 420_000


class StaffError(ValueError):
    pass


def configured(conn):
    return conn.execute("SELECT 1 FROM staff_users LIMIT 1").fetchone() is not None


def _hash(pin):
    salt=secrets.token_bytes(16)
    secret=hashlib.pbkdf2_hmac("sha256",pin.encode("utf-8"),salt,ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${secret.hex()}"


def _verify(pin, hashed):
    try:
        _,n,salt,expected=hashed.split("$")
        digest=hashlib.pbkdf2_hmac("sha256",pin.encode("utf-8"),bytes.fromhex(salt),int(n))
        return hmac.compare_digest(digest.hex(),expected)
    except (ValueError,TypeError):
        return False


def add_user(conn,username,display_name,role,pin):
    username = str(username or "").strip().lower()
    display_name = str(display_name or "").strip()
    role = str(role or "").strip().lower()
    pin = str(pin or "")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,31}",username):
        raise StaffError("Username must be 3–32 letters, digits, dots, dashes or underscores")
    if not display_name or len(display_name)>100:
        raise StaffError("Enter a display name (up to 100 characters)")
    if role not in ("admin","manager","cashier"):
        raise StaffError("Invalid staff role")
    if not re.fullmatch(r"\d{6,12}",pin):
        raise StaffError("PIN must contain 6–12 digits")
    if not configured(conn) and role!="admin":
        raise StaffError("The first staff member must be an administrator")
    try:
        cur=conn.execute("""INSERT INTO staff_users(username,display_name,role,pin_hash)
            VALUES(?,?,?,?)""",(username,display_name,role,_hash(pin)))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise StaffError("Username already exists") from exc
        raise
    return {"id":cur.lastrowid,"username":username,"display_name":display_name,"role":role}


def verify_login(conn,username,pin):
    row=conn.execute("SELECT * FROM staff_users WHERE username=? COLLATE NOCASE AND active=1",
                     (str(username or "").strip(),)).fetchone()
    if not row or not _verify(str(pin or ""),row["pin_hash"]):
        return None
    return {k:row[k] for k in ("id","username","display_name","role")}


def get_user(conn,user_id):
    row=conn.execute("SELECT id,username,display_name,role,active FROM staff_users WHERE id=?",(user_id,)).fetchone()
    return dict(row) if row else None


def list_users(conn):
    return [dict(r) for r in conn.execute("SELECT id,username,display_name,role,active,created_at FROM staff_users ORDER BY id")]


def set_active(conn,user_id,active):
    conn.execute("BEGIN IMMEDIATE")
    try:
        row=conn.execute("SELECT id,role,active FROM staff_users WHERE id=?",(user_id,)).fetchone()
        if not row:
            raise StaffError("Staff member not found")
        active=bool(active)
        if not active and row["role"]=="admin" and row["active"]:
            n=conn.execute("SELECT COUNT(*) FROM staff_users WHERE role='admin' AND active=1").fetchone()[0]
            if n<=1:
                raise StaffError("Cannot disable the last active administrator")
        conn.execute("UPDATE staff_users SET active=? WHERE id=?",(int(active),user_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_user(conn,user_id)


def audit(conn,actor,method,action,status):
    conn.execute("""INSERT INTO staff_audit_events
        (staff_id,username_at_event,method,action,response_status)
        VALUES(?,?,?,?,?)""",(actor["id"],actor["username"],method,action,status))


def list_audit(conn,limit=200):
    return [dict(r) for r in conn.execute("SELECT * FROM staff_audit_events ORDER BY id DESC LIMIT ?",
                                          (max(1,min(1000,int(limit))),))]
