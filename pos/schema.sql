PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS product_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT UNIQUE,
  email TEXT,
  notes TEXT,
  store_credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (store_credit_cents >= 0),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS credit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  delta_cents INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS parked_sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  items_json TEXT NOT NULL,
  discount_mode TEXT CHECK (discount_mode IN ('percent', 'amount')),
  discount_value REAL NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cash_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  closed_at TIMESTAMP,
  opening_float REAL NOT NULL DEFAULT 0 CHECK (opening_float >= 0),
  counted_cash REAL,
  drawer_difference REAL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER REFERENCES product_groups(id),
  SKU TEXT NOT NULL UNIQUE,
  barcode TEXT UNIQUE,
  price REAL NOT NULL CHECK (price >= 0),
  quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  cost_price REAL CHECK (cost_price >= 0) NOT NULL DEFAULT 0,
  tax_rate REAL NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_attributes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  attribute_name TEXT NOT NULL,
  attribute_value TEXT NOT NULL,
  UNIQUE (product_id, attribute_name)
);

CREATE TABLE IF NOT EXISTS products_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER,
  SKU TEXT,
  barcode TEXT,
  price REAL,
  quantity INTEGER,
  cost_price REAL,
  tax_rate REAL,
  changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  action TEXT CHECK (action IN ('UPDATE', 'DELETE'))
);

CREATE TABLE IF NOT EXISTS quantity_history (
  history_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  old_quantity INTEGER NOT NULL,
  new_quantity INTEGER NOT NULL,
  changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  subtotal REAL NOT NULL,
  tax_total REAL NOT NULL,
  discount_total REAL NOT NULL DEFAULT 0 CHECK (discount_total >= 0),
  total REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'COMPLETED' CHECK (status IN ('COMPLETED', 'REFUNDED', 'VOID')),
  parent_sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  tax_rate_at_sale REAL NOT NULL DEFAULT 0 CHECK (tax_rate_at_sale >= 0),
  discount_cents REAL NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
  tax_cents REAL NOT NULL DEFAULT 0 CHECK (tax_cents >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  method TEXT NOT NULL CHECK (method IN ('CASH', 'CARD', 'UPI')),
  amount REAL NOT NULL CHECK (amount >= 0),
  is_partial BOOLEAN NOT NULL DEFAULT 0 CHECK (is_partial IN (0, 1)),
  parent_payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_group ON products(group_id);
CREATE INDEX IF NOT EXISTS idx_product_attributes_product ON product_attributes(product_id);
CREATE INDEX IF NOT EXISTS idx_product_attributes_name_value ON product_attributes(attribute_name, attribute_value);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product ON sale_items(product_id);
CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
CREATE INDEX IF NOT EXISTS idx_quantity_history_product ON quantity_history(product_id);
CREATE INDEX IF NOT EXISTS idx_sales_timestamp ON sales(timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_credit_events_customer ON credit_events(customer_id);
CREATE INDEX IF NOT EXISTS idx_parked_created ON parked_sales(created_at);

CREATE VIEW IF NOT EXISTS product_variants AS
SELECT
  g.id AS group_id,
  g.name AS product_name,
  p.id AS product_id,
  p.SKU,
  p.price,
  p.quantity,
  (
    SELECT json_group_object(pa.attribute_name, pa.attribute_value)
    FROM product_attributes pa
    WHERE pa.product_id = p.id
  ) AS attributes
FROM products p
JOIN product_groups g ON g.id = p.group_id;

CREATE TRIGGER IF NOT EXISTS log_product_update
BEFORE UPDATE ON products
FOR EACH ROW
WHEN NEW.SKU IS NOT OLD.SKU
  OR NEW.barcode IS NOT OLD.barcode
  OR NEW.price IS NOT OLD.price
  OR NEW.cost_price IS NOT OLD.cost_price
  OR NEW.tax_rate IS NOT OLD.tax_rate
BEGIN
  INSERT INTO products_history (product_id, SKU, barcode, price, quantity, cost_price, tax_rate, action)
  VALUES (OLD.id, OLD.SKU, OLD.barcode, OLD.price, OLD.quantity, OLD.cost_price, OLD.tax_rate, 'UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS log_product_delete
BEFORE DELETE ON products
FOR EACH ROW
BEGIN
  INSERT INTO products_history (product_id, SKU, barcode, price, quantity, cost_price, tax_rate, action)
  VALUES (OLD.id, OLD.SKU, OLD.barcode, OLD.price, OLD.quantity, OLD.cost_price, OLD.tax_rate, 'DELETE');
END;

CREATE TRIGGER IF NOT EXISTS log_quantity_change
AFTER UPDATE ON products
FOR EACH ROW
WHEN NEW.quantity IS NOT OLD.quantity
BEGIN
  INSERT INTO quantity_history (product_id, old_quantity, new_quantity)
  VALUES (NEW.id, OLD.quantity, NEW.quantity);
END;

-- Inventory is driven by sale status at the moment items are inserted/deleted.
-- COMPLETED sale  -> inserting items takes stock out
-- REFUNDED sale   -> inserting items puts stock back
CREATE TRIGGER IF NOT EXISTS adjust_inventory_on_item_insert
AFTER INSERT ON sale_items
BEGIN
  UPDATE products
  SET quantity = CASE
    WHEN (SELECT status FROM sales WHERE id = NEW.sale_id) = 'COMPLETED'
      THEN quantity - NEW.quantity
    WHEN (SELECT status FROM sales WHERE id = NEW.sale_id) = 'REFUNDED'
      THEN quantity + NEW.quantity
    ELSE quantity
  END
  WHERE id = NEW.product_id;
END;

CREATE TRIGGER IF NOT EXISTS restore_inventory_on_item_delete
AFTER DELETE ON sale_items
BEGIN
  UPDATE products
  SET quantity = CASE
    WHEN (SELECT status FROM sales WHERE id = OLD.sale_id) = 'COMPLETED'
      THEN quantity + OLD.quantity
    WHEN (SELECT status FROM sales WHERE id = OLD.sale_id) = 'REFUNDED'
      THEN quantity - OLD.quantity
    ELSE quantity
  END
  WHERE id = OLD.product_id;
END;
