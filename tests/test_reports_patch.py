"""Regression checks for revised Indian reporting, search and refund accounting."""
import unittest
from datetime import datetime, timedelta, timezone

from base import POSTestCase
from pos import reports, sales
from pos.india import IST
from pos import settings as settings_mod


class ReportingPatchTests(POSTestCase):
    def _checkout(self, pid, qty=1, customer=None, discount=None):
        p = self.conn.execute('SELECT price, tax_rate FROM products WHERE id=?', (pid,)).fetchone()
        amount = p['price'] * qty * (1 + p['tax_rate'] / 100)
        return sales.checkout(self.conn, [{'product_id': pid, 'quantity': qty}],
                              payments=[{'method': 'CASH', 'amount': amount + 100}],
                              customer_id=customer,
                              discount_mode='amount' if discount else None,
                              discount_value=discount)

    def test_completed_partial_refund_void_and_payment_mix(self):
        pid = self.mkproduct(price=100, quantity=10, tax_rate=0)
        sold = self._checkout(pid, qty=3)
        returned = sales.refund_sale(self.conn, sold['id'], lines=[{'product_id': pid, 'quantity': 1}])
        other = self._checkout(pid, qty=1)
        sales.void_sale(self.conn, other['id'])
        rep = reports.summary(self.conn, days=7)
        self.assertEqual(rep['period']['gross_sales'], 300)
        self.assertEqual(rep['period']['refund_total'], 100)
        self.assertEqual(rep['period']['net_revenue'], 200)
        self.assertEqual(rep['period']['transactions'], 1)
        self.assertEqual(rep['period']['refund_count'], 1)
        self.assertEqual(rep['period']['void_count'], 1)
        self.assertEqual(rep['period']['net_items'], 2)
        self.assertEqual(rep['top_products'][0]['qty'], 2)
        self.assertEqual(rep['top_products'][0]['revenue'], 200)
        self.assertEqual(rep['payment_mix'][0]['amount'], 200)
        self.assertEqual(sum(d['revenue'] for d in rep['series']), 200)
        self.assertEqual(len(rep['series']), 7)
        self.assertEqual(returned['status'], 'REFUNDED')

    def test_gst_inclusive_net_product_value_and_refund(self):
        settings_mod.save_settings(self.conn, {'price_tax_mode': 'inclusive'})
        pid = self.mkproduct(price=118, tax_rate=18, quantity=5)
        r = sales.checkout(self.conn, [{'product_id': pid, 'quantity': 2}],
                           payments=[{'method': 'UPI', 'amount': 236}])
        sales.refund_sale(self.conn, r['id'], lines=[{'product_id': pid, 'quantity': 1}])
        rep = reports.summary(self.conn, 7)
        self.assertEqual(rep['period']['net_revenue'], 118)
        self.assertEqual(rep['period']['net_tax'], 18)
        self.assertEqual(rep['top_products'][0]['revenue'], 118)
        self.assertEqual(rep['top_products'][0]['qty'], 1)

    def test_refund_dated_separately_from_original_sale(self):
        pid = self.mkproduct(price=50, tax_rate=0, quantity=6)
        sold = self._checkout(pid)
        old = (datetime.now(timezone.utc) - timedelta(days=20)).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute('UPDATE sales SET timestamp=? WHERE id=?', (old, sold['id']))
        self.conn.commit()
        sales.refund_sale(self.conn, sold['id'])
        rep = reports.summary(self.conn, 7)
        self.assertEqual(rep['period']['gross_sales'], 0)
        self.assertEqual(rep['period']['refund_total'], 50)
        self.assertEqual(rep['period']['net_revenue'], -50)
        self.assertEqual(sum(x['revenue'] for x in rep['series']), -50)

    def test_search_matches_invoice_customer_phone_tender_and_id(self):
        from pos.customers import create_customer
        cid = create_customer(self.conn, 'Rani Das', phone='9876543210')['id']
        pid = self.mkproduct(price=80, tax_rate=0)
        r = sales.checkout(self.conn, [{'product_id': pid, 'quantity': 1}],
                           payments=[{'method': 'UPI', 'amount': 80}], customer_id=cid)
        for term in ('Rani', '9876543210', 'UPI', str(r['id']), r['invoice_number']):
            hits = sales.list_sales(self.conn, search=term)
            self.assertTrue(any(hit['id'] == r['id'] for hit in hits), term)
        self.assertFalse(sales.list_sales(self.conn, search='not-a-customer'))

    def test_customer_points_and_credit_filters(self):
        from pos.customers import create_customer, list_customers
        one = create_customer(self.conn, 'Asha')
        two = create_customer(self.conn, 'Bina')
        self.conn.execute('UPDATE customers SET loyalty_points=25 WHERE id=?', (one['id'],))
        self.conn.execute('UPDATE customers SET store_credit_cents=4750 WHERE id=?', (two['id'],))
        self.conn.commit()
        self.assertEqual([r['name'] for r in list_customers(self.conn, balance_filter='points')], ['Asha'])
        self.assertEqual([r['name'] for r in list_customers(self.conn, balance_filter='credit')], ['Bina'])
        self.assertEqual(len(list_customers(self.conn)), 2)

    def test_series_date_ist_bounds_and_days_clamp(self):
        self.assertEqual(len(reports.summary(self.conn, days=0)['series']), 1)
        self.assertEqual(len(reports.summary(self.conn, days=370)['series']), 365)
        now = datetime.now(IST).date()
        lo, hi = reports._period_bounds(1, today=now)
        self.assertLess(lo, hi)
        self.assertEqual(reports._local_day(hi), now.isoformat())


if __name__ == '__main__':
    unittest.main()
