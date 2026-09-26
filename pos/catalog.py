import sqlite3

from .db import connect


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
        ''' SELECT g.id, g.name, COUNT(p.id) AS product_count,
                   COALESCE(SUM(p.quantity), 0) AS total_stock
            FROM product_groups g
            LEFT JOIN products p ON p.group_id = g.id
            GROUP BY g.id
            ORDER BY g.name COLLATE NOCASE '''
    )
    return [dict(r) for r in cur.fetchall()]


def get_group(conn, group_id):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM product_groups WHERE id = ?", (group_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def add_product_to_group(conn, group_id, SKU, barcode, price, quantity, cost_price, tax_rate):
    if not SKU or not str(SKU).strip():
        raise InvalidInputError("SKU cannot be empty")
    if price is None or float(price) < 0:
        raise InvalidInputError("Price cannot be negative")
    if quantity is None or int(quantity) < 0:
        raise InvalidInputError("Quantity cannot be negative")
    if cost_price is not None and float(cost_price) < 0:
        raise InvalidInputError("Cost price cannot be negative")
    if tax_rate is None or float(tax_rate) < 0:
        raise InvalidInputError("Tax rate cannot be negative")

    sql = ''' INSERT INTO products(group_id, SKU, barcode, price, quantity, cost_price, tax_rate)
              VALUES(?, ?, ?, ?, ?, ?, ?) '''
    cur = conn.cursor()
    try:
        cur.execute(sql, (
            group_id, str(SKU).strip(), barcode, float(price), int(quantity),
            # column is NOT NULL DEFAULT 0 — never bind an explicit NULL
            float(cost_price) if cost_price is not None else 0.0,
            float(tax_rate) if tax_rate is not None else 0.0,
        ))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        conn.rollback()
        message = str(e)
        if "SKU" in message:
            raise DuplicateSKUError(f"SKU '{SKU}' already exists") from e
        if "barcode" in message and "UNIQUE" in message.upper():
            raise DuplicateBarcodeError(f"Barcode '{barcode}' already exists") from e
        if "FOREIGN KEY" in message:
            raise GroupNotFoundError(f"No product group with id {group_id}") from e
        raise CatalogError(message) from e


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


def adjust_stock(conn, product_id, amount):
    """Add (positive) or remove (negative) stock for a product."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise InvalidInputError("Quantity adjustment must be an integer")
    if amount == 0:
        raise InvalidInputError("Adjustment cannot be zero")

    sql = ''' UPDATE products SET quantity = quantity + ?, updated = CURRENT_TIMESTAMP WHERE id = ? '''
    cur = conn.cursor()
    try:
        cur.execute(sql, (amount, product_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "CHECK" in str(e):
            raise InvalidInputError(
                f"Adjusting quantity by {amount} would make stock negative"
            ) from e
        raise CatalogError(str(e)) from e

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")

    cur.execute("SELECT quantity FROM products WHERE id = ?", (product_id,))
    return cur.fetchone()["quantity"]


# Kept for compatibility with earlier code.
add_quantity = adjust_stock


def update_product(conn, product_id, **fields):
    ALLOWED_FIELDS = {"SKU", "barcode", "cost_price", "tax_rate", "price", "group_id"}
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

    values = []
    assignments = []
    for column, value in fields.items():
        assignments.append(f"{column} = ?")
        values.append(value)
    values.append(product_id)

    sql = f''' UPDATE products SET {", ".join(assignments)}, updated = CURRENT_TIMESTAMP WHERE id = ? '''
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(values))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        message = str(e)
        if "SKU" in message and "UNIQUE" in message.upper():
            raise DuplicateSKUError(f"SKU '{fields.get('SKU')}' already exists") from e
        if "barcode" in message and "UNIQUE" in message.upper():
            raise DuplicateBarcodeError(f"Barcode '{fields.get('barcode')}' already exists") from e
        if "FOREIGN KEY" in message:
            raise GroupNotFoundError(f"No product group with id {fields.get('group_id')}") from e
        raise CatalogError(message) from e

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return cur.rowcount


def delete_product(conn, product_id):
    sql = ''' DELETE FROM products WHERE id = ? '''
    cur = conn.cursor()
    try:
        cur.execute(sql, (product_id,))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        if "FOREIGN KEY" in str(e):
            raise ProductHasSalesError(
                f"Product {product_id} has sales history and cannot be deleted"
            ) from e
        raise CatalogError(str(e)) from e

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return cur.rowcount


def _attach_attributes(conn, products):
    if not products:
        return products
    ids = [p["id"] for p in products]
    marks = ",".join("?" * len(ids))
    cur = conn.cursor()
    cur.execute(
        f''' SELECT product_id, attribute_name, attribute_value
             FROM product_attributes WHERE product_id IN ({marks})
             ORDER BY attribute_name COLLATE NOCASE ''',
        ids,
    )
    attrs = {}
    for r in cur.fetchall():
        attrs.setdefault(r["product_id"], []).append(
            {"name": r["attribute_name"], "value": r["attribute_value"]}
        )
    for p in products:
        p["attributes"] = attrs.get(p["id"], [])
    return products


PRODUCT_SELECT = '''
    SELECT p.id, p.group_id, p.SKU, p.barcode, p.price, p.quantity,
           p.cost_price, p.tax_rate, p.updated, g.name AS group_name
    FROM products p
    LEFT JOIN product_groups g ON g.id = p.group_id
'''


def list_products(conn, query=None, group_id=None, low_stock_threshold=None,
                  limit=None, offset=0):
    where, params = [], []
    if query:
        like = f"%{query.strip()}%"
        where.append(
            ''' (p.SKU LIKE ? OR p.barcode LIKE ? OR g.name LIKE ?
                OR p.id IN (
                    SELECT pa.product_id FROM product_attributes pa
                    WHERE pa.attribute_name LIKE ? OR pa.attribute_value LIKE ?
                )) '''
        )
        params += [like, like, like, like, like]
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
    """Exact match on SKU or barcode (used by barcode scanners at the register)."""
    cur = conn.cursor()
    cur.execute(PRODUCT_SELECT + " WHERE p.SKU = ? OR p.barcode = ?", (code, code))
    row = cur.fetchone()
    if row is None:
        return None
    return _attach_attributes(conn, [dict(row)])[0]


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
