"""Internal refund references, including partial returns, retries and migrations.

All data is disposable in-memory or temporary SQLite; no production store is touched.
"""
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from base import POSTestCase
from pos import catalog, sales
from pos.db import init_db, schema_sql, connect
from pos.india import IST, financial_year, next_invoice_number, next_refund_number


class RefundNumberingTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(SKU="NUMBERED-REFUND", price=100, quantity=12, tax_rate=5)

    def checkout(self, qty=3):
        return sales.checkout(self.conn, [{"product_id": self.pid, "quantity": qty}],
                              [{"method": "CASH", "amount": 105 * qty}])

    def return_one(self, sale_id, request_id=None):
        return sales.refund_sale(self.conn, sale_id,
                                [{"product_id": self.pid, "quantity": 1}],
                                reason="Returned item", request_id=request_id)

    def test_two_partial_refunds_have_unique_codes_and_original_sale_id(self):
        parent = self.checkout()
        first = self.return_one(parent["id"], "reference_check_0001")
        second = self.return_one(parent["id"], "reference_check_0002")
        year = financial_year()
        self.assertEqual(first["refund_number"], f"{year}/R000001-{parent['id']:06d}")
        self.assertEqual(second["refund_number"], f"{year}/R000002-{parent['id']:06d}")
        self.assertIsNone(first["invoice_number"])
        self.assertEqual(first["parent_invoice_number"], parent["invoice_number"])
        self.assertEqual(sales.get_sale_detail(self.conn, first["id"])["refund_number"], first["refund_number"])
        self.assertEqual(self.conn.execute("SELECT refund_number FROM sales WHERE id=?", (first["id"],)).fetchone()[0],
                         first["refund_number"])
        self.assertEqual(sales.get_sale_detail(self.conn, parent["id"])["invoice_number"], parent["invoice_number"])
        self.assertIsNone(sales.get_sale_detail(self.conn, parent["id"])["refund_number"])

    def test_retry_returns_original_reference_without_consuming_sequence(self):
        parent = self.checkout()
        first = self.return_one(parent["id"], "reference_retry_001")
        again = self.return_one(parent["id"], "reference_retry_001")
        self.assertEqual(first["refund_number"], again["refund_number"])
        self.assertEqual(first["id"], again["id"])
        second = self.return_one(parent["id"], "reference_retry_002")
        self.assertIn("/R000002-", second["refund_number"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales WHERE status='REFUNDED'").fetchone()[0], 2)

    def test_codes_are_searchable_and_in_export(self):
        parent = self.checkout()
        refund = self.return_one(parent["id"])
        rows = sales.list_sales(self.conn, search=refund["refund_number"])
        self.assertEqual([r["id"] for r in rows], [refund["id"]])
        self.assertEqual(rows[0]["refund_number"], refund["refund_number"])
        exported = sales.refund_export_rows(self.conn)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0][-1], refund["refund_number"])
        self.assertEqual(exported[0][3], parent["invoice_number"])
        self.assertIsNone(sales.get_sale_detail(self.conn, parent["id"])["refund_number"])

    def test_later_rollback_does_not_consume_reference(self):
        parent = self.checkout()
        before_qty = catalog.get_product(self.conn, self.pid)["quantity"]
        with patch.object(sales.loyalty, "reverse_for_return", side_effect=RuntimeError("test failure")):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                self.return_one(parent["id"])
        self.assertEqual(catalog.get_product(self.conn, self.pid)["quantity"], before_qty)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales WHERE status='REFUNDED'").fetchone()[0], 0)
        refund = self.return_one(parent["id"])
        self.assertIn("/R000001-", refund["refund_number"])

    def test_issue_time_in_ist_is_shared_by_timestamp_and_fy(self):
        parent = self.checkout()
        # One second before midnight in India at the FY boundary.
        from datetime import timezone
        before = datetime(2026, 3, 31, 18, 29, 59, tzinfo=timezone.utc)
        with patch.object(sales, "datetime") as clock:
            clock.now.return_value = before
            first = self.return_one(parent["id"])
        self.assertTrue(first["refund_number"].startswith("25-26/R000001-"))
        self.assertEqual(first["timestamp"], "2026-03-31 18:29:59")
        after = datetime(2026, 3, 31, 18, 30, 0, tzinfo=timezone.utc)
        with patch.object(sales, "datetime") as clock:
            clock.now.return_value = after
            second = self.return_one(parent["id"])
        self.assertTrue(second["refund_number"].startswith("26-27/R000001-"))
        self.assertEqual(second["timestamp"], "2026-03-31 18:30:00")

    def test_refund_sequence_is_not_invoice_sequence(self):
        parent = self.checkout()
        self.return_one(parent["id"])
        next_sale = self.checkout()
        self.assertEqual(int(next_sale["invoice_number"].split("/")[-1]),
                         int(parent["invoice_number"].split("/")[-1]) + 1)


class RefundFiscalSequenceTests(POSTestCase):
    def test_independent_financial_years_and_orig_id(self):
        date_25 = datetime(2026, 3, 31, 23, 59, tzinfo=IST)
        date_26 = datetime(2026, 4, 1, 0, 0, tzinfo=IST)
        self.assertEqual(next_refund_number(self.conn, 42, date_25), "25-26/R000001-000042")
        self.assertEqual(next_refund_number(self.conn, 42, date_25), "25-26/R000002-000042")
        self.assertEqual(next_refund_number(self.conn, 42, date_26), "26-27/R000001-000042")
        self.assertEqual(next_invoice_number(self.conn, date_26), "26-27/000001")
        with self.assertRaises(ValueError):
            next_refund_number(self.conn, 0, date_26)


class RefundLegacyMigrationTests(unittest.TestCase):
    def test_existing_refunds_are_not_fabricated_or_renumbered(self):
        old_sql = schema_sql().replace("  refund_number TEXT UNIQUE,\n", "")
        old_sql = re.sub(r"\n-- Refund codes have their own fiscal-year sequence.*?\n\);\n", "\n",
                         old_sql, flags=re.S)
        old_sql = old_sql.replace("CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_refund_number ON sales(refund_number);\n", "")
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "shop.sqlite")
            conn = sqlite3.connect(db)
            conn.executescript(old_sql)
            conn.execute("INSERT INTO sales(id,subtotal,tax_total,discount_total,total,status,invoice_number) "
                         "VALUES (11,100,5,0,105,'COMPLETED','25-26/000011')")
            conn.execute("INSERT INTO sales(id,subtotal,tax_total,discount_total,total,status,parent_sale_id) "
                         "VALUES (12,100,5,0,105,'REFUNDED',11)")
            conn.commit()
            conn.close()
            init_db(db)
            init_db(db)  # migration must be idempotent
            new = connect(db)
            try:
                old = new.execute("SELECT id,invoice_number,refund_number FROM sales WHERE id=12").fetchone()
                self.assertEqual(old["id"], 12)
                self.assertIsNone(old["invoice_number"])
                self.assertIsNone(old["refund_number"])
                self.assertEqual(new.execute("SELECT COUNT(*) FROM refund_sequences").fetchone()[0], 0)
                self.assertEqual(new.execute("SELECT invoice_number FROM sales WHERE id=11").fetchone()[0], "25-26/000011")
            finally:
                new.close()


if __name__ == "__main__":
    unittest.main()
