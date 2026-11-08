import sqlite3

from .db import connect
from .india import hsn
import re


def _unit(value):
    value = str(value or "NOS").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,8}", value):
        raise InvalidInputError("Unit of quantity must be 2–8 letters, e.g. NOS or PCS")
    return value


class CatalogError(Exception):
    """Base class for all catalog-related errors."""


class InvalidInputError(CatalogError):
    """Raised when a value fails validation before it ever reaches SQL
    (empty name, negative price, negative quantity, etc)."""


class DuplicateSKUError(CatalogError):
    """Raised when a SKU already exists on another product."""


class DuplicateBarcodeError(CatalogError):
    """Raised when a barcode already exists on another product."""


class GroupNotFoundError(CatalogError):
    """Raised when a product_group id doesn't exist."""


class ProductNotFoundError(CatalogError):
    """Raised when a product id doesn't exist."""


class AttributeNotFoundError(CatalogError):
    """Raised when trying to remove an attribute that isn't set."""


class ProductHasSalesError(CatalogError):
    """Raised when trying to delete a product that has sale history."""


class GroupNotEmptyError(CatalogError):
    """Raised when deleting a group that still has products."""


def create_product_group(conn, name):
    if not name or not name.strip():
        raise InvalidInputError("Group name cannot be empty")

    sql = ''' INSERT INTO product_groups(name)
              VALUES(?) '''
    cur = conn.cursor()
    cur.execute(sql, (name.strip(),))
    conn.commit()
    return cur.lastrowid


def rename_product_group(conn, group_id, new_name):
    if not new_name or not new_name.strip():
        raise InvalidInputError("Group name cannot be empty")

    sql = ''' UPDATE product_groups SET name = ? WHERE id = ? '''
    cur = conn.cursor()
    cur.execute(sql, (new_name.strip(), group_id))
    conn.commit()

    if cur.rowcount == 0:
        raise GroupNotFoundError(f"No product group with id {group_id}")
    return cur.rowcount


def delete_product_group(conn, group_id):
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM product_groups WHERE id = ?", (group_id,))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "FOREIGN KEY" in str(e):
            raise GroupNotEmptyError(
                f"Group {group_id} still has products. Move or delete them first."
            ) from e
        raise CatalogError(str(e)) from e

    if cur.rowcount == 0:
        raise GroupNotFoundError(f"No product group with id {group_id}")
    return cur.rowcount


def list_groups(conn):
    cur = conn.cursor()
    cur.execute(
        ''' SELECT g.id, g.name, g.brand_id, g.category_id,
                   b.name AS brand_name, c.name AS category_name,
                   COUNT(p.id) AS product_count,
                   COALESCE(SUM(p.quantity), 0) AS total_stock
            FROM product_groups g
            LEFT JOIN products p ON p.group_id = g.id
            LEFT JOIN brands b ON b.id = g.brand_id
            LEFT JOIN categories c ON c.id = g.category_id
            GROUP BY g.id
            ORDER BY g.name COLLATE NOCASE '''
    )
    return [dict(r) for r in cur.fetchall()]


def get_group(conn, group_id):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM product_groups WHERE id = ?", (group_id,))
    row = cur.fetchone()
    return dict(row) if row else None




def _barcode_owners(conn, code, *, exclude_id=None):
    if not code:
        return []
    return [dict(r) for r in conn.execute("""
        SELECT p.id, p.group_id, p.price, p.SKU, g.name AS group_name
        FROM products p LEFT JOIN product_groups g ON g.id=p.group_id
        WHERE (p.barcode=? OR EXISTS(
           SELECT 1 FROM barcode_price_options o
           WHERE o.product_id=p.id AND o.barcode=?))
          AND (? IS NULL OR p.id != ?)
        ORDER BY p.id
    """, (code, code, exclude_id, exclude_id))]


def _safe_barcode_for_new_product(conn, code, group_id, price):
    """Return (canonical barcode, alias barcode) without weakening UNIQUE.

    A shared EAN may identify different *price editions* of the same style,
    but never silently a different style or two identical priced SKUs.
    """
    code = str(code).strip() if code else None
    owners = _barcode_owners(conn, code)
    if not owners:
        return code, None
    if group_id is None or any(o["group_id"] != group_id for o in owners):
        raise DuplicateBarcodeError("EAN already belongs to a different style; review the barcode")
    if any(round(float(o["price"])*100) == round(float(price)*100) for o in owners):
        raise DuplicateBarcodeError("This EAN and selling price already exist; adjust its stock instead")
    return None, code


def _assign_barcode_on_update(conn, product_id, code, group_id, price):
    """Explicit barcode edits also work on secondary-price SKU rows."""
    code = str(code).strip() if code else None
    owners = _barcode_owners(conn, code, exclude_id=product_id)
    if owners:
        if group_id is None or any(o["group_id"] != group_id for o in owners):
            raise DuplicateBarcodeError("EAN belongs to a different style")
        if any(round(float(o["price"])*100) == round(float(price)*100) for o in owners):
            raise DuplicateBarcodeError("EAN and MRP combination already exists")
        conn.execute("UPDATE products SET barcode=NULL WHERE id=?", (product_id,))
        conn.execute("""INSERT INTO barcode_price_options(product_id,barcode)
            VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET barcode=excluded.barcode""",
            (product_id, code))
    else:
        conn.execute("DELETE FROM barcode_price_options WHERE product_id=?", (product_id,))
        conn.execute("UPDATE products SET barcode=? WHERE id=?", (code, product_id))

def add_product_to_group(conn, group_id, SKU, barcode, price, quantity, cost_price, tax_rate, hsn_code="", unit_code="NOS", *, attributes=None, audit_actor=None):
    if attributes is None:
        attributes = {}
    if not isinstance(attributes, dict) or len(attributes) > 40:
        raise InvalidInputError("Product attributes must be an object of at most 40 name/value pairs")
    validated_attrs = {}
    for key, value in attributes.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InvalidInputError("Variant attribute names and values must be text")
        name = key.strip()
        val = value.strip()
        if not name or not val or len(name) > 80 or len(val) > 500:
            raise InvalidInputError("Variant attributes need a name and value of reasonable length")
        if name.casefold() in ("price review required", "gst review required", "cost review required", "manager review required"):
            raise InvalidInputError("Reserved review flags cannot be set as product variants")
        validated_attrs[name] = val
    if not SKU or not str(SKU).strip():
        raise InvalidInputError("SKU cannot be empty")
    if type(quantity) is bool or not re.fullmatch(r"[0-9]+", str(quantity)):
        raise InvalidInputError("Opening stock must be a non-negative whole quantity")
    if int(quantity) > 1_000_000:
        raise InvalidInputError("Opening stock cannot exceed 1,000,000 units")
    if price is None or float(price) < 0:
        raise InvalidInputError("Price cannot be negative")
    if quantity is None or int(quantity) < 0:
        raise InvalidInputError("Quantity cannot be negative")
    if cost_price is not None and float(cost_price) < 0:
        raise InvalidInputError("Cost price cannot be negative")
    if tax_rate is None or float(tax_rate) < 0:
        raise InvalidInputError("Tax rate cannot be negative")

    sql = ''' INSERT INTO products(group_id, SKU, barcode, price, quantity, cost_price, tax_rate, hsn_code, unit_code)
              VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) '''
    cur = conn.cursor()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("SELECT 1 FROM products WHERE SKU = ? COLLATE NOCASE", (str(SKU).strip(),)).fetchone():
            raise DuplicateSKUError(f"SKU '{SKU}' already exists (case-insensitive match)")
        if audit_actor and audit_actor.get("role") == "cashier" and _barcode_owners(conn, str(barcode).strip() if barcode else None):
            raise DuplicateBarcodeError("EAN already exists; a manager must approve an additional MRP edition")
        canonical, secondary = _safe_barcode_for_new_product(conn, barcode, group_id, price)
        cur.execute(sql, (
            group_id, str(SKU).strip(), canonical, float(price), int(quantity),
            # column is NOT NULL DEFAULT 0 — never bind an explicit NULL
            float(cost_price) if cost_price is not None else 0.0,
            float(tax_rate) if tax_rate is not None else 0.0,
            hsn(hsn_code), _unit(unit_code),
        ))
        pid = cur.lastrowid
        if secondary:
            conn.execute("INSERT INTO barcode_price_options(product_id, barcode) VALUES(?,?)",
                         (pid, secondary))
        if audit_actor and audit_actor.get("role") == "cashier":
            username = str(audit_actor.get("username") or "unknown")[:60]
            validated_attrs["Manager Review Required"] = f"New SKU created by operator={username}; verify MRP, tax and identity"
            validated_attrs["Cost Review Required"] = "Purchase cost not entered; manager review required"
        for name, value in validated_attrs.items():
            conn.execute("INSERT INTO product_attributes(product_id, attribute_name, attribute_value) VALUES(?,?,?)",
                         (pid, name, value))
        if audit_actor and int(quantity) > 0:
            actor = str(audit_actor.get("username") or "unknown")[:60]
            role = str(audit_actor.get("role") or "unknown")[:20]
            conn.execute("""UPDATE stock_movements SET reason=?
                WHERE product_id=? AND kind='INITIAL_STOCK'""",
                (f"Product created by operator={actor} role={role}", pid))
        conn.commit()
        return pid
    except (sqlite3.IntegrityError, DuplicateBarcodeError, DuplicateSKUError) as e:
        conn.rollback()
        if isinstance(e, (DuplicateBarcodeError, DuplicateSKUError)):
            raise
        message = str(e)
        if "SKU" in message:
            raise DuplicateSKUError(f"SKU '{SKU}' already exists") from e
        if "barcode" in message and "UNIQUE" in message.upper():
            raise DuplicateBarcodeError(f"Barcode '{barcode}' already exists") from e
        if "FOREIGN KEY" in message:
            raise GroupNotFoundError(f"No product group with id {group_id}") from e
        raise CatalogError(message) from e
    except Exception:
        conn.rollback()
        raise


def set_product_attribute(conn, product_id, attribute_name, attribute_value):
    if not attribute_name or not attribute_name.strip():
        raise InvalidInputError("Attribute name cannot be empty")
    if attribute_value is None or not str(attribute_value).strip():
        raise InvalidInputError("Attribute value cannot be empty")

    sql = ''' INSERT INTO product_attributes(product_id, attribute_name, attribute_value)
              VALUES(?, ?, ?)
              ON CONFLICT (product_id, attribute_name) DO UPDATE SET attribute_value = excluded.attribute_value '''
    cur = conn.cursor()
    try:
        cur.execute(sql, (product_id, attribute_name.strip(), str(attribute_value).strip()))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        message = str(e)
        if "FOREIGN KEY" in message:
            raise ProductNotFoundError(f"No product with id {product_id}") from e
        raise CatalogError(message) from e
    return cur.lastrowid


def remove_product_attribute(conn, product_id, attribute_name):
    sql = ''' DELETE FROM product_attributes WHERE product_id = ? AND attribute_name = ? '''
    cur = conn.cursor()
    cur.execute(sql, (product_id, attribute_name))
    conn.commit()

    if cur.rowcount == 0:
        raise AttributeNotFoundError(
            f"Product {product_id} has no attribute '{attribute_name}'"
        )
    return cur.rowcount


def adjust_stock(conn, product_id, amount, *, reason="", kind="ADJUSTMENT"):
    """Atomic stock change; every entry is recorded by the database trigger."""
    try:
        if isinstance(amount, bool) or not re.fullmatch(r"[+-]?[0-9]+", str(amount)):
            raise ValueError
        amount = int(amount)
    except (TypeError, ValueError):
        raise InvalidInputError("Quantity adjustment must be a whole number")
    if amount == 0:
        raise InvalidInputError("Adjustment cannot be zero")
    if kind not in ("ADJUSTMENT", "RECEIPT"):
        raise InvalidInputError("Unknown stock movement type")
    if kind == "RECEIPT" and amount < 0:
        raise InvalidInputError("Goods receipt quantities must be positive")
    reason = str(reason or "").strip()
    if len(reason) > 300:
        raise InvalidInputError("Stock adjustment reason is too long")
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE products SET quantity=quantity+?, updated=CURRENT_TIMESTAMP WHERE id=?",
            (amount, product_id))
        if cur.rowcount == 0:
            raise ProductNotFoundError(f"No product with id {product_id}")
        row = conn.execute("SELECT quantity FROM products WHERE id=?", (product_id,)).fetchone()
        conn.execute("""UPDATE stock_movements SET kind=?, reason=?
            WHERE id=(SELECT MAX(id) FROM stock_movements WHERE product_id=?)""",
                     (kind, reason, product_id))
        conn.commit()
        return row["quantity"]
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "CHECK" in str(e):
            raise InvalidInputError("Adjustment would make stock negative") from e
        raise CatalogError(str(e)) from e
    except Exception:
        conn.rollback()
        raise


# Kept for compatibility with earlier code.
add_quantity = adjust_stock


def update_product(conn, product_id, *, verified_gst=False, verified_price=False, **fields):
    """Explicit price/tax confirmation clears only the matching import review flag."""
    if verified_gst and "tax_rate" not in fields:
        raise InvalidInputError("Supply tax_rate to confirm GST review")
    if verified_price and "price" not in fields:
        raise InvalidInputError("Supply price to confirm price review")
    ALLOWED_FIELDS = {"SKU", "barcode", "cost_price", "tax_rate", "price", "group_id", "hsn_code", "unit_code", "reorder_point"}
    NON_NEGATIVE_FIELDS = {"cost_price", "tax_rate", "price"}

    if not fields:
        return 0

    for field in fields:
        if field not in ALLOWED_FIELDS:
            raise InvalidInputError(f"Invalid field: {field}")

    for field in NON_NEGATIVE_FIELDS:
        if field in fields and fields[field] is not None and float(fields[field]) < 0:
            raise InvalidInputError(f"{field} cannot be negative")

    # NOT NULL numeric columns: an explicit null resets to the column default.
    for field in ("cost_price", "tax_rate"):
        if field in fields and fields[field] is None:
            fields[field] = 0.0
    if "price" in fields and fields["price"] is None:
        raise InvalidInputError("Price cannot be empty")

    if "SKU" in fields and (not fields["SKU"] or not str(fields["SKU"]).strip()):
        raise InvalidInputError("SKU cannot be empty")

    if "hsn_code" in fields:
        fields["hsn_code"] = hsn(fields["hsn_code"])
    if "unit_code" in fields:
        fields["unit_code"] = _unit(fields["unit_code"])
    if "reorder_point" in fields and fields["reorder_point"] not in (None, ""):
        value = fields["reorder_point"]
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
            raise InvalidInputError("Reorder point must be a whole number >= 0")
        fields["reorder_point"] = int(value)
    elif "reorder_point" in fields:
        fields["reorder_point"] = None

    code_changed = "barcode" in fields
    wanted_code = fields.pop("barcode", None)
    # Edits cannot create two identical MRPs for the same EAN.
    sql_assignments = [f"{k} = ?" for k in fields]
    sql_values = list(fields.values())
    cur = conn.cursor()
    conn.execute("BEGIN IMMEDIATE")
    try:
        prior = conn.execute("""SELECT p.group_id, p.price, COALESCE(p.barcode,
                   (SELECT o.barcode FROM barcode_price_options o WHERE o.product_id=p.id)) AS barcode
                   FROM products p WHERE p.id=?""", (product_id,)).fetchone()
        if not prior:
            raise ProductNotFoundError(f"No product with id {product_id}")
        target_code = wanted_code if code_changed else prior["barcode"]
        target_group = fields.get("group_id", prior["group_id"])
        target_price = fields.get("price", prior["price"])
        if target_code:
            other = _barcode_owners(conn, str(target_code).strip(), exclude_id=product_id)
            if other and (any(o["group_id"] != target_group for o in other) or
                          any(round(float(o["price"])*100) == round(float(target_price)*100) for o in other)):
                raise DuplicateBarcodeError("EAN conflicts with another style or the same MRP")
        if sql_assignments:
            cur.execute(f"UPDATE products SET {', '.join(sql_assignments)}, updated=CURRENT_TIMESTAMP WHERE id=?",
                        (*sql_values, product_id))
        else:
            cur.execute("UPDATE products SET updated=CURRENT_TIMESTAMP WHERE id=?", (product_id,))
        if code_changed:
            _assign_barcode_on_update(conn, product_id, wanted_code, target_group, target_price)
        if cur.rowcount:
            if verified_gst:
                conn.execute("DELETE FROM product_attributes WHERE product_id=? AND attribute_name=?",
                             (product_id, "GST Review Required"))
            if verified_price:
                conn.execute("DELETE FROM product_attributes WHERE product_id=? AND attribute_name=?",
                             (product_id, "Price Review Required"))
        conn.commit()
    except (sqlite3.IntegrityError, DuplicateBarcodeError) as e:
        conn.rollback()
        if isinstance(e, DuplicateBarcodeError):
            raise
        message = str(e)
        if "SKU" in message and "UNIQUE" in message.upper():
            raise DuplicateSKUError(f"SKU '{fields.get('SKU')}' already exists") from e
        if "barcode" in message and "UNIQUE" in message.upper():
            raise DuplicateBarcodeError(f"Barcode '{fields.get('barcode')}' already exists") from e
        if "FOREIGN KEY" in message:
            raise GroupNotFoundError(f"No product group with id {fields.get('group_id')}") from e
        raise CatalogError(message) from e
    except Exception:
        conn.rollback()
        raise

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return cur.rowcount


def delete_product(conn, product_id):
    """Never erase an SKU with stock on hand or a linked sales history.

    A manager must first post a separately logged stock correction (and cannot
    hide it by deleting the SKU immediately).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT quantity FROM products WHERE id=?", (product_id,)).fetchone()
        if row is None:
            raise ProductNotFoundError(f"No product with id {product_id}")
        if row["quantity"] != 0:
            raise InvalidInputError(
                "Product still has stock. Record a documented stock adjustment before deletion")
        cur = conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.commit()
        return cur.rowcount
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "FOREIGN KEY" in str(e):
            raise ProductHasSalesError(
                f"Product {product_id} has linked transaction history and cannot be deleted") from e
        raise CatalogError(str(e)) from e
    except Exception:
        conn.rollback()
        raise


def _attach_attributes(conn, products):
    if not products:
        return products
    ids = [p["id"] for p in products]
    attrs = {}
    # Older SQLite builds limit bound variables to 999. Imports can easily
    # produce 1,000+ SKUs; use small chunks so opening Catalog never breaks.
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        marks = ",".join("?" * len(batch))
        cur = conn.execute(
            f''' SELECT product_id, attribute_name, attribute_value
                 FROM product_attributes WHERE product_id IN ({marks})
                 ORDER BY attribute_name COLLATE NOCASE ''',
            batch,
        )
        for r in cur.fetchall():
            attrs.setdefault(r["product_id"], []).append(
                {"name": r["attribute_name"], "value": r["attribute_value"]}
            )
    for p in products:
        p["attributes"] = attrs.get(p["id"], [])
    return products


PRODUCT_SELECT = '''
    SELECT p.id, p.group_id, p.SKU,
           COALESCE(p.barcode, (SELECT o.barcode FROM barcode_price_options o WHERE o.product_id=p.id)) AS barcode,
           p.price, p.quantity,
           p.cost_price, p.tax_rate, p.hsn_code, p.unit_code, p.updated,
           p.reorder_point, g.name AS group_name, g.brand_id, g.category_id,
           b.name AS brand_name, c.name AS category_name
    FROM products p
    LEFT JOIN product_groups g ON g.id = p.group_id
    LEFT JOIN brands b ON b.id = g.brand_id
    LEFT JOIN categories c ON c.id = g.category_id
'''


def list_products(conn, query=None, group_id=None, low_stock_threshold=None,
                  limit=None, offset=0):
    where, params = [], []
    if query:
        like = f"%{query.strip()}%"
        where.append(
            ''' (p.SKU LIKE ? OR p.barcode LIKE ? OR g.name LIKE ?
                OR b.name LIKE ? OR c.name LIKE ?
                OR p.id IN (SELECT o.product_id FROM barcode_price_options o WHERE o.barcode LIKE ?)
                OR p.id IN (
                    SELECT pa.product_id FROM product_attributes pa
                    WHERE pa.attribute_name LIKE ? OR pa.attribute_value LIKE ?
                )) '''
        )
        params += [like, like, like, like, like, like, like, like]
    if group_id is not None:
        where.append("p.group_id = ?")
        params.append(group_id)
    if low_stock_threshold is not None:
        where.append("p.quantity <= ?")
        params.append(low_stock_threshold)

    sql = PRODUCT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY g.name COLLATE NOCASE, p.SKU COLLATE NOCASE"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"

    cur = conn.cursor()
    cur.execute(sql, params)
    products = [dict(r) for r in cur.fetchall()]
    return _attach_attributes(conn, products)


def get_product(conn, product_id):
    cur = conn.cursor()
    cur.execute(PRODUCT_SELECT + " WHERE p.id = ?", (product_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return _attach_attributes(conn, [dict(row)])[0]


def find_by_code(conn, code):
    """Return one exact SKU or one/multiple price options for a scanned EAN."""
    # Exact SKU lookup always wins, including for SKUs sharing a barcode.
    direct = conn.execute(PRODUCT_SELECT + " WHERE p.SKU = ?", (code,)).fetchone()
    if direct:
        return _attach_attributes(conn, [dict(direct)])[0]
    rows = conn.execute(PRODUCT_SELECT + """ WHERE p.barcode = ? OR EXISTS (
        SELECT 1 FROM barcode_price_options o WHERE o.product_id=p.id AND o.barcode=?)
        ORDER BY p.price, p.id""", (code, code)).fetchall()
    if not rows:
        return None
    matches = _attach_attributes(conn, [dict(r) for r in rows])
    if len(matches) == 1:
        return matches[0]
    return {"barcode": code, "matches": matches}


def cart_adjust_item(conn, cart, product_id, quantity):
    current_quantity = cart.get(product_id, 0)
    cur = conn.cursor()

    cur.execute(''' SELECT quantity FROM products WHERE id = ? ''', (product_id,))
    row = cur.fetchone()

    if row is None:
        raise ProductNotFoundError(f"No product with id {product_id}")

    if quantity + current_quantity < 0:
        raise ValueError("Can't remove more than what's in the cart.")

    if quantity + current_quantity == 0:
        cart.pop(product_id, None)
        return cart

    stock = row["quantity"]

    if current_quantity + quantity > stock:
        raise InsufficientStockError(f"Only {stock} items available")

    cart[product_id] = current_quantity + quantity
    return cart


class InsufficientStockError(CatalogError):
    """Raised when trying to sell more units than are in stock."""


def list_brands(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM brands ORDER BY name COLLATE NOCASE")]


def list_categories(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE")]


def create_label(conn, table, name, parent_id=None):
    if table not in ("brands", "categories"):
        raise InvalidInputError("Invalid label table")
    name = str(name or "").strip()
    if not name or len(name) > 100:
        raise InvalidInputError("Name must contain 1–100 characters")
    if table == "categories" and parent_id is not None:
        if not conn.execute("SELECT 1 FROM categories WHERE id=?", (parent_id,)).fetchone():
            raise InvalidInputError("Parent category not found")
    try:
        if table == "brands":
            cur = conn.execute("INSERT INTO brands(name) VALUES(?)", (name,))
        else:
            cur = conn.execute("INSERT INTO categories(name, parent_id) VALUES(?,?)",
                               (name, parent_id))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise InvalidInputError(f"This {table[:-1]} already exists") from exc


def classify_group(conn, group_id, *, brand_id=None, category_id=None):
    for table, key in (("brands", brand_id), ("categories", category_id)):
        if key is not None and not conn.execute(
                f"SELECT 1 FROM {table} WHERE id=?", (key,)).fetchone():
            raise InvalidInputError(f"Unknown {table[:-1]}")
    cur = conn.execute("UPDATE product_groups SET brand_id=?, category_id=? WHERE id=?",
                       (brand_id, category_id, group_id))
    if not cur.rowcount:
        raise GroupNotFoundError(f"No product group with id {group_id}")
    conn.commit()


def stock_overview(conn, default_threshold=0):
    default_threshold = max(0, int(default_threshold))
    row = conn.execute("""SELECT COUNT(*) sku_count,
        COALESCE(SUM(quantity),0) units_on_hand,
        SUM(CASE WHEN quantity=0 THEN 1 ELSE 0 END) out_of_stock,
        SUM(CASE WHEN quantity>0 AND quantity<=COALESCE(reorder_point, ?)
            THEN 1 ELSE 0 END) low_stock,
        SUM(CASE WHEN cost_price=0 THEN 1 ELSE 0 END) missing_cost
        FROM products""", (default_threshold,)).fetchone()
    return dict(row)


def stock_history(conn, product_id=None, limit=100, offset=0):
    limit = min(200, max(1, int(limit)))
    offset = max(0, int(offset))
    if product_id is None:
        rows = conn.execute("""SELECT * FROM stock_movements
            ORDER BY id DESC LIMIT ? OFFSET ?""", (limit, offset)).fetchall()
    else:
        rows = conn.execute("""SELECT * FROM stock_movements
            WHERE product_id=? ORDER BY id DESC LIMIT ? OFFSET ?""",
            (product_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]
