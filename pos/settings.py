from .db import connect
from .india import gstin, logo_data

SETTING_KEYS = {
    "store_name": "My Store",
    "store_address": "",
    "store_phone": "",
    "currency": "₹",
    "store_gstin": "",
    "store_state": "",
    "store_state_code": "",
    "store_fssai": "",
    "store_upi_id": "",
    "store_logo": "",
    # Appearance on POS screens (independent of the GST invoice seller name).
    "pos_display_name": "pyPOS",
    "pos_accent_color": "#1B6BB5",
    "pos_logo_mode": "default",  # default mark, receipt, custom, or none
    "pos_logo_size": "28",  # square icon width in POS sidebar, pixels
    "pos_logo": "",
    "show_staff_menu": "true",
    "cashier_allow_stock_reduction": "false",  # Default denies manual reductions.
    "cashier_allow_stock_import": "true",  # Existing approved SKU receipts only.
    "cashier_max_discount_percent": "0",  # 0% by default; approve discounts in Settings.
    # Return rules are owner-controlled and validated on every API commit.
    "refund_allow_original": "true",       # Cash/Card/UPI payout recording
    "refund_allow_credit": "true",         # Named-customer ledger credit
    "exchange_allow": "true",
    "exchange_allow_anonymous": "true",    # No name or phone for direct offsets
    "exchange_allow_forfeit_difference": "false",  # Explicitly acknowledged, never default
    "show_receipt_logo": "true",
    "receipt_logo_width": "32",
    "receipt_paper": "80mm",
    # RAW ESC/POS is sent by the local pyPOS backend to a configured OS queue.
    # This does NOT change any historical invoices or payment records.
    "receipt_print_mode": "thermal",  # thermal or browser (manual fallback)
    "receipt_auto_print": "true",  # print after successful checkout/refund/exchange
    "receipt_printer_name": "",  # blank uses the current OS default printer
    "receipt_heading": "Retail receipt",
    "store_local_tax": "SGST",
    "tax_label": "GST",
    "price_tax_mode": "exclusive",
    "receipt_footer": "Thank you for shopping with us!",
    # Earning: floor(eligible spend / threshold) * points. Redemption credits
    # the customer wallet and does not modify any issued GST invoice.
    "loyalty_enabled": "true",
    "loyalty_earn_spend_rupees": "100",
    "loyalty_earn_points": "1",
    "loyalty_redeem_paise": "100",
    "low_stock_threshold": "5",
    # Receipt tax breakup (e.g. GST printed as CGST + SGST)
    "tax_breakup": "false",
    "tax_comp1_enabled": "true",
    "tax_comp1_name": "CGST",
    "tax_comp1_share": "50",
    "tax_comp2_enabled": "true",
    "tax_comp2_name": "SGST",
    "tax_comp2_share": "50",
    "auto_updates_enabled": "true",  # Managed launcher: check, stage and activate updates
    "lan_access_enabled": "false",  # Administrator opt-in; takes effect after restart.
    "auto_backup_enabled": "true",
    "auto_backup_interval_hours": "24",  # Legacy API for old clients.
    "auto_backup_interval_minutes": "10",
    "backup_retention_days": "30",
    "backup_day_end_enabled": "true",
    "backup_pre_migration_enabled": "true",
    "backup_replication_enabled": "true",  # Outbox for Syncthing, not the live DB.
    "backup_keep": "500",  # Secondary safety ceiling after tiered retention.
    "backup_max_mb": "4096",  # Storage safety ceiling; can shorten retention.

    # Google Drive OAuth credentials (native, no rclone needed)
    "gdrive_client_id": "",
    "gdrive_client_secret": "",
    "gdrive_refresh_token": "",
    # Settings-area admin access (web UI only; CLI bypasses)
    "admin_password": "",
    "session_secret": "",
}

# Never returned by get_settings(); only get_all_settings() (backend use).
SECRET_KEYS = {"gdrive_client_secret", "gdrive_refresh_token",
               "admin_password", "session_secret"}


def get_settings(conn):
    """Public view of settings — safe to send to the browser."""
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    stored = {r["key"]: r["value"] for r in cur.fetchall()}
    merged = dict(SETTING_KEYS)
    merged.update({k: v for k, v in stored.items() if k in SETTING_KEYS})
    # A legacy 58 mm or A4 store must not suddenly receive 80 mm ESC/POS
    # commands merely because it upgraded to a release with direct printing.
    if "receipt_print_mode" not in stored and merged.get("receipt_paper") != "80mm":
        merged["receipt_print_mode"] = "browser"
    for k in SECRET_KEYS:
        merged[k] = ""
    return merged


def get_all_settings(conn):
    """Full settings including secrets — backend use only."""
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    stored = {r["key"]: r["value"] for r in cur.fetchall()}
    merged = dict(SETTING_KEYS)
    merged.update({k: v for k, v in stored.items() if k in SETTING_KEYS})
    if "receipt_print_mode" not in stored and merged.get("receipt_paper") != "80mm":
        merged["receipt_print_mode"] = "browser"
    return merged


def save_settings(conn, values):
    """Persist settings. Secret keys: empty string means 'keep current'."""
    if not isinstance(values, dict):
        raise ValueError("Settings must be an object")
    unknown = [k for k in values if k not in SETTING_KEYS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
    normalized = {}
    for key, value in values.items():
        value = "" if value is None else str(value)
        if key == "store_gstin":
            value = gstin(value)
        elif key in ("store_logo", "pos_logo"):
            value = logo_data(value)
        elif key in ("show_staff_menu", "cashier_allow_stock_reduction",
                     "cashier_allow_stock_import", "refund_allow_original",
                     "refund_allow_credit", "exchange_allow",
                     "exchange_allow_anonymous",
                     "exchange_allow_forfeit_difference") and value not in ("true", "false"):
            raise ValueError(f"{key} must be true or false")
        elif key == "cashier_max_discount_percent":
            if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 100:
                raise ValueError("Cashier maximum discount must be a whole percentage (0–100)")
        elif key == "pos_display_name":
            value = " ".join(value.split())
            if not 1 <= len(value) <= 48 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("POS display name must be 1 to 48 characters without control characters")
        elif key == "pos_accent_color":
            import re
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                raise ValueError("Accent colour must be a six-digit hexadecimal colour (#RRGGBB)")
            value = value.upper()
        elif key == "pos_logo_size":
            if not value.isascii() or not value.isdecimal() or not 24 <= int(value) <= 48:
                raise ValueError("POS logo size must be 24 to 48 pixels")
        elif key == "pos_logo_mode" and value not in ("default", "receipt", "custom", "none"):
            raise ValueError("POS logo must be default, receipt, custom or none")
        elif key == "store_state_code":
            value = value.strip()
            if value and (not value.isdigit() or len(value) != 2 or
                          not (1 <= int(value) <= 38 or value == "97")):
                raise ValueError("State/UT code must be a valid two-digit GST code")
        elif key == "price_tax_mode" and value not in ("inclusive", "exclusive"):
            raise ValueError("Price tax mode must be inclusive or exclusive")
        elif key in ("receipt_auto_print", "auto_updates_enabled",
                     "auto_backup_enabled", "backup_day_end_enabled",
                     "backup_pre_migration_enabled", "backup_replication_enabled",
                     "lan_access_enabled") and value not in ("true", "false"):
            raise ValueError(f"{key} must be true or false")
        elif key == "auto_backup_interval_minutes":
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 1440:
                raise ValueError("Backup interval must be between 1 and 1440 minutes")
        elif key == "backup_retention_days":
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 365:
                raise ValueError("Backup retention must be between 1 and 365 days")
        elif key in ("backup_keep", "backup_max_mb", "auto_backup_interval_hours"):
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= (100000 if key == "backup_keep" else 1000000):
                raise ValueError(f"{key} must be a positive integer")
        elif key in ("receipt_auto_print", "auto_updates_enabled") and value not in ("true", "false"):

            raise ValueError(f"{key} must be true or false")
        elif key == "receipt_print_mode" and value not in ("thermal", "browser"):
            raise ValueError("Receipt print mode must be thermal or browser")
        elif key == "receipt_printer_name":
            value = value.strip()
            if len(value) > 160 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError("Printer queue name must be at most 160 visible characters")
        elif key == "receipt_paper" and value not in ("58mm", "80mm", "A4"):
            raise ValueError("Receipt paper must be 58mm, 80mm or A4")
        elif key == "store_local_tax" and value not in ("SGST", "UTGST"):
            raise ValueError("Local GST type must be SGST or UTGST")
        elif key == "receipt_heading" and value not in ("Retail receipt", "Tax invoice", "Bill of supply"):
            raise ValueError("Invalid receipt heading")
        elif key == "show_receipt_logo" and value not in ("true", "false"):
            raise ValueError("Invalid logo visibility")
        elif key == "receipt_logo_width":
            if not value.isdigit() or not 12 <= int(value) <= 65:
                raise ValueError("Logo width must be 12–65 mm")
        elif key == "loyalty_enabled":
            if value not in ("true", "false"):
                raise ValueError("Loyalty enabled must be true or false")
        elif key in ("loyalty_earn_spend_rupees", "loyalty_earn_points", "loyalty_redeem_paise"):
            # Integer-only policy values make payout calculations exact.
            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 100000:
                raise ValueError(f"{key} must be an integer between 1 and 100000")
        elif key == "store_fssai":
            value = value.strip()
            if value and (not value.isdigit() or len(value) != 14):
                raise ValueError("FSSAI licence number must be 14 digits")
        elif key in ("store_name", "store_address", "store_phone", "store_state", "receipt_footer", "store_upi_id"):
            if len(value) > (500 if key in ("store_address", "receipt_footer") else 120):
                raise ValueError(f"{key} is too long")
        if key in SECRET_KEYS and value == "":
            continue  # blank field on a form should not erase stored secrets
        normalized[key] = value
    combined = {**get_settings(conn), **normalized}
    if combined["receipt_print_mode"] == "thermal" and combined["receipt_paper"] != "80mm":
        raise ValueError("Epson TM-T81 direct printing uses 80mm paper; use browser mode for other sizes")
    if combined["pos_logo_mode"] == "custom" and not combined["pos_logo"]:
        raise ValueError("Upload a custom POS logo before selecting custom logo mode")
    if (combined["store_gstin"] and combined["store_state_code"] and
            combined["store_gstin"][:2] != combined["store_state_code"]):
        raise ValueError("GSTIN State/UT code must match the supplier State/UT code")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for key, value in normalized.items():
            conn.execute(
                ''' INSERT INTO settings(key, value) VALUES(?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value ''',
                (key, value),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_settings(conn)


def clear_setting(conn, key):
    """Explicitly erase a setting (e.g. revoking the Drive refresh token)."""
    if key not in SETTING_KEYS:
        raise ValueError(f"Unknown setting: {key}")
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
