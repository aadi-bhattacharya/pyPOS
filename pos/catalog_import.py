"""Additive, previewable catalogue imports from an editable CSV/XLSX template.

No new runtime dependency: .xlsx is read through stdlib ZIP/XML. Import is a
single database transaction and never deletes, replaces, or locks products.
"""
import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
import io
import re
import sqlite3
from . import catalog
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

MAX_UPLOAD = 8 * 1024 * 1024
MAX_ROWS = 12000
MAX_COLUMNS = 80
MAX_UNCOMPRESSED = 32 * 1024 * 1024
MODES = {"skip", "add_stock", "merge"}
BASE_FIELDS = {"sku", "product_name", "barcode", "price", "quantity", "cost_price",
               "tax_rate", "hsn_code", "unit_code", "notes"}
NEEDED = {"sku", "product_name", "quantity", "price"}
XML_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class ImportError(ValueError):
    pass


def _text(value):
    return str(value).strip() if value is not None else ""


def _parse_amount(value, field, row, optional=False):
    text = _text(value).replace(",", "").replace("₹", "")
    if not text:
        if optional:
            return None
        raise ImportError(f"Row {row}: {field} is required")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ImportError(f"Row {row}: {field} must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise ImportError(f"Row {row}: {field} must be a non-negative finite number")
    if result > 100_000_000:
        raise ImportError(f"Row {row}: {field} exceeds the supported limit")
    return result


def _read_xlsx(data):
    try:
        with ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 600 or sum(f.file_size for f in infos) > MAX_UNCOMPRESSED:
                raise ImportError("XLSX is too large or contains too many entries")
            names = {info.filename for info in infos}
            if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
                raise ImportError("XLSX has no readable workbook")
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.findall(f"{{{XML_MAIN}}}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{{{XML_MAIN}}}t")))
            book = ET.fromstring(z.read("xl/workbook.xml"))
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            targets = {}
            for rel in rels.findall(f"{{{XML_PKG_REL}}}Relationship"):
                targets[rel.attrib["Id"]] = rel.attrib.get("Target", "")
            sheets = book.find(f"{{{XML_MAIN}}}sheets")
            if sheets is None or not list(sheets):
                raise ImportError("XLSX has no worksheets")
            chosen = next((s for s in sheets if s.attrib.get("name", "").lower() == "import"), None)
            if chosen is None:
                chosen = list(sheets)[0]
            rid = chosen.attrib.get(f"{{{XML_REL}}}id", "")
            target = targets.get(rid, "")
            if not target:
                raise ImportError("Cannot identify the Import worksheet")
            if target.startswith("/xl/"):
                path = target.lstrip("/")
            elif target.startswith("xl/"):
                path = target
            else:
                path = "xl/" + target
            from posixpath import normpath
            path = normpath(path)
            if not path.startswith("xl/worksheets/") or path not in names:
                raise ImportError("Import worksheet path is invalid")
            root = ET.fromstring(z.read(path))
            data_el = root.find(f"{{{XML_MAIN}}}sheetData")
            if data_el is None:
                raise ImportError("Import worksheet has no data")
            rows = []
            for row in data_el.findall(f"{{{XML_MAIN}}}row"):
                if len(rows) >= MAX_ROWS + 1:
                    raise ImportError(f"Import exceeds the {MAX_ROWS}-row limit")
                cells = {}
                for cell in row.findall(f"{{{XML_MAIN}}}c"):
                    addr = cell.get("r", "")
                    m = re.match(r"^([A-Z]+)\d+$", addr)
                    if not m:
                        continue
                    col = 0
                    for letter in m.group(1):
                        col = col * 26 + ord(letter) - 64
                    col -= 1
                    if col >= MAX_COLUMNS:
                        raise ImportError("Import contains too many columns")
                    kind = cell.attrib.get("t", "")
                    v = cell.find(f"{{{XML_MAIN}}}v")
                    if kind == "inlineStr":
                        is_el = cell.find(f"{{{XML_MAIN}}}is")
                        value = "".join(e.text or "" for e in is_el.iter(f"{{{XML_MAIN}}}t")) if is_el is not None else ""
                    elif v is None:
                        value = ""
                    elif kind == "s":
                        idx = int(v.text or 0)
                        if idx < 0 or idx >= len(shared):
                            raise ImportError("Invalid XLSX shared string")
                        value = shared[idx]
                    else:
                        value = v.text or ""
                    cells[col] = value
                rows.append([cells.get(i, "") for i in range(max(cells, default=-1) + 1)])
            return rows
    except (BadZipFile, ET.ParseError, KeyError, IndexError, OSError, OverflowError,
            ValueError, RuntimeError) as exc:
        raise ImportError(f"Unreadable XLSX file: {exc}") from exc


def _read_table(file_bytes, filename):
    if len(file_bytes) > MAX_UPLOAD:
        raise ImportError("Maximum upload size is 8 MB")
    if not file_bytes:
        raise ImportError("File is empty")
    if filename.lower().endswith(".xlsx"):
        return _read_xlsx(file_bytes)
    if filename.lower().endswith(".csv"):
        try:
            content = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportError("Save the CSV as UTF-8 (CSV UTF-8 in Excel)") from exc
        rows = list(csv.reader(io.StringIO(content, newline="")))
        if len(rows) > MAX_ROWS + 1:
            raise ImportError(f"Import exceeds the {MAX_ROWS}-row limit")
        return rows
    raise ImportError("Please select a .csv or .xlsx catalogue template")


def parse(file_bytes, filename):
    table = _read_table(file_bytes, filename)
    if not table:
        raise ImportError("There are no rows")
    raw_header = [_text(v).lstrip("\ufeff") for v in table[0]]
    header = [("attr:" + h[5:].strip()) if h.lower().startswith("attr:")
              else h.lower() for h in raw_header]
    if len(header) > MAX_COLUMNS:
        raise ImportError("Import contains too many columns")
    if len(set(h.casefold() for h in header if h)) != len([h for h in header if h]):
        raise ImportError("Duplicate column names. Each attribute heading must be unique")
    missing = NEEDED - set(header)
    if missing:
        raise ImportError("Missing required columns: " + ", ".join(sorted(missing)))
    invalid_headers = [h for h in header if h and h not in BASE_FIELDS and not h.startswith("attr:")]
    if invalid_headers:
        raise ImportError("Unexpected columns: " + ", ".join(invalid_headers[:6]) + ". Use attr:Name for custom fields")
    items = []
    issues = []
    seen = set()
    for rownum, raw in enumerate(table[1:], start=2):
        if not any(_text(x) for x in raw):
            continue
        row = dict(zip(header, raw + [""] * max(0, len(header) - len(raw))))
        try:
            sku = _text(row.get("sku"))
            name = _text(row.get("product_name"))
            if not sku or not name:
                raise ImportError(f"Row {rownum}: SKU and product_name are required")
            if len(sku) > 180 or len(name) > 220:
                raise ImportError(f"Row {rownum}: product name or SKU is too long")
            if sku.casefold() in seen:
                raise ImportError(f"Row {rownum}: duplicate SKU '{sku}' inside this file")
            seen.add(sku.casefold())
            qty = _parse_amount(row.get("quantity"), "quantity", rownum)
            if qty != qty.to_integral_value() or qty > 1_000_000:
                raise ImportError(f"Row {rownum}: quantity must be a whole number (0–1,000,000)")
            price = _parse_amount(row.get("price"), "price", rownum, optional=True)
            cost = _parse_amount(row.get("cost_price"), "cost_price", rownum, optional=True)
            rate = _parse_amount(row.get("tax_rate"), "tax_rate", rownum, optional=True)
            if rate is not None and rate > 100:
                raise ImportError(f"Row {rownum}: GST rate cannot exceed 100%")
            hsn = _text(row.get("hsn_code"))
            if hsn and not re.fullmatch(r"\d{2,8}", hsn):
                raise ImportError(f"Row {rownum}: HSN/SAC should be 2–8 digits")
            unit = _text(row.get("unit_code") or "NOS").upper()
            if not re.fullmatch(r"[A-Z]{2,8}", unit):
                raise ImportError(f"Row {rownum}: unit_code must be 2–8 letters")
            barcode = _text(row.get("barcode")) or None
            if barcode and len(barcode) > 180:
                raise ImportError(f"Row {rownum}: barcode is too long")
            attrs = {h[5:].strip(): _text(row.get(h)) for h in header
                     if h.startswith("attr:") and h[5:].strip() and _text(row.get(h))}
            if _text(row.get("notes")):
                attrs["Import Notes"] = _text(row["notes"])
            items.append({"source_row": rownum, "sku": sku, "name": name,
                          "barcode": barcode, "price": float(price) if price is not None else None,
                          "quantity": int(qty), "cost_price": float(cost) if cost is not None else None,
                          "tax_rate": float(rate) if rate is not None else None, "hsn_code": hsn,
                          "unit_code": unit, "attrs": attrs})
        except ImportError as exc:
            issues.append(str(exc))
    if not items and not issues:
        raise ImportError("No product rows found")
    return items, issues


def _barcode_review(conn, items):
    """Resolve repeated EANs by MRP, not by barcode uniqueness.

    A single EAN can have several prices in stock, but must not silently
    represent different colours, sizes or named styles.
    """
    rows = conn.execute("""SELECT p.id, p.SKU, p.price, p.group_id,
         COALESCE(p.barcode, (SELECT x.barcode FROM barcode_price_options x
                            WHERE x.product_id=p.id)) AS barcode,
         g.name AS name FROM products p
         LEFT JOIN product_groups g ON g.id=p.group_id""").fetchall()
    index = {}
    by_sku = {}
    for r in rows:
        r = dict(r)
        by_sku[r["SKU"].casefold()] = r
        if r["barcode"]:
            index.setdefault(r["barcode"], []).append(r)
    all_attrs = {}
    for r in conn.execute("""SELECT p.id, a.attribute_name, a.attribute_value
        FROM products p JOIN product_attributes a ON a.product_id=p.id
        WHERE lower(a.attribute_name) IN ('colour','color','size','style')"""):
        all_attrs.setdefault(r[0], {})[r[1].casefold()] = str(r[2]).strip().casefold()
    errors = []
    resolved = []
    for it in items:
        old_by_sku = by_sku.get(it["sku"].casefold())
        matches = index.get(it["barcode"], []) if it["barcode"] else []
        variant = {k.casefold(): str(v).strip().casefold() for k, v in it["attrs"].items()
                   if k.casefold() in ('colour', 'color', 'size', 'style')}
        for prev in matches:
            oldattrs = all_attrs.get(prev.get("id"), prev.get("attrs", {}))
            if (str(prev.get("name") or "").strip().casefold() != it["name"].strip().casefold() or
                any(k in variant and oldattrs.get(k) and oldattrs[k] != v
                    for k, v in variant.items())):
                errors.append(f"Row {it['source_row']}: EAN {it['barcode']} has a different style/size/colour; review before import")
                break
        if old_by_sku and it["barcode"] and old_by_sku["barcode"] != it["barcode"]:
            errors.append(f"Row {it['source_row']}: SKU {it['sku']} already belongs to another barcode")
        if old_by_sku and matches and any(m["id"] == old_by_sku["id"] for m in matches):
            if it["price"] is not None and round(float(old_by_sku["price"])*100) != round(float(it["price"])*100):
                errors.append(f"Row {it['source_row']}: existing SKU {it['sku']} has a different MRP. Use a separate SKU for each MRP")
        same_price = next((m for m in matches if it["price"] is not None and
                          round(float(m["price"])*100) == round(float(it["price"])*100)), None)
        resolved.append(old_by_sku or same_price)
        if it["barcode"]:
            fake = {"id": -it["source_row"], "SKU": it["sku"], "price": it["price"] or 0,
                    "barcode": it["barcode"], "name": it["name"], "attrs": variant}
            index.setdefault(it["barcode"], []).append(fake)
        if not old_by_sku and not same_price:
            by_sku[it["sku"].casefold()] = fake if it["barcode"] else {
                "id": -it["source_row"], "SKU": it["sku"], "price": it["price"] or 0,
                "barcode": None, "name": it["name"]}
    return errors, resolved


def preview(conn, items, issues, mode="skip", allow_zero_prices=False):
    if mode not in MODES:
        raise ImportError("Unknown import mode")
    review_errors, resolved = _barcode_review(conn, items)
    existing = {row["SKU"].casefold(): row["id"] for row in conn.execute("SELECT id, SKU FROM products")}
    counts = {"new": 0, "existing": 0, "stock_to_add": 0, "missing_prices": 0,
              "missing_gst": 0, "skipped": 0, "total_rows": len(items)}
    for item, target in zip(items, resolved):
        duplicate = bool(target)
        counts["existing" if duplicate else "new"] += 1
        if duplicate and mode == "skip":
            counts["skipped"] += 1
        if duplicate and mode != "skip":
            counts["stock_to_add"] += item["quantity"]
        if not duplicate and item["price"] is None:
            counts["missing_prices"] += 1
        if not duplicate and item["tax_rate"] is None:
            counts["missing_gst"] += 1
    if len(issues) > 0 or review_errors:
        # No partial application: the spreadsheet must be fixed first.
        ready = False
    else:
        ready = counts["missing_prices"] == 0 or bool(allow_zero_prices)
    return {**counts, "errors": (issues + review_errors)[:40], "error_count": len(issues + review_errors),
            "can_import": ready, "mode": mode,
            "sample": [{"row": x["source_row"], "sku": x["sku"], "name": x["name"],
                        "quantity": x["quantity"], "price": x["price"],
                        "action": ("skip" if mode == "skip" else "update")
                        if x["sku"].casefold() in existing else "create"}
                       for x in items[:15]]}


def apply(conn, items, issues, mode="skip", allow_zero_prices=False,
          force_repeat=False, repeat_reason=""):
    """Atomic import; repeat of the same inventory receipt needs explicit acknowledgement."""
    digest = hashlib.sha256(json.dumps(items, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    repeat_reason = str(repeat_reason or "").strip()
    if force_repeat and (len(repeat_reason) < 8 or len(repeat_reason) > 250):
        raise ImportError("Explain why the same stock file must be received again (8–250 characters)")
    # Rescan under a write lock; a change between preview and apply cannot
    # accidentally be interpreted as a new product or bypass price review.
    conn.execute("BEGIN IMMEDIATE")
    try:
        movement_start = conn.execute("SELECT COALESCE(MAX(id),0) FROM stock_movements").fetchone()[0]
        if mode in ("add_stock", "merge") and not force_repeat:
            old = conn.execute("SELECT id FROM stock_import_batches WHERE digest=? AND mode=? LIMIT 1",
                               (digest, mode)).fetchone()
            if old:
                raise ImportError("This same stock file was already received. Use Skip Existing, "
                                  "or explicitly confirm a new delivery with a reason.")
        check = preview(conn, items, issues, mode, allow_zero_prices)
        if not check["can_import"]:
            if check["error_count"]:
                raise ImportError("Correct the file errors before importing: " + check["errors"][0])
            raise ImportError("Some new products have no price. Fill the prices or explicitly allow ₹0 products")
        groups = {}
        for group in conn.execute("SELECT id, name FROM product_groups"):
            groups.setdefault(group["name"].casefold(), group["id"])
        existing = {p["SKU"].casefold(): dict(p) for p in conn.execute(
            "SELECT id, SKU, barcode, price FROM products")}
        review_errors, resolved = _barcode_review(conn, items)
        if review_errors:
            raise ImportError(review_errors[0])
        result = {"created": 0, "skipped": 0, "stock_added": 0, "updated": 0,
                  "no_price_created": 0, "gst_missing_created": 0}
        for item, target in zip(items, resolved):
            row = existing.get(item["sku"].casefold()) or target
            if row:
                if int(row["id"]) < 0:
                    raise ImportError("The file repeats the same EAN/MRP. Combine quantities into one row")
                if mode == "skip":
                    result["skipped"] += 1
                    continue
                if item["quantity"]:
                    conn.execute("UPDATE products SET quantity = quantity + ?, updated = CURRENT_TIMESTAMP WHERE id = ?",
                                 (item["quantity"], row["id"]))
                    result["stock_added"] += item["quantity"]
                if mode == "merge":
                    fields = []
                    vals = []
                    for col in (("price",) if not item["barcode"] else ()) + ("cost_price", "tax_rate", "hsn_code", "unit_code"):
                        val = item[col]
                        if val is not None and val != "":
                            fields.append(f"{col} = ?")
                            vals.append(val)
                    if fields:
                        conn.execute(f"UPDATE products SET {', '.join(fields)}, updated = CURRENT_TIMESTAMP WHERE id = ?",
                                     (*vals, row["id"]))
                    # A supplied tax rate is an explicit catalogue correction;
                    # blank rates must never silently clear GST review flags.
                    if item["tax_rate"] is not None:
                        conn.execute("DELETE FROM product_attributes WHERE product_id=? AND attribute_name=?",
                                     (row["id"], "GST Review Required"))
                    if item["price"] is not None:
                        conn.execute("DELETE FROM product_attributes WHERE product_id=? AND attribute_name=?",
                                     (row["id"], "Price Review Required"))
                    for name, val in item["attrs"].items():
                        conn.execute("""INSERT INTO product_attributes(product_id, attribute_name, attribute_value)
                            VALUES(?, ?, ?) ON CONFLICT(product_id, attribute_name)
                            DO UPDATE SET attribute_value=excluded.attribute_value""",
                                     (row["id"], name, val))
                    result["updated"] += 1
                continue
            group_id = groups.get(item["name"].casefold())
            if group_id is None:
                cur = conn.execute("INSERT INTO product_groups(name) VALUES(?)", (item["name"],))
                group_id = cur.lastrowid
                groups[item["name"].casefold()] = group_id
            canonical, secondary = catalog._safe_barcode_for_new_product(
                conn, item["barcode"], group_id, item["price"] if item["price"] is not None else 0)
            cur = conn.execute("""INSERT INTO products
                (group_id, SKU, barcode, price, quantity, cost_price, tax_rate, hsn_code, unit_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (group_id, item["sku"], canonical, item["price"] if item["price"] is not None else 0,
                 item["quantity"], item["cost_price"] if item["cost_price"] is not None else 0,
                 item["tax_rate"] if item["tax_rate"] is not None else 0,
                 item["hsn_code"], item["unit_code"]))
            pid = cur.lastrowid
            if secondary:
                conn.execute("INSERT INTO barcode_price_options(product_id,barcode) VALUES(?,?)",
                             (pid, secondary))
            attributes = dict(item["attrs"])
            if item["price"] is None:
                attributes.setdefault("Price Review Required", "No selling price supplied; created at ₹0")
            if item["tax_rate"] is None:
                attributes.setdefault("GST Review Required", "Unverified: no GST rate supplied; 0 is only a database placeholder")
            if item["cost_price"] is None:
                attributes.setdefault("Cost Review Required", "No purchase cost supplied; stored as ₹0")
            for name, val in attributes.items():
                conn.execute("INSERT INTO product_attributes(product_id, attribute_name, attribute_value) VALUES(?, ?, ?)",
                             (pid, name, val))
            existing[item["sku"].casefold()] = {"id": pid, "SKU": item["sku"], "barcode": item["barcode"]}
            result["created"] += 1
            result["no_price_created"] += (item["price"] is None)
            result["gst_missing_created"] += (item["tax_rate"] is None)
        conn.execute("""UPDATE stock_movements SET kind='IMPORT_RECEIPT',
            reason=? WHERE id>? AND kind='CHANGE'""",
            (f"Catalogue import ({mode})", movement_start))
        if mode in ("add_stock", "merge"):
            conn.execute("""INSERT INTO stock_import_batches
                (digest, mode, rows_added, stock_added, repeat_reason)
                VALUES(?, ?, ?, ?, ?)""",
                (digest, mode, result["created"], result["stock_added"],
                 repeat_reason if force_repeat else ""))
        conn.commit()
        return result
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ImportError(f"Import cancelled; no rows changed. Database conflict: {exc}") from exc
    except Exception:
        conn.rollback()
        raise
