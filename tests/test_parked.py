import unittest

from base import POSTestCase
from pos import catalog, sales, customers, parked


class ParkedSaleTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(SKU="P-1", price=5.0, tax_rate=10, quantity=8)

    def test_park_list_and_get(self):
        t = parked.park_cart(self.conn,
                             [{"product_id": self.pid, "quantity": 2}],
                             label="Morning order")
        self.assertEqual(t["label"], "Morning order")
        self.assertEqual(t["item_count"], 2)
        self.assertAlmostEqual(t["est_total"], 10.0)
        self.assertEqual(len(parked.list_parked(self.conn)), 1)
        got = parked.get_parked(self.conn, t["id"])
        self.assertEqual(got["items"][0]["name"], "General")

    def test_park_merges_duplicate_products(self):
        t = parked.park_cart(self.conn,
                             [{"product_id": self.pid, "quantity": 1},
                              {"product_id": self.pid, "quantity": 3}])
        self.assertEqual(t["items"][0]["quantity"], 4)

    def test_park_validates_items(self):
        from pos.catalog import InvalidInputError
        with self.assertRaises(InvalidInputError):
            parked.park_cart(self.conn, [])
        with self.assertRaises(InvalidInputError):
            parked.park_cart(self.conn, [{"product_id": self.pid, "quantity": 0}])
        with self.assertRaises(InvalidInputError):
            parked.park_cart(self.conn, [{"product_id": 9999, "quantity": 1}])

    def test_park_with_customer(self):
        c = customers.create_customer(self.conn, "Asha")
        t = parked.park_cart(self.conn,
                             [{"product_id": self.pid, "quantity": 1}],
                             customer_id=c["id"])
        self.assertEqual(t["customer_name"], "Asha")
        # deleting the customer detaches the ticket instead of failing
        customers.delete_customer(self.conn, c["id"])
        self.assertIsNone(parked.get_parked(self.conn, t["id"])["customer_name"])

    def test_stock_not_held_while_parked(self):
        parked.park_cart(self.conn, [{"product_id": self.pid, "quantity": 5}])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], 8)

    def test_recall_then_checkout_flow(self):
        t = parked.park_cart(self.conn,
                             [{"product_id": self.pid, "quantity": 3}])
        items = [{"product_id": it["product_id"], "quantity": it["quantity"]}
                 for it in parked.get_parked(self.conn, t["id"])["items"]]
        receipt = sales.checkout(self.conn, items,
                                 payments=[{"method": "CASH", "amount": 50}])
        self.assertAlmostEqual(receipt["total"], 16.5)  # 15 + 10% tax
        parked.delete_parked(self.conn, t["id"])
        self.assertEqual(parked.list_parked(self.conn), [])

    def test_delete_missing_raises(self):
        from pos.parked import ParkedNotFoundError
        with self.assertRaises(ParkedNotFoundError):
            parked.delete_parked(self.conn, 4242)


if __name__ == "__main__":
    unittest.main()
