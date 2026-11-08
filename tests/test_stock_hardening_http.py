"""Real HTTP permission tests; installs with `python -m pip install -e .`.

Uses a fresh disposable SQLite database. The entire module skips when Flask
is not available in the running environment; no live shop data is touched.
"""
import io
import os
import tempfile
import unittest
try:
    from pos.app import create_app
except ModuleNotFoundError as exc:
    if exc.name != "flask":
        raise
    create_app = None

from pos.db import connect, init_db
from pos import staff, settings, cash_sessions, catalog


@unittest.skipIf(create_app is None, "Flask is required for HTTP integration tests")
class CashierShrinkageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpath = os.path.join(self.temp.name, "test.db")
        init_db(self.dbpath)
        db = connect(self.dbpath)
        try:
            db.execute("INSERT INTO product_groups(name) VALUES('Coats')")
            db.execute("INSERT INTO products(group_id,SKU,barcode,price,cost_price,tax_rate,quantity) "
                       "VALUES(1,'COAT-M','8901112223334',100,50,0,5)")
            staff.add_user(db, "owner1", "Owner", "admin", "12345678")
            staff.add_user(db, "counter1", "Counter clerk", "cashier", "87654321")
            staff.add_user(db, "manager1", "Manager", "manager", "45678912")
            cash_sessions.open_session(db, 0)
        finally:
            db.close()
        self.app = create_app(db_path=self.dbpath)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.login("counter1", "87654321")

    def tearDown(self):
        self.temp.cleanup()

    def login(self, username, pin):
        response = self.client.post("/staff-login", data={"username": username, "pin": pin})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))

    def test_stock_apis_obey_roles_and_reasons(self):
        url = "/api/products/1/stock"
        self.assertEqual(self.client.post(url, json={"delta": -1, "reason": "Shrinkage"}).status_code, 403)
        self.assertEqual(self.client.post(url, json={"delta": 1, "reason": ""}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"delta": 1, "reason": "Invoice 123", "kind": "SALE"}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"delta": 2, "reason": "Invoice 123", "kind": "RECEIPT"}).status_code, 200)
        for method, path, kwargs in (
                ("patch", "/api/products/1", {"json": {"price": 1}}),
                ("delete", "/api/products/1", {}),
                ("post", "/api/products", {"json": {"SKU": "FREE", "price": 0}}),
                ("post", "/api/stocktakes", {"json": {}}),
                ("delete", "/api/customers/1", {})):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path, **kwargs).status_code, 403)
        for path in ("/api/export/inventory.csv", "/api/export/sales.csv", "/api/export/refunds.csv"):
            self.assertEqual(self.client.get(path).status_code, 403)
        rows = self.client.get("/api/products").get_json()
        self.assertIsNone(rows[0]["cost_price"])
        db = connect(self.dbpath)
        try:
            self.assertEqual(db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0], 7)
            movement = db.execute("SELECT reason FROM stock_movements WHERE product_id=1 ORDER BY id DESC LIMIT 1").fetchone()
            self.assertIn("operator=counter1 role=cashier", movement[0])
        finally:
            db.close()
        self.client.get("/staff-logout")
        self.login("manager1", "45678912")
        self.assertEqual(self.client.post(url, json={"delta": -1, "reason": ""}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"delta": -1, "reason": "Checked discrepancy"}).status_code, 200)
        self.assertEqual(self.client.delete("/api/products/1").status_code, 400)  # still stocked

    def test_cashier_discount_and_sale_identity(self):
        sale = {"items": [{"product_id": 1, "quantity": 1}],
                "payments": [{"method": "CASH", "amount": 100}],
                "salesperson": "Owner"}
        self.assertEqual(self.client.post("/api/sales", json={**sale,
                "discount": {"mode":"percent", "value":100}}).status_code, 400)
        self.assertEqual(self.client.post("/api/sales", json=sale).status_code, 201)
        db = connect(self.dbpath)
        try:
            row = db.execute("SELECT salesperson FROM sales ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row[0], "Counter clerk")  # cannot spoof another operator
        finally:
            db.close()

    def test_multi_mrp_lookup_does_not_leak_cost(self):
        db = connect(self.dbpath)
        try:
            catalog.add_product_to_group(db, 1, "COAT-M-NEW", "8901112223334", 120, 4, 65, 0)
        finally:
            db.close()
        result = self.client.get("/api/lookup?code=8901112223334")
        self.assertEqual(result.status_code, 200)
        matches = result.get_json()["matches"]
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(p["cost_price"] is None for p in matches))

    def test_cashier_can_create_product_and_group_but_not_edit_existing(self):
        self.assertEqual(self.client.post("/api/groups",json={"name":"Summer"}).status_code,201)
        new = self.client.post("/api/products",json={
            "group_id":1,"SKU":"SHIRT-S","barcode":"NEW123","price":150.0,
            "quantity":3,"tax_rate":5,"attributes":{"Size":"S", "Colour":"Navy"}})
        self.assertEqual(new.status_code,201,new.get_data(as_text=True))
        payload=new.get_json()
        self.assertIsNone(payload["cost_price"])
        self.assertEqual(payload["quantity"],3)
        attrs={x["name"]:x["value"] for x in payload["attributes"]}
        self.assertEqual((attrs["Size"],attrs["Colour"]),("S","Navy"))
        self.assertIn("operator=counter1",attrs["Manager Review Required"])
        new_id=payload["id"]
        self.assertEqual(self.client.patch(f"/api/products/{new_id}",json={"price":1}).status_code,403)
        self.assertEqual(self.client.post(f"/api/products/{new_id}/attributes",json={"name":"Size","value":"XL"}).status_code,403)
        self.assertEqual(self.client.delete(f"/api/products/{new_id}").status_code,403)
        self.assertEqual(self.client.post("/api/products",json={
            "group_id":1,"SKU":"FAKE-MRP","barcode":"8901112223334","price":1,
            "quantity":3,"tax_rate":0}).status_code,400)
        db=connect(self.dbpath)
        try:
            row=db.execute("SELECT kind,reason FROM stock_movements WHERE product_id=?",(new_id,)).fetchone()
            self.assertIn("operator=counter1",row["reason"])
            self.assertEqual(row["kind"],"INITIAL_STOCK")
        finally:
            db.close()

    def test_cashier_import_creates_new_skus_but_not_existing_sku_edits(self):
        def upload(row, mode="add_stock"):
            content = ("sku,product_name,barcode,price,quantity,cost_price,tax_rate\n" + row + "\n").encode()
            return self.client.post("/api/catalog/import/apply", data={
                "mode": mode, "file": (io.BytesIO(content), "import.csv")},
                content_type="multipart/form-data")
        self.assertEqual(upload("NEW-001,Coats,NEWBAR,350,4,100,5",mode="skip").status_code,200)
        self.assertEqual(upload("COAT-M,Coats,8901112223334,1,2,50,0").status_code,400)
        self.assertEqual(upload("COAT-M,Coats,8901112223334,100,2,50,0",mode="merge").status_code,403)
        self.assertEqual(upload("COAT-M,Coats,8901112223334,100,2,50,0").status_code,200)
        db=connect(self.dbpath)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM products").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT quantity FROM products WHERE id=1").fetchone()[0],7)
            self.assertEqual(db.execute("SELECT cost_price FROM products WHERE SKU='NEW-001'").fetchone()[0],0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
