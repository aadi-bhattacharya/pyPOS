"""Regression tests for financial input validation and record integrity.

Does not require Flask and never touches a user's live database.
"""
import unittest
from decimal import Decimal
from base import POSTestCase
from pos import cash_sessions, catalog, parked, sales


class AuditInvalidMoneyTests(POSTestCase):
    def test_bad_opening_floats_are_rejected_without_opening_day(self):
        for value in ('NaN', 'Infinity', '-1', '10.001', True):
            with self.subTest(value=value), self.assertRaises(catalog.InvalidInputError):
                cash_sessions.open_session(self.conn, value)
            self.assertIsNone(cash_sessions.current_session(self.conn))

    def test_bad_close_counts_do_not_destroy_open_session(self):
        sid = cash_sessions.open_session(self.conn, '120.50')['session']['id']
        for value in ('NaN', 'Infinity', '-1', '10.001', None, True):
            with self.subTest(value=value), self.assertRaises(catalog.InvalidInputError):
                cash_sessions.close_session(self.conn, value)
            self.assertEqual(cash_sessions.current_session(self.conn)['id'], sid)
        rep = cash_sessions.close_session(self.conn, '120.50')
        self.assertEqual(rep['report']['difference'], 0)
        self.assertEqual(rep['session']['counted_cash'], 120.5)


class AuditQuantityTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(quantity=25, price=100, tax_rate=0)

    def test_checkout_rejects_fraction_bool_string_scientific(self):
        for value in (1.9, True, 0.8, '1.5', '1e2', False, -1, None):
            with self.subTest(value=value), self.assertRaises(sales.InvalidSaleError):
                sales.checkout(self.conn, [{'product_id':self.pid,'quantity':value}],
                               payments=[{'method':'CASH','amount':1000}])
        self.assertEqual(catalog.get_product(self.conn,self.pid)['quantity'],25)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sales').fetchone()[0],0)

    def test_parked_cart_rejects_fraction_bool(self):
        for value in (1.9, True, '1.5'):
            with self.subTest(value=value), self.assertRaises(catalog.InvalidInputError):
                parked.park_cart(self.conn,[{'product_id':self.pid,'quantity':value}])
        self.assertEqual(parked.count_parked(self.conn),0)

    def test_refund_preview_and_commit_reject_fraction(self):
        sale = sales.checkout(self.conn,[{'product_id':self.pid,'quantity':4}],
                             payments=[{'method':'CASH','amount':400}])
        for quantity in (1.9,True,'1.5'):
            lines = [{'product_id':self.pid,'quantity':quantity}]
            with self.subTest(value=quantity),self.assertRaises(sales.InvalidSaleError):
                sales.preview_refund(self.conn,sale['id'],lines)
            with self.subTest(value=quantity),self.assertRaises(sales.InvalidSaleError):
                sales.refund_sale(self.conn,sale['id'],lines,request_id='audit_request_77')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales WHERE status='REFUNDED'").fetchone()[0],0)
        self.assertEqual(catalog.get_product(self.conn,self.pid)['quantity'],21)

    def test_valid_integer_and_string_quantities_remain_supported(self):
        for quantity in (2,'2'):
            r=sales.checkout(self.conn,[{'product_id':self.pid,'quantity':quantity}],
                             payments=[{'method':'CASH','amount':200}])
            self.assertEqual(r['items'][0]['quantity'],2)
        self.assertEqual(catalog.get_product(self.conn,self.pid)['quantity'],21)


if __name__ == '__main__':
    unittest.main()


class AuditOnlineRestoreTests(unittest.TestCase):
    def test_restore_with_open_connection_does_not_split_database(self):
        import tempfile
        from pathlib import Path
        from pos import backups
        from pos.db import connect, init_db
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'shop.db')
            init_db(path)
            active = connect(path)
            active.execute("INSERT INTO settings(key,value) VALUES('audit_mark','before')")
            name = backups.create_backup(path)
            active.execute("UPDATE settings SET value='after' WHERE key='audit_mark'")
            backups.restore_backup(name, live_path=path)
            current = connect(path)
            self.assertEqual(active.execute("SELECT value FROM settings WHERE key='audit_mark'").fetchone()[0], 'before')
            self.assertEqual(current.execute("SELECT value FROM settings WHERE key='audit_mark'").fetchone()[0], 'before')
            active.execute("UPDATE settings SET value='latest' WHERE key='audit_mark'")
            self.assertEqual(current.execute("SELECT value FROM settings WHERE key='audit_mark'").fetchone()[0], 'latest')
            self.assertEqual(current.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            active.close()
            current.close()

class AuditPaymentPrecisionTests(POSTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.mkproduct(quantity=10, price=100, tax_rate=0)

    def test_fractional_paise_rejected(self):
        for tender in ('100.001', '100.009', 'NaN', 'Infinity', '-1'):
            with self.subTest(tender=tender), self.assertRaises(sales.InvalidPaymentError):
                sales.checkout(self.conn, [{'product_id':self.pid,'quantity':1}],
                               payments=[{'method':'CASH','amount':tender}])
        self.assertEqual(catalog.get_product(self.conn,self.pid)['quantity'],10)

    def test_bad_discounts_rejected(self):
        for mode,value in [('percent',101),('percent',-1),('typo',10)]:
            with self.subTest(mode=mode,value=value),self.assertRaises(sales.InvalidSaleError):
                sales.checkout(self.conn,[{'product_id':self.pid,'quantity':1}],
                               payments=[{'method':'CASH','amount':100}],
                               discount_mode=mode,discount_value=value)
