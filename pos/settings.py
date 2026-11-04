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
    "show_receipt_logo": "true",
    "receipt_logo_width": "32",
    "receipt_paper": "80mm",
    "receipt_heading": "Retail receipt",
    "store_local_tax": "SGST",
    "tax_label": "GST",
    "price_tax_mode": "exclusive",
    "receipt_footer": "Thank you for shopping with us!",
    "low_stock_threshold": "5",
    # Receipt tax breakup (e.g. GST printed as CGST + SGST)
    "tax_breakup": "false",
    "tax_comp1_enabled": "true",
    "tax_comp1_name": "CGST",
    "tax_comp1_share": "50",
    "tax_comp2_enabled": "true",
    "tax_comp2_name": "SGST",
    "tax_comp2_share": "50",
    "auto_backup_enabled": "true",
    "auto_backup_interval_hours": "24",
    "backup_keep": "14",
    "backup_max_mb": "800",
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
        elif key == "store_logo":
            value = logo_data(value)
        elif key == "store_state_code":
            value = value.strip()
            if value and (not value.isdigit() or len(value) != 2 or
                          not (1 <= int(value) <= 38 or value == "97")):
                raise ValueError("State/UT code must be a valid two-digit GST code")
        elif key == "price_tax_mode" and value not in ("inclusive", "exclusive"):
            raise ValueError("Price tax mode must be inclusive or exclusive")
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
