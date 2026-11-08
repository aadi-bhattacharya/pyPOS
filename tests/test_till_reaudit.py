"""Regression checks discovered in the 7 Nov UPI/till release re-audit.

Uses a disposable SQLite database only. Does not require Flask.
"""
import unittest
from pathlib import Path
from base import POSTestCase
from pos import catalog, sales

ROOT = Path(__file__).resolve().parents[1]

class UPIReauditTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(quantity=10, price=100, tax_rate=0)

    def make_sale(self, ref, utr, amount="100.00"):
        return sales.checkout(
            self.conn,
            [{"product_id": self.pid, "quantity": 1}],
            payments=[{"method": "UPI", "amount": amount,
                       "reference": ref, "upi_utr": utr}],
        )

    def test_same_bank_utr_cannot_be_booked_twice(self):
        one = self.make_sale("POSAAAAAAA1111", "987654321012")
        self.assertEqual(one['payments'][0]['upi_utr'], "987654321012")
        with self.assertRaisesRegex(sales.InvalidPaymentError, "bank UTR"):
            self.make_sale("POSBBBBBBB2222", "987654321012")
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 1)
        self.assertEqual(catalog.get_product(self.conn, self.pid)['quantity'], 9)

    def test_utr_is_case_normalized_and_detects_reuse(self):
        self.make_sale("POSAAAAAAA3333", "aBc123xYz")
        with self.assertRaisesRegex(sales.InvalidPaymentError, "bank UTR"):
            self.make_sale("POSBBBBBBB4444", "ABC123XYZ")
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 1)

    def test_duplicate_utr_in_one_split_payment_is_rejected_atomically(self):
        with self.assertRaisesRegex(sales.InvalidPaymentError, "bank UTR"):
            sales.checkout(self.conn, [{"product_id": self.pid, "quantity": 1}], payments=[
                {"method": "UPI", "amount": "50", "reference": "POSAAAAAAA5555", "upi_utr": "77665544"},
                {"method": "UPI", "amount": "50", "reference": "POSBBBBBBB6666", "upi_utr": "77665544"},
            ])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)
        self.assertEqual(catalog.get_product(self.conn, self.pid)['quantity'], 10)

    def test_upi_without_bank_utr_remains_accepted_for_manual_reconciliation(self):
        a = self.make_sale("POSAAAAAAA7777", "")
        b = self.make_sale("POSBBBBBBB8888", "")
        self.assertNotEqual(a['id'], b['id'])

class RuntimePackagingTests(unittest.TestCase):
    def test_every_install_method_includes_runtime_dependencies(self):
        mandatory = ["Flask", "waitress", "qrcode"]
        files = ["requirements.txt", "pyproject.toml", "build_portable.py", ".github/workflows/build-binaries.yml"]
        for file in files:
            contents = (ROOT/file).read_text().lower()
            for name in mandatory:
                with self.subTest(file=file, package=name):
                    self.assertIn(name.lower(), contents)
        for file in ["run.sh", "run.bat"]:
            contents = (ROOT/file).read_text().lower()
            self.assertIn("import flask, waitress, qrcode", contents)

if __name__ == '__main__':
    unittest.main()
