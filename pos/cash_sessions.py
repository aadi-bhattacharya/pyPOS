from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from .catalog import InvalidInputError


class SessionError(Exception):
    """Base class for cash-session errors."""


class SessionAlreadyOpenError(SessionError):
    """Raised when opening a day that is already open."""


class NoOpenSessionError(SessionError):
    """Raised when closing a day with no open session."""


class SessionNotFoundError(SessionError):
    """Raised when a session id doesn't exist."""


MONEY_Q = Decimal("0.01")


def _money(v):
    return Decimal(str(v)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _cash_input(value, field):
    """Never close a day with NaN, negative cash or silently rounded paise."""
    if isinstance(value, bool):
        raise InvalidInputError(f"{field} must be a valid nonnegative amount")
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or amount < 0 or amount > 100_000_000 or
                amount != amount.quantize(MONEY_Q)):
            raise InvalidInputError(f"{field} must be a nonnegative amount with at most two decimals")
        return amount
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field} must be a valid nonnegative amount") from exc


def _round2(v):
    return float(_money(v))


def get_session(conn, session_id):
    r = conn.execute(
        "SELECT * FROM cash_sessions WHERE id = ?", (session_id,)).fetchone()
    if r is None:
        raise SessionNotFoundError(f"No cash session with id {session_id}")
    return dict(r)


def current_session(conn):
    r = conn.execute(
        "SELECT * FROM cash_sessions WHERE closed_at IS NULL "
        "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def open_session(conn, opening_float=0.0):
    opening_float = _cash_input(opening_float if opening_float is not None else 0, "Opening float")

    conn.execute("BEGIN IMMEDIATE")
    try:
        if current_session(conn) is not None:
            raise SessionAlreadyOpenError(
                "The day is already open — close it before starting a new one")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cash_sessions(opening_float) VALUES(?)",
            (float(opening_float),))
        session_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"session": get_session(conn, session_id),
            "report": session_report(conn, session_id)}


def close_session(conn, counted_cash, note=""):
    counted_cash = _cash_input(counted_cash, "Counted cash")
    conn.execute("BEGIN IMMEDIATE")
    try:
        session = current_session(conn)
        if session is None:
            raise NoOpenSessionError("No open day to close — open one first")
        report = session_report(conn, session["id"])
        difference = counted_cash - _money(report["expected_cash"])
        conn.execute(
            ''' UPDATE cash_sessions
                SET closed_at = CURRENT_TIMESTAMP, counted_cash = ?,
                    drawer_difference = ?, note = ?
                WHERE id = ? ''',
            (float(counted_cash), float(difference),
             str(note or "").strip()[:500], session["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"session": get_session(conn, session["id"]),
            "report": session_report(conn, session["id"])}


def list_sessions(conn, limit=60):
    rows = conn.execute(
        ''' SELECT id FROM cash_sessions ORDER BY id DESC LIMIT ? ''',
        (int(limit),)).fetchall()
    out = []
    for r in rows:
        s = get_session(conn, r["id"])
        rep = session_report(conn, s["id"])
        out.append({
            "id": s["id"],
            "opened_at": s["opened_at"],
            "closed_at": s["closed_at"],
            "opening_float": s["opening_float"],
            "counted_cash": s["counted_cash"],
            "drawer_difference": s["drawer_difference"],
            "note": s["note"],
            "net_revenue": rep["net_revenue"],
            "transactions": rep["transactions"],
        })
    return out


def session_report(conn, session_id):
    """X report while open; Z report once closed."""
    s = get_session(conn, session_id)
    end_ts = s["closed_at"] or "9999-12-31 23:59:59"
    lo, hi = s["opened_at"], end_ts

    cur = conn.cursor()

    def sales_in(status):
        cur.execute(
            ''' SELECT subtotal, discount_total, tax_total, total
                FROM sales WHERE status = ?
                  AND (cash_session_id = ? OR (cash_session_explicit = 0 AND cash_session_id IS NULL
                      AND timestamp >= ? AND timestamp < ?))''',
            (status, session_id, lo, hi))
        return [dict(r) for r in cur.fetchall()]

    completed = sales_in("COMPLETED")
    refunded = sales_in("REFUNDED")

    gross_sales = sum(r["subtotal"] for r in completed)
    discounts = sum(r["discount_total"] for r in completed)
    taxes = sum(r["tax_total"] for r in completed)
    refund_total = sum(r["total"] for r in refunded)
    net_revenue = sum(r["total"] for r in completed) - refund_total

    # items sold in completed sales of this window
    cur.execute(
        ''' SELECT COALESCE(SUM(si.quantity), 0) AS n FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE s.status = 'COMPLETED'
              AND (s.cash_session_id = ? OR (s.cash_session_explicit = 0 AND s.cash_session_id IS NULL
                     AND s.timestamp >= ? AND s.timestamp < ?))''', (session_id, lo, hi))
    items_sold = cur.fetchone()["n"]

    def tender_sums(status):
        cur.execute(
            ''' SELECT pm.method, COUNT(*) AS count,
                       COALESCE(SUM(pm.amount), 0) AS amount
                FROM payments pm JOIN sales s ON s.id = pm.sale_id
                WHERE s.status = ? AND (s.cash_session_id = ? OR
                    (s.cash_session_explicit = 0 AND s.cash_session_id IS NULL AND s.timestamp >= ? AND s.timestamp < ?))
                GROUP BY pm.method''', (status, session_id, lo, hi))
        return {r["method"]: {"count": r["count"], "amount": float(r["amount"])}
                for r in cur.fetchall()}

    taken = tender_sums("COMPLETED")
    returned = tender_sums("REFUNDED")

    def net(method):
        t, r = taken.get(method, {}), returned.get(method, {})
        return {
            "in": _round2(t.get("amount", 0.0)),
            "out": _round2(r.get("amount", 0.0)),
            "net": _round2(t.get("amount", 0.0) - r.get("amount", 0.0)),
        }

    cash, card, upi = net("CASH"), net("CARD"), net("UPI")

    cur.execute(
        ''' SELECT COALESCE(SUM(ce.delta_cents), 0) AS c FROM credit_events ce
            JOIN sales s ON s.id = ce.sale_id
            WHERE ce.delta_cents < 0
              AND (s.cash_session_id = ? OR (s.cash_session_explicit = 0 AND s.cash_session_id IS NULL
                     AND s.timestamp >= ? AND s.timestamp < ?))''', (session_id, lo, hi))
    credit_spent_cents = -cur.fetchone()["c"]

    adjustments = [dict(row) for row in conn.execute(
        "SELECT id, kind, amount_cents, reason, reference, recorded_at FROM cash_movements "
        "WHERE cash_session_id=? ORDER BY id", (session_id,))]
    moved_in = sum(row["amount_cents"] for row in adjustments if row["kind"] == "CASH_IN")
    moved_out = sum(row["amount_cents"] for row in adjustments if row["kind"] != "CASH_IN")
    expected_cash = float(s["opening_float"]) + cash["in"] - cash["out"] + (moved_in - moved_out) / 100

    return {
        "session_id": s["id"],
        "opened_at": s["opened_at"],
        "closed_at": s["closed_at"],
        "is_open": s["closed_at"] is None,
        "opening_float": round(float(s["opening_float"]), 2),
        "transactions": len(completed),
        "items_sold": items_sold,
        "gross_sales": _round2(gross_sales),
        "discounts": _round2(discounts),
        "taxes": _round2(taxes),
        "net_revenue": _round2(net_revenue),
        "refund_count": len(refunded),
        "refund_total": _round2(refund_total),
        "cash_in": cash["in"],
        "cash_out": cash["out"],
        "cash_net": cash["net"],
        "card_net": card["net"],
        "upi_net": upi["net"],
        "credit_spent": _round2(Decimal(credit_spent_cents) / 100),
        "drawer_cash_in": moved_in / 100,
        "drawer_cash_out": moved_out / 100,
        "cash_movements": [{**row, "amount": row["amount_cents"] / 100}
                           for row in adjustments],
        "expected_cash": _round2(expected_cash),
        "counted_cash": _round2(s["counted_cash"]) if s["counted_cash"] is not None else None,
        "difference": _round2(s["drawer_difference"]) if s["drawer_difference"] is not None else None,
        "note": s["note"] or "",
    }


def record_cash_movement(conn, kind, amount, reason, reference=""):
    """Record physical money entering/leaving an open till. Never a sale or refund."""
    if kind not in ("CASH_IN", "CASH_OUT", "BANK_DEPOSIT", "PETTY_CASH"):
        raise InvalidInputError("Choose a valid drawer movement type")
    try:
        value = _money(amount)
    except Exception as exc:
        raise InvalidInputError("Invalid cash movement amount") from exc
    if not value.is_finite() or value <= 0 or value > 100000000:
        raise InvalidInputError("Cash movement must be positive and finite")
    reason = str(reason or "").strip()
    reference = str(reference or "").strip()
    if not reason or len(reason) > 250 or len(reference) > 120:
        raise InvalidInputError("Enter a reason (up to 250 characters) and a short reference")
    conn.execute("BEGIN IMMEDIATE")
    try:
        session = current_session(conn)
        if not session:
            raise NoOpenSessionError("Start Day before recording cash movements")
        cur = conn.execute("""INSERT INTO cash_movements
            (cash_session_id, kind, amount_cents, reason, reference)
            VALUES(?,?,?,?,?)""", (session["id"], kind, int(value*100), reason, reference))
        movement_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"id": movement_id, "session_id": session["id"],
            "report": session_report(conn, session["id"])}
