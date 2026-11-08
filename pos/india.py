"""Conservative India-specific field validation and fiscal-year invoice numbering.

A structurally valid GSTIN does not prove active registration. GST treatment
must still be determined by the merchant/accountant for each supply.
"""
import base64
import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
HSN_PATTERN = re.compile(r"^[0-9]{2,8}$")
LOGO_PATTERN = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
LOGO_MAX_BYTES = 200 * 1024


def gstin(value):
    value = str(value or "").strip().upper()
    if value and not GSTIN_PATTERN.fullmatch(value):
        raise ValueError("GSTIN must have the 15-character Indian GSTIN format")
    if value and not (1 <= int(value[:2]) <= 38 or value[:2] == "97"):
        raise ValueError("GSTIN contains an invalid State/UT code")
    return value


def hsn(value):
    value = str(value or "").strip()
    if value and not HSN_PATTERN.fullmatch(value):
        raise ValueError("HSN/SAC must contain 2 to 8 digits")
    return value


def logo_data(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 280000:
        raise ValueError("Store logo is too large (maximum 200 KiB)")
    match = LOGO_PATTERN.fullmatch(value)
    if not match:
        raise ValueError("Store logo must be a PNG, JPEG or WebP image")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid base64 image data") from exc
    if len(raw) > LOGO_MAX_BYTES:
        raise ValueError("Store logo is too large (maximum 200 KiB)")
    signature_ok = {
        "png": raw.startswith(bytes.fromhex("89504e470d0a1a0a")),
        "jpeg": raw.startswith(b"\xff\xd8\xff"),
        "webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
    }
    if not signature_ok[match.group(1)]:
        raise ValueError("The file content does not match its image type")
    return value


def financial_year(when=None):
    local = (when or datetime.now(IST)).astimezone(IST)
    start_year = local.year if local.month >= 4 else local.year - 1
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def next_invoice_number(conn, when=None):
    """Must be invoked inside the checkout's BEGIN IMMEDIATE transaction."""
    year = financial_year(when)
    conn.execute("""INSERT INTO invoice_sequences(financial_year, last_number)
                    VALUES (?, 0) ON CONFLICT(financial_year) DO NOTHING""", (year,))
    conn.execute("UPDATE invoice_sequences SET last_number=last_number+1 WHERE financial_year=?", (year,))
    n = conn.execute("SELECT last_number FROM invoice_sequences WHERE financial_year=?", (year,)).fetchone()[0]
    # 16-char cap with room for a 10-digit sequence. A larger number is rejected.
    if n > 9999999999:
        raise ValueError("Invoice serial limit reached for this financial year")
    return f"{year}/{n:06d}"


def next_refund_number(conn, original_sale_id, when=None):
    """Issue a unique operational refund reference under BEGIN IMMEDIATE.

    Format: FY/R<6+ digit per-FY sequence>-<6+ digit original sale ID>.
    Example: 26-27/R000001-000123. Unlike a GST credit note, this is only
    an internal refund receipt identifier. No original invoice is renumbered.
    Both sequence increment and refund insertion must commit atomically.
    """
    sale_id = int(original_sale_id)
    if sale_id <= 0:
        raise ValueError("Original transaction ID must be positive")
    year = financial_year(when)
    conn.execute("""INSERT INTO refund_sequences(financial_year, last_number)
                    VALUES (?, 0) ON CONFLICT(financial_year) DO NOTHING""", (year,))
    conn.execute("UPDATE refund_sequences SET last_number=last_number+1 WHERE financial_year=?", (year,))
    serial = conn.execute(
        "SELECT last_number FROM refund_sequences WHERE financial_year=?", (year,)
    ).fetchone()[0]
    return f"{year}/R{serial:06d}-{sale_id:06d}"
