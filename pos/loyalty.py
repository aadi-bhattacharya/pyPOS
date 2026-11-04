"""Transactional customer loyalty ledger for Indian-retail pyPOS.

Points are *not* money. Redeeming points creates existing store credit, which
is applied after GST invoice totals (no implicit tax discount). Configurable
rates are snapshotted in each event so changing policy never rewrites history.
"""
from . import settings as settings_mod
from .customers import CustomerNotFoundError, _apply_credit, CustomersError


class LoyaltyError(CustomersError):
    pass


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 1_000_000:
        raise LoyaltyError(f"{name} must be a positive integer (maximum 1,000,000)")
    return value


def _update(conn, customer_id, change, kind, reason, sale_id=None, allow_debt=False):
    """No commits; caller must own BEGIN IMMEDIATE transaction."""
    if not isinstance(change, int) or isinstance(change, bool) or change == 0:
        raise LoyaltyError("Loyalty points change must be a non-zero integer")
    row = conn.execute("SELECT loyalty_points FROM customers WHERE id=?", (customer_id,)).fetchone()
    if row is None:
        raise CustomerNotFoundError(f"No customer with id {customer_id}")
    result = row["loyalty_points"] + change
    if result < 0 and not allow_debt:
        raise LoyaltyError("Not enough available loyalty points")
    conn.execute("UPDATE customers SET loyalty_points=? WHERE id=?", (result, customer_id))
    conn.execute('''INSERT INTO loyalty_events
        (customer_id, delta_points, balance_after, event_type, reason, sale_id)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (customer_id, change, result, kind, reason[:240], sale_id))
    return result


def policy(conn):
    settings = settings_mod.get_settings(conn)
    return {
        "enabled": settings["loyalty_enabled"] == "true",
        "spend_paise": int(settings["loyalty_earn_spend_rupees"]) * 100,
        "earn_points": int(settings["loyalty_earn_points"]),
        "redeem_paise": int(settings["loyalty_redeem_paise"]),
    }


def award_on_sale(conn, customer_id, sale_id, eligible_paise):
    """Must run within checkout transaction; return number of points awarded."""
    cfg = policy(conn)
    if not cfg["enabled"] or not customer_id or eligible_paise <= 0:
        return 0
    earned = (eligible_paise // cfg["spend_paise"]) * cfg["earn_points"]
    if earned:
        _update(conn, customer_id, earned, "EARN",
                f"Sale #{sale_id}: {cfg['earn_points']} point(s) per "
                f"Rs {cfg['spend_paise'] // 100} eligible spend", sale_id)
    return earned


def reverse_for_return(conn, original_sale_id, return_sale_id, returned_paise, original_paise, kind):
    """Cumulative proportional reversal, including last-paise reconciliation.

    A historical earning must be reversed even if points were redeemed later;
    that can leave a temporary negative balance (debt) rather than blocking a
    legally valid customer refund. Future earnings offset this debt.
    """
    orig = conn.execute('''SELECT customer_id, COALESCE(SUM(delta_points), 0) AS earned
        FROM loyalty_events WHERE sale_id=? AND event_type='EARN' ''',
        (original_sale_id,)).fetchone()
    if not orig or not orig["earned"] or not orig["customer_id"]:
        return 0
    original_earned = int(orig["earned"])
    previously_reversed = conn.execute('''SELECT COALESCE(SUM(-delta_points), 0) AS reversed
        FROM loyalty_events WHERE event_type IN ('REFUND','VOID') AND
        sale_id IN (SELECT id FROM sales WHERE parent_sale_id=? AND status='REFUNDED')''',
        (original_sale_id,)).fetchone()["reversed"]
    # Also account for possible VOID event explicitly, though refund and void
    # are mutually exclusive in the POS.
    previously_reversed += conn.execute('''SELECT COALESCE(SUM(-delta_points),0) AS n
        FROM loyalty_events WHERE sale_id=? AND event_type='VOID' ''',
        (original_sale_id,)).fetchone()["n"]
    if kind == "VOID":
        reversal_target = original_earned
    else:
        previous_returns = conn.execute('''SELECT COALESCE(SUM(total),0) AS val
            FROM sales WHERE parent_sale_id=? AND status='REFUNDED' AND id != ?''',
            (original_sale_id, return_sale_id)).fetchone()["val"]
        from decimal import Decimal, ROUND_HALF_UP
        prev_paise = int((Decimal(str(previous_returns)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        refunded_total = min(original_paise, prev_paise + returned_paise)
        reversal_target = original_earned * refunded_total // max(1, original_paise)
    reversal = max(0, reversal_target - previously_reversed)
    if reversal:
        _update(conn, orig["customer_id"], -reversal, kind,
                f"{kind.title()} for sale #{original_sale_id}",
                return_sale_id if kind == 'REFUND' else original_sale_id, allow_debt=True)
    return reversal


def adjust(conn, customer_id, *, delta=None, set_to=None, reason=""):
    """Admin-only API service. Exactly one of delta / set_to must be supplied."""
    if (delta is None) == (set_to is None):
        raise LoyaltyError("Specify delta_points OR set_points")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 240:
        raise LoyaltyError("A reason (1–240 characters) is required")
    for x in (delta, set_to):
        if x is not None and (isinstance(x, bool) or not isinstance(x, int) or abs(x) > 1_000_000):
            raise LoyaltyError("Points must be an integer up to 1,000,000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT loyalty_points FROM customers WHERE id=?", (customer_id,)).fetchone()
        if row is None:
            raise CustomerNotFoundError(f"No customer with id {customer_id}")
        if set_to is not None:
            if set_to < 0:
                raise LoyaltyError("Cannot manually set a negative balance")
            change = set_to - row["loyalty_points"]
        else:
            change = delta
        if not change:
            raise LoyaltyError("No change to points balance")
        balance = _update(conn, customer_id, change, "ADMIN", reason.strip())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return balance


def redeem(conn, customer_id, points, reason="Loyalty points redeemed"):
    """Atomic redemption into existing store-credit wallet; not a tax discount."""
    _positive_int(points, "Points to redeem")
    cfg = policy(conn)
    if not cfg["enabled"]:
        raise LoyaltyError("Loyalty programme is disabled")
    value_paise = points * cfg["redeem_paise"]
    if value_paise > 100_000_000:
        raise LoyaltyError("Redemption exceeds the per-transaction limit")
    conn.execute("BEGIN IMMEDIATE")
    try:
        balance = _update(conn, customer_id, -points, "REDEEM",
                          f"{reason[:160]}: {points} points @ {cfg['redeem_paise']} paise/point")
        credit = _apply_credit(conn, customer_id, value_paise,
                              f"Loyalty redemption of {points} point(s)")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"loyalty_points": balance, "store_credit_cents": credit,
            "credited_paise": value_paise}


def events(conn, customer_id, limit=100):
    return [dict(r) for r in conn.execute('''SELECT id, delta_points, balance_after,
        event_type, reason, sale_id, timestamp FROM loyalty_events
        WHERE customer_id=? ORDER BY id DESC LIMIT ?''', (customer_id, limit))]
