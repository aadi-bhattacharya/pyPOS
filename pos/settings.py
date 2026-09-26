from .db import connect

SETTING_KEYS = {
    "store_name": "My Store",
    "store_address": "",
    "store_phone": "",
    "currency": "$",
    "tax_label": "Tax",
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
    cur = conn.cursor()
    for key, value in values.items():
        value = "" if value is None else str(value)
        if key in SECRET_KEYS and value == "":
            continue  # blank field on a form should not erase stored secrets
        cur.execute(
            ''' INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value ''',
            (key, value),
        )
    conn.commit()
    return get_settings(conn)


def clear_setting(conn, key):
    """Explicitly erase a setting (e.g. revoking the Drive refresh token)."""
    if key not in SETTING_KEYS:
        raise ValueError(f"Unknown setting: {key}")
    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
