"""Stock-shrinkage and discount abuse regressions; no Flask dependency.

Run with: PYTHONPATH=.:tests python -m pytest -q tests/test_shrinkage_hardening.py
"""
import unittest
from base import POSTestCase
from pos import catalog, catalog_import, sales, stock_policy, settings, operations


class StockPolicyTests(unittest.TestCase):
    cashier = {"id": 4, "username": "counter4", "role": "cashier"}
    manager = {"id": 5, "username": "mgr5", "role": "manager"}

    def test_cashier_denied_reduction_and_invalid_quantities(self):
        for d in (-1, "-5", -100):
            with self.subTest(delta=d), self.assertRaises(stock_policy.InventoryForbidden):
                stock_policy.authorize_adjustment(d, "Damaged package", "ADJUSTMENT", self.cashier)
        for d in (0, True, False, 1.3, "1.3", None, "1e3", "", 1000001):
            with self.subTest(delta=d), self.assertRaises(catalog.InvalidInputError):
                stock_policy.authorize_adjustment(d, "Supplier bill", "RECEIPT", self.cashier)

    def test_server_originates_identity_and_restricts_movement_kind(self):
        d, reason, kind = stock_policy.authorize_adjustment("+3", "Invoice 18", "RECEIPT", self.cashier)
        self.assertEqual((d, kind), (3, "RECEIPT"))
        self.assertIn("operator=counter4 role=cashier", reason)
        with self.assertRaises(catalog.InvalidInputError):
            stock_policy.authorize_adjustment(5, "Invoice 18", "SALE", self.cashier)
        with self.assertRaises(catalog.InvalidInputError):
            stock_policy.authorize_adjustment(5, "", "RECEIPT", self.cashier)
        with self.assertRaises(catalog.InvalidInputError):
            stock_policy.authorize_adjustment(-1, "Stock mismatch", "RECEIPT", self.manager)

    def test_admin_override_applies_to_only_cashier_manual_reductions(self):
        self.assertIsNone(stock_policy.cashier_import_mode("skip"))
        for args in (("merge", False, False), ("add_stock", True, False), ("add_stock", False, True)):
            with self.assertRaises(stock_policy.InventoryForbidden):
                stock_policy.cashier_import_mode(*args)
        self.assertIsNone(stock_policy.cashier_import_mode("add_stock"))
        d, _, kind = stock_policy.authorize_adjustment(-2, "Corrected miscount", "ADJUSTMENT",
                                                       self.cashier, cashier_can_reduce=True)
        self.assertEqual((d, kind), (-2, "ADJUSTMENT"))

    def test_cashier_product_creation_is_allowed_but_not_free_or_costed(self):
        payload = {"SKU": "NEW", "price": 1299.0, "quantity": 3, "tax_rate": 5}
        stock_policy.cashier_new_product(payload)
        for changes in ({"price": 0}, {"price": -1}, {"price": "nan"},
                        {"price": "1.234"}, {"quantity": -1},
                        {"quantity": 1.9}, {"quantity": True},
                        {"cost_price": 999}, {"quantity": 1000001},
                        {"tax_rate": "nan"}, {"tax_rate": 101}, {"tax_rate": -1}):
            with self.subTest(changes=changes), self.assertRaises(stock_policy.InventoryForbidden):
                stock_policy.cashier_new_product({**payload, **changes})


class ShrinkageServiceTests(POSTestCase):
    def test_cashier_product_creation_atomic_variants_and_audit(self):
        gid = self.mkgroup("Shirts")
        actor = {"username": "counter1", "role": "cashier"}
        pid = catalog.add_product_to_group(self.conn, gid, "SKU-NEW", "1230123123", 900, 12, 0, 5,
                                           attributes={"Size": "L", "Colour": "Blue"}, audit_actor=actor)
        product = catalog.get_product(self.conn, pid)
        self.assertEqual(product["quantity"], 12)
        attrs={a["name"]: a["value"] for a in product["attributes"]}
        self.assertEqual((attrs["Size"], attrs["Colour"]), ("L", "Blue"))
        self.assertIn("operator=counter1", attrs["Manager Review Required"])
        mv = self.conn.execute("SELECT kind,reason FROM stock_movements WHERE product_id=?", (pid,)).fetchone()
        self.assertEqual(mv["kind"], "INITIAL_STOCK")
        self.assertIn("operator=counter1 role=cashier", mv["reason"])
        with self.assertRaises(catalog.DuplicateSKUError):
            catalog.add_product_to_group(self.conn, gid, "sku-new", None, 200, 1, 0, 5, audit_actor=actor)
        with self.assertRaises(catalog.DuplicateBarcodeError):
            catalog.add_product_to_group(self.conn, gid, "SKU-ALT-MRP", "1230123123", 600, 2, 0, 5, audit_actor=actor)
        with self.assertRaises(catalog.InvalidInputError):
            catalog.add_product_to_group(self.conn, gid, "SKU-FAIL", "NEW-CODE", 100, 1, 0, 0,
                                         attributes={"Size": "L", "GST Review Required": "Hide"}, audit_actor=actor)
        self.assertIsNone(catalog.find_by_code(self.conn, "SKU-FAIL"))

    def test_cashier_safe_import_creates_new_product_and_receives_existing(self):
        gid = self.mkgroup("Shirts")
        pid = catalog.add_product_to_group(self.conn, gid, "OLD", "EANOLD", 200, 5, 88, 5)
        new = {"sku": "NEW", "name": "Shirts", "barcode": "EANNEW", "price": 300.0,
               "quantity": 4, "cost_price": 150.0, "tax_rate": 5.0, "hsn_code": "",
               "unit_code": "NOS", "attrs": {"Size": "M"}, "source_row": 2}
        old = {"sku": "OLD", "name": "Shirts", "barcode": "EANOLD", "price": 200.0,
               "quantity": 3, "cost_price": 999.0, "tax_rate": 5.0, "hsn_code": "",
               "unit_code": "NOS", "attrs": {}, "source_row": 3}
        self.assertTrue(catalog_import.preview(self.conn,[new,old],[],"add_stock",cashier_safe=True)["can_import"])
        result=catalog_import.apply(self.conn,[new,old],[],"add_stock",cashier_safe=True,
                                    audit_actor={"username":"counter1","role":"cashier"})
        self.assertEqual(result["created"],1)
        self.assertEqual(result["stock_added"],3)
        self.assertEqual(catalog.get_product(self.conn,pid)["quantity"],8)
        created=catalog.find_by_code(self.conn,"NEW")
        self.assertEqual((created["quantity"],created["cost_price"]),(4,0))
        audit=self.conn.execute("SELECT kind,reason FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 1",(created["id"],)).fetchone()
        self.assertEqual(audit["kind"],"INITIAL_STOCK")
        self.assertIn("operator=counter1",audit["reason"])
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn,[new,old],[],"add_stock",cashier_safe=True)

    def test_cashier_safe_import_rejects_malicious_rows_atomically(self):
        gid = self.mkgroup("Shirts")
        pid = catalog.add_product_to_group(self.conn,gid,"OLD","EANOLD",200,5,100,5)
        valid = {"sku":"NEW","name":"Shirts","barcode":"EANNEW","price":250.0,
                 "quantity":3,"cost_price":100.0,"tax_rate":5.0,"hsn_code":"",
                 "unit_code":"NOS","attrs":{},"source_row":2}
        existing = {**valid, "sku":"OLD", "barcode":"EANOLD", "price":200.,"source_row":3}
        for changes in ({"sku":"NEW","price":0}, {"sku":"NEW","tax_rate":None},
                        {"sku":"ALT","barcode":"EANOLD", "price":150.},
                        {"sku":"OLD","barcode":"EANOLD","price":1.},
                        {"sku":"OLD","barcode":"EANOLD","price":200.,"tax_rate":0.},
                        {"sku":"OLD","barcode":"EANOLD","price":200.,"name":"Other"},
                        {"attrs":{"Cost Review Required":"No issues"}}):
            candidate={**valid,**changes,"source_row":3}
            result=catalog_import.preview(self.conn,[valid,candidate],[],"add_stock",cashier_safe=True)
            self.assertFalse(result["can_import"],(changes,result))
            with self.assertRaises(catalog_import.ImportError):
                catalog_import.apply(self.conn,[valid,candidate],[],"add_stock",cashier_safe=True)
            self.assertIsNone(catalog.find_by_code(self.conn,"NEW"))
            self.assertEqual(catalog.get_product(self.conn,pid)["quantity"],5)
        self.assertTrue(catalog_import.preview(self.conn,[valid,existing],[],"skip",cashier_safe=True)["can_import"])
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn,[valid],[],"merge",cashier_safe=True)

    def test_delete_cannot_erase_stock_without_visible_adjustment(self):
        pid = self.mkproduct(SKU="STOCKED")
        with self.assertRaises(catalog.InvalidInputError):
            catalog.delete_product(self.conn, pid)
        self.assertIsNotNone(catalog.get_product(self.conn, pid))
        self.assertEqual(catalog.adjust_stock(self.conn, pid, -5, reason="Approved inventory writeoff"), 0)
        catalog.delete_product(self.conn, pid)
        audit = self.conn.execute("SELECT sku_at_event, delta FROM stock_movements WHERE sku_at_event='STOCKED' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((audit[0], audit[1]), ("STOCKED", -5))

    def test_cashier_import_only_adds_existing_exact_sku(self):
        gid = self.mkgroup("Coats")
        pid = catalog.add_product_to_group(self.conn, gid, "COAT-L-OLD", "8901111", 1499, 2, 800, 12)
        catalog.set_product_attribute(self.conn, pid, "Size", "L")
        item = {"sku": "COAT-L-OLD", "name": "Coats", "barcode": "8901111", "price": 1499.0,
                "quantity": 3, "cost_price": 800.0, "tax_rate": 12.0, "hsn_code": "",
                "unit_code": "NOS", "attrs": {"Size": "L"}, "source_row": 2}
        preview = catalog_import.preview(self.conn, [item], [], "add_stock", existing_only=True)
        self.assertTrue(preview["can_import"], preview)
        receipt = catalog_import.apply(self.conn, [item], [], "add_stock", existing_only=True,
                                       audit_actor={"username": "counter1", "role": "cashier"})
        self.assertEqual(receipt["created"], 0)
        self.assertEqual(receipt["stock_added"], 3)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 5)
        movement = self.conn.execute("SELECT kind, reason FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        self.assertEqual(movement["kind"], "IMPORT_RECEIPT")
        self.assertIn("operator=counter1 role=cashier", movement["reason"])

    def test_cashier_import_rejects_new_or_mismatched_mrp_without_partial_mutation(self):
        gid = self.mkgroup("Coats")
        pid = catalog.add_product_to_group(self.conn, gid, "COAT-L", "8901112", 1499, 2, 800, 12)
        good = {"sku": "COAT-L", "name": "Coats", "barcode": "8901112", "price": 1499.0,
                "quantity": 2, "cost_price": 800.0, "tax_rate": 12.0, "hsn_code": "",
                "unit_code": "NOS", "attrs": {}, "source_row": 2}
        for changes in ({"sku": "NOT-IN-CATALOG", "source_row": 3},
                        {"price": 1299.0}, {"barcode": "ANOTHER-EAN"},
                        {"name": "Other style"}, {"quantity": 0}):
            bad = {**good, **changes}
            result = catalog_import.preview(self.conn, [good, bad], [], "add_stock", existing_only=True)
            self.assertFalse(result["can_import"], result)
            with self.assertRaises(catalog_import.ImportError):
                catalog_import.apply(self.conn, [good, bad], [], "add_stock", existing_only=True)
            self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 2)
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn, [good], [], "skip", existing_only=True)
        with self.assertRaises(catalog_import.ImportError):
            catalog_import.apply(self.conn, [good], [], "add_stock", existing_only=True, force_repeat=True)

    def test_cashier_discount_limit_rejects_free_or_underpriced_sales(self):
        pid = self.mkproduct(SKU="SALE-PRODUCT", price=100, quantity=10, tax_rate=0)
        items = [{"product_id": pid, "quantity": 1}]
        for mode, value in (("percent", 100), ("amount", 100), ("percent", 11)):
            with self.subTest(mode=mode, amount=value), self.assertRaises(sales.InvalidSaleError):
                sales.checkout(self.conn, items, [{"method": "CASH", "amount": 100}],
                               discount_mode=mode, discount_value=value,
                               max_discount_percent=10)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 10)
        receipt = sales.checkout(self.conn, items, [{"method": "CASH", "amount": 90}],
                                 discount_mode="amount", discount_value=10,
                                 max_discount_percent=10)
        self.assertEqual(receipt["total"], 90)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 9)
        with self.assertRaises(sales.InvalidSaleError):
            sales.checkout(self.conn, items, [{"method": "CASH", "amount": 100}],
                           discount_mode="percent", discount_value=1,
                           max_discount_percent=0)

    def test_stocktake_shortage_requires_reason_and_records_actor(self):
        pid = self.mkproduct(SKU="COUNTED", quantity=8)
        st = operations.start_stocktake(self.conn, "Shelf count")
        operations.count_stock(self.conn, st["id"], pid, 3)
        with self.assertRaisesRegex(operations.OperationError, "requires a reason"):
            operations.post_stocktake(self.conn, st["id"])
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 8)
        operations.post_stocktake(self.conn, st["id"], adjustment_reason="Broken packaging counted",
            actor={"username":"manager1", "role":"manager"})
        movement = self.conn.execute("SELECT reason FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 1",(pid,)).fetchone()
        self.assertIn("operator=manager1 role=manager",movement[0])
        self.assertIn("Broken packaging counted",movement[0])
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 3)

    def test_discount_policy_setting_is_strict(self):
        settings.save_settings(self.conn, {"cashier_max_discount_percent": "10"})
        self.assertEqual(settings.get_settings(self.conn)["cashier_max_discount_percent"], "10")
        for value in ("101", "-1", "3.5", "", "nan"):
            with self.assertRaises(ValueError):
                settings.save_settings(self.conn, {"cashier_max_discount_percent": value})
