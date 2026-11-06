"""pyPOS 2.0 non-destructive migration, SKU classification and stock audit tests."""
import tempfile
from pathlib import Path

from base import POSTestCase
from pos import catalog, sales
from pos.db import connect, init_db


class InventoryV2Tests(POSTestCase):
    def test_group_classification_does_not_merge_skus(self):
        group = self.mkgroup("Amante Full Cover")
        one = self.mkproduct(group_id=group, SKU="BLK-34B", quantity=3)
        two = self.mkproduct(group_id=group, SKU="PNK-36B", quantity=6)
        catalog.set_product_attribute(self.conn, one, "Colour", "Black")
        catalog.set_product_attribute(self.conn, two, "Colour", "Pink")
        brand = catalog.create_label(self.conn, "brands", "Amante")
        category = catalog.create_label(self.conn, "categories", "Bras")
        catalog.classify_group(self.conn, group, brand_id=brand, category_id=category)
        rows = {r["SKU"]: r for r in catalog.list_products(self.conn)}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["BLK-34B"]["quantity"], 3)
        self.assertEqual(rows["PNK-36B"]["quantity"], 6)
        self.assertEqual(rows["BLK-34B"]["brand_name"], "Amante")
        self.assertEqual(rows["PNK-36B"]["category_name"], "Bras")
        self.assertTrue(catalog.list_products(self.conn, query="Amante"))

    def test_stock_receiving_and_adjustments_preserve_audit(self):
        pid = self.mkproduct(quantity=3)
        self.assertEqual(catalog.adjust_stock(self.conn, pid, 5, kind="RECEIPT", reason="INV/22"), 8)
        self.assertEqual(catalog.adjust_stock(self.conn, pid, -2, reason="Damaged"), 6)
        history = catalog.stock_history(self.conn, pid)
        self.assertEqual([(r["kind"], r["delta"], r["balance_after"]) for r in history],
                         [("ADJUSTMENT", -2, 6), ("RECEIPT", 5, 8), ("INITIAL_STOCK", 3, 3)])
        self.assertEqual(history[1]["reason"], "INV/22")
        self.assertEqual(history[0]["reason"], "Damaged")
        with self.assertRaises(catalog.InvalidInputError):
            catalog.adjust_stock(self.conn, pid, -1, kind="RECEIPT")
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 6)

    def test_sales_refunds_and_voids_are_labelled(self):
        pid = self.mkproduct(quantity=10, price=10, tax_rate=0)
        sale = sales.checkout(self.conn, [{"product_id": pid, "quantity": 3}],
                              payments=[{"method": "CASH", "amount": 30}])
        self.assertEqual(catalog.stock_history(self.conn, pid)[0]["kind"], "SALE")
        self.assertEqual(catalog.stock_history(self.conn, pid)[0]["reference_id"], sale["id"])
        sales.refund_sale(self.conn, sale["id"], [{"product_id": pid, "quantity": 1}])
        self.assertEqual(catalog.stock_history(self.conn, pid)[0]["kind"], "REFUND")
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 8)
        later = sales.checkout(self.conn, [{"product_id": pid, "quantity": 1}],
                               payments=[{"method": "CASH", "amount": 10}])
        sales.void_sale(self.conn, later["id"])
        self.assertEqual(catalog.stock_history(self.conn, pid)[0]["kind"], "VOID")
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 8)

    def test_reorder_is_editable_and_computes_sku_alerts(self):
        pid = self.mkproduct(quantity=3)
        catalog.update_product(self.conn, pid, reorder_point=2)
        self.assertEqual(catalog.stock_overview(self.conn, 5)["low_stock"], 0)
        catalog.update_product(self.conn, pid, reorder_point=3)
        self.assertEqual(catalog.stock_overview(self.conn, 5)["low_stock"], 1)
        catalog.update_product(self.conn, pid, reorder_point=None)
        self.assertEqual(catalog.stock_overview(self.conn, 5)["low_stock"], 1)
        with self.assertRaises(catalog.InvalidInputError):
            catalog.update_product(self.conn, pid, reorder_point=1.5)

    def test_failed_sale_has_no_stock_movement(self):
        pid = self.mkproduct(quantity=1)
        count = len(catalog.stock_history(self.conn, pid))
        with self.assertRaises(Exception):
            sales.checkout(self.conn, [{"product_id": pid, "quantity": 7}],
                           payments=[{"method": "CASH", "amount": 1000}])
        self.assertEqual(len(catalog.stock_history(self.conn, pid)), count)

    def test_additive_import_receipt_is_recorded(self):
        from pos import catalog_import
        import io, csv
        file = io.StringIO()
        writer = csv.writer(file)
        writer.writerow(["sku", "product_name", "price", "quantity"])
        writer.writerow(["SKU-1", "T Shirts", "300", "2"])
        pid = self.mkproduct(quantity=5)
        items, errors = catalog_import.parse(file.getvalue().encode(), "items.csv")
        catalog_import.apply(self.conn, items, errors, mode="add_stock")
        event = catalog.stock_history(self.conn, pid)[0]
        self.assertEqual((event["kind"], event["delta"], event["balance_after"]),
                         ("IMPORT_RECEIPT", 2, 7))

    def test_legacy_database_carries_forward_only_known_balances(self):
        # Simulate a pre-v2 installation containing a real sale and an SKU.
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "historic.db"
            conn = connect(database)
            conn.executescript("""
                CREATE TABLE product_groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE products(id INTEGER PRIMARY KEY, group_id INTEGER REFERENCES product_groups(id),
                    SKU TEXT NOT NULL UNIQUE, barcode TEXT UNIQUE, price REAL NOT NULL,
                    quantity INTEGER NOT NULL, cost_price REAL NOT NULL DEFAULT 0, tax_rate REAL NOT NULL DEFAULT 0,
                    updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP, hsn_code TEXT NOT NULL DEFAULT '',
                    unit_code TEXT NOT NULL DEFAULT 'NOS');
                CREATE TABLE sales(id INTEGER PRIMARY KEY, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    subtotal REAL NOT NULL, tax_total REAL NOT NULL, discount_total REAL NOT NULL DEFAULT 0,
                    total REAL NOT NULL, status TEXT NOT NULL DEFAULT 'COMPLETED',
                    parent_sale_id INTEGER, customer_id INTEGER, invoice_number TEXT,
                    invoice_snapshot TEXT);
                CREATE TABLE sale_items(id INTEGER PRIMARY KEY, sale_id INTEGER REFERENCES sales(id),
                    product_id INTEGER REFERENCES products(id), quantity INTEGER NOT NULL,
                    unit_price REAL NOT NULL, tax_rate_at_sale REAL NOT NULL DEFAULT 0,
                    discount_cents REAL NOT NULL DEFAULT 0, tax_cents REAL NOT NULL DEFAULT 0,
                    hsn_at_sale TEXT NOT NULL DEFAULT '', unit_at_sale TEXT NOT NULL DEFAULT 'NOS',
                    description_at_sale TEXT NOT NULL DEFAULT '', sku_at_sale TEXT NOT NULL DEFAULT '');
                CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT,
                    store_credit_cents INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO product_groups(id,name) VALUES(51,'Legacy Style');
                INSERT INTO products(id,group_id,SKU,barcode,price,quantity,cost_price,tax_rate)
                    VALUES(99,51,'SKU-OLD','400400',250,7,100,5);
                INSERT INTO sales(id,subtotal,tax_total,discount_total,total,status)
                    VALUES(33,250,12.5,0,262.5,'COMPLETED');
                INSERT INTO sale_items(id,sale_id,product_id,quantity,unit_price)
                    VALUES(77,33,99,1,250);
            """)
            # Remove synthetic legacy tables that the original migrations would
            # otherwise try to update incorrectly? init_db should handle them.
            conn.close()
            init_db(database)
            init_db(database)
            check = connect(database)
            self.assertEqual(check.execute("SELECT quantity FROM products WHERE id=99").fetchone()[0], 7)
            self.assertEqual(check.execute("SELECT total FROM sales WHERE id=33").fetchone()[0], 262.5)
            self.assertEqual(check.execute("SELECT COUNT(*) FROM sale_items WHERE id=77").fetchone()[0], 1)
            rows = check.execute("SELECT kind,delta,balance_after FROM stock_movements WHERE product_id=99").fetchall()
            self.assertEqual([tuple(x) for x in rows], [("OPENING",7,7)])
            check.close()
