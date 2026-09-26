import json
from datetime import datetime

from .catalog import InvalidInputError


class ParkedError(Exception):
    """Base class for parked-cart errors."""


class ParkedNotFoundError(ParkedError):
    """Raised when a parked cart id doesn't exist."""


MAX_ITEMS = 500
MAX_LABEL = 120


def _normalize_items(items):
    if not isinstance(items, list) or not items:
        raise InvalidInputError("A parked order needs at least one item")
    merged = {}
    order = []
    for it in items:
        try:
            pid = int(it["product_id"])
            qty = int(it["quantity"])
        except (KeyError, TypeError, ValueError):
            raise InvalidInputError("Each item needs product_id and quantity")
        if qty <= 0:
            raise InvalidInputError("Item quantity must be at least 1")
        if pid in merged:
            merged[pid] += qty
        else:
            merged[pid] = qty
            order.append(pid)
    if len(order) > MAX_ITEMS:
        raise InvalidInputError(f"Too many lines (max {MAX_ITEMS})")
    return [{"product_id": pid, "quantity": merged[pid]} for pid in order]


def _resolve_products(conn, items):
    """Attach live product data to a stored item list."""
    if not items:
        return []
    marks = ",".join("?" * len(items))
    cur = conn.cursor()
    cur.execute(
        f''' SELECT p.id, p.SKU, p.price, p.tax_rate, p.quantity,
                    g.name AS group_name
             FROM products p LEFT JOIN product_groups g ON g.id = p.group_id
             WHERE p.id IN ({marks}) ''',
        [it["product_id"] for it in items],
    )
    rows = {r["id"]: dict(r) for r in cur.fetchall()}
    out = []
    for it in items:
        row = rows.get(it["product_id"])
        if row is None:
            continue
        out.append({
            **it,
            "name": row["group_name"] or f"Product {row['id']}",
            "SKU": row["SKU"],
            "price": row["price"],
            "tax_rate": row["tax_rate"],
            "in_stock": row["quantity"],
        })
    return out


def park_cart(conn, items, label=None, discount_mode=None, discount_value=0,
              customer_id=None):
    items = _normalize_items(items)
    ids = [it["product_id"] for it in items]
    marks = ",".join("?" * len(ids))
    found = {r[0] for r in conn.execute(
        f"SELECT id FROM products WHERE id IN ({marks})", ids).fetchall()}
    missing = [pid for pid in ids if pid not in found]
    if missing:
        raise InvalidInputError(f"Unknown product id(s): {', '.join(map(str, missing))}")

    label = str(label or "").strip()[:MAX_LABEL]
    if not label:
        label = "Order " + datetime.now().strftime("%H:%M")

    if discount_mode not in (None, "percent", "amount"):
        raise InvalidInputError("Discount mode must be percent or amount")
    try:
        discount_value = max(0.0, float(discount_value or 0))
    except (TypeError, ValueError):
        raise InvalidInputError("Invalid discount value")

    if customer_id is not None:
        row = conn.execute("SELECT id FROM customers WHERE id = ?",
                           (int(customer_id),)).fetchone()
        if row is None:
            raise InvalidInputError(f"No customer with id {customer_id}")

    cur = conn.cursor()
    cur.execute(
        ''' INSERT INTO parked_sales(label, items_json, discount_mode,
                                     discount_value, customer_id)
            VALUES(?, ?, ?, ?, ?) ''',
        (label, json.dumps(items), discount_mode, discount_value, customer_id),
    )
    conn.commit()
    return get_parked(conn, cur.lastrowid)


def _row_to_dict(conn, r):
    items = _resolve_products(conn, json.loads(r["items_json"]))
    est_total = round(sum(i["price"] * i["quantity"] for i in items), 2)
    customer_name = None
    if r["customer_id"]:
        crow = conn.execute("SELECT name FROM customers WHERE id = ?",
                            (r["customer_id"],)).fetchone()
        customer_name = crow["name"] if crow else None
    return {
        "id": r["id"],
        "label": r["label"],
        "created_at": r["created_at"],
        "discount_mode": r["discount_mode"],
        "discount_value": r["discount_value"],
        "customer_id": r["customer_id"],
        "customer_name": customer_name,
        "items": items,
        "item_count": sum(i["quantity"] for i in items),
        "est_total": est_total,
    }


def get_parked(conn, parked_id):
    r = conn.execute(
        "SELECT * FROM parked_sales WHERE id = ?", (parked_id,)).fetchone()
    if r is None:
        raise ParkedNotFoundError(f"Parked order #{parked_id} was already handled")
    return _row_to_dict(conn, r)


def list_parked(conn):
    rows = conn.execute(
        "SELECT * FROM parked_sales ORDER BY id DESC").fetchall()
    return [_row_to_dict(conn, r) for r in rows]


def delete_parked(conn, parked_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM parked_sales WHERE id = ?", (parked_id,))
    if cur.rowcount == 0:
        raise ParkedNotFoundError(f"Parked order #{parked_id} was already handled")
    conn.commit()


def count_parked(conn):
    return conn.execute("SELECT COUNT(*) FROM parked_sales").fetchone()[0]
