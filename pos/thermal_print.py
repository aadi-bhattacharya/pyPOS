"""Print recorded POS receipts directly to an installed ESC/POS printer.

The browser never supplies receipt text or ESC/POS commands. Everything is
rendered from the committed invoice and the server's current store settings.
Supports original Epson TM-T81 (512 dots at 180 dpi) and an OS RAW print queue.
"""
from __future__ import annotations

import base64
import ctypes
import io
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

PAPER_DOTS = 512  # TM-T81: 79.5 mm paper, ~72.2 mm printable area
# India uses UTC+05:30 year-round for current POS transactions. A fixed
# offset avoids depending on an OS IANA zone database, missing on many
# Windows systems and some frozen builds.
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
MAX_IMAGE_HEIGHT = 60000


class PrinterError(RuntimeError):
    """Printer unavailable or refused the spool job; sale remains recorded."""


def _money(value):
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        amount = Decimal('0.00')
    return f"Rs {amount:,.2f}"


def _clean(value):
    # Terminal control bytes and newlines from user-controlled product names
    # must never become printer commands or unbounded line breaks.
    return ' '.join(str(value or '').split()).strip()


def _printed_date(value):
    if not value:
        return ''
    try:
        dt = datetime.fromisoformat(str(value).replace(' ', 'T').replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(INDIA_TIMEZONE).strftime('%d %b %Y %I:%M %p IST')
    except (ValueError, OverflowError):
        return _clean(value)


def _logo(settings, max_pixels):
    if settings.get('show_receipt_logo') != 'true':
        return None
    value = settings.get('store_logo') or ''
    match = re.fullmatch(r'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value)
    if not match or len(value) > 350000:
        return None
    from PIL import Image, ImageOps
    try:
        raw = base64.b64decode(match.group(1), validate=True)
        with Image.open(io.BytesIO(raw)) as original:
            original.load()
            if original.width * original.height > 4000000:
                return None
            image = ImageOps.exif_transpose(original).convert('RGBA')
        target = max(80, min(max_pixels, round(float(settings.get('receipt_logo_width') or 32) * 180 / 25.4)))
        image.thumbnail((target, 350))
        white = Image.new('RGB', image.size, 'white')
        white.paste(image, mask=image.getchannel('A'))
        return white
    except (OSError, ValueError, TypeError):
        return None


class _Canvas:
    def __init__(self, settings):
        from PIL import Image, ImageDraw, ImageFont
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.font = ImageFont.load_default(size=20)
        self.heading = ImageFont.load_default(size=25)
        self.width = PAPER_DOTS
        self.margin = 10
        self.lines = []
        self.settings = settings
        self.measure = ImageDraw.Draw(Image.new('RGB', (1, 1)))

    def _wrap(self, text, font):
        max_width = self.width - 2 * self.margin
        words = _clean(text).split(' ')
        current = ''
        output = []
        for word in words:
            candidate = (current + ' ' + word).strip()
            if self.measure.textlength(candidate, font=font) <= max_width:
                current = candidate
                continue
            if current:
                output.append(current)
                current = ''
            # A single uninterrupted barcode/identifier can exceed the width.
            for char in word:
                if self.measure.textlength(current + char, font=font) > max_width and current:
                    output.append(current)
                    current = ''
                current += char
        if current:
            output.append(current)
        return output or ['']

    def text(self, text, *, center=False, bold=False):
        font = self.heading if bold else self.font
        for line in self._wrap(text, font):
            self.lines.append(('text', line, center, font, 34 if bold else 27))

    def pair(self, left, right, *, bold=False):
        font = self.heading if bold else self.font
        right = _clean(right)
        right_width = self.measure.textlength(right, font=font)
        available = max(40, self.width - self.margin * 2 - right_width - 12)
        words = _clean(left).split(' ')
        line = ''
        wrapped = []
        for word in words:
            candidate = (line + ' ' + word).strip()
            if self.measure.textlength(candidate, font=font) <= available:
                line = candidate
            else:
                if line: wrapped.append(line)
                line = word
        if line: wrapped.append(line)
        for prior in wrapped[:-1]:
            self.text(prior, bold=bold)
        self.lines.append(('pair', wrapped[-1] if wrapped else '', right, font, 34 if bold else 27))

    def rule(self):
        self.lines.append(('rule', 9))

    def logo(self):
        img = _logo(self.settings, self.width - 2 * self.margin)
        if img:
            self.lines.append(('image', img))

    def image(self):
        total = self.margin + sum(x[-1] if x[0] in ('text', 'pair', 'rule') else x[1].height + 10
                                  for x in self.lines) + 12
        if total > MAX_IMAGE_HEIGHT:
            raise PrinterError('Receipt too long for this thermal print job')
        canvas = self.Image.new('RGB', (self.width, total), 'white')
        draw = self.ImageDraw.Draw(canvas)
        y = self.margin
        for op in self.lines:
            if op[0] == 'text':
                _, text, centered, font, height = op
                x = (self.width - draw.textlength(text, font=font)) / 2 if centered else self.margin
                draw.text((x, y), text, font=font, fill='black')
                y += height
            elif op[0] == 'pair':
                _, left, right, font, height = op
                draw.text((self.margin, y), left, font=font, fill='black')
                draw.text((self.width - self.margin - draw.textlength(right, font=font), y),
                          right, font=font, fill='black')
                y += height
            elif op[0] == 'rule':
                draw.line((self.margin, y + 3, self.width - self.margin, y + 3), fill='black', width=1)
                y += op[1]
            else:
                img = op[1]
                canvas.paste(img, ((self.width - img.width)//2, y))
                y += img.height + 10
        return canvas


def render_receipt(receipt, store):
    """Render the immutable recorded invoice values to 512-dot thermal pixels."""
    cfg = {**store, **(receipt.get('invoice') or {})}
    c = _Canvas(cfg)
    c.logo()
    c.text(cfg.get('store_name') or 'My Store', center=True, bold=True)
    for k in ('store_address', 'store_phone', 'store_state', 'store_gstin', 'store_fssai'):
        v = cfg.get(k)
        if v:
            prefix = 'GSTIN: ' if k == 'store_gstin' else 'FSSAI: ' if k == 'store_fssai' else ''
            c.text(prefix + str(v), center=True)
    c.text('REFUND RECEIPT' if receipt.get('status') == 'REFUNDED' else
           cfg.get('receipt_heading') or 'Retail receipt', center=True, bold=True)
    c.rule()
    if receipt.get('refund_number'):
        c.text('Refund: ' + str(receipt['refund_number']))
    elif receipt.get('invoice_number'):
        c.text('Invoice: ' + str(receipt['invoice_number']))
    else:
        c.text('Receipt #' + str(receipt.get('id', '')))
    c.text(_printed_date(receipt.get('timestamp')))
    if receipt.get('parent_sale_id'):
        c.text('For sale: ' + str(receipt.get('parent_invoice_number') or receipt['parent_sale_id']))
    if receipt.get('customer_id') is not None:
        c.text('Customer ID: ' + str(receipt['customer_id']))
    if cfg.get('customer_gstin'):
        if cfg.get('customer_name'):
            c.text('Buyer: ' + str(cfg['customer_name']))
        c.text('Buyer GSTIN: ' + str(cfg['customer_gstin']))
    if cfg.get('customer_address'):
        c.text('Bill to: ' + str(cfg['customer_address']))
    if cfg.get('supply_type') == 'inter' and cfg.get('place_of_supply'):
        c.text('Place of supply: ' + str(cfg['place_of_supply']))
    if receipt.get('refund_reason'):
        c.text('Reason: ' + str(receipt['refund_reason']))
    for link in receipt.get('exchange_links') or []:
        if link.get('role') == 'return':
            c.text('Exchange to ' + str(link.get('replacement_invoice_number') or ''))
        elif link.get('role') == 'replacement':
            c.text('Exchange from ' + str(link.get('refund_number') or ''))
    c.rule()
    c.pair('ITEM / QTY x RATE', 'TOTAL')
    for item in receipt.get('items') or []:
        c.text(item.get('receipt_name') or item.get('name') or 'Item')
        if item.get('receipt_variant'):
            c.text(str(item['receipt_variant']))
        if item.get('hsn_code'):
            c.text('HSN: ' + str(item['hsn_code']))
        c.pair(f"{item.get('quantity', 0)} {item.get('unit_code') or ''} x {_money(item.get('unit_price'))}",
               _money(item.get('line_total')))
    c.rule()
    c.pair('Subtotal', _money(receipt.get('subtotal')))
    if float(receipt.get('discount_total') or 0) > 0:
        c.pair('Discount', '-' + _money(receipt['discount_total']))
    c.pair('Taxable value', _money(Decimal(str(receipt.get('total') or 0)) -
                                   Decimal(str(receipt.get('tax_total') or 0))))
    c.pair('GST included' if cfg.get('price_tax_mode') == 'inclusive' else 'GST',
           _money(receipt.get('tax_total')))
    for b in receipt.get('tax_breakup') or []:
        c.pair(f"{b.get('name', 'GST')} @ {b.get('rate', 0)}%", _money(b.get('amount')))
    if receipt.get('tax_review_warning'):
        c.text('GST REVIEW REQUIRED: check original invoice')
    c.pair('TOTAL REFUNDED' if receipt.get('status') == 'REFUNDED' else 'TOTAL',
           _money(receipt.get('total')), bold=True)
    c.rule()
    for payment in receipt.get('payments') or []:
        c.pair(str(payment.get('method') or 'Paid'), _money(payment.get('amount')))
    for link in receipt.get('exchange_links') or []:
        if link.get('role') == 'return' and float(link.get('offset_amount') or 0) > 0:
            c.pair('Return applied to exchange (not cash)', _money(link['offset_amount']))
        if link.get('role') == 'replacement' and float(link.get('forfeited_amount') or 0) > 0:
            c.pair('Exchange difference forfeited', _money(link['forfeited_amount']))
    if float(receipt.get('exchange_offset_used') or 0) > 0:
        c.pair('Exchange return applied (not cash)', '-' + _money(receipt['exchange_offset_used']))
    if float(receipt.get('store_credit_used') or 0) > 0:
        c.pair('Store credit used', '-' + _money(receipt['store_credit_used']))
    if float(receipt.get('store_credit_refunded') or 0) > 0:
        c.pair('Returned to credit', _money(receipt['store_credit_refunded']))
    if float(receipt.get('change') or 0) > 0:
        c.pair('Change', _money(receipt['change']))
    if receipt.get('loyalty_points_earned'):
        c.pair('Loyalty earned', str(receipt['loyalty_points_earned']))
    if receipt.get('loyalty_points_reversed'):
        c.pair('Loyalty reversed', str(receipt['loyalty_points_reversed']))
    c.rule()
    if receipt.get('status') == 'REFUNDED': c.text('** REFUND **', center=True, bold=True)
    elif receipt.get('status') == 'VOID': c.text('** VOIDED **', center=True, bold=True)
    if cfg.get('store_upi_id') and receipt.get('status') != 'REFUNDED':
        c.text('UPI: ' + str(cfg['store_upi_id']), center=True)
    if cfg.get('receipt_footer'):
        c.text(str(cfg['receipt_footer']), center=True)
    return c.image()


def render_test(store):
    c = _Canvas(store)
    c.logo()
    c.text(store.get('store_name') or 'My Store', center=True, bold=True)
    c.rule()
    c.text('TM-T81 TEST PRINT', center=True, bold=True)
    c.text('80 mm roll / 512 dots / 180 dpi', center=True)
    c.text('NOT A SALE', center=True)
    c.text('Rs 123.45 | GST 5% | INR Rs', center=True)
    c.rule()
    return c.image()


def raster_commands(image):
    """ESC/POS GS v 0, 512 dots across; chunks stay below TM-T81 row limits."""
    from PIL import Image
    if image.width != PAPER_DOTS or not 1 <= image.height <= MAX_IMAGE_HEIGHT:
        raise PrinterError('Unsupported thermal image dimensions')
    # Convert with black pixel == 0, white == 255. Dithering reproduces logos.
    mono = image.convert('1', dither=Image.Dither.FLOYDSTEINBERG)
    raw = bytearray(b'\x1b@')
    for top in range(0, mono.height, 128):
        rows = min(128, mono.height - top)
        raw += b'\x1dv0\x00' + bytes((64, 0, rows & 255, rows >> 8))
        for y in range(top, top + rows):
            for x in range(0, PAPER_DOTS, 8):
                byte = 0
                for bit in range(8):
                    if mono.getpixel((x + bit, y)) == 0:
                        byte |= (1 << (7 - bit))
                raw.append(byte)
    raw += b'\n\n\n\x1dV\x01'  # advance and partial cut
    return bytes(raw)


def _windows_default_printer():
    from ctypes import wintypes
    spool = ctypes.WinDLL('winspool.drv', use_last_error=True)
    spool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    spool.GetDefaultPrinterW.restype = wintypes.BOOL
    count = wintypes.DWORD()
    spool.GetDefaultPrinterW(None, ctypes.byref(count))
    if count.value == 0:
        raise PrinterError('No default printer. Configure an Epson TM-T81 printer in Windows.')
    buf = ctypes.create_unicode_buffer(count.value)
    if not spool.GetDefaultPrinterW(buf, ctypes.byref(count)):
        raise PrinterError('Cannot identify the Windows default printer')
    return buf.value


def _spool_windows(printer_name, payload):
    from ctypes import wintypes
    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [('pDocName', wintypes.LPWSTR), ('pOutputFile', wintypes.LPWSTR),
                    ('pDatatype', wintypes.LPWSTR)]
    spool = ctypes.WinDLL('winspool.drv', use_last_error=True)
    spool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    spool.OpenPrinterW.restype = wintypes.BOOL
    spool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    spool.StartDocPrinterW.restype = wintypes.DWORD
    spool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    spool.StartPagePrinter.restype = wintypes.BOOL
    spool.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD)]
    spool.WritePrinter.restype = wintypes.BOOL
    spool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    spool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    spool.ClosePrinter.argtypes = [wintypes.HANDLE]
    handle = wintypes.HANDLE()
    if not spool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise PrinterError(f'Cannot open Windows printer {printer_name!r} (error {ctypes.get_last_error()})')
    try:
        doc = DOC_INFO_1('pyPOS receipt', None, 'RAW')
        if not spool.StartDocPrinterW(handle, 1, ctypes.byref(doc)):
            raise PrinterError(f'Windows spooler rejected receipt (error {ctypes.get_last_error()})')
        try:
            if not spool.StartPagePrinter(handle):
                raise PrinterError(f'Windows printer could not start a page (error {ctypes.get_last_error()})')
            try:
                written = wintypes.DWORD()
                buffer = ctypes.create_string_buffer(payload)
                if not spool.WritePrinter(handle, buffer, len(payload), ctypes.byref(written)):
                    raise PrinterError(f'Windows printer write failed (error {ctypes.get_last_error()})')
                if written.value != len(payload):
                    raise PrinterError(f'Windows printer accepted {written.value}/{len(payload)} bytes')
            finally:
                spool.EndPagePrinter(handle)
        finally:
            spool.EndDocPrinter(handle)
    finally:
        spool.ClosePrinter(handle)


def _spool_cups(printer_name, payload):
    args = ['lp', '-o', 'raw', '-t', 'pyPOS receipt']
    if printer_name:
        args.extend(['-d', printer_name])
    try:
        res = subprocess.run(args, input=payload, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=20, check=False)
    except FileNotFoundError as exc:
        raise PrinterError('CUPS lp command not installed. Install and configure CUPS for your TM-T81.') from exc
    except subprocess.TimeoutExpired as exc:
        raise PrinterError('Printer spooler timed out; check its queue before reprinting') from exc
    except OSError as exc:
        raise PrinterError(f'Cannot submit to the CUPS printer queue: {exc}') from exc
    if res.returncode:
        msg = res.stderr.decode('utf-8', 'replace').strip()[:300]
        raise PrinterError(f'CUPS print job rejected: {msg or res.returncode}')


def send_image(image, settings):
    if settings.get('receipt_paper') != '80mm':
        raise PrinterError('Epson TM-T81 direct printing requires 80mm receipt paper setting')
    name = (settings.get('receipt_printer_name') or '').strip()
    if len(name) > 160 or any(ord(c) < 32 for c in name):
        raise PrinterError('Invalid printer queue name')
    payload = raster_commands(image)
    if sys.platform == 'win32':
        selected = name or _windows_default_printer()
        _spool_windows(selected, payload)
    elif sys.platform.startswith(('linux', 'darwin', 'freebsd')):
        selected = name or 'system default'
        _spool_cups(name, payload)
    else:
        raise PrinterError(f'Direct printing is unsupported on {sys.platform}')
    # Spooler acknowledgement does NOT prove that ink/paper was physically used.
    return selected


def default_printer():
    if sys.platform == 'win32':
        return _windows_default_printer()
    try:
        res = subprocess.run(['lpstat', '-d'], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=4, check=False)
        if res.returncode:
            return ''
        txt = res.stdout.decode('utf-8', 'replace').strip()
        return txt.split(':', 1)[1].strip() if ':' in txt else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def print_recorded_sale(conn, sale_id, *, manual=False):
    """At-most-once *automatic spool submission* per invoice, including retries.

    A retry after uncertainty is manual (audit logged), never a silent repeat.
    """
    from . import sales, settings as settings_mod
    cfg = settings_mod.get_settings(conn)
    if cfg['receipt_print_mode'] != 'thermal':
        raise PrinterError('Thermal printing is disabled; enable it in Settings')
    kind = 'manual' if manual else 'auto'
    # A unique index makes two racing auto-print requests idempotent.
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not manual:
            prior = conn.execute('SELECT status FROM receipt_print_jobs WHERE sale_id=? AND kind=?',
                                 (sale_id, kind)).fetchone()
            if prior:
                conn.commit()
                return {'status': 'already_requested', 'previous_status': prior['status']}
        receipt = sales.get_sale_detail(conn, sale_id)  # do not insert a job for an invalid ID
        # Checkout records cash tender change with its idempotency key. Applied
        # payment rows deliberately exclude change, so restore the recorded
        # figure here instead of recalculating it from today's tender rules.
        change_row = conn.execute(
            'SELECT change_cents FROM sale_requests WHERE sale_id=?', (sale_id,)).fetchone()
        if change_row:
            receipt['change'] = change_row['change_cents'] / 100
        cur = conn.execute('INSERT INTO receipt_print_jobs (sale_id, kind, status) VALUES (?, ?, ?)',
                           (sale_id, kind, 'CLAIMED'))
        job_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    try:
        printer = send_image(render_receipt(receipt, cfg), cfg)
    except Exception as exc:
        error = str(exc)[:350]
        conn.execute('UPDATE receipt_print_jobs SET status=?, error=? WHERE id=?',
                     ('FAILED', error, job_id))
        if isinstance(exc, PrinterError): raise
        raise PrinterError(f'Thermal print failed: {error}') from exc
    conn.execute('UPDATE receipt_print_jobs SET status=?, printer_name=? WHERE id=?',
                 ('SPOOLED', printer, job_id))
    return {'status': 'spooled', 'printer': printer, 'job_id': job_id}


def render_z_report(report, settings):
    """Print X/Z cash-session report from server-owned database data."""
    c = _Canvas(settings)
    c.logo()
    c.text(settings.get('store_name') or 'My Store', center=True, bold=True)
    c.text('X REPORT - DAY OPEN' if report['is_open'] else 'Z REPORT - DAY CLOSED',
           center=True, bold=True)
    c.text('Session #' + str(report['session_id']), center=True)
    c.text(_printed_date(report['opened_at']))
    c.text(_printed_date(report.get('closed_at')) or 'OPEN')
    c.rule()
    values = [
        ('Transactions', str(report['transactions'])),
        ('Items sold', str(report['items_sold'])),
        ('Gross sales', _money(report['gross_sales'])),
        ('Discounts', _money(report['discounts'])),
        ('GST collected', _money(report['taxes'])),
        ('Net revenue', _money(report['net_revenue'])),
        ('Refunds', _money(report['refund_total'])),
        ('Cash in', _money(report['cash_in'])),
        ('Cash refunded', _money(report['cash_out'])),
        ('Card net', _money(report['card_net'])),
        ('UPI net', _money(report['upi_net'])),
        ('Credit spent', _money(report['credit_spent'])),
        ('Opening float', _money(report['opening_float'])),
        ('Manual cash in', _money(report['drawer_cash_in'])),
        ('Cash removed', _money(report['drawer_cash_out'])),
        ('Expected cash', _money(report['expected_cash'])),
    ]
    for k, v in values:
        c.pair(k, v, bold=k in ('Net revenue', 'Expected cash'))
    if report.get('counted_cash') is not None:
        c.pair('Counted cash', _money(report['counted_cash']))
    if report.get('difference') is not None:
        c.pair('Difference', _money(report['difference']))
    if report.get('note'):
        c.text('Note: ' + str(report['note']))
    c.rule()
    c.text('DAY OPEN' if report['is_open'] else 'DAY CLOSED', center=True)
    return c.image()
