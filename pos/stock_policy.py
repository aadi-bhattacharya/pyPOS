"""Inventory permissions and audit input rules.

The server always derives the identity and movement type.  UI checks are for
usability only and must never be treated as authority.
"""
import re

from .catalog import InvalidInputError


class InventoryForbidden(PermissionError):
    """Stock operation is outside the signed-in operator's permissions."""


def parse_delta(value):
    if type(value) is int:
        amount = value
    elif type(value) is str and re.fullmatch(r"[+-]?[0-9]+", value):
        amount = int(value)
    else:
        raise InvalidInputError("Stock change must be a whole number")
    if amount == 0 or abs(amount) > 1_000_000:
        raise InvalidInputError("Stock change must be between -1,000,000 and +1,000,000, excluding zero")
    return amount


def authorize_adjustment(delta, reason, requested_kind, actor, *, cashier_can_reduce=False):
    """Return validated (delta, reason, kind) for the existing stock service.

    Never permit a browser to choose an internal stock ledger classification.
    Keep the operator identity in the movement reason without a schema change.
    """
    delta = parse_delta(delta)
    if not actor or actor.get("role") not in ("staff", "cashier", "manager", "admin"):
        raise InventoryForbidden("A signed-in operator is required")
    if actor["role"] in ("cashier", "staff") and delta < 0 and not cashier_can_reduce:
        raise InventoryForbidden("Stock reductions require an explicit permission")
    if requested_kind not in (None, "RECEIPT", "ADJUSTMENT"):
        raise InvalidInputError("Invalid manual stock movement type")
    if requested_kind == "RECEIPT" and delta < 0:
        raise InvalidInputError("A goods receipt cannot reduce stock")
    if not isinstance(reason, str):
        raise InvalidInputError("Enter a stock movement reason or supplier reference")
    reason = " ".join(reason.strip().split())
    if len(reason) < 4 or len(reason) > 200:
        raise InvalidInputError("Stock movement reason must be 4–200 characters")
    # Whitelist known DB-backed username/role; do not use client-supplied identity.
    username = str(actor.get("username") or "unknown")[:60]
    role = actor["role"]
    audit_reason = f"operator={username} role={role} | {reason}"
    movement_kind = "RECEIPT" if requested_kind == "RECEIPT" else "ADJUSTMENT"
    return delta, audit_reason, movement_kind


def cashier_import_mode(mode, allow_zero=False, force_repeat=False):
    # Both create-only and additive receiving are legitimate at a store counter.
    # Merge edits catalogue prices and taxes and is manager-only.
    if mode not in ("skip", "add_stock") or allow_zero or force_repeat:
        raise InventoryForbidden("Staff may create new SKUs or receive stock, but cannot merge edits, create zero-price drafts or bypass repeat checks")


def cashier_new_product(data):
    """Permit adding a saleable SKU, but not secretly writing cost valuations."""
    from decimal import Decimal, InvalidOperation
    try:
        price = Decimal(str(data.get("price")))
    except (InvalidOperation, ValueError, TypeError):
        raise InventoryForbidden("Enter a valid positive selling price")
    if not price.is_finite() or price <= 0 or price > 10_000_000 or price.as_tuple().exponent < -2:
        raise InventoryForbidden("Cashier-created products need a positive selling price in rupees and paise")
    quantity = data.get("quantity", 0)
    if type(quantity) is int:
        qty = quantity
    elif type(quantity) is str and re.fullmatch(r"[0-9]+", quantity):
        qty = int(quantity)
    else:
        raise InventoryForbidden("Opening stock must be a non-negative whole quantity")
    if not 0 <= qty <= 1_000_000:
        raise InventoryForbidden("Opening stock must be between 0 and 1,000,000")
    if "tax_rate" not in data:
        raise InventoryForbidden("Enter the applicable GST rate, including zero if appropriate")
    try:
        tax = Decimal(str(data.get("tax_rate")))
    except (InvalidOperation, ValueError, TypeError):
        raise InventoryForbidden("Enter a valid GST rate")
    if not tax.is_finite() or not 0 <= tax <= 100 or tax.as_tuple().exponent < -2:
        raise InventoryForbidden("GST rate must be between 0 and 100 with at most two decimal places")
    if data.get("cost_price") not in (None, "", 0, "0"):
        raise InventoryForbidden("Only a manager can enter or revise inventory cost prices")
