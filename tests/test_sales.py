import unittest
from decimal import Decimal

from base import POSTestCase

from pos import catalog, sales


class TotalsTests(unittest.TestCase):
    def test_simple_tax(self):
        t = sales.compute_totals([{"price": 10, "tax_rate": 10, "quantity": 2}])
        self.assertEqual(t["subtotal_gross"], Decimal("20"))
        self.assertEqual(t["tax_total"], Decimal("2.00"))
        self.assertEqual(t["total"], Decimal("22.00"))

    def test_percent_discount_allocation_sums_exactly(self):
        lines = [
            {"price": 33.33, "tax_rate": 5, "quantity": 1},
            {"price": 19.99, "tax_rate": 0, "quantity": 2},
            {"price": 7.77, "tax_rate": 12.5, "quantity": 3},
        ]
        t = sales.compute_totals(lines, "percent", 15)
        alloc = sum(l["discount_cents"] for l in t["lines"])
        self.assertEqual(alloc, int(t["discount_total"] * 100))
        self.assertEqual(
            t["total"], t["subtotal_net"] + t["tax_total"]
        )

    def test_amount_discount_clamped_and_exact(self):
        t = sales.compute_totals(
            [{"price": 5, "tax_rate": 10, "quantity": 1}], "amount", 500
        )
        self.assertEqual(t["discount_total"], Decimal("5.00"))
        self.assertEqual(t["total"], Decimal("0.00"))

    def test_zero_tax_line(self):
        t = sales.compute_totals([{"price": 8.5, "tax_rate": 0, "quantity": 3}])
        self.assertEqual(t["tax_total"], Decimal("0.00"))
        self.assertEqual(t["total"], Decimal("25.50"))


class CheckoutTests(POSTestCase):
    def sale_items(self, pid, qty):
        return [{"product_id": pid, "quantity": qty}]

    def test_checkout_happy_path(self):
        pid = self.mkproduct(price=10, tax_rate=10, quantity=5)
        receipt = sales.checkout(
            self.conn, self.sale_items(pid, 2),
            payments=[{"method": "CASH", "amount": 50}],
        )
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertAlmostEqual(receipt["subtotal"], 20.0)
        self.assertAlmostEqual(receipt["tax_total"], 2.0)
        self.assertAlmostEqual(receipt["total"], 22.0)
        self.assertAlmostEqual(receipt["change"], 28.0)
        p = catalog.get_product(self.conn, pid)
        self.assertEqual(p["quantity"], 3)

    def test_checkout_with_discount(self):
        pid = self.mkproduct(price=100, tax_rate=10, quantity=10)
        r = sales.checkout(
            self.conn, self.sale_items(pid, 1),
            payments=[{"method": "CARD", "amount": 99}],
            discount_mode="percent", discount_value=10,
        )
        self.assertAlmostEqual(r["discount_total"], 10.0)
        self.assertAlmostEqual(r["total"], 99.0)  # 90 net + 9 tax
        self.assertAlmostEqual(r["change"], 0.0)

    def test_insufficient_stock_rolls_back(self):
        pid = self.mkproduct(quantity=1)
        before = self.conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]
        with self.assertRaises(catalog.InsufficientStockError):
            sales.checkout(self.conn, self.sale_items(pid, 5),
                           payments=[{"method": "CASH", "amount": 100}])
        after = self.conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 1)

    def test_unknown_product_rolls_back(self):
        with self.assertRaises(catalog.ProductNotFoundError):
            sales.checkout(self.conn, [{"product_id": 4242, "quantity": 1}],
                           payments=[{"method": "CASH", "amount": 10}])

    def test_empty_cart_rejected(self):
        with self.assertRaises(sales.InvalidSaleError):
            sales.checkout(self.conn, [], payments=[{"method": "CASH", "amount": 1}])

    def test_short_payment_rejected(self):
        pid = self.mkproduct(price=50, quantity=2)
        with self.assertRaises(sales.InsufficientPaymentError):
            sales.checkout(self.conn, self.sale_items(pid, 1),
                           payments=[{"method": "CARD", "amount": 10}])

    def test_noncash_overpay_rejected(self):
        pid = self.mkproduct(price=50, tax_rate=0, quantity=2)
        with self.assertRaises(sales.InvalidPaymentError):
            sales.checkout(self.conn, self.sale_items(pid, 1),
                           payments=[{"method": "UPI", "amount": 60}])
        # cash overpayment is fine and yields change
        r = sales.checkout(self.conn, self.sale_items(pid, 1),
                           payments=[{"method": "CASH", "amount": 60}])
        self.assertAlmostEqual(r["change"], 10.0)

    def test_split_payments_applied_sum_equals_total(self):
        pid = self.mkproduct(price=30, tax_rate=0, quantity=2)
        r = sales.checkout(
            self.conn, self.sale_items(pid, 2),
            payments=[{"method": "CASH", "amount": 40}, {"method": "CARD", "amount": 25}],
        )
        applied = sum(p["amount"] for p in r["payments"])
        self.assertAlmostEqual(applied, 60.0)
        methods = sorted(p["method"] for p in r["payments"])
        self.assertEqual(methods, ["CARD", "CASH"])
        self.assertAlmostEqual(r["change"], 5.0)

    def test_list_sales_item_count_with_split_payments(self):
        pid1 = self.mkproduct(SKU="A", price=10, tax_rate=0, quantity=5)
        pid2 = self.mkproduct(SKU="B", price=5, tax_rate=0, quantity=5)
        r = sales.checkout(
            self.conn,
            [{"product_id": pid1, "quantity": 2}, {"product_id": pid2, "quantity": 3}],
            payments=[{"method": "CASH", "amount": 20}, {"method": "CARD", "amount": 15}],
        )
        rows = sales.list_sales(self.conn)
        row = next(x for x in rows if x["id"] == r["id"])
        self.assertEqual(row["item_count"], 5)
        self.assertEqual(sorted(row["methods"].split(",")), ["CARD", "CASH"])

    def test_bad_method_rejected(self):
        pid = self.mkproduct()
        with self.assertRaises(sales.InvalidPaymentError):
            sales.checkout(self.conn, self.sale_items(pid, 1),
                           payments=[{"method": "IOU", "amount": 100}])


class RefundVoidTests(POSTestCase):
    def complete_sale(self, price=10, qty=3):
        pid = self.mkproduct(price=price, tax_rate=0, quantity=qty + 5)
        r = sales.checkout(
            self.conn, [{"product_id": pid, "quantity": qty}],
            payments=[{"method": "CASH", "amount": price * qty}],
        )
        return pid, r

    def test_refund_restores_stock_once_only(self):
        pid, r = self.complete_sale(qty=3)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 5)
        refund = sales.refund_sale(self.conn, r["id"])
        self.assertEqual(refund["status"], "REFUNDED")
        self.assertEqual(refund["parent_sale_id"], r["id"])
        self.assertAlmostEqual(refund["total"], r["total"])
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 8)
        with self.assertRaises(sales.AlreadyProcessedError):
            sales.refund_sale(self.conn, r["id"])

    def test_void_restores_stock(self):
        pid, r = self.complete_sale(qty=2)
        voided = sales.void_sale(self.conn, r["id"])
        self.assertEqual(voided["status"], "VOID")
        self.assertEqual(len(voided["items"]), 0)
        self.assertEqual(catalog.get_product(self.conn, pid)["quantity"], 7)
        with self.assertRaises(sales.AlreadyProcessedError):
            sales.void_sale(self.conn, r["id"])
        with self.assertRaises(sales.SalesError):
            sales.refund_sale(self.conn, r["id"])

    def test_missing_sale(self):
        with self.assertRaises(sales.SaleNotFoundError):
            sales.get_sale_detail(self.conn, 999)

    def test_detail_shape(self):
        pid, r = self.complete_sale(qty=2, price=12.5)
        d = sales.get_sale_detail(self.conn, r["id"])
        self.assertEqual(d["items"][0]["name"], "General")
        self.assertEqual(d["items"][0]["SKU"], "SKU-1")
        self.assertEqual(d["items"][0]["quantity"], 2)
        self.assertEqual(d["payments"][0]["method"], "CASH")
        self.assertFalse(d["refunded"])

    def test_variant_name_in_receipt(self):
        gid = catalog.create_product_group(self.conn, "T-Shirt")
        pid = catalog.add_product_to_group(
            self.conn, gid, "TEE-1", None, 15, 10, 5, 5
        )
        catalog.set_product_attribute(self.conn, pid, "Size", "M")
        catalog.set_product_attribute(self.conn, pid, "Color", "Navy")
        r = sales.checkout(
            self.conn, [{"product_id": pid, "quantity": 1}],
            payments=[{"method": "CASH", "amount": 100}],
        )
        d = sales.get_sale_detail(self.conn, r["id"])
        self.assertIn("T-Shirt (Color: Navy, Size: M)", d["items"][0]["name"])


if __name__ == "__main__":
    unittest.main()
