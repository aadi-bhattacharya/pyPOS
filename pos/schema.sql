PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS brands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE,
  parent_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
  UNIQUE(name, parent_id)
);

CREATE TABLE IF NOT EXISTS product_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  brand_id INTEGER REFERENCES brands(id) ON DELETE SET NULL,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT UNIQUE,
  email TEXT,
  notes TEXT,
  store_credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (store_credit_cents >= 0),
  loyalty_points INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  gstin TEXT NOT NULL DEFAULT '',
  billing_address TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS credit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  delta_cents INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
  timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Append-only loyalty ledger; negative points are permitted only for reversal
-- of earned points after refunds/voids (a customer may already have spent them).
CREATE TABLE IF NOT EXISTS loyalty_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  delta_points INTEGER NOT NULL CHECK (delta_points != 0),
  balance_after INTEGER NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('EARN', 'REFUND', 'VOID', 'ADMIN', 'REDEEM')),
  reason TEXT NOT NULL DEFAULT '',
  sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL,
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS parked_sales (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL,
  items_json TEXT NOT NULL,
  discount_mode TEXT CHECK (discount_mode IN ('percent', 'amount')),
  discount_value REAL NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  salesperson TEXT NOT NULL DEFAULT '',
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
  updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  hsn_code TEXT NOT NULL DEFAULT '',
  unit_code TEXT NOT NULL DEFAULT 'NOS',
  reorder_point INTEGER CHECK (reorder_point IS NULL OR reorder_point >= 0)
);

CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  sku_at_event TEXT NOT NULL,
  delta INTEGER NOT NULL,
  balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
  kind TEXT NOT NULL DEFAULT 'CHANGE',
  reason TEXT NOT NULL DEFAULT '',
  reference_id INTEGER,
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A second/third MRP for the same EAN is a separate product/SKU with its own
-- quantity. Keep the existing UNIQUE products.barcode constraint intact;
-- extra price options point to the same scanner barcode through this table.
CREATE TABLE IF NOT EXISTS barcode_price_options (
  product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
  barcode TEXT NOT NULL CHECK(length(trim(barcode)) > 0)
);
CREATE INDEX IF NOT EXISTS idx_barcode_price_options_code
  ON barcode_price_options(barcode);

CREATE TABLE IF NOT EXISTS product_attributes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  attribute_name TEXT NOT NULL,
  attribute_value TEXT NOT NULL,
  UNIQUE (product_id, attribute_name)
);


-- Purchases and stock counts are additive operational records. No prior sale is rewritten.
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  phone TEXT NOT NULL DEFAULT '',
  gstin TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS purchase_receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  supplier_invoice TEXT NOT NULL,
  received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  total_cents INTEGER NOT NULL CHECK(total_cents >= 0),
  invoice_gst_cents INTEGER CHECK(invoice_gst_cents IS NULL OR invoice_gst_cents >= 0),
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(supplier_id, supplier_invoice)
);
CREATE TABLE IF NOT EXISTS purchase_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  purchase_id INTEGER NOT NULL REFERENCES purchase_receipts(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK(quantity > 0),
  unit_cost_cents INTEGER NOT NULL CHECK(unit_cost_cents >= 0),
  sku_at_receipt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchase_lines_receipt ON purchase_lines(purchase_id);
CREATE TABLE IF NOT EXISTS supplier_payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  purchase_id INTEGER NOT NULL REFERENCES purchase_receipts(id),
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  method TEXT NOT NULL CHECK(method IN ('CASH','UPI','CARD','BANK','OTHER')),
  reference TEXT NOT NULL DEFAULT '',
  request_id TEXT UNIQUE,
  paid_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_supplier_payments_purchase ON supplier_payments(purchase_id);

-- Optional staff PIN access control; disabled until the first admin is enrolled.
CREATE TABLE IF NOT EXISTS staff_users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL COLLATE NOCASE UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('cashier','manager','admin')),
  pin_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Capability flags for each operator. Legacy cashier/manager rows remain valid;
-- their previous access is mapped to a safe preset until explicitly customized.
CREATE TABLE IF NOT EXISTS staff_permissions (
  staff_id INTEGER PRIMARY KEY REFERENCES staff_users(id) ON DELETE CASCADE,
  permissions_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  staff_id INTEGER REFERENCES staff_users(id) ON DELETE SET NULL,
  username_at_event TEXT NOT NULL,
  method TEXT NOT NULL,
  action TEXT NOT NULL,
  response_status INTEGER NOT NULL,
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_staff_audit_actor ON staff_audit_events(staff_id,id);

CREATE TABLE IF NOT EXISTS stocktakes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','POSTED','CANCELLED')),
  note TEXT NOT NULL DEFAULT '',
  opened_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  posted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stocktake_lines (
  stocktake_id INTEGER NOT NULL REFERENCES stocktakes(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  expected INTEGER NOT NULL CHECK(expected >= 0),
  counted INTEGER NOT NULL CHECK(counted >= 0),
  PRIMARY KEY(stocktake_id,product_id)
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
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  invoice_number TEXT UNIQUE,
  -- Operational refund reference, distinct from sales invoice and GST credit note.
  refund_number TEXT,
  salesperson TEXT NOT NULL DEFAULT '',
  invoice_snapshot TEXT,
  cash_session_id INTEGER REFERENCES cash_sessions(id) ON DELETE SET NULL,
  cash_session_explicit INTEGER NOT NULL DEFAULT 0 CHECK(cash_session_explicit IN (0,1))
);

CREATE TABLE IF NOT EXISTS sale_requests (
  request_id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL,
  sale_id INTEGER NOT NULL UNIQUE REFERENCES sales(id),
  change_cents INTEGER NOT NULL DEFAULT 0,
  credit_cents INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_import_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  digest TEXT NOT NULL,
  mode TEXT NOT NULL,
  imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  rows_added INTEGER NOT NULL DEFAULT 0,
  stock_added INTEGER NOT NULL DEFAULT 0,
  repeat_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_stock_import_digest ON stock_import_batches(digest, mode);

CREATE TABLE IF NOT EXISTS cash_movements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cash_session_id INTEGER NOT NULL REFERENCES cash_sessions(id),
  kind TEXT NOT NULL CHECK(kind IN ('CASH_IN', 'CASH_OUT', 'BANK_DEPOSIT', 'PETTY_CASH')),
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
  reference TEXT NOT NULL DEFAULT '',
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cash_movements_session ON cash_movements(cash_session_id,id);

-- Retries of an already committed return resolve to its original refund receipt.
-- This is a transaction key, not a payment gateway integration.
CREATE TABLE IF NOT EXISTS refund_requests (
  request_id TEXT PRIMARY KEY,
  original_sale_id INTEGER NOT NULL REFERENCES sales(id),
  fingerprint TEXT NOT NULL,
  refund_sale_id INTEGER NOT NULL UNIQUE REFERENCES sales(id),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sale_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  tax_rate_at_sale REAL NOT NULL DEFAULT 0 CHECK (tax_rate_at_sale >= 0),
  discount_cents REAL NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
  tax_cents REAL NOT NULL DEFAULT 0 CHECK (tax_cents >= 0),
  hsn_at_sale TEXT NOT NULL DEFAULT '',
  unit_at_sale TEXT NOT NULL DEFAULT 'NOS',
  description_at_sale TEXT NOT NULL DEFAULT '',
  sku_at_sale TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  method TEXT NOT NULL CHECK (method IN ('CASH', 'CARD', 'UPI')),
  amount REAL NOT NULL CHECK (amount >= 0),
  is_partial BOOLEAN NOT NULL DEFAULT 0 CHECK (is_partial IN (0, 1)),
  parent_payment_id INTEGER REFERENCES payments(id) ON DELETE SET NULL,
  reference TEXT NOT NULL DEFAULT '',
  upi_utr TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS invoice_sequences (
  financial_year TEXT PRIMARY KEY,
  last_number INTEGER NOT NULL CHECK (last_number >= 0)
);

-- Refund codes have their own fiscal-year sequence and never consume an invoice number.
CREATE TABLE IF NOT EXISTS refund_sequences (
  financial_year TEXT PRIMARY KEY,
  last_number INTEGER NOT NULL CHECK (last_number >= 0)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_group ON products(group_id);
CREATE INDEX IF NOT EXISTS idx_groups_brand ON product_groups(brand_id);
CREATE INDEX IF NOT EXISTS idx_groups_category ON product_groups(category_id);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_movements(product_id, id);
CREATE INDEX IF NOT EXISTS idx_stock_kind ON stock_movements(kind, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_category_root_name ON categories(name COLLATE NOCASE)
  WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_stock_opening ON stock_movements(product_id) WHERE kind = 'OPENING';
CREATE INDEX IF NOT EXISTS idx_product_attributes_product ON product_attributes(product_id);
CREATE INDEX IF NOT EXISTS idx_product_attributes_name_value ON product_attributes(attribute_name, attribute_value);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product ON sale_items(product_id);
CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
CREATE INDEX IF NOT EXISTS idx_quantity_history_product ON quantity_history(product_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_refund_number ON sales(refund_number);
CREATE INDEX IF NOT EXISTS idx_sales_timestamp ON sales(timestamp);
CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_cash_session ON sales(cash_session_id);
CREATE INDEX IF NOT EXISTS idx_credit_events_customer ON credit_events(customer_id);
CREATE INDEX IF NOT EXISTS idx_loyalty_events_customer ON loyalty_events(customer_id, id);
CREATE INDEX IF NOT EXISTS idx_loyalty_events_sale ON loyalty_events(sale_id);
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

-- Every mutation, including sales/refunds/imports and direct SQL changes, is
-- captured. Operations with known context label the resulting row in their
-- existing atomic transaction. Unknown legacy modifications remain CHANGE.
CREATE TRIGGER IF NOT EXISTS stock_ledger_on_insert
AFTER INSERT ON products
WHEN NEW.quantity > 0
BEGIN
  INSERT INTO stock_movements(product_id, sku_at_event, delta, balance_after, kind)
  VALUES(NEW.id, NEW.SKU, NEW.quantity, NEW.quantity, 'INITIAL_STOCK');
END;

CREATE TRIGGER IF NOT EXISTS stock_ledger_on_update
AFTER UPDATE OF quantity ON products
WHEN NEW.quantity != OLD.quantity
BEGIN
  INSERT INTO stock_movements(product_id, sku_at_event, delta, balance_after, kind)
  VALUES(NEW.id, NEW.SKU, NEW.quantity - OLD.quantity, NEW.quantity, 'CHANGE');
END;

CREATE TRIGGER IF NOT EXISTS stock_ledger_on_delete
BEFORE DELETE ON products
WHEN OLD.quantity > 0
BEGIN
  INSERT INTO stock_movements(product_id, sku_at_event, delta, balance_after, kind)
  VALUES(OLD.id, OLD.SKU, -OLD.quantity, 0, 'REMOVED');
END;
