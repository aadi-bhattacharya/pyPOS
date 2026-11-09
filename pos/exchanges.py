"""Auditable all-or-nothing exchanges with configurable settlement.

Named-customer exchanges may use the existing wallet for the returned amount.
Anonymous exchanges use an offset linked to their replacement invoice; they
never create a dummy customer, disburse money or create unclaimable credit.
"""
import hashlib
import json
import re
from decimal import Decimal

from . import sales, settings as settings_mod
from .customers import CustomerNotFoundError


def _customer(conn, original, requested, cfg):
    if requested in (None, ""):
        cid = original["customer_id"]
    else:
        cid = sales._whole_positive(requested, "Customer ID", 2**63 - 1)
    if not cid:
        if cfg["exchange_allow_anonymous"] != "true":
            raise sales.InvalidSaleError("Store policy requires a customer for exchanges")
        return None
    if original["customer_id"] and cid != original["customer_id"]:
        raise sales.InvalidSaleError("Use the customer attached to the original sale")
    if conn.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone() is None:
        raise CustomerNotFoundError(f"No customer with id {cid}")
    return cid


def _validate_lines(lines, name):
    if not isinstance(lines, list) or not lines:
        raise sales.InvalidSaleError(f"Select at least one {name} item")
    quantities = {}
    for line in lines:
        try:
            pid = sales._whole_positive(line["product_id"], "Product ID", 2**63 - 1)
            qty = sales._whole_positive(line["quantity"])
        except (TypeError, KeyError, ValueError):
            raise sales.InvalidSaleError(f"Invalid {name} item")
        quantities[pid] = quantities.get(pid, 0) + qty
        if quantities[pid] > 1_000_000:
            raise sales.InvalidSaleError(f"Too many {name} items")
    return quantities


def _plan(conn, original_id, returns, replacements, customer_id=None,
          supply_type="intra", place_of_supply=""):
    original = sales.get_sale(conn, original_id)
    if original is None:
        raise sales.SaleNotFoundError(f"No sale with id {original_id}")
    if original["status"] != "COMPLETED":
        raise sales.AlreadyProcessedError("Only a completed invoice can be exchanged")
    cfg = settings_mod.get_settings(conn)
    if cfg["exchange_allow"] != "true":
        raise sales.InvalidSaleError("Exchanges are disabled by store policy")
    cid = _customer(conn, original, customer_id, cfg)
    returned = _validate_lines(returns, "returned")
    _validate_lines(replacements, "replacement")
    wallet = cid is not None and cfg["refund_allow_credit"] == "true"
    refund = (sales.preview_refund(conn, original_id, returns, to_credit=True,
                                   credit_customer_id=cid) if wallet else
              sales.preview_refund(conn, original_id, returns, exchange_only=True))
    # Items physically returned can immediately be sold again, including a
    # last-in-stock SKU. The commit still revalidates under BEGIN IMMEDIATE.
    new_items = sales._normalize_items(conn, replacements, stock_additions=returned)
    price_pending = [p["SKU"] for p in new_items if p["price_review_required"]]
    if price_pending:
        raise sales.InvalidSaleError("Selling price needs review for SKU(s): " + ", ".join(price_pending[:5]))
    gst_pending = [p["SKU"] for p in new_items if p["gst_review_required"]]
    if gst_pending:
        raise sales.InvalidSaleError("GST rate unverified for SKU(s): " + ", ".join(gst_pending[:5]))
    if supply_type not in ("intra", "inter"):
        raise sales.InvalidSaleError("Supply type must be intra or inter-state")
    if supply_type == "inter" and not str(place_of_supply or "").strip():
        raise sales.InvalidSaleError("Enter the inter-state place of supply")
    gross = sales.compute_totals(new_items,
                                 tax_inclusive=cfg.get("price_tax_mode") == "inclusive")
    returned_cents = sales._cents(refund["total"])
    new_total_cents = sales._cents(gross["total"])
    applied = min(returned_cents, new_total_cents)
    unspent = returned_cents - applied
    if not wallet and unspent and cfg["exchange_allow_forfeit_difference"] != "true":
        raise sales.InvalidSaleError(
            "Replacement costs less than the return: select a customer for store credit, "
            "add replacement items, or ask the administrator about the store's difference policy")
    return {
        "customer_id": cid,
        "settlement_mode": "wallet" if wallet else "offset",
        "exchange_offset": 0 if wallet else applied / 100,
        "forfeited_amount": 0 if wallet else unspent / 100,
        "refund_total": returned_cents / 100,
        "replacement_total": new_total_cents / 100,
        "replacement_tax": float(gross["tax_total"]),
        "credit_applied": applied / 100 if wallet else 0,
        "additional_due": (new_total_cents - applied) / 100,
        "remaining_credit": unspent / 100 if wallet else 0,
        "refund_cents": returned_cents,
        "credit_spent_cents": applied if wallet else 0,
        "offset_cents": applied if not wallet else 0,
        "forfeited_cents": unspent if not wallet else 0,
        "due_cents": new_total_cents - applied,
        "remaining_cents": unspent if wallet else 0,
    }


def preview_exchange(conn, original_id, returns, replacements, customer_id=None,
                     supply_type="intra", place_of_supply=""):
    """Read-only preview. Commit ALWAYS recalculates within a write lock."""
    return _plan(conn, original_id, returns, replacements, customer_id,
                 supply_type, place_of_supply)


def _fingerprint(original_id, returns, replacements, customer_id, payments,
                 reason, supply_type, place_of_supply, salesperson, accept_forfeit):
    a = _validate_lines(returns, "returned")
    b = _validate_lines(replacements, "replacement")
    payload = [original_id, sorted(a.items()), sorted(b.items()), customer_id,
               payments or [], reason, supply_type, place_of_supply, salesperson,
               bool(accept_forfeit)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _receipt(conn, row):
    original_id = row["original_sale_id"]
    return {
        "request_id": row["request_id"],
        "original_sale_id": original_id,
        "customer_id": row["customer_id"],
        "refund": sales.get_sale_detail(conn, row["refund_sale_id"]),
        "replacement": sales.get_sale_detail(conn, row["replacement_sale_id"]),
        "refund_total": row["refund_cents"] / 100,
        "credit_applied": row["credit_spent_cents"] / 100,
        "exchange_offset": row["offset_cents"] / 100,
        "settlement_mode": row["settlement_mode"],
        "forfeited_amount": row["forfeited_cents"] / 100,
        "additional_due": row["payment_due_cents"] / 100,
        "remaining_credit": row["credit_remainder_cents"] / 100,
    }


def exchange_sale(conn, original_id, returns, replacements, customer_id=None,
                  payments=None, reason="", request_id=None, supply_type="intra",
                  place_of_supply="", salesperson=None, require_open_session=False,
                  accept_forfeit=False):
    """Create linked return + replacement, or neither. Safe to retry with key."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
        raise sales.InvalidSaleError("A valid exchange request ID is required")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 250:
        raise sales.InvalidSaleError("Choose a return reason (up to 250 characters)")
    reason = reason.strip()
    salesperson = salesperson or ""
    if type(accept_forfeit) is not bool:
        raise sales.InvalidSaleError("Invalid forfeit acknowledgement")
    key = _fingerprint(original_id, returns, replacements, customer_id,
                       payments, reason, supply_type, place_of_supply,
                       salesperson, accept_forfeit)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT * FROM exchanges WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            if existing["fingerprint"] != key:
                raise sales.InvalidSaleError("Exchange request ID reused with different details")
            result = _receipt(conn, existing)
            conn.commit()
            return result
        if require_open_session and conn.execute(
                "SELECT 1 FROM cash_sessions WHERE closed_at IS NULL LIMIT 1").fetchone() is None:
            raise sales.InvalidSaleError("Start Day before recording an exchange")
        plan = _plan(conn, original_id, returns, replacements, customer_id,
                     supply_type, place_of_supply)
        if plan["forfeited_cents"] > 0 and not accept_forfeit:
            raise sales.InvalidSaleError(
                "Explicitly acknowledge that the customer loses the exchange difference")
        # Validate the tender BEFORE mutating either document, including
        # overpayment/change constraints and UPI format.
        due = Decimal(plan["due_cents"]) / 100
        if due > 0:
            sales._normalize_payments(payments, due)
        elif payments:
            raise sales.InvalidPaymentError("No additional payment is due for this exchange")
        wallet = plan["settlement_mode"] == "wallet"
        refund = sales.refund_sale(
            conn, original_id, lines=returns, to_credit=wallet, reason=reason,
            credit_customer_id=plan["customer_id"] if wallet else None,
            exchange_only=not wallet, require_open_session=require_open_session,
            manage_transaction=False)
        replacement = sales.checkout(
            conn, items=replacements, payments=payments or [],
            customer_id=plan["customer_id"], use_credit=wallet,
            credit_limit_cents=plan["credit_spent_cents"] if wallet else None,
            exchange_offset_cents=plan["offset_cents"],
            supply_type=supply_type, place_of_supply=place_of_supply,
            salesperson=salesperson, require_open_session=require_open_session,
            manage_transaction=False)
        if (sales._cents(refund["total"]) != plan["refund_cents"] or
            sales._cents(replacement["store_credit_used"]) != plan["credit_spent_cents"] or
            sales._cents(replacement["exchange_offset_used"]) != plan["offset_cents"] or
            sales._cents(replacement["total"]) - plan["credit_spent_cents"] -
                plan["offset_cents"] != plan["due_cents"]):
            raise sales.SalesError("Exchange accounting mismatch; no records were saved")
        conn.execute('''INSERT INTO exchanges (
            request_id, fingerprint, original_sale_id, refund_sale_id,
            replacement_sale_id, customer_id, settlement_mode,
            offset_cents, forfeited_cents, refund_cents, credit_spent_cents,
            payment_due_cents, credit_remainder_cents)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (request_id, key, original_id, refund["id"], replacement["id"],
             plan["customer_id"], plan["settlement_mode"],
             plan["offset_cents"], plan["forfeited_cents"],
             plan["refund_cents"], plan["credit_spent_cents"],
             plan["due_cents"], plan["remaining_cents"]))
        result = _receipt(conn, conn.execute(
            "SELECT * FROM exchanges WHERE request_id=?", (request_id,)).fetchone())
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
