"""India-specific POS financial invariants (pure SQLite, no web server)."""
import base64
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pos import catalog, customers, sales, settings
from pos.db import connect, init_db
from pos.india import IST, financial_year, logo_data


class IndianPOSFinancialTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.file = str(Path(self.temp.name) / "shop.db")
        init_db(self.file)
        self.conn = connect(self.file)
        group_id = catalog.create_product_group(self.conn, "Grocery")
        self.pid = catalog.add_product_to_group(self.conn, group_id, "K-10", None,
                                                118, 20, 75, 18, hsn_code="1234")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _checkout(self, quantity=1, **kw):
        return sales.checkout(self.conn, [{"product_id": self.pid, "quantity": quantity}],
                              payments=[{"method": "CASH", "amount": 1000}], **kw)

    def test_invoice_series_increment_and_receipt_tax_snapshot(self):
        settings.save_settings(self.conn, {
            "store_name": "Original Seller", "store_state": "West Bengal",
            "store_state_code": "19", "store_gstin": "19ABCDE1234F1Z5",
        })
        a = self._checkout()
        b = self._checkout(supply_type="inter", place_of_supply="Bihar")
        self.assertEqual(a["invoice_number"], "26-27/000001" if financial_year()=="26-27" else financial_year()+"/000001")
        self.assertEqual(int(b["invoice_number"].split("/")[1]), 2)
        self.assertEqual([t["name"] for t in a["tax_breakup"]], ["CGST", "SGST"])
        self.assertEqual([t["name"] for t in b["tax_breakup"]], ["IGST"])
        settings.save_settings(self.conn, {"store_name": "Renamed Seller", "tax_breakup": "false"})
        reread = sales.get_sale_detail(self.conn, a["id"])
        self.assertEqual(reread["invoice"]["store_name"], "Original Seller")
        self.assertEqual(reread["items"][0]["hsn_code"], "1234")
        self.assertEqual(reread["tax_breakup"], a["tax_breakup"])

    def test_tax_inclusive_checkout_and_refunds_remain_inclusive(self):
        settings.save_settings(self.conn, {"price_tax_mode": "inclusive"})
        receipt = self._checkout(quantity=3)
        self.assertEqual(receipt["subtotal"], 354)
        self.assertEqual(receipt["tax_total"], 54)
        self.assertEqual(receipt["total"], 354)
        settings.save_settings(self.conn, {"price_tax_mode": "exclusive"})
        total_refunded = 0
        for _ in range(3):
            refund = sales.refund_sale(self.conn, receipt["id"],
                                       lines=[{"product_id": self.pid, "quantity": 1}])
            self.assertEqual(refund["total"], 118)
            total_refunded += refund["total"]
        self.assertEqual(total_refunded, receipt["total"])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], 20)

    def test_partials_reconcile_odd_cents(self):
        catalog.update_product(self.conn, self.pid, price=0.09, tax_rate=5)
        first = self._checkout(quantity=3)
        returned = [sales.refund_sale(self.conn, first["id"],
                   lines=[{"product_id": self.pid, "quantity": 1}]) for _ in range(3)]
        self.assertAlmostEqual(sum(r["total"] for r in returned), first["total"])
        self.assertAlmostEqual(sum(r["tax_total"] for r in returned), first["tax_total"])

    def test_gstin_and_logo_validation_are_atomic(self):
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"store_name": "Must not save", "receipt_paper": "A0"})
        self.assertNotEqual(settings.get_settings(self.conn)["store_name"], "Must not save")
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"store_gstin": "not-a-gstin"})
        with self.assertRaises(ValueError):
            settings.save_settings(self.conn, {"store_gstin": "19ABCDE1234F1Z5",
                                               "store_state_code": "27"})
        image = b"\x89PNG\r\n\x1a\n" + b"safe-sample"
        encoded = "data:image/png;base64," + base64.b64encode(image).decode()
        self.assertEqual(logo_data(encoded), encoded)
        with self.assertRaises(ValueError):
            logo_data("data:image/png;base64," + base64.b64encode(b"not png").decode())

    def test_customer_tax_fields_and_receipt_snapshot(self):
        buyer = customers.create_customer(self.conn, "Buyer", gstin_number="27ABCDE1234F1Z5",
                                          billing_address="Mumbai, Maharashtra")
        original = self._checkout(customer_id=buyer["id"])
        self.assertEqual(original["invoice"]["customer_gstin"], buyer["gstin"])
        self.assertEqual(original["invoice"]["customer_address"], buyer["billing_address"])
        customers.update_customer(self.conn, buyer["id"], billing_address="New address")
        self.assertEqual(sales.get_sale_detail(self.conn, original["id"])["invoice"]["customer_address"],
                         "Mumbai, Maharashtra")

    def test_refund_original_credit_and_cash_reconcile(self):
        settings.save_settings(self.conn, {"price_tax_mode": "inclusive"})
        buyer = customers.create_customer(self.conn, "Cash + credit customer")
        customers.adjust_credit(self.conn, buyer["id"], 10000, "Initial balance")
        receipt = sales.checkout(
            self.conn, [{"product_id": self.pid, "quantity": 2}],
            [{"method": "CASH", "amount": 200}], customer_id=buyer["id"],
            use_credit=True)
        self.assertEqual(receipt["total"], 236)
        self.assertEqual(receipt["store_credit_used"], 100)
        one = sales.refund_sale(self.conn, receipt["id"],
            lines=[{"product_id": self.pid, "quantity": 1}])
        two = sales.refund_sale(self.conn, receipt["id"],
            lines=[{"product_id": self.pid, "quantity": 1}])
        self.assertEqual([one["store_credit_refunded"], two["store_credit_refunded"]], [50, 50])
        self.assertEqual([one["payments"][0]["amount"], two["payments"][0]["amount"]], [68, 68])
        self.assertEqual(customers.get_customer(self.conn, buyer["id"])["store_credit"], 100)
        self.assertEqual(sales.get_sale_detail(self.conn, one["id"])["store_credit_refunded"], 50)

    def test_refund_split_tenders_with_repeated_methods(self):
        receipt = sales.checkout(self.conn, [{"product_id": self.pid, "quantity": 2}],
            [{"method": "CASH", "amount": 75}, {"method": "CASH", "amount": 100},
             {"method": "UPI", "amount": 103.48}])
        one = sales.refund_sale(self.conn, receipt["id"],
            lines=[{"product_id": self.pid, "quantity": 1}])
        two = sales.refund_sale(self.conn, receipt["id"],
            lines=[{"product_id": self.pid, "quantity": 1}])
        amounts = {}
        for r in (one, two):
            for payment in r["payments"]:
                amounts[payment["method"]] = round(
                    amounts.get(payment["method"], 0) + payment["amount"], 2)
        self.assertEqual(amounts, {"CASH": 175, "UPI": 103.48})

    def test_product_rename_does_not_rewrite_issued_receipt(self):
        original = self._checkout()
        catalog.rename_product_group(self.conn,
            catalog.get_product(self.conn, self.pid)["group_id"], "Renamed group")
        self.assertEqual(sales.get_sale_detail(self.conn, original["id"])["items"][0]["name"], "Grocery")

    def test_void_preserves_invoice_items_and_rejects_refunded_sale(self):
        original = self._checkout(quantity=2)
        sales.refund_sale(self.conn, original["id"],
            lines=[{"product_id": self.pid, "quantity": 1}])
        from pos.sales import AlreadyProcessedError
        with self.assertRaises(AlreadyProcessedError):
            sales.void_sale(self.conn, original["id"])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], 19)
        second = self._checkout(quantity=1)
        voided = sales.void_sale(self.conn, second["id"])
        self.assertEqual(voided["status"], "VOID")
        self.assertEqual(voided["items"][0]["name"], "Grocery")
        self.assertEqual(voided["invoice_number"], second["invoice_number"])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], 19)

    def test_local_fiscal_year_boundary(self):
        self.assertEqual(financial_year(datetime(2026, 3, 31, 23, 59, tzinfo=IST)), "25-26")
        self.assertEqual(financial_year(datetime(2026, 4, 1, 0, 0, tzinfo=IST)), "26-27")


if __name__ == "__main__":
    unittest.main()
