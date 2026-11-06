import unittest
from base import POSTestCase
from pos import sales, catalog, parked

class SalespersonTrackingTests(POSTestCase):
    def test_attribution_is_internal_and_searchable(self):
        pid = self.mkproduct(price=100, tax_rate=0, quantity=10)
        sale = sales.checkout(self.conn, [{"product_id": pid, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 100}], salesperson="  Priya   S. ")
        self.assertEqual(sale["salesperson"], "Priya S.")
        self.assertNotIn("salesperson", sale.get("invoice") or {})
        self.assertEqual(len(sales.list_sales(self.conn, search="Priya S.")), 1)
        self.assertEqual(self.conn.execute("SELECT salesperson FROM sales WHERE id=?", (sale["id"],)).fetchone()[0], "Priya S.")

    def test_retry_preserves_salesperson_and_rejects_reassignment(self):
        pid = self.mkproduct(price=10, tax_rate=0, quantity=3)
        args = dict(items=[{"product_id": pid, "quantity": 1}], payments=[{"method": "CASH", "amount": 10}],request_id="salesperson_retry_123")
        one=sales.checkout(self.conn, **args, salesperson="Ravi")
        two=sales.checkout(self.conn, **args, salesperson="Ravi")
        self.assertEqual(one["id"],two["id"])
        with self.assertRaises(sales.InvalidSaleError):
            sales.checkout(self.conn, **args, salesperson="Other")
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"],2)

    def test_park_and_recall(self):
        pid=self.mkproduct(price=15,tax_rate=0,quantity=3)
        ticket=parked.park_cart(self.conn,[{"product_id":pid,"quantity":1}],salesperson="Meera")
        self.assertEqual(ticket["salesperson"],"Meera")
        self.assertEqual(parked.get_parked(self.conn,ticket["id"])["salesperson"],"Meera")

    def test_validation_and_optional_empty(self):
        pid=self.mkproduct(price=5,tax_rate=0,quantity=2)
        args=dict(items=[{"product_id":pid,"quantity":1}],payments=[{"method":"CASH","amount":5}])
        with self.assertRaises(sales.InvalidSaleError):sales.checkout(self.conn,**args,salesperson="x"*81)
        self.assertEqual(sales.checkout(self.conn,**args)["salesperson"],"")
