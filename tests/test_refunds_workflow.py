"""Return workflow regression: preview and commit must be equivalent.

Use disposable in-memory SQLite databases. No shop data is touched.
"""
import json
from base import POSTestCase
from pos import catalog, customers, sales


class RefundWorkflowTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(SKU="RETURN-TEE", price=100, tax_rate=5, quantity=30)
        self.customer = customers.create_customer(self.conn, "Example Buyer")

    def sale(self, qty=3, payment=None, customer_id=None, use_credit=False):
        amount = qty * 105
        return sales.checkout(
            self.conn, items=[{"product_id": self.pid, "quantity": qty}],
            payments=payment if payment is not None else [{"method": "CASH", "amount": amount}],
            customer_id=customer_id, use_credit=use_credit)

    def test_payout_breakdown_is_identical_to_committed_refund(self):
        sale = self.sale(payment=[{"method": "CASH", "amount": 210},
                                  {"method": "UPI", "amount": 105}],
                         customer_id=self.customer["id"])
        lines = [{"product_id": self.pid, "quantity": 1}]
        preview = sales.preview_refund(self.conn, sale["id"], lines)
        actual = sales.refund_sale(self.conn, sale["id"], lines=lines,
                                   reason="Size / fit issue", request_id="return_test_001")
        self.assertEqual(actual["total"], preview["total"])
        self.assertEqual(actual["payments"], [{"method": p["method"], "amount": p["amount"],
                                                "is_partial": True} for p in preview["refund_payments"]])
        self.assertEqual(actual["store_credit_refunded"], preview["refund_store_credit"])
        self.assertEqual(actual["refund_reason"], "Size / fit issue")
        self.assertEqual(actual["parent_invoice_number"], sale["invoice_number"])
        self.assertEqual(actual["items"][0]["quantity"], 1)
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], 28)

    def test_retry_returns_same_receipt_without_double_stock_or_loyalty(self):
        sale = self.sale(qty=2, customer_id=self.customer["id"])
        lines = [{"product_id": self.pid, "quantity": 1}]
        refund = sales.refund_sale(self.conn, sale["id"], lines, reason="Defective",
                                   request_id="return_test_002")
        stock = catalog.get_product(self.conn, self.pid)["quantity"]
        events = self.conn.execute('SELECT COUNT(*) FROM loyalty_events').fetchone()[0]
        again = sales.refund_sale(self.conn, sale["id"], lines, reason="Defective",
                                  request_id="return_test_002")
        self.assertEqual(refund["id"], again["id"])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], stock)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM loyalty_events').fetchone()[0], events)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sales WHERE status="REFUNDED"').fetchone()[0], 1)

    def test_reusing_key_with_other_details_is_rejected(self):
        sale = self.sale(qty=2)
        sales.refund_sale(self.conn, sale["id"], [{"product_id": self.pid, "quantity": 1}],
                          reason="Wrong item supplied", request_id="return_test_003")
        with self.assertRaisesRegex(sales.InvalidSaleError, "different details"):
            sales.refund_sale(self.conn, sale["id"], [{"product_id": self.pid, "quantity": 1}],
                              reason="Different reason", request_id="return_test_003")

    def test_empty_lines_must_not_turn_into_full_refund(self):
        sale = self.sale()
        with self.assertRaisesRegex(sales.InvalidSaleError, "Select at least one"):
            sales.refund_sale(self.conn, sale["id"], lines=[])
        self.assertFalse(sales.get_sale_detail(self.conn, sale["id"])["refunded"])

    def test_partial_refund_destination_then_second_preview(self):
        sale = self.sale(qty=3, customer_id=self.customer["id"],
                         payment=[{"method": "CARD", "amount": 105},
                                  {"method": "CASH", "amount": 210}])
        lines = [{"product_id": self.pid, "quantity": 1}]
        p_credit = sales.preview_refund(self.conn, sale["id"], lines, to_credit=True)
        self.assertEqual(p_credit["refund_store_credit"], 105)
        first = sales.refund_sale(self.conn, sale["id"], lines, to_credit=True)
        self.assertEqual(first["payments"], [])
        p_orig = sales.preview_refund(self.conn, sale["id"], lines, to_credit=False)
        second = sales.refund_sale(self.conn, sale["id"], lines)
        self.assertEqual(second["total"], p_orig["total"])
        self.assertEqual(sum(x["amount"] for x in second["payments"]), 105)
        self.assertEqual(second["store_credit_refunded"], p_orig["refund_store_credit"])
        self.assertEqual(sales.get_sale_detail(self.conn, sale["id"])["items"][0]["refundable_qty"], 1)

    def test_credit_only_invoice_refund_preview_requires_credit_destination(self):
        customers.adjust_credit(self.conn, self.customer["id"], 50000, "Deposit")
        sale = self.sale(qty=1, customer_id=self.customer["id"], payment=[], use_credit=True)
        with self.assertRaisesRegex(sales.SalesError, "store credit"):
            sales.preview_refund(self.conn, sale["id"], [{"product_id": self.pid, "quantity": 1}])
        p = sales.preview_refund(self.conn, sale["id"],
                                [{"product_id": self.pid, "quantity": 1}], to_credit=True)
        self.assertEqual(p["refund_store_credit"], 105)

    def test_reason_retained_in_refund_snapshot_not_original_invoice(self):
        sale = self.sale()
        original_snapshot = self.conn.execute(
            "SELECT invoice_snapshot FROM sales WHERE id=?", (sale["id"],)).fetchone()[0]
        refund = sales.refund_sale(self.conn, sale["id"],
                                   [{"product_id": self.pid, "quantity": 1}],
                                   reason="Billing correction")
        self.assertEqual(refund["invoice"]["refund_reason"], "Billing correction")
        self.assertEqual(self.conn.execute("SELECT invoice_snapshot FROM sales WHERE id=?",
                                           (sale["id"],)).fetchone()[0], original_snapshot)

    def test_refund_over_remaining_quantity_rejected(self):
        sale = self.sale(qty=2)
        sales.refund_sale(self.conn, sale["id"], [{"product_id": self.pid, "quantity": 1}])
        with self.assertRaises(sales.InvalidSaleError):
            sales.preview_refund(self.conn, sale["id"],
                                 [{"product_id": self.pid, "quantity": 2}])

    def test_export_records_original_invoice_reason_and_tender(self):
        sale = self.sale(qty=2, customer_id=self.customer["id"])
        child = sales.refund_sale(self.conn, sale["id"],
                                  [{"product_id": self.pid, "quantity": 1}],
                                  reason="Customer changed mind")
        rows = sales.refund_export_rows(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], child["id"])
        self.assertEqual(rows[0][3], sale["invoice_number"])
        self.assertEqual(rows[0][5], "Customer changed mind")
        self.assertEqual(rows[0][9], 105)
        self.assertEqual(rows[0][10], 105)

    def test_old_api_without_reason_or_request_id_still_works(self):
        sale = self.sale(qty=2)
        refund = sales.refund_sale(self.conn, sale["id"],
                                   [{"product_id": self.pid, "quantity": 1}])
        self.assertEqual(refund["total"], 105)


if __name__ == "__main__":
    import unittest
    unittest.main()
