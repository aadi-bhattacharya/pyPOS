import sqlite3
from decimal import Decimal

database_file = "./data/database.sqlite"


class CatalogError(Exception):
    """Base class for all catalog-related errors."""
    pass

class InvalidInputError(CatalogError):
    """Raised when a value fails validation before it ever reaches SQL
    (empty name, negative price, negative quantity, etc)."""
    pass

class DuplicateSKUError(CatalogError):
    """Raised when a SKU already exists on another product."""
    pass

class DuplicateBarcodeError(CatalogError):
    """Raised when a barcode already exists on another product."""
    pass

class GroupNotFoundError(CatalogError):
    """Raised when a product_group id doesn't exist."""
    pass

class ProductNotFoundError(CatalogError):
    """Raised when a product id doesn't exist."""
    pass

class AttributeNotFoundError(CatalogError):
    """Raised when trying to remove an attribute that isn't set."""
    pass

class ProductHasSalesError(CatalogError):
    """Raised when trying to delete a product that has sale history."""
    pass


def create_product_group(conn, name):
    if not name or not name.strip():
        raise InvalidInputError("Group name cannot be empty")

    sql = ''' INSERT INTO product_groups(name)
              VALUES(?) '''
    cur = conn.cursor()
    cur.execute(sql, (name,))
    conn.commit()
    return cur.lastrowid

def rename_product_group(conn, group_id, new_name):
    if not new_name or not new_name.strip():
        raise InvalidInputError("Group name cannot be empty")

    sql = ''' UPDATE product_groups SET name = ? WHERE id = ? '''
    cur = conn.cursor()
    cur.execute(sql, (new_name, group_id))
    conn.commit()

    if cur.rowcount == 0:
        raise GroupNotFoundError(f"No product group with id {group_id}")
    return cur.rowcount

def add_product_to_group(conn, group_id, SKU, barcode, price, quantity, cost_price, tax_rate):
    if not SKU or not SKU.strip():
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
            group_id, SKU, barcode, float(price), int(quantity),
            float(cost_price) if cost_price is not None else None,
            float(tax_rate)
        ))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        message = str(e)
        if "SKU" in message:
            raise DuplicateSKUError(f"SKU '{SKU}' already exists") from e
        if "barcode" in message:
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
        cur.execute(sql, (product_id, attribute_name, attribute_value))
        conn.commit()
    except sqlite3.IntegrityError as e:
        message = str(e)
        if "FOREIGN KEY" in message:
            raise ProductNotFoundError(f"No product with id {product_id}") from e
        raise CatalogError(message) from e

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

def add_quantity(conn, product_id, amount):
    sql = ''' UPDATE products SET quantity = quantity + ? WHERE id = ? '''
    cur = conn.cursor()
    try:
        cur.execute(sql, (amount, product_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        message = str(e)
        if "CHECK" in message:
            raise InvalidInputError(
                f"Adjusting quantity by {amount} would make stock negative"
            ) from e
        raise CatalogError(message) from e

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return cur.rowcount

def update_product(conn, product_id, **fields):
    ALLOWED_FIELDS = {"SKU", "barcode", "cost_price", "tax_rate"}
    NON_NEGATIVE_FIELDS = {"cost_price", "tax_rate"}

    if not fields:
        return 0

    for field in fields:
        if field not in ALLOWED_FIELDS:
            raise InvalidInputError(f"Invalid field: {field}")

    for field in NON_NEGATIVE_FIELDS:
        if field in fields and fields[field] is not None and float(fields[field]) < 0:
            raise InvalidInputError(f"{field} cannot be negative")

    if "SKU" in fields and (not fields["SKU"] or not fields["SKU"].strip()):
        raise InvalidInputError("SKU cannot be empty")

    assignments = []
    values = []
    for column, value in fields.items():
        assignments.append(f"{column} = ?")
        values.append(value)
    set_clause = ", ".join(assignments)
    sql = f''' UPDATE products SET {set_clause} WHERE id = ? '''
    values.append(product_id)

    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(values))
        conn.commit()
    except sqlite3.IntegrityError as e:
        message = str(e)
        if "SKU" in message:
            raise DuplicateSKUError(f"SKU '{fields.get('SKU')}' already exists") from e
        if "barcode" in message:
            raise DuplicateBarcodeError(f"Barcode '{fields.get('barcode')}' already exists") from e
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
        message = str(e)
        if "FOREIGN KEY" in message:
            raise ProductHasSalesError(
                f"Product {product_id} has sales history and cannot be deleted"
            ) from e
        raise CatalogError(message) from e

    if cur.rowcount == 0:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return cur.rowcount


def main():
    try:
        with sqlite3.connect(database_file) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            print("boo")
    except sqlite3.Error as e:
        print(e)


if __name__ == '__main__':
    main()
