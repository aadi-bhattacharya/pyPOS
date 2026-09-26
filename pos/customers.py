from .catalog import InvalidInputError


class CustomersError(Exception):
    """Base class for customer-related errors."""


class CustomerNotFoundError(CustomersError):
    """Raised when a customer id doesn't exist."""


class DuplicatePhoneError(CustomersError):
    """Raised when the phone number is already used by another customer."""


class CustomerHasCreditError(CustomersError):
    """Raised when deleting a customer that still holds store credit."""


class InsufficientCreditError(CustomersError):
    """Raised when spending more credit than the customer holds."""


NAME_MAX = 120
NOTES_MAX = 2000


def _clean(value, limit, field, required=False):
    if value is None:
        if required:
            raise InvalidInputError(f"{field} is required")
        return None
    value = str(value).strip()
    if not value:
        if required:
            raise InvalidInputError(f"{field} is required")
        return None
    if len(value) > limit:
        raise InvalidInputError(f"{field} is too long (max {limit} characters)")
    return value


def _phone(conn, phone, exclude_id=None):
    phone = _clean(phone, 40, "Phone")
    if phone is None:
        return None
    cur = conn.cursor()
    if exclude_id is None:
        row = cur.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    else:
        row = cur.execute(
            "SELECT id FROM customers WHERE phone = ? AND id != ?",
            (phone, exclude_id)).fetchone()
    if row:
        raise DuplicatePhoneError(f"Phone number {phone} is already in use")
    return phone


def create_customer(conn, name, phone=None, email=None, notes=None):
    name = _clean(name, NAME_MAX, "Name", required=True)
    phone = _phone(conn, phone)
    email = _clean(email, 200, "Email")
    notes = _clean(notes, NOTES_MAX, "Notes")
    cur = conn.cursor()
    cur.execute(
        ''' INSERT INTO customers(name, phone, email, notes) VALUES(?, ?, ?, ?) ''',
        (name, phone, email, notes),
    )
    conn.commit()
    return get_customer(conn, cur.lastrowid)


def get_customer(conn, customer_id):
    cur = conn.cursor()
    cur.execute(''' SELECT * FROM customers WHERE id = ? ''', (customer_id,))
    row = cur.fetchone()
    if row is None:
        raise CustomerNotFoundError(f"No customer with id {customer_id}")

    stats = cur.execute(
        ''' SELECT COUNT(*) AS orders,
                   COALESCE(SUM(total), 0) AS spent
            FROM sales WHERE customer_id = ? AND status = 'COMPLETED' ''',
        (customer_id,),
    ).fetchone()

    return {
        "id": row["id"],
        "name": row["name"],
        "phone": row["phone"],
        "email": row["email"],
        "notes": row["notes"],
        "store_credit_cents": row["store_credit_cents"],
        "store_credit": round(row["store_credit_cents"] / 100, 2),
        "created_at": row["created_at"],
        "orders": stats["orders"],
        "total_spent": round(stats["spent"], 2),
    }


def find_by_phone(conn, phone):
    """Exact phone match — used to attach walk-ins quickly."""
    if not phone:
        return None
    cur = conn.cursor()
    cur.execute("SELECT id FROM customers WHERE phone = ?", (str(phone).strip(),))
    row = cur.fetchone()
    return get_customer(conn, row["id"]) if row else None


def list_customers(conn, query=None, limit=500):
    where, params = [], []
    if query:
        like = f"%{query.strip()}%"
        where.append("(name LIKE ? OR COALESCE(phone, '') LIKE ? OR COALESCE(email, '') LIKE ?)")
        params += [like, like, like]
    sql = '''
        SELECT c.id FROM customers c
    '''
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.name COLLATE NOCASE LIMIT ?"
    params.append(int(limit))
    cur = conn.cursor()
    ids = [r["id"] for r in cur.execute(sql, params).fetchall()]
    return [get_customer(conn, cid) for cid in ids]


def update_customer(conn, customer_id, name=None, phone=None, email=None, notes=None):
    get_customer(conn, customer_id)
    fields = {}
    if name is not None:
        fields["name"] = _clean(name, NAME_MAX, "Name", required=True)
    if phone is not None:
        fields["phone"] = _phone(conn, phone, exclude_id=customer_id)
    if email is not None:
        fields["email"] = _clean(email, 200, "Email")
    if notes is not None:
        fields["notes"] = _clean(notes, NOTES_MAX, "Notes")
    if fields:
        marks = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE customers SET {marks} WHERE id = ?",
                     (*fields.values(), customer_id))
        conn.commit()
    return get_customer(conn, customer_id)


def delete_customer(conn, customer_id):
    get_customer(conn, customer_id)
    row = conn.execute(
        "SELECT store_credit_cents FROM customers WHERE id = ?",
        (customer_id,)).fetchone()
    if row["store_credit_cents"] != 0:
        raise CustomerHasCreditError(
            "This customer still holds store credit — spend it or adjust "
            "the balance to zero before deleting.")
    conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    conn.commit()


def _apply_credit(conn, customer_id, delta_cents, reason, sale_id=None):
    """Caller must hold an open transaction."""
    delta_cents = int(delta_cents)
    if delta_cents == 0:
        raise InvalidInputError("Credit adjustment cannot be zero")
    row = conn.execute(
        "SELECT store_credit_cents FROM customers WHERE id = ?",
        (customer_id,)).fetchone()
    if row is None:
        raise CustomerNotFoundError(f"No customer with id {customer_id}")
    new_balance = row["store_credit_cents"] + delta_cents
    if new_balance < 0:
        from decimal import Decimal
        short = Decimal(-new_balance) / 100
        raise InsufficientCreditError(f"Not enough store credit (short by {short})")
    conn.execute(
        "UPDATE customers SET store_credit_cents = ? WHERE id = ?",
        (new_balance, customer_id))
    conn.execute(
        ''' INSERT INTO credit_events(customer_id, delta_cents, reason, sale_id)
            VALUES(?, ?, ?, ?) ''',
        (customer_id, delta_cents, reason or "", sale_id))
    return new_balance


def adjust_credit(conn, customer_id, delta_cents, reason="", sale_id=None):
    """Add (or spend, negative) store credit atomically. Returns new balance."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        balance = _apply_credit(conn, customer_id, delta_cents,
                                str(reason or "").strip()[:200], sale_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return balance


def credit_events(conn, customer_id, limit=50):
    cur = conn.cursor()
    cur.execute(
        ''' SELECT id, delta_cents, reason, sale_id, timestamp
            FROM credit_events WHERE customer_id = ?
            ORDER BY id DESC LIMIT ? ''',
        (customer_id, int(limit)))
    return [{
        "id": r["id"],
        "delta_cents": r["delta_cents"],
        "delta": round(r["delta_cents"] / 100, 2),
        "reason": r["reason"],
        "sale_id": r["sale_id"],
        "timestamp": r["timestamp"],
    } for r in cur.fetchall()]


def recent_sales(conn, customer_id, limit=20):
    cur = conn.cursor()
    cur.execute(
        ''' SELECT id, timestamp, total, status FROM sales
            WHERE customer_id = ? ORDER BY id DESC LIMIT ? ''',
        (customer_id, int(limit)))
    return [dict(r) for r in cur.fetchall()]
