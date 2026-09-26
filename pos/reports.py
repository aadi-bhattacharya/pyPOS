from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .sales import get_sale, _d, _money


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _period_bounds(days):
    """UTC timestamp strings covering the last `days` local days (including today)."""
    tz = _local_tz()
    now = datetime.now(tz)
    start_local = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_local = now.replace(hour=23, minute=59, second=59, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    start_utc = start_local.astimezone(timezone.utc).strftime(fmt)
    end_utc = end_local.astimezone(timezone.utc).strftime(fmt)
    return start_utc, end_utc


def summary(conn, days=14, low_stock_threshold=5):
    start_utc, end_utc = _period_bounds(days)

    cur = conn.cursor()
    cur.execute(
        ''' SELECT id, timestamp, subtotal, tax_total, discount_total, total, status,
                   parent_sale_id
            FROM sales
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp ''',
        (start_utc, end_utc),
    )
    sales_rows = [dict(r) for r in cur.fetchall()]

    # ---- daily series (completed minus refunds), bucketed by LOCAL date ----
    tz = _local_tz()
    by_day = {}
    for r in sales_rows:
        day = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).astimezone(tz).date().isoformat()
        d = by_day.setdefault(day, {"revenue": 0.0, "refunds": 0.0, "txns": 0, "refund_count": 0})
        if r["status"] == "COMPLETED":
            d["revenue"] += r["total"]
            d["txns"] += 1
        elif r["status"] == "REFUNDED":
            d["revenue"] -= r["total"]
            d["refunds"] += r["total"]
            d["refund_count"] += 1

    series = []
    for i in range(days):
        day = (datetime.now(tz) - timedelta(days=days - 1 - i)).date().isoformat()
        d = by_day.get(day, {"revenue": 0.0, "refunds": 0.0, "txns": 0, "refund_count": 0})
        series.append({"date": day, **{k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()}})

    # ---- today's headline numbers (completed only) ----
    today = datetime.now(tz).date().isoformat()
    today_rows = [
        r for r in sales_rows
        if r["status"] == "COMPLETED" and
        datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc).astimezone(tz).date().isoformat() == today
    ]
    today_revenue = round(sum(r["total"] for r in today_rows), 2)
    cur.execute(
        ''' SELECT COALESCE(SUM(si.quantity), 0) AS items FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE s.status = 'COMPLETED' AND s.timestamp BETWEEN ? AND ? ''',
        (start_utc, end_utc),
    )
    period_items_sold = cur.fetchone()["items"]

    # ---- top products in period (completed only) ----
    cur.execute(
        ''' SELECT g.name AS group_name, p.SKU, p.id AS product_id,
                   SUM(si.quantity) AS qty,
                   ROUND(SUM(si.unit_price * si.quantity), 2) AS revenue
            FROM sale_items si
            JOIN sales s ON s.id = si.sale_id
            JOIN products p ON p.id = si.product_id
            LEFT JOIN product_groups g ON g.id = p.group_id
            WHERE s.status = 'COMPLETED' AND s.timestamp BETWEEN ? AND ?
            GROUP BY p.id ORDER BY revenue DESC LIMIT 10 ''',
        (start_utc, end_utc),
    )
    top_products = [dict(r) for r in cur.fetchall()]

    # ---- payment mix (completed only) ----
    cur.execute(
        ''' SELECT method, COUNT(*) AS count, ROUND(SUM(amount), 2) AS amount
            FROM payments pm JOIN sales s ON s.id = pm.sale_id
            WHERE s.status = 'COMPLETED' AND s.timestamp BETWEEN ? AND ?
            GROUP BY method ORDER BY amount DESC ''',
        (start_utc, end_utc),
    )
    payment_mix = [dict(r) for r in cur.fetchall()]

    # ---- low stock ----
    cur.execute(
        ''' SELECT p.id, p.SKU, p.quantity, g.name AS group_name
            FROM products p LEFT JOIN product_groups g ON g.id = p.group_id
            WHERE p.quantity <= ? ORDER BY p.quantity ASC, g.name LIMIT 50 ''',
        (low_stock_threshold,),
    )
    low_stock = [dict(r) for r in cur.fetchall()]

    refund_rows = [r for r in sales_rows if r["status"] == "REFUNDED"]
    return {
        "days": days,
        "today_revenue": today_revenue,
        "today_transactions": len(today_rows),
        "today_avg_basket": round(today_revenue / len(today_rows), 2) if today_rows else 0.0,
        "series": series,
        "top_products": top_products,
        "payment_mix": payment_mix,
        "low_stock": low_stock,
        "period_items_sold": period_items_sold,
        "period_refund_count": len(refund_rows),
        "period_refund_total": round(sum(r["total"] for r in refund_rows), 2),
    }
