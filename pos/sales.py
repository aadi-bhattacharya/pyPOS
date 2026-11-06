import json
import hashlib
import re
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
from .product_display import receipt_parts, public_variant, description_from_parts

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
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean is not an amount")
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Amount must be finite")
        return number
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise InvalidSaleError("Amount must be a finite number") from exc


def _money(value):
    return _d(value).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _whole_positive(value, label="Quantity", max_value=1_000_000):
    """Parse API identifiers/counts without truncating fractional or bool values."""
    if type(value) is int:
        n = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        n = int(value)
    else:
        raise InvalidSaleError(f"{label} must be a positive whole number")
    if n < 1 or n > max_value:
        raise InvalidSaleError(f"{label} must be a positive whole number")
    return n


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
    if not isinstance(lines, list) or not lines:
        raise InvalidSaleError("Sale must contain at least one item")
    if discount_mode not in (None, "", "percent", "amount"):
        raise InvalidSaleError("Discount mode must be percent or amount")

    prepared = []
    for ln in lines:
        qty = _whole_positive(ln["quantity"])
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
        if pct < 0 or pct > 100:
            raise InvalidSaleError("Percentage discount must be between 0 and 100")
        disc_cents = _cents(gross_total_cents * pct / Decimal(10000))
    elif discount_mode == "amount":
        disc_cents = _cents(_d(discount_value or 0))
    else:
        disc_cents = 0
    disc_cents = max(0, min(disc_cents, gross_total_cents))

    # Largest-remainder allocation, bounded by each line's gross amount.
    # Never assign a 2-paise discount to a 1-paise item.
    weights = [ln["gross_cents"] for ln in prepared]
    alloc = [0] * len(weights)
    if gross_total_cents and disc_cents:
        alloc = [(disc_cents * gross) // gross_total_cents for gross in weights]
        unallocated = disc_cents - sum(alloc)
        # Residual allocation: stable ties by original cart order.
        by_remainder = sorted(range(len(weights)),
            key=lambda i: (-(disc_cents * weights[i] % gross_total_cents), i))
        for i in by_remainder:
            if unallocated <= 0:
                break
            if alloc[i] < weights[i]:
                alloc[i] += 1
                unallocated -= 1
        if unallocated:
            raise InvalidSaleError("Unable to distribute discount across sale items")
    result_lines = []
    for ln, share in zip(prepared, alloc):
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
    if not isinstance(items, list) or not items:
        raise InvalidSaleError("Cart is empty")
    merged = {}
    order = []
    for it in items:
        try:
            pid = _whole_positive(it["product_id"], "Product ID", 2**63 - 1)
            qty = _whole_positive(it["quantity"])
        except (KeyError, TypeError, ValueError):
            raise InvalidSaleError("Each item needs product_id and quantity")
        if qty <= 0:
            raise InvalidSaleError("Item quantity must be at least 1")
        if pid in merged:
            merged[pid]["quantity"] += qty
            if merged[pid]["quantity"] > 1_000_000:
                raise InvalidSaleError("Quantity exceeds supported limit")
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
        variant = public_variant((a["attribute_name"], a["attribute_value"]) for a in attributes)
        pending = {a["attribute_name"].casefold() for a in attributes}
        group_name = row["group_name"] or f"Product {pid}"
        normalized.append({**item, **{
            "description": description_from_parts(group_name, variant),
            "gst_review_required": "gst review required" in pending,
            "price_review_required": "price review required" in pending,
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
    if not isinstance(payments, list):
        raise InvalidPaymentError("Payments must be a list")
    total_cents = _cents(total)
    tendered_cents = 0
    clean = []
    for p in payments:
        if not isinstance(p, dict):
            raise InvalidPaymentError("Invalid payment entry")
        method = str(p.get("method", "")).upper()
        if method not in PAYMENT_METHODS:
            raise InvalidPaymentError(f"Unsupported payment method: {p.get('method')}")
        try:
            raw_amount = _d(p.get("amount") if p.get("amount") is not None else 0)
        except InvalidSaleError as exc:
            raise InvalidPaymentError("Payment amount must be finite") from exc
        if raw_amount < 0 or raw_amount > Decimal("100000000"):
            raise InvalidPaymentError("Payment amount must be between ₹0 and ₹10 crore")
        if raw_amount != _money(raw_amount):
            raise InvalidPaymentError("Payment amount cannot have fractional paise")
        amt = raw_amount
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
             customer_id=None, use_credit=False, supply_type="intra", place_of_supply="",
             request_id=None, salesperson=None):
    """
    Complete a sale atomically: validate stock, recompute all money server-side,
    insert sale + items + payments, decrement inventory (via trigger), commit.

    When `customer_id` is given the sale is attached to that customer; with
    `use_credit` any available store credit is spent first and the rest of the
    balance due must be covered by `payments`.
    """
    # Free-text sales attribution is independent of staff login and is never
    # included in the legal invoice snapshot or customer-facing receipt.
    if salesperson is None:
        salesperson = ""
    if not isinstance(salesperson, str):
        raise InvalidSaleError("Salesperson name must be text")
    salesperson = " ".join(salesperson.strip().split())
    if len(salesperson) > 80 or any(ord(c) < 32 for c in salesperson):
        raise InvalidSaleError("Salesperson name must be 80 characters or fewer")
    if supply_type not in ("intra", "inter"):
        raise InvalidSaleError("Supply type must be intra or inter-state")
    place_of_supply = str(place_of_supply or "").strip()
    if len(place_of_supply) > 120:
        raise InvalidSaleError("Place of supply is too long")
    if request_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(request_id)):
        raise InvalidSaleError("Invalid checkout request ID")
    request_id = str(request_id) if request_id is not None else None
    fingerprint = hashlib.sha256(json.dumps({"items": items, "payments": payments,
        "discount_mode": discount_mode, "discount_value": discount_value,
        "customer_id": customer_id, "use_credit": bool(use_credit),
        "supply_type": supply_type, "place_of_supply": place_of_supply,
        "salesperson": salesperson},
        sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if request_id:
            existing = conn.execute("SELECT * FROM sale_requests WHERE request_id=?",
                                    (request_id,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise InvalidSaleError("Checkout request ID reused with different contents")
                conn.commit()
                receipt = get_sale_detail(conn, existing["sale_id"])
                receipt["change"] = existing["change_cents"] / 100
                if existing["credit_cents"]:
                    receipt["store_credit_used"] = existing["credit_cents"] / 100
                return receipt
        movement_start = conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
        normalized = _normalize_items(conn, items)
        cfg_checkout = settings_mod.get_settings(conn)
        price_pending = [p["SKU"] for p in normalized if p["price_review_required"]]
        if price_pending:
            raise InvalidSaleError("Selling price needs review for SKU(s): " +
                                   ", ".join(price_pending[:5]) +
                                   ". Set and confirm the selling price in Catalog first.")
        pending_gst = [p["SKU"] for p in normalized if p["gst_review_required"]]
        if pending_gst:
            raise InvalidSaleError("GST rate unverified for SKU(s): " +
                                   ", ".join(pending_gst[:5]) +
                                   ". Confirm the rate in Catalog; 0% import placeholders are not verified.")
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
                                  status, customer_id, invoice_number, invoice_snapshot, cash_session_id, cash_session_explicit, salesperson)
                VALUES(?, ?, ?, ?, 'COMPLETED', ?, ?, ?, ?, 1, ?) ''',
            (float(totals["subtotal_gross"]), float(totals["tax_total"]),
             float(totals["discount_total"]), float(totals["total"]),
             customer["id"] if customer else None, invoice_number,
             json.dumps(invoice_snapshot, ensure_ascii=False),
             (conn.execute("SELECT id FROM cash_sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1")
              .fetchone() or [None])[0], salesperson),
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
        conn.execute("""UPDATE stock_movements SET kind='SALE', reference_id=?
            WHERE id>? AND kind='CHANGE'""", (sale_id, movement_start))
        if request_id:
            conn.execute("""INSERT INTO sale_requests
                (request_id, fingerprint, sale_id, change_cents, credit_cents)
                VALUES(?, ?, ?, ?, ?)""",
                (request_id, fingerprint, sale_id, _cents(change), credit_cents))
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
        name = r["group_name"] or f"Product {r['product_id']}"
        saved_description = r["description_at_sale"] or name
        # Do not mutate the immutable historical sale line: only clean its
        # customer-facing representation, including old import-heavy receipts.
        receipt_name, receipt_variant = receipt_parts(saved_description,
            [tuple(a.split(": ", 1)) for a in attrs_by_product.get(r["product_id"], [])
             if ": " in a] if not r["description_at_sale"] else None)
        display_name = description_from_parts(receipt_name, receipt_variant)
        line_gross = _money(_d(r["unit_price"]) * r["quantity"])
        refunded_qty = min(refunded_by_pid.get(r["product_id"], 0), r["quantity"])
        items.append({
            "product_id": r["product_id"],
            "name": display_name,
            "receipt_name": receipt_name,
            "receipt_variant": receipt_variant,
            "gst_review_at_sale": "GST Review Required:" in saved_description,
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
    parent_invoice_number = None
    if sale["parent_sale_id"]:
        parent = conn.execute("SELECT invoice_number FROM sales WHERE id=?",
                              (sale["parent_sale_id"],)).fetchone()
        if parent:
            parent_invoice_number = parent["invoice_number"]
    return {
        "id": sale["id"],
        "invoice_number": sale.get("invoice_number"),
        "invoice": json.loads(sale["invoice_snapshot"]) if sale.get("invoice_snapshot") else None,
        "timestamp": sale["timestamp"],
        "status": sale["status"],
        "parent_sale_id": sale["parent_sale_id"],
        "parent_invoice_number": parent_invoice_number,
        "tax_review_warning": any(i["gst_review_at_sale"] for i in items),
        "subtotal": sale["subtotal"],
        "tax_total": sale["tax_total"],
        "discount_total": sale["discount_total"],
        "total": sale["total"],
        "paid": round(paid, 2),
        "customer_id": sale["customer_id"],
        "customer_name": customer_name,
        "salesperson": sale.get("salesperson", ""),
        "loyalty_points_earned": points_earned,
        "loyalty_points_reversed": points_reversed,
        "store_credit_used": round(credit_used / 100, 2),
        "store_credit_refunded": round(credit_returned / 100, 2),
        "refunded_to_credit": credit_returned > 0,
        "refund_reason": (json.loads(sale["invoice_snapshot"] or "{}").get("refund_reason", "")
                          if sale["status"] == "REFUNDED" else ""),
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


def list_sales(conn, from_ts=None, to_ts=None, status=None, limit=500, offset=0, search=None):
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
    if search and str(search).strip():
        pattern = "%" + str(search).strip()[:100] + "%"
        where.append("(" + " OR ".join([
            "CAST(s.id AS TEXT) LIKE ?",
            "COALESCE(s.invoice_number, '') LIKE ?",
            "COALESCE(c.name, '') LIKE ?",
            "COALESCE(c.phone, '') LIKE ?",
            "COALESCE(pm.methods, '') LIKE ?",
            "COALESCE(s.salesperson, '') LIKE ?",
        ]) + ")")
        params.extend([pattern] * 6)
    sql = '''
        SELECT s.id, s.invoice_number, s.timestamp, s.subtotal, s.tax_total, s.discount_total,
               s.total, s.status, s.parent_sale_id, s.customer_id, s.salesperson,
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


def _refund_distribution(conn, sale, refund_total_cents, to_credit=False, ignore_refund_id=0):
    '''Preview and commit use one allocation algorithm, always in paise.

    The original non-cash/cash tenders are repaid in their original proportions.
    Any store credit used to pay the sale is restored as store credit. Refunds
    explicitly sent to store credit do not consume the original tender budget.
    This method only records tender flows; the operator must process external
    card/UPI transfers separately.
    '''
    if to_credit:
        if not sale["customer_id"]:
            raise SalesError("Refund to store credit requires an attached customer")
        return [], refund_total_cents

    sale_id = sale["id"]
    source_payments = [dict(r) for r in conn.execute(
        "SELECT method, amount FROM payments WHERE sale_id=? ORDER BY id", (sale_id,))]
    if not source_payments or all(_cents(r["amount"]) == 0 for r in source_payments):
        raise SalesError("This sale was paid with store credit — refund to store credit instead")

    orig_tender = sum(_cents(p["amount"]) for p in source_payments)
    orig_total = _cents(sale["total"])
    orig_credit = max(0, orig_total - orig_tender)
    previous = conn.execute('''
        SELECT total, invoice_snapshot FROM sales
        WHERE parent_sale_id=? AND status='REFUNDED' AND id != ? ''', (sale_id, ignore_refund_id)).fetchall()
    previous_original = 0
    for child in previous:
        snapshot = json.loads(child["invoice_snapshot"] or "{}")
        if snapshot.get("refund_destination") != "credit":
            previous_original += _cents(child["total"])

    target_total = _split_refund(
        min(orig_total, previous_original + refund_total_cents),
        [orig_tender, orig_credit])[0]
    by_method = {}
    for p in source_payments:
        by_method[p["method"]] = by_method.get(p["method"], 0) + _cents(p["amount"])
    methods = list(by_method)
    targets = _split_refund(target_total, [by_method[m] for m in methods])
    old_rows = conn.execute('''
        SELECT p.method, COALESCE(SUM(p.amount),0) AS amount
        FROM payments p JOIN sales rs ON rs.id=p.sale_id
        WHERE rs.parent_sale_id=? AND rs.status='REFUNDED' AND rs.id != ?
        GROUP BY p.method''', (sale_id, ignore_refund_id)).fetchall()
    old = {r["method"]: _cents(r["amount"]) for r in old_rows}
    refund_tender = []
    for method, target in zip(methods, targets):
        now = target - old.get(method, 0)
        if now < 0:
            raise SalesError("Previous refunds exceed the original tender allocation")
        if now:
            refund_tender.append({"method": method, "amount_cents": now})
    credit = refund_total_cents - sum(p["amount_cents"] for p in refund_tender)
    if credit < 0:
        raise SalesError("Refund exceeds the original tender allocation")
    if credit and not sale["customer_id"]:
        raise SalesError("Store credit refund needs an original customer")
    return refund_tender, credit


def _refund_request_fingerprint(sale_id, lines, to_credit, reason):
    # Semantic request fingerprint. Reordering equivalent line entries does
    # not change a retry; any changed quantity/destination/reason does.
    qtys = {}
    if lines is not None:
        for item in lines:
            try:
                pid = _whole_positive(item["product_id"], "Product ID", 2**63 - 1)
                qty = _whole_positive(item["quantity"])
            except (KeyError, TypeError, ValueError):
                raise InvalidSaleError("Invalid refund request line")
            qtys[pid] = qtys.get(pid, 0) + qty
    payload = [sale_id, sorted(qtys.items()) if lines is not None else None,
               bool(to_credit), reason]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def refund_export_rows(conn):
    """All recorded refunds, with original invoice and exact tender allocation.

    Returns operational refund data, not GST statutory credit-note records.
    """
    rows = conn.execute('''SELECT r.id, r.timestamp, r.parent_sale_id, p.invoice_number,
        r.customer_id, r.subtotal, r.discount_total, r.tax_total, r.total,
        r.invoice_snapshot,
        COALESCE((SELECT SUM(amount) FROM payments
                  WHERE sale_id=r.id AND method='CASH'),0) AS cash,
        COALESCE((SELECT SUM(amount) FROM payments
                  WHERE sale_id=r.id AND method='CARD'),0) AS card,
        COALESCE((SELECT SUM(amount) FROM payments
                  WHERE sale_id=r.id AND method='UPI'),0) AS upi,
        COALESCE((SELECT SUM(delta_cents) FROM credit_events
                  WHERE sale_id=r.id AND delta_cents>0),0) AS credit_cents
        FROM sales r JOIN sales p ON p.id=r.parent_sale_id
        WHERE r.status='REFUNDED' ORDER BY r.id DESC''').fetchall()
    exported = []
    for row in rows:
        invoice = json.loads(row["invoice_snapshot"] or "{}")
        exported.append((row["id"], row["timestamp"], row["parent_sale_id"],
                         row["invoice_number"] or "", row["customer_id"] or "",
                         invoice.get("refund_reason", ""),
                         row["subtotal"], row["discount_total"], row["tax_total"],
                         row["total"], row["cash"], row["card"], row["upi"],
                         round(row["credit_cents"] / 100, 2)))
    return exported


def preview_refund(conn, sale_id, lines=None, to_credit=False):
    """Read-only, cent-exact preview using recorded original sale allocations.

    This does not mutate inventory, tender, customer credit or loyalty. The
    actual refund recalculates under BEGIN IMMEDIATE to prevent stale previews
    from becoming authoritative payouts.
    """
    sale = get_sale(conn, sale_id)
    if sale is None:
        raise SaleNotFoundError(f"No sale with id {sale_id}")
    if sale["status"] != "COMPLETED":
        raise AlreadyProcessedError(f"Sale {sale_id} cannot be refunded")
    source = {row["product_id"]: dict(row) for row in conn.execute(
        "SELECT * FROM sale_items WHERE sale_id=?", (sale_id,))}
    refunded = {row["product_id"]: int(row["qty"]) for row in conn.execute(
        """SELECT si.product_id, SUM(si.quantity) qty FROM sale_items si
           JOIN sales s ON si.sale_id=s.id
           WHERE s.parent_sale_id=? AND s.status='REFUNDED'
           GROUP BY si.product_id""", (sale_id,))}
    if lines is None:
        requested = {pid: int(row["quantity"]) - refunded.get(pid, 0)
                     for pid, row in source.items()}
        requested = {pid: qty for pid, qty in requested.items() if qty > 0}
    else:
        if not isinstance(lines, list):
            raise InvalidSaleError("lines must be a list")
        requested = {}
        for line in lines:
            try:
                pid = _whole_positive(line["product_id"], "Product ID", 2**63 - 1)
                qty = _whole_positive(line["quantity"])
            except (TypeError, ValueError, KeyError):
                raise InvalidSaleError("Each return needs product_id and quantity")
            if qty <= 0 or pid not in source:
                raise InvalidSaleError("Select valid original items and positive return quantities")
            requested[pid] = requested.get(pid, 0) + qty
    mode = json.loads(sale["invoice_snapshot"] or "{}").get("price_tax_mode")
    inclusive = mode == "inclusive"
    gross = discount = net = tax = 0
    for pid, qty in requested.items():
        row = source[pid]
        if qty > int(row["quantity"]) - refunded.get(pid, 0):
            raise InvalidSaleError("Refund exceeds remaining quantity for product " + str(pid))
        g, d, n, t = _refund_line_money(row, qty, int(row["quantity"]),
                                       inclusive, refunded.get(pid, 0))
        gross += g
        discount += d
        net += n
        tax += t
    cents = net if inclusive else net + tax
    tender, credit = _refund_distribution(conn, sale, cents, to_credit=to_credit) if requested else ([], 0)
    return {
        "subtotal": gross / 100,
        "discount_total": discount / 100,
        "tax_total": tax / 100,
        "total": cents / 100,
        "quantity": sum(requested.values()),
        "price_tax_mode": mode or "exclusive",
        "refund_payments": [{"method": p["method"], "amount": p["amount_cents"] / 100}
                            for p in tender],
        "refund_store_credit": credit / 100,
        "refund_destination": "credit" if to_credit else "original",
    }


def refund_sale(conn, sale_id, lines=None, to_credit=False, reason="", request_id=None):
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
    reason = str(reason or "").strip()
    if len(reason) > 250:
        raise InvalidSaleError("Refund reason must be 250 characters or fewer")
    if request_id is not None:
        request_id = str(request_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise InvalidSaleError("Invalid refund request ID")
    fingerprint = _refund_request_fingerprint(sale_id, lines, to_credit, reason) if request_id else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        if request_id:
            existing = conn.execute(
                "SELECT original_sale_id, fingerprint, refund_sale_id FROM refund_requests "
                "WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["original_sale_id"] != sale_id or existing["fingerprint"] != fingerprint:
                    raise InvalidSaleError("Refund request ID was reused with different details")
                conn.commit()
                return get_sale_detail(conn, existing["refund_sale_id"])
        movement_start = conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
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
                    pid = _whole_positive(ln["product_id"], "Product ID", 2**63 - 1)
                    qty = _whole_positive(ln["quantity"])
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

        # Omission means "everything remaining" for legacy callers. An
        # explicitly empty list must never silently return the whole sale.
        if lines is not None and not requested:
            raise InvalidSaleError("Select at least one item to refund")
        if lines is None:
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
        refund_invoice = dict(parent_invoice or {})
        refund_invoice["refund_destination"] = "credit" if to_credit else "original"
        refund_invoice["refund_reason"] = reason
        refund_invoice["tax_breakup"] = _tax_breakup(
            conn, refund_tax_lines,
            refund_invoice.get("tax_configuration") or settings_mod.get_settings(conn))
        cur.execute(
            ''' INSERT INTO sales(subtotal, tax_total, discount_total, total,
                                  status, parent_sale_id, customer_id, invoice_snapshot, cash_session_id, cash_session_explicit)
                VALUES(?, ?, ?, ?, 'REFUNDED', ?, ?, ?, ?, 1) ''',
            (gross_c / 100, tax_c / 100, disc_c / 100, refund_total_cents / 100,
             sale_id, sale["customer_id"],
             json.dumps(refund_invoice, ensure_ascii=False) if refund_invoice else None,
             (conn.execute("SELECT id FROM cash_sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1")
              .fetchone() or [None])[0]),
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

        tender_payments, credit_cents = _refund_distribution(
            conn, sale, refund_total_cents, to_credit=to_credit,
            ignore_refund_id=refund_id)
        for pay in tender_payments:
            cur.execute(''' INSERT INTO payments(sale_id, method, amount, is_partial)
                VALUES(?, ?, ?, ?) ''',
                (refund_id, pay["method"], pay["amount_cents"] / 100,
                 int(refund_total_cents < _cents(sale["total"]))))
        if credit_cents:
            _apply_credit(conn, sale["customer_id"], credit_cents,
                          f"Return credit from sale #{sale_id}" if not to_credit else
                          f"Refund of sale #{sale_id}", sale_id=refund_id)
        if request_id:
            conn.execute('''INSERT INTO refund_requests(
                request_id, original_sale_id, fingerprint, refund_sale_id)
                VALUES(?, ?, ?, ?)''',
                (request_id, sale_id, fingerprint, refund_id))
        loyalty.reverse_for_return(conn, sale_id, refund_id, refund_total_cents,
                                   _cents(sale["total"]), "REFUND")
        conn.execute("""UPDATE stock_movements SET kind='REFUND', reference_id=?
            WHERE id>? AND kind='CHANGE'""", (refund_id, movement_start))
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
        movement_start = conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
        sale = get_sale(conn, sale_id)
        if sale is None:
            raise SaleNotFoundError(f"No sale with id {sale_id}")
        if sale["status"] != "COMPLETED":
            raise AlreadyProcessedError(
                f"Sale {sale_id} is {sale['status']} and cannot be voided")
        # A closed Z report is a historical cash record. Correct an old sale
        # using the explicit return workflow, never by rewriting a closed day.
        linked = conn.execute("""SELECT cs.closed_at FROM sales s
            LEFT JOIN cash_sessions cs ON cs.id=s.cash_session_id
            WHERE s.id=?""", (sale_id,)).fetchone()
        if linked and linked["closed_at"] is not None:
            raise AlreadyProcessedError(
                "Cannot void an invoice from a closed cash session; use Returns & Refunds")
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
        conn.execute("""UPDATE stock_movements SET kind='VOID', reference_id=?
            WHERE id>? AND kind='CHANGE'""", (sale_id, movement_start))
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
