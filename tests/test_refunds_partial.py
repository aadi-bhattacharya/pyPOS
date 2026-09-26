import unittest

from base import POSTestCase
from pos import catalog, sales, customers


class PartialRefundTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(SKU="P-1", price=10.0, tax_rate=10, quantity=20)

    def sell(self, qty, payments=None):
        return sales.checkout(
            self.conn,
            items=[{"product_id": self.pid, "quantity": qty}],
            payments=payments or [{"method": "CASH", "amount": qty * 100}])

    def stock(self):
        return catalog.get_product(self.conn, self.pid)["quantity"]

    def test_partial_refund_exact_money_and_stock(self):
        s = self.sell(4)  # 44 total (40 + 10% tax)
        refund = sales.refund_sale(
            self.conn, s["id"], lines=[{"product_id": self.pid, "quantity": 1}])
        self.assertAlmostEqual(refund["total"], 11.0)  # 10 + 1 tax
        self.assertEqual([(p["method"], p["amount"]) for p in refund["payments"]],
                         [("CASH", 11.0)])
        self.assertEqual(self.stock(), 20 - 4 + 1)
        detail = sales.get_sale_detail(self.conn, s["id"])
        item = detail["items"][0]
        self.assertEqual(item["refunded_qty"], 1)
        self.assertEqual(item["refundable_qty"], 3)
        self.assertFalse(detail["fully_refunded"])
        self.assertTrue(detail["refunded"])

    def test_over_refund_blocked_with_accurate_count(self):
        s = self.sell(3)
        sales.refund_sale(self.conn, s["id"],
                          lines=[{"product_id": self.pid, "quantity": 1}])
        from pos.sales import SalesError
        with self.assertRaisesRegex(SalesError, "Only 2"):
            sales.refund_sale(self.conn, s["id"],
                              lines=[{"product_id": self.pid, "quantity": 99}])

    def test_multiple_partials_then_nothing_left(self):
        s = self.sell(3)
        r1 = sales.refund_sale(self.conn, s["id"],
                               lines=[{"product_id": self.pid, "quantity": 1}])
        r2 = sales.refund_sale(self.conn, s["id"],
                               lines=[{"product_id": self.pid, "quantity": 2}])
        self.assertAlmostEqual(r1["total"], 11.0)
        self.assertAlmostEqual(r2["total"], 22.0)
        from pos.sales import AlreadyProcessedError
        with self.assertRaises(AlreadyProcessedError):
            sales.refund_sale(self.conn, s["id"])
        self.assertTrue(sales.get_sale_detail(self.conn, s["id"])["fully_refunded"])

    def test_lines_for_foreign_product_rejected(self):
        other = self.mkproduct(SKU="OTHER", price=1, quantity=3)
        s = self.sell(1)
        from pos.sales import InvalidSaleError
        with self.assertRaises(InvalidSaleError):
            sales.refund_sale(self.conn, s["id"],
                              lines=[{"product_id": other, "quantity": 1}])

    def test_split_tender_refund_proportional(self):
        s = self.sell(4, payments=[{"method": "CARD", "amount": 22},
                                   {"method": "CASH", "amount": 22},
                                   {"method": "UPI", "amount": 0.01}])
        refund = sales.refund_sale(
            self.conn, s["id"], lines=[{"product_id": self.pid, "quantity": 2}])
        got = {p["method"]: p["amount"] for p in refund["payments"]}
        self.assertAlmostEqual(sum(got.values()), 22.0)
        self.assertAlmostEqual(got.get("CARD", 0), 11.0)
        self.assertAlmostEqual(got.get("CASH", 0), 11.0)

    def test_refund_to_store_credit(self):
        c = customers.create_customer(self.conn, "Asha")
        s = self.sell(4)
        # attach the customer retroactively is not possible; re-sell with one:
        s2 = sales.checkout(
            self.conn,
            items=[{"product_id": self.pid, "quantity": 2}],
            payments=[{"method": "CASH", "amount": 100}],
            customer_id=c["id"])
        refund = sales.refund_sale(
            self.conn, s2["id"], to_credit=True,
            lines=[{"product_id": self.pid, "quantity": 1}])
        self.assertTrue(refund["refunded_to_credit"])
        self.assertEqual(refund["payments"], [])          # no tender rows
        self.assertAlmostEqual(refund["total"], 11.0)
        bal = customers.get_customer(self.conn, c["id"])["store_credit"]
        self.assertAlmostEqual(bal, 11.0)
        events = customers.credit_events(self.conn, c["id"])
        self.assertEqual(events[0]["reason"], f"Refund of sale #{s2['id']}")

    def test_credit_refund_needs_customer(self):
        from pos.sales import SalesError
        s = self.sell(2)
        with self.assertRaises(SalesError):
            sales.refund_sale(self.conn, s["id"], to_credit=True)

    def test_credit_paid_sale_cannot_return_to_tender(self):
        from pos.sales import SalesError
        c = customers.create_customer(self.conn, "Asha")
        customers.adjust_credit(self.conn, c["id"], 2000, "gift")  # covers 11.00 total
        s = sales.checkout(
            self.conn,
            items=[{"product_id": self.pid, "quantity": 1}],
            payments=None, customer_id=c["id"], use_credit=True)
        with self.assertRaisesRegex(SalesError, "store credit"):
            sales.refund_sale(self.conn, s["id"])

    def test_list_flags_partial_and_full(self):
        c = customers.create_customer(self.conn, "Asha")
        s = sales.checkout(
            self.conn,
            items=[{"product_id": self.pid, "quantity": 4}],
            payments=[{"method": "CASH", "amount": 100}], customer_id=c["id"])
        rows = sales.list_sales(self.conn)
        parent = [r for r in rows if r["id"] == s["id"]][0]
        self.assertFalse(parent["partially_refunded"])

        sales.refund_sale(self.conn, s["id"],
                          lines=[{"product_id": self.pid, "quantity": 1}])
        parent = [r for r in sales.list_sales(self.conn) if r["id"] == s["id"]][0]
        self.assertTrue(parent["partially_refunded"])
        self.assertEqual(parent["refunded_qty"], 1)

        sales.refund_sale(self.conn, s["id"], to_credit=True)
        parent = [r for r in sales.list_sales(self.conn) if r["id"] == s["id"]][0]
        self.assertFalse(parent["partially_refunded"])
        self.assertTrue(parent["refunded"])

    def test_legacy_line_without_recorded_cents_still_refunds(self):
        # simulate a pre-migration row: no discount/tax cents recorded
        s = self.sell(2)
        self.conn.execute(
            "UPDATE sale_items SET discount_cents = 0, tax_cents = 0 WHERE sale_id = ?",
            (s["id"],))
        refund = sales.refund_sale(
            self.conn, s["id"], lines=[{"product_id": self.pid, "quantity": 1}])
        # gross 10.00; tax derived from the stored rate on net -> 1.00
        self.assertAlmostEqual(refund["total"], 11.0)


if __name__ == "__main__":
    unittest.main()
