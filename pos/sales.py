import json
from decimal import Decimal, ROUND_HALF_UP
from .india import next_invoice_number
from datetime import datetime, timezone
from .india import IST

from .catalog import (
    ProductNotFoundError,
    InvalidInputError,
    InsufficientStockError,
)
from .customers import CustomerNotFoundError, _apply_credit
from . import settings as settings_mod
from . import loyalty

MONEY_Q = Decimal("0.01")
PAYMENT_METHODS = ("CASH", "CARD", "UPI")


class SalesError(Exception):
    """Base class for sale-related errors."""


class SaleNotFoundError(SalesError):
    """Raised when a sale id doesn't exist."""


class AlreadyProcessedError(SalesError):
    """Raised when a sale has already been refunded or voided."""


class InsufficientPaymentError(SalesError):
    """Raised when payment total does not suffice the total amount."""


class InvalidPaymentError(SalesError):
    """Raised when a payment line is malformed or unsupported."""


class InvalidSaleError(SalesError):
    """Raised when a cart is malformed."""


def _d(value):
    return Decimal(str(value))


def _money(value):
    return _d(value).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _cents(value):
    """Decimal currency units -> integer paise (half-up)."""
    return int(_money(value) * 100)


def compute_totals(lines, discount_mode=None, discount_value=None, tax_inclusive=False):
    """
    Compute sale totals for lines of {price, tax_rate, quantity}.

    Tax rates are percent points (5 means 5%). Any order-level discount is
    allocated across lines proportionally to their gross value, in exact
    integer cents, so line allocations always sum to the discount total.

    Returns dict:
        lines: [{gross_cents, discount_cents, net_cents, tax_cents}]
        subtotal_gross, discount_total, subtotal_net, tax_total, total  (Decimals)
    """
    if not lines:
        raise InvalidSaleError("Sale must contain at least one item")

    prepared = []
    for ln in lines:
        qty = int(ln["quantity"])
        price = _d(ln["price"])
        rate_pct = _d(ln.get("tax_rate") or 0)
        if qty <= 0:
            raise InvalidSaleError("Item quantity must be at least 1")
        if price < 0:
            raise InvalidSaleError("Item price cannot be negative")
        prepared.append({
            "qty": qty,
            "gross_cents": _cents(price * qty),
            "rate_fraction": rate_pct / Decimal(100),
        })

    gross_total_cents = sum(l["gross_cents"] for l in prepared)

    # Order-level discount -> integer cents, clamped to [0, gross].
    if discount_mode == "percent":
        pct = _d(discount_value or 0)
        if pct < 0:
            raise InvalidSaleError("Discount cannot be negative")
        disc_cents = _cents(gross_total_cents * pct / Decimal(10000))
    elif discount_mode == "amount":
        disc_cents = _cents(_d(discount_value or 0))
    else:
        disc_cents = 0
    disc_cents = max(0, min(disc_cents, gross_total_cents))

    # Proportional cent-exact allocation; last line absorbs rounding remainder.
    result_lines = []
    allocated = 0
    for i, ln in enumerate(prepared):
        if i < len(prepared) - 1 and gross_total_cents > 0:
            share = disc_cents * ln["gross_cents"] // gross_total_cents
        else:
            share = disc_cents - allocated
        allocated += share
        net_cents = ln["gross_cents"] - share
        tax_cents = int(_money(
            (Decimal(net_cents) / 100 * ln["rate_fraction"] /
             (Decimal(1) + ln["rate_fraction"])) if tax_inclusive
            else (Decimal(net_cents) / 100 * ln["rate_fraction"])
        ) * 100)
        result_lines.append({
            **ln,
            "discount_cents": share,
            "net_cents": net_cents,
            "tax_cents": tax_cents,
        })

    tax_total_cents = sum(l["tax_cents"] for l in result_lines)
    return {
        "lines": result_lines,
        "subtotal_gross": Decimal(gross_total_cents) / 100,
        "discount_total": Decimal(disc_cents) / 100,
        "subtotal_net": Decimal(gross_total_cents - disc_cents) / 100,
        "tax_total": Decimal(tax_total_cents) / 100,
        "total": Decimal(gross_total_cents - disc_cents +
                         (0 if tax_inclusive else tax_total_cents)) / 100,
    }


def _normalize_items(conn, items):
    """Merge duplicate products, validate shape, fetch live product data."""
    if not items:
        raise InvalidSaleError("Cart is empty")
    merged = {}
    order = []
    for it in items:
        try:
            pid = int(it["product_id"])
            qty = int(it["quantity"])
        except (KeyError, TypeError, ValueError):
            raise InvalidSaleError("Each item needs product_id and quantity")
        if qty <= 0:
            raise InvalidSaleError("Item quantity must be at least 1")
        if pid in merged:
            merged[pid]["quantity"] += qty
        else:
            merged[pid] = {"product_id": pid, "quantity": qty}
            order.append(pid)

    cur = conn.cursor()
    marks = ",".join("?" * len(order))
    cur.execute(
        f''' SELECT p.id, p.SKU, p.price, p.tax_rate, p.quantity, p.hsn_code, p.unit_code, g.name AS group_name
             FROM products p LEFT JOIN product_groups g ON g.id = p.group_id
             WHERE p.id IN ({marks}) ''',
        order,
    )
    rows = {r["id"]: dict(r) for r in cur.fetchall()}

    normalized = []
    for pid in order:
        row = rows.get(pid)
        if row is None:
            raise ProductNotFoundError(f"No product with id {pid}")
        item = merged[pid]
        if item["quantity"] > row["quantity"]:
            name = row["group_name"] or f"Product {pid}"
            raise InsufficientStockError(f"{name}: only {row['quantity']} left in stock")
        attributes = cur.execute(
            """SELECT attribute_name, attribute_value FROM product_attributes
               WHERE product_id = ? ORDER BY attribute_name COLLATE NOCASE""", (pid,)).fetchall()
        variant = ", ".join(f"{a['attribute_name']}: {a['attribute_value']}" for a in attributes)
        group_name = row["group_name"] or f"Product {pid}"
        normalized.append({**item, **{
            "description": f"{group_name} ({variant})" if variant else group_name,
            "SKU": row["SKU"],
            "price": row["price"],
            "tax_rate": row["tax_rate"],
            "group_name": row["group_name"],
            "hsn_code": row["hsn_code"],
            "unit_code": row["unit_code"],
        }})
    return normalized


def _normalize_payments(payments, total, currency_note=True):
    """
    Validate tendered payments. Returns (applied_rows, change).
    Applied amounts sum exactly to `total`; change is returned on cash only.
    """
    if not payments:
        raise InsufficientPaymentError("No payment provided")
    total_cents = _cents(total)
    tendered_cents = 0
    clean = []
    for p in payments:
        method = str(p.get("method", "")).upper()
        if method not in PAYMENT_METHODS:
            raise InvalidPaymentError(f"Unsupported payment method: {p.get('method')}")
        amt = _money(p.get("amount") or 0)
        if amt < 0:
            raise InvalidPaymentError("Payment amount cannot be negative")
        if amt == 0:
            continue
        tendered_cents += _cents(amt)
        clean.append({"method": method, "amount_cents": _cents(amt)})

    if tendered_cents < total_cents:
        short = (Decimal(total_cents - tendered_cents) / 100).quantize(MONEY_Q)
        raise InsufficientPaymentError(f"Payment is short by {short}")

    change = Decimal(tendered_cents - total_cents) / 100

    # Apply non-cash tenders first (they cannot produce change); cash goes
    # last and absorbs both its share of the balance and any change due.
    ordered = sorted(clean, key=lambda p: p["method"] == "CASH")
    applied = []
    remaining = total_cents
    for p in ordered:
        take = min(p["amount_cents"], remaining)
        leftover = p["amount_cents"] - take
        if leftover > 0 and p["method"] != "CASH":
            raise InvalidPaymentError(
                f"Overpayment in {p['method']} cannot be returned as change"
            )
        if take > 0:
            applied.append({
                "method": p["method"],
                "applied": Decimal(take) / 100,
                "tendered": Decimal(p["amount_cents"]) / 100,
            })
            remaining -= take
    return applied, change


def _refund_line_money(row, k, qty_orig, tax_inclusive=False, already_qty=0):
    """Cumulative cent allocation for partial refunds.

    Take the difference between total allocations at previous/new quantities,
    rather than rounding independent refunds. Consequently repeated partial
    refunds reconcile to the original recorded discount and tax to the cent.
    """
    gross_c = _cents(_d(row["unit_price"]) * qty_orig)
    disc_c = int(_d(row["discount_cents"] or 0))
    recorded_tax_c = int(_d(row["tax_cents"] or 0))

    def apportioned(cents, quantity):
        return int(_money(Decimal(cents) * quantity / qty_orig))

    before = already_qty
    after = before + k
    r_gross = apportioned(gross_c, after) - apportioned(gross_c, before)
    r_disc = apportioned(disc_c, after) - apportioned(disc_c, before)
    r_net = r_gross - r_disc
    if recorded_tax_c:
        r_tax = apportioned(recorded_tax_c, after) - apportioned(recorded_tax_c, before)
    else:
        # A legacy invoice may pre-date recorded tax cents. Reconstruct only
        # its tax rate's share; newly recorded zero-tax lines remain zero.
        rate = _d(row["tax_rate_at_sale"]) / 100
        full_net = gross_c - disc_c
        fraction = rate / (1 + rate) if tax_inclusive else rate
        tax_estimate = int(_money(Decimal(full_net) / 100 * fraction) * 100)
        r_tax = apportioned(tax_estimate, after) - apportioned(tax_estimate, before)
    return r_gross, r_disc, r_net, r_tax


def _split_refund(total, parts):
    """
    Split `total` integer cents across `parts` (original payment amounts in
    cents) proportionally; last part absorbs the rounding remainder so the
    shares always sum to `total`.
    """
    parts = [max(Decimal(0), _d(p)) for p in parts]
    total_orig = sum(parts)
    if total_orig <= 0 or total <= 0:
        return [0] * len(parts)
    shares = []
    allocated = 0
    for i, c in enumerate(parts):
        if i < len(parts) - 1:
            s = int(Decimal(total) * c / total_orig)
            allocated += s
        else:
            s = total - allocated
        shares.append(s)
    return shares


def checkout(conn, items, payments=None, discount_mode=None, discount_value=None,
             customer_id=None, use_credit=False, supply_type="intra", place_of_supply=""):
    """
    Complete a sale atomically: validate stock, recompute all money server-side,
    insert sale + items + payments, decrement inventory (via trigger), commit.

    When `customer_id` is given the sale is attached to that customer; with
    `use_credit` any available store credit is spent first and the rest of the
    balance due must be covered by `payments`.
    """
    if supply_type not in ("intra", "inter"):
        raise InvalidSaleError("Supply type must be intra or inter-state")
    place_of_supply = str(place_of_supply or "").strip()
    if len(place_of_supply) > 120:
        raise InvalidSaleError("Place of supply is too long")
    conn.execute("BEGIN IMMEDIATE")
    try:
        normalized = _normalize_items(conn, items)
        cfg_checkout = settings_mod.get_settings(conn)
        tax_inclusive = cfg_checkout.get("price_tax_mode") == "inclusive"
        totals = compute_totals(normalized, discount_mode, discount_value,
                                tax_inclusive=tax_inclusive)

        customer = None
        if customer_id is not None:
            cur = conn.cursor()
            cur.execute(
                ''' SELECT id, name, store_credit_cents, gstin, billing_address FROM customers
                    WHERE id = ? ''', (int(customer_id),))
            row = cur.fetchone()
            if row is None:
                raise CustomerNotFoundError(f"No customer with id {customer_id}")
            customer = dict(row)

        credit_cents = 0
        if use_credit:
            if customer is None:
                raise InvalidSaleError("Store credit requires a customer on the sale")
            total_cents = _cents(totals["total"])
            credit_cents = min(int(customer["store_credit_cents"]), total_cents)
        due = totals["total"] - Decimal(credit_cents) / 100

        if payments:
            applied_payments, change = _normalize_payments(payments, due)
        elif due > 0:
            raise InsufficientPaymentError(
                f"Payment is short by {due.quantize(MONEY_Q)}")
        else:
            # fully covered by store credit
            applied_payments, change = [], Decimal(0)

        cfg = settings_mod.get_settings(conn)
        if supply_type == "inter" and not place_of_supply:
            raise InvalidSaleError("Enter the inter-state place of supply")
        if supply_type == "intra":
            place_of_supply = cfg.get("store_state") or ""
        # Tax allocation and business details are captured at checkout; editing
        # Settings or customer records must not rewrite old invoices.
        tax_cfg = dict(cfg)
        if cfg.get("store_gstin"):
            tax_cfg.update({"tax_breakup": "true", "tax_comp1_enabled": "true",
                            "tax_comp1_name": "IGST" if supply_type == "inter" else "CGST",
                            "tax_comp1_share": "100" if supply_type == "inter" else "50",
                            "tax_comp2_enabled": "false" if supply_type == "inter" else "true",
                            "tax_comp2_name": cfg.get("store_local_tax") or "SGST",
                            "tax_comp2_share": "50"})
        tax_items = [{"tax_rate_at_sale": src["tax_rate"], "tax_cents": tot["tax_cents"]}
                     for src, tot in zip(normalized, totals["lines"])]
        invoice_snapshot = {
            "store_name": cfg.get("store_name") or "",
            "store_address": cfg.get("store_address") or "",
            "store_phone": cfg.get("store_phone") or "",
            "store_gstin": cfg.get("store_gstin") or "",
            "store_state": cfg.get("store_state") or "",
            "store_state_code": cfg.get("store_state_code") or "",
            "store_fssai": cfg.get("store_fssai") or "",
            "store_upi_id": cfg.get("store_upi_id") or "",
            "receipt_heading": cfg.get("receipt_heading") or "Retail receipt",
            "currency": cfg.get("currency") or "₹",
            "tax_label": cfg.get("tax_label") or "GST",
            "receipt_footer": cfg.get("receipt_footer") or "",
            "supply_type": supply_type,
            "price_tax_mode": "inclusive" if tax_inclusive else "exclusive",
            "place_of_supply": place_of_supply,
            "customer_name": customer.get("name") if customer else "",
            "customer_gstin": customer.get("gstin") if customer else "",
            "customer_address": customer.get("billing_address") if customer else "",
            "tax_breakup": _tax_breakup(conn, tax_items, tax_cfg),
            "tax_configuration": {
                k: tax_cfg.get(k) for k in ("tax_breakup", "tax_comp1_enabled",
                "tax_comp1_name", "tax_comp1_share", "tax_comp2_enabled",
                "tax_comp2_name", "tax_comp2_share")
            },
        }
        invoice_number = next_invoice_number(conn)
        cur = conn.cursor()
        cur.execute(
            ''' INSERT INTO sales(subtotal, tax_total, discount_total, total,
                                  status, customer_id, invoice_number, invoice_snapshot)
                VALUES(?, ?, ?, ?, 'COMPLETED', ?, ?, ?) ''',
            (float(totals["subtotal_gross"]), float(totals["tax_total"]),
             float(totals["discount_total"]), float(totals["total"]),
             customer["id"] if customer else None, invoice_number,
             json.dumps(invoice_snapshot, ensure_ascii=False)),
        )
        sale_id = cur.lastrowid

        cents_by_pid = {ln_src["product_id"]: ln_tot for ln_src, ln_tot in
                        zip(normalized, totals["lines"])}
        for src_line in normalized:
            line_cents = cents_by_pid[src_line["product_id"]]
            cur.execute(
                ''' INSERT INTO sale_items(sale_id, product_id, quantity,
                                           unit_price, tax_rate_at_sale,
                                           discount_cents, tax_cents, hsn_at_sale, unit_at_sale,
                                           description_at_sale, sku_at_sale)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ''',
                (sale_id, src_line["product_id"], src_line["quantity"],
                 float(src_line["price"]), float(src_line["tax_rate"]),
                 line_cents["discount_cents"], line_cents["tax_cents"],
                 src_line["hsn_code"], src_line["unit_code"],
                 src_line["description"], src_line["SKU"]),
            )

        for p in applied_payments:
            cur.execute(
                ''' INSERT INTO payments(sale_id, method, amount, is_partial)
                    VALUES(?, ?, ?, 0) ''',
                (sale_id, p["method"], float(p["applied"])),
            )

        if credit_cents > 0:
            _apply_credit(conn, customer["id"], -credit_cents,
                          f"Spent on sale #{sale_id}", sale_id=sale_id)

        loyalty.award_on_sale(
            conn, customer["id"] if customer else None, sale_id,
            max(0, _cents(totals["total"]) - credit_cents))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    receipt = get_sale_detail(conn, sale_id)
    receipt["change"] = float(change)
    if credit_cents > 0:
        receipt["store_credit_used"] = round(credit_cents / 100, 2)
    return receipt


def get_sale(conn, sale_id):
    cur = conn.cursor()
    cur.execute(''' SELECT * FROM sales WHERE id = ? ''', (sale_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _tax_breakup(conn, item_rows, cfg=None):
    """
    Split each tax rate into receipt components (e.g. 5% GST -> CGST 2.5% +
    SGST 2.5%), configured in Settings. Returns [{name, rate, amount}] or []
    when the breakup is disabled. Cent-exact: components always sum to the
    sale's tax total.
    """
    cfg = cfg if cfg is not None else settings_mod.get_settings(conn)
    if cfg.get("tax_breakup") != "true":
        return []

    comps = []
    for i in (1, 2):
        if cfg.get(f"tax_comp{i}_enabled") == "true":
            name = (cfg.get(f"tax_comp{i}_name") or "").strip() or f"Tax {i}"
            try:
                share = float(cfg.get(f"tax_comp{i}_share") or 0)
            except ValueError:
                share = 0
            if share > 0:
                comps.append({"name": name[:20], "share": share})
    if not comps:
        return []

    # Aggregate recorded tax cents by rate (REFUNDED children included —
    # their breakup mirrors what the customer was charged).
    by_rate = {}
    for it in item_rows:
        rate = round(float(it["tax_rate_at_sale"]), 4)
        by_rate[rate] = by_rate.get(rate, 0) + int(it.get("tax_cents") or 0)

    out = []
    for rate in sorted(by_rate):
        total_cents = by_rate[rate]
        if total_cents <= 0:
            continue
        shares = _split_refund(total=total_cents,
                               parts=[c["share"] for c in comps])
        for comp, cents in zip(comps, shares):
            if cents > 0:
                out.append({
                    "name": comp["name"],
                    "rate": round(rate * comp["share"] / 100.0, 4),
                    "amount": round(cents / 100, 2),
                })
    return out


def get_sale_detail(conn, sale_id):
    sale = get_sale(conn, sale_id)
    if sale is None:
        raise SaleNotFoundError(f"No sale with id {sale_id}")

    cur = conn.cursor()
    cur.execute(
        ''' SELECT si.product_id, si.quantity, si.unit_price, si.tax_rate_at_sale,
                   si.tax_cents, si.hsn_at_sale, si.unit_at_sale,
                   si.description_at_sale, si.sku_at_sale,
                   g.name AS group_name, p.SKU
            FROM sale_items si
            JOIN products p ON p.id = si.product_id
            LEFT JOIN product_groups g ON g.id = p.group_id
            WHERE si.sale_id = ?
            ORDER BY si.id ''',
        (sale_id,),
    )
    item_rows = [dict(r) for r in cur.fetchall()]

    # How much of each product has already been returned by refunds of this sale.
    cur.execute(
        ''' SELECT csi.product_id, SUM(csi.quantity) AS refunded
            FROM sales c JOIN sale_items csi ON csi.sale_id = c.id
            WHERE c.parent_sale_id = ? AND c.status = 'REFUNDED'
            GROUP BY csi.product_id ''',
        (sale_id,),
    )
    refunded_by_pid = {r["product_id"]: r["refunded"] for r in cur.fetchall()}

    ids = [r["product_id"] for r in item_rows]
    attrs_by_product = {}
    if ids:
        marks = ",".join("?" * len(ids))
        cur.execute(
            f''' SELECT product_id, attribute_name, attribute_value
                 FROM product_attributes WHERE product_id IN ({marks})
                 ORDER BY attribute_name COLLATE NOCASE ''',
            ids,
        )
        for r in cur.fetchall():
            attrs_by_product.setdefault(r["product_id"], []).append(
                f"{r['attribute_name']}: {r['attribute_value']}"
            )

    items = []
    for r in item_rows:
        variant = ", ".join(attrs_by_product.get(r["product_id"], []))
        name = r["group_name"] or f"Product {r['product_id']}"
        display_name = r["description_at_sale"] or (f"{name} ({variant})" if variant else name)
        line_gross = _money(_d(r["unit_price"]) * r["quantity"])
        refunded_qty = min(refunded_by_pid.get(r["product_id"], 0), r["quantity"])
        items.append({
            "product_id": r["product_id"],
            "name": display_name,
            "SKU": r["sku_at_sale"] or r["SKU"],
            "hsn_code": r["hsn_at_sale"],
            "unit_code": r["unit_at_sale"],
            "quantity": r["quantity"],
            "unit_price": float(r["unit_price"]),
            "tax_rate": float(r["tax_rate_at_sale"]),
            "line_total": float(line_gross),
            "refunded_qty": refunded_qty,
            "refundable_qty": max(0, r["quantity"] - refunded_qty),
        })

    cur.execute(
        ''' SELECT method, amount, is_partial FROM payments
            WHERE sale_id = ? ORDER BY id ''',
        (sale_id,),
    )
    payments = [
        {"method": r["method"], "amount": float(r["amount"]), "is_partial": bool(r["is_partial"])}
        for r in cur.fetchall()
    ]

    cur.execute(
        ''' SELECT id, status FROM sales WHERE parent_sale_id = ? ''', (sale_id,)
    )
    children = [dict(r) for r in cur.fetchall()]

    cur.execute(
        ''' SELECT COALESCE(SUM(-delta_cents), 0) AS c FROM credit_events
            WHERE sale_id = ? AND delta_cents < 0 ''', (sale_id,))
    credit_used = cur.fetchone()["c"]
    cur.execute(
        """SELECT COALESCE(SUM(delta_cents), 0) AS c FROM credit_events
           WHERE sale_id = ? AND delta_cents > 0""", (sale_id,))
    credit_returned = cur.fetchone()["c"] if sale["status"] == "REFUNDED" else 0

    customer_name = None
    if sale["customer_id"]:
        cur.execute("SELECT name FROM customers WHERE id = ?", (sale["customer_id"],))
        crow = cur.fetchone()
        customer_name = crow["name"] if crow else None

    loyalty_rows = cur.execute('''SELECT event_type, delta_points FROM loyalty_events
        WHERE sale_id=? AND event_type IN ('EARN','REFUND','VOID')''', (sale_id,)).fetchall()
    points_earned = sum(r["delta_points"] for r in loyalty_rows if r["event_type"] == "EARN")
    points_reversed = -sum(r["delta_points"] for r in loyalty_rows if r["event_type"] in ("REFUND", "VOID"))
    paid = sum(p["amount"] for p in payments)
    return {
        "id": sale["id"],
        "invoice_number": sale.get("invoice_number"),
        "invoice": json.loads(sale["invoice_snapshot"]) if sale.get("invoice_snapshot") else None,
        "timestamp": sale["timestamp"],
        "status": sale["status"],
        "parent_sale_id": sale["parent_sale_id"],
        "subtotal": sale["subtotal"],
        "tax_total": sale["tax_total"],
        "discount_total": sale["discount_total"],
        "total": sale["total"],
        "paid": round(paid, 2),
        "customer_id": sale["customer_id"],
        "customer_name": customer_name,
        "loyalty_points_earned": points_earned,
        "loyalty_points_reversed": points_reversed,
        "store_credit_used": round(credit_used / 100, 2),
        "store_credit_refunded": round(credit_returned / 100, 2),
        "refunded_to_credit": credit_returned > 0,
        "items": items,
        "payments": payments,
        "children": children,
        "refunded": any(c["status"] == "REFUNDED" for c in children),
        "fully_refunded": bool(items) and all(
            it["refundable_qty"] == 0 for it in items) and any(
            c["status"] == "REFUNDED" for c in children),
        "tax_breakup": (json.loads(sale["invoice_snapshot"]).get("tax_breakup", [])
                        if sale.get("invoice_snapshot") else _tax_breakup(conn, item_rows)),
    }


def list_sales(conn, from_ts=None, to_ts=None, status=None, limit=500, offset=0):
    where, params = [], []
    if from_ts:
        where.append("s.timestamp >= ?")
        params.append(from_ts)
    if to_ts:
        where.append("s.timestamp <= ?")
        params.append(to_ts)
    if status:
        where.append("s.status = ?")
        params.append(status)
    sql = '''
        SELECT s.id, s.invoice_number, s.timestamp, s.subtotal, s.tax_total, s.discount_total,
               s.total, s.status, s.parent_sale_id, s.customer_id,
               c.name AS customer_name,
               COALESCE(i.item_count, 0) AS item_count,
               COALESCE(rq.refunded_qty, 0) AS refunded_qty,
               CASE WHEN COALESCE(rq.refunded_qty, 0) > 0
                     AND COALESCE(rq.refunded_qty, 0) < COALESCE(i.item_count, 0)
                    THEN 1 ELSE 0 END AS partially_refunded,
               CASE WHEN EXISTS(SELECT 1 FROM sales c
                                WHERE c.parent_sale_id = s.id
                                  AND c.status = 'REFUNDED')
                     AND COALESCE(rq.refunded_qty, 0) >= COALESCE(i.item_count, 0)
                    THEN 1 ELSE 0 END AS fully_refunded,
               COALESCE(pm.methods, '') AS methods,
               EXISTS(SELECT 1 FROM sales c WHERE c.parent_sale_id = s.id
                      AND c.status = 'REFUNDED') AS refunded
        FROM sales s
        LEFT JOIN customers c ON c.id = s.customer_id
        LEFT JOIN (
            SELECT sale_id, SUM(quantity) AS item_count
            FROM sale_items GROUP BY sale_id
        ) i ON i.sale_id = s.id
        LEFT JOIN (
            SELECT pc.parent_sale_id AS sale_id, SUM(pci.quantity) AS refunded_qty
            FROM sales pc JOIN sale_items pci ON pci.sale_id = pc.id
            WHERE pc.status = 'REFUNDED'
            GROUP BY pc.parent_sale_id
        ) rq ON rq.sale_id = s.id
        LEFT JOIN (
            SELECT sale_id, GROUP_CONCAT(DISTINCT method) AS methods
            FROM payments GROUP BY sale_id
        ) pm ON pm.sale_id = s.id
    '''
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.id DESC LIMIT ? OFFSET ?"
    params += [int(limit), int(offset)]
    cur = conn.cursor()
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def refund_sale(conn, sale_id, lines=None, to_credit=False):
    """
    Refund a completed sale.

    lines=None refunds everything still refundable (classic full refund).
    Otherwise lines is a list of {product_id, quantity}; each quantity must
    not exceed what is still refundable for that product. Money is returned
    to the original tenders proportionally, or to the customer's store credit
    when to_credit=True (the sale must have a customer attached).

    Stock comes back via the inventory trigger as REFUNDED child items are
    inserted.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        sale = get_sale(conn, sale_id)
        if sale is None:
            raise SaleNotFoundError(f"No sale with id {sale_id}")
        if sale["status"] != "COMPLETED":
            raise AlreadyProcessedError(
                f"Sale {sale_id} is {sale['status']} and cannot be refunded")

        cur = conn.cursor()
        cur.execute(
            ''' SELECT si.*, p.id AS pid FROM sale_items si
                JOIN products p ON p.id = si.product_id
                WHERE si.sale_id = ? ORDER BY si.id ''',
            (sale_id,))
        src_rows = [dict(r) for r in cur.fetchall()]
        orig_by_pid = {}
        for r in src_rows:
            orig_by_pid[r["product_id"]] = r

        cur.execute(
            ''' SELECT csi.product_id, SUM(csi.quantity) AS refunded
                FROM sales c JOIN sale_items csi ON csi.sale_id = c.id
                WHERE c.parent_sale_id = ? AND c.status = 'REFUNDED'
                GROUP BY csi.product_id ''',
            (sale_id,))
        already = {r["product_id"]: r["refunded"] for r in cur.fetchall()}

        # Resolve requested quantities per product (merge duplicates).
        requested = {}
        order = []
        if lines is not None:
            if not isinstance(lines, list):
                raise InvalidSaleError("lines must be a list of {product_id, quantity}")
            for ln in lines:
                try:
                    pid = int(ln["product_id"])
                    qty = int(ln["quantity"])
                except (KeyError, TypeError, ValueError):
                    raise InvalidSaleError("Each line needs product_id and quantity")
                if qty <= 0:
                    raise InvalidSaleError("Refund quantity must be at least 1")
                if pid in requested:
                    requested[pid] += qty
                else:
                    requested[pid] = qty
                    order.append(pid)
            for pid in order:
                if pid not in orig_by_pid:
                    raise InvalidSaleError(
                        f"Product {pid} is not part of sale {sale_id}")

        # Fall back to "everything remaining" when no explicit lines given.
        if not requested:
            for r in src_rows:
                rem = r["quantity"] - already.get(r["product_id"], 0)
                if rem > 0:
                    requested[r["product_id"]] = rem
                    order.append(r["product_id"])

        if not requested:
            raise AlreadyProcessedError(
                f"Sale {sale_id} has nothing left to refund")

        # Refunds use the ORIGINAL sale's price mode, not today's Settings.
        source_snapshot = json.loads(sale["invoice_snapshot"]) if sale.get("invoice_snapshot") else {}
        tax_inclusive = source_snapshot.get("price_tax_mode") == "inclusive"
        # Validate quantities against what remains refundable; compute money.
        gross_c = disc_c = tax_c = net_c = 0
        refund_tax_lines = []
        for pid in order:
            row = orig_by_pid[pid]
            qty_orig = int(row["quantity"])
            k = requested[pid]
            remaining = qty_orig - already.get(pid, 0)
            if k > remaining:
                raise SalesError(
                    f"Only {remaining} unit(s) of that item can still be "
                    f"refunded on sale {sale_id}")
            rg, rd, rn, rt = _refund_line_money(row, k, qty_orig, tax_inclusive, already.get(pid, 0))
            gross_c += rg
            disc_c += rd
            net_c += rn
            tax_c += rt
            refund_tax_lines.append({"tax_rate_at_sale": row["tax_rate_at_sale"],
                                     "tax_cents": rt})

        refund_total_cents = net_c if tax_inclusive else net_c + tax_c

        parent_invoice = json.loads(sale["invoice_snapshot"]) if sale.get("invoice_snapshot") else None
        refund_invoice = None
        if parent_invoice:
            refund_invoice = dict(parent_invoice)
            refund_invoice["refund_destination"] = "credit" if to_credit else "original"
            refund_invoice["tax_breakup"] = _tax_breakup(
                conn, refund_tax_lines,
                parent_invoice.get("tax_configuration") or settings_mod.get_settings(conn))
        cur.execute(
            ''' INSERT INTO sales(subtotal, tax_total, discount_total, total,
                                  status, parent_sale_id, customer_id, invoice_snapshot)
                VALUES(?, ?, ?, ?, 'REFUNDED', ?, ?, ?) ''',
            (gross_c / 100, tax_c / 100, disc_c / 100, refund_total_cents / 100,
             sale_id, sale["customer_id"],
             json.dumps(refund_invoice, ensure_ascii=False) if refund_invoice else None),
        )
        refund_id = cur.lastrowid

        for pid in order:
            row = orig_by_pid[pid]
            k = requested[pid]
            qty_orig = int(row["quantity"])
            rg, rd, rn, rt = _refund_line_money(row, k, qty_orig, tax_inclusive, already.get(pid, 0))
            cur.execute(
                ''' INSERT INTO sale_items(sale_id, product_id, quantity,
                                           unit_price, tax_rate_at_sale,
                                           discount_cents, tax_cents, hsn_at_sale, unit_at_sale,
                                           description_at_sale, sku_at_sale)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ''',
                (refund_id, pid, k, float(row["unit_price"]),
                 float(row["tax_rate_at_sale"]), rd, rt, row["hsn_at_sale"], row["unit_at_sale"],
                 row["description_at_sale"], row["sku_at_sale"]),
            )

        if to_credit:
            if not sale["customer_id"]:
                raise SalesError(
                    "Refunding to store credit needs a customer on the sale")
            _apply_credit(conn, sale["customer_id"], refund_total_cents,
                          f"Refund of sale #{sale_id}", sale_id=refund_id)
        else:
            cur.execute(
                ''' SELECT method, amount FROM payments
                    WHERE sale_id = ? ORDER BY id ''', (sale_id,))
            original_payments = [dict(r) for r in cur.fetchall()]
            if not original_payments or all(
                    _cents(p["amount"] or 0) == 0 for p in original_payments):
                raise SalesError(
                    "This sale was paid with store credit — "
                    "refund to store credit instead")

            original_tender_cents = sum(_cents(p["amount"]) for p in original_payments)
            original_total_cents = _cents(sale["total"])
            original_credit_cents = max(0, original_total_cents - original_tender_cents)
            # Earlier explicit credit refunds must not force later returns to
            # the original tender. Track only returns choosing 'original'.
            previous = cur.execute(
                ''' SELECT total, invoice_snapshot FROM sales
                    WHERE parent_sale_id = ? AND status = 'REFUNDED' AND id != ? ''',
                (sale_id, refund_id)).fetchall()
            previous_original_cents = 0
            for child in previous:
                snapshot = json.loads(child["invoice_snapshot"]) if child["invoice_snapshot"] else {}
                if snapshot.get("refund_destination") != "credit":
                    previous_original_cents += _cents(child["total"])

            # Use cumulative allocation so many small partial refunds exactly
            # reconcile to the original tender/credit split at the last refund.
            accumulated = min(original_total_cents,
                              previous_original_cents + refund_total_cents)
            target_tender_total = _split_refund(accumulated,
                                     [original_tender_cents, original_credit_cents])[0]
            # Payments can contain repeated methods (two cash entries).
            # Combine them by method to make cumulative refunds unambiguous.
            tender_by_method = {}
            for p in original_payments:
                method = p["method"]
                tender_by_method[method] = tender_by_method.get(method, 0) + _cents(p["amount"])
            methods = list(tender_by_method)
            target_each_payment = _split_refund(
                target_tender_total, [tender_by_method[method] for method in methods])
            already_paid = cur.execute(
                ''' SELECT p.method, COALESCE(SUM(p.amount), 0) AS amount
                    FROM payments p JOIN sales rs ON rs.id = p.sale_id
                    WHERE rs.parent_sale_id = ? AND rs.status = 'REFUNDED'
                    GROUP BY p.method ''', (sale_id,)).fetchall()
            paid_by_method = {r["method"]: _cents(r["amount"]) for r in already_paid}
            is_partial_refund = refund_total_cents < original_total_cents
            tender_now_cents = 0
            for method, target in zip(methods, target_each_payment):
                prior = paid_by_method.get(method, 0)
                share_cents = target - prior
                if share_cents < 0:
                    raise SalesError("Previous refunds exceed the original tender allocation")
                if share_cents:
                    cur.execute(
                        ''' INSERT INTO payments(sale_id, method, amount, is_partial)
                            VALUES(?, ?, ?, ?) ''',
                        (refund_id, method, share_cents / 100,
                         1 if is_partial_refund else 0),
                    )
                    tender_now_cents += share_cents
            credit_now_cents = refund_total_cents - tender_now_cents
            if credit_now_cents < 0:
                raise SalesError("Refund would exceed original tender allocation")
            if credit_now_cents:
                if not sale["customer_id"]:
                    raise SalesError("Store credit refund needs an original customer")
                _apply_credit(conn, sale["customer_id"], credit_now_cents,
                              f"Return credit from sale #{sale_id}", sale_id=refund_id)
        loyalty.reverse_for_return(conn, sale_id, refund_id, refund_total_cents,
                                   _cents(sale["total"]), "REFUND")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return get_sale_detail(conn, refund_id)


def void_sale(conn, sale_id):
    """Void a never-refunded sale, retaining its original invoice item audit trail.

    Rather than deleting the lines and losing evidence of what was sold,
    reverse inventory and preserve the lines while changing the sale status.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        sale = get_sale(conn, sale_id)
        if sale is None:
            raise SaleNotFoundError(f"No sale with id {sale_id}")
        if sale["status"] != "COMPLETED":
            raise AlreadyProcessedError(
                f"Sale {sale_id} is {sale['status']} and cannot be voided")
        cur = conn.cursor()
        if cur.execute(
                "SELECT 1 FROM sales WHERE parent_sale_id = ? AND status = 'REFUNDED' LIMIT 1",
                (sale_id,)).fetchone():
            raise AlreadyProcessedError(
                "A refunded sale cannot be voided: refund stock was already restored")
        item_rows = cur.execute(
            "SELECT product_id, quantity FROM sale_items WHERE sale_id = ?",
            (sale_id,)).fetchall()
        if not item_rows:
            raise SalesError(f"Sale {sale_id} has no items to void")
        for row in item_rows:
            cur.execute("UPDATE products SET quantity=quantity+? WHERE id=?",
                        (row["quantity"], row["product_id"]))
        cur.execute("UPDATE sales SET status='VOID' WHERE id=?", (sale_id,))
        loyalty.reverse_for_return(conn, sale_id, sale_id, _cents(sale["total"]),
                                   _cents(sale["total"]), "VOID")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_sale_detail(conn, sale_id)


def parse_utc_bounds(from_local_date=None, to_local_date=None):
    """
    Convert local dates (YYYY-MM-DD) to UTC timestamp strings matching the
    format stored by SQLite CURRENT_TIMESTAMP ('YYYY-MM-DD HH:MM:SS').
    """
    out_from = out_to = None
    tz = IST
    if from_local_date:
        dt = datetime.strptime(str(from_local_date), "%Y-%m-%d").replace(tzinfo=tz)
        out_from = datetime.utcfromtimestamp(dt.timestamp()).strftime("%Y-%m-%d %H:%M:%S")
    if to_local_date:
        dt = datetime.strptime(str(to_local_date), "%Y-%m-%d").replace(tzinfo=tz)
        end_of_day = dt.replace(hour=23, minute=59, second=59)
        out_to = datetime.utcfromtimestamp(end_of_day.timestamp()).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    return out_from, out_to
