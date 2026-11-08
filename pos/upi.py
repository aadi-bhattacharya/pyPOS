"""UPI payment QR generation: offline, without claiming payment confirmation."""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

VPA = re.compile(r"^[A-Za-z0-9_.+\-]{2,128}@[A-Za-z0-9.\-]{2,64}$")
REFERENCE = re.compile(r"^[A-Za-z0-9._-]{6,35}$")

def payment_uri(vpa, name, amount, reference):
    if not isinstance(vpa, str) or not VPA.fullmatch(vpa):
        raise ValueError("Configure a valid store UPI ID in Settings")
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
        raise ValueError("Invalid payment reference")
    try:
        money = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid payment amount")
    if not money.is_finite() or money <= 0 or money > 10000000 or money != money.quantize(Decimal('0.01')):
        raise ValueError("Amount must be positive INR with at most two decimals")
    name = str(name or 'Store').strip()[:60]
    return 'upi://pay?' + urlencode({'pa':vpa, 'pn':name, 'am':f'{money:.2f}',
        'cu':'INR', 'tr':reference, 'tn':reference})

def payment_svg(vpa, name, amount, reference):
    from io import BytesIO
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError as e:
        raise RuntimeError("QR generator not installed. Install qrcode>=7.4 to enable UPI QR") from e
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=8)
    qr.add_data(payment_uri(vpa, name, amount, reference))
    qr.make(fit=True)
    stream = BytesIO()
    qr.make_image(image_factory=SvgPathImage).save(stream)
    return stream.getvalue()
