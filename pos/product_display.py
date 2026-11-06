"""Customer-facing item descriptions, separate from editable product metadata.

Never use inventory diagnostics, supplier cost, source-sheet data or internal
style codes in receipts. Older sale snapshots are preserved in SQLite; only
their on-screen/print presentation is cleaned here.
"""
import re

_PUBLIC = {
    "colour": "Colour", "color": "Color", "size": "Size",
    "fit": "Fit", "fabric": "Fabric", "pattern": "Pattern",
    "pack": "Pack", "length": "Length", "waist": "Waist",
}
_ORDER = {name: i for i, name in enumerate((
    "Colour", "Color", "Size", "Fit", "Fabric", "Pattern", "Pack", "Length", "Waist"))}
_END_ATTRIBUTES = re.compile(r"^(.*?)\s+\(([^()]*)\)$", flags=re.DOTALL)


def public_variant(attributes):
    """Allowlist: new unknown attributes stay editable, not printable by default."""
    values = {}
    for name, value in attributes:
        key = _PUBLIC.get(str(name).strip().casefold())
        value = str(value or "").strip()
        if key and value and key not in values:
            values[key] = value
    return ", ".join(f"{key}: {value}" for key, value in
                      sorted(values.items(), key=lambda pair: _ORDER[pair[0]]))


def receipt_parts(snapshot, fallback_attributes=None):
    """Parse a legacy 'Name (all attributes)' description safely.

    When no recognisable legacy suffix exists, keep the name intact; never
    heuristically alter arbitrary product names such as 'T-Shirt (Classic)'.
    """
    original = str(snapshot or "").strip()
    match = _END_ATTRIBUTES.fullmatch(original)
    if match:
        attrs = []
        saw_kv = False
        for piece in re.split(r", (?=[A-Za-z][A-Za-z /-]*: )", match.group(2)):
            if ": " not in piece:
                continue
            saw_kv = True
            name, value = piece.split(": ", 1)
            attrs.append((name, value))
        if saw_kv:
            return match.group(1), public_variant(attrs)
    if fallback_attributes is not None:
        return original, public_variant(fallback_attributes)
    return original, ""


def description_from_parts(name, variant):
    return f"{name} ({variant})" if variant else name
