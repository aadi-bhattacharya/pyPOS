"""Service-level checks for daily operations upgrade; safe disposable databases."""
import os
import tempfile
import unittest
from pos.db import connect, init_db
from pos import sales, parked, cash_sessions, staff, settings
from pos.upi import payment_uri, payment_svg

class TillUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'store.db')
        init_db(self.path)
        self.db = connect(self.path)
        self.db.execute("INSERT INTO product_groups(name) VALUES('Test garment')")
        self.db.execute("INSERT INTO products(group_id, SKU, barcode, price, cost_price, tax_rate, quantity) VALUES(1, 'GARMENT', '8900000000001', 200, 100, 5, 20)")
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_upi_reference_and_bank_utr_are_separate(self):
        original = sales.checkout(self.db, [{"product_id":1, "quantity":1}],
            payments=[{"method":"UPI", "amount":"210.00", "reference":"POS9DBF123456", "upi_utr":"432100987654"}])
        self.assertEqual(original['payments'][0]['reference'], 'POS9DBF123456')
        self.assertEqual(original['payments'][0]['upi_utr'], '432100987654')
        self.assertEqual(self.db.execute('SELECT reference, upi_utr FROM payments').fetchone()['upi_utr'], '432100987654')
        with self.assertRaises(sales.InvalidPaymentError):
            sales.checkout(self.db, [{"product_id":1, "quantity":1}],
                payments=[{"method":"UPI", "amount":"210.00", "reference":"POS9DBF123456"}])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0], 19)

    def test_payment_metadata_does_not_change_legacy_receipt_shape(self):
        result = sales.checkout(self.db, [{"product_id":1, "quantity":1}],
            payments=[{"method":"CASH", "amount":"210.00"}])
        self.assertEqual(result['payments'][0], {'method':'CASH','amount':210.0,'is_partial':False})

    def test_parking_does_not_need_checkout_id(self):
        obj = parked.park_cart(self.db,[{'product_id':1,'quantity':2}],
            label='Customer waiting', salesperson='Anita')
        self.assertEqual(obj['salesperson'], 'Anita')
        self.assertEqual(parked.get_parked(self.db,obj['id'])['items'][0]['quantity'], 2)

    def test_upi_qr_payload(self):
        uri = payment_uri('test@upi','Shop & Sons','100.50','POS12345678')
        self.assertIn('pa=test%40upi', uri)
        self.assertIn('am=100.50', uri)
        self.assertIn('tn=POS12345678', uri)
        self.assertIn('tr=POS12345678', uri)
        self.assertIn('pn=Shop+%26+Sons', uri)
        self.assertIn(b'<svg', payment_svg('test@upi','Shop',100,'POS12345678'))
        for amt in ['NaN', '-1', '0', '100.001']:
            with self.assertRaises(ValueError): payment_uri('test@upi','Shop',amt,'POS12345678')
        with self.assertRaises(ValueError): payment_uri('wrong','Shop','10','POS12345678')

    def test_settings_permissions_flags(self):
        prefs = settings.get_settings(self.db)
        self.assertEqual(prefs['cashier_allow_stock_reduction'],'false')
        self.assertEqual(prefs['cashier_allow_stock_import'],'true')
        prefs = settings.save_settings(self.db,{'cashier_allow_stock_reduction':'true'})
        self.assertEqual(prefs['cashier_allow_stock_reduction'],'true')
        with self.assertRaises(ValueError): settings.save_settings(self.db,{'cashier_allow_stock_reduction':'yes'})

    def test_legacy_payment_upgrade_preserves_history(self):
        # Migration idempotency and data persistence over repeated initialization.
        before = self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0]
        init_db(self.path)
        init_db(self.path)
        after = self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0]
        self.assertEqual(before, after)
        cols = {r['name'] for r in self.db.execute('PRAGMA table_info(payments)')}
        self.assertTrue({'reference','upi_utr'} <= cols)

if __name__=='__main__': unittest.main()

class TillDayGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "daygate.db")
        init_db(self.path)
        self.db = connect(self.path)
        self.db.execute("INSERT INTO product_groups(name) VALUES('Test garment')")
        self.db.execute("INSERT INTO products(group_id,SKU,price,tax_rate,quantity) VALUES (1,'ONLY',100,0,10)")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_checkout_and_void_require_open_day_inside_transaction(self):
        sale_args = dict(items=[{'product_id':1,'quantity':1}],
                         payments=[{'method':'CASH','amount':'100.00'}],
                         require_open_session=True)
        with self.assertRaises(sales.InvalidSaleError):
            sales.checkout(self.db,**sale_args)
        self.assertEqual(self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0],10)
        cash_sessions.open_session(self.db, 0)
        original = sales.checkout(self.db,**sale_args)
        self.assertEqual(self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0],9)
        cash_sessions.close_session(self.db, counted_cash=100)
        with self.assertRaises(sales.InvalidSaleError):
            sales.void_sale(self.db,original['id'],require_open_session=True)
        with self.assertRaises(sales.InvalidSaleError):
            sales.refund_sale(self.db,original['id'],require_open_session=True)
        self.assertEqual(self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0],9)

    def test_refund_requires_day_and_retains_transactional_stock_updates(self):
        cash_sessions.open_session(self.db,0)
        receipt=sales.checkout(self.db,[{'product_id':1,'quantity':2}],
                               payments=[{'method':'CASH','amount':200}],require_open_session=True)
        cash_sessions.close_session(self.db, counted_cash=200)
        with self.assertRaises(sales.InvalidSaleError):
            sales.refund_sale(self.db,receipt['id'],lines=[{'product_id':1,'quantity':1}],require_open_session=True)
        cash_sessions.open_session(self.db,0)
        result=sales.refund_sale(self.db,receipt['id'],lines=[{'product_id':1,'quantity':1}],require_open_session=True)
        self.assertTrue(result)
        self.assertEqual(self.db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0],9)
