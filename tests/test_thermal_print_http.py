"""HTTP authorization and spool idempotency. Only runs where Flask is installed."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pos import thermal_print as tp
from pos.db import connect, init_db

if importlib.util.find_spec('flask'):
    from pos.app import create_app
    from pos import staff
else:
    create_app = None


@unittest.skipUnless(create_app is not None, 'Flask unavailable in offline test environment')
class PrintingAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        init_db(self.path)
        conn = connect(self.path)
        try:
            staff.add_user(conn, 'owner', 'Owner', 'admin', '12345678')
            staff.add_user(conn, 'cashier', 'Cashier', 'staff', '87654321')
            self.sale_id = conn.execute('''INSERT INTO sales
                (subtotal,tax_total,total,invoice_number)
                VALUES(100,5,105,'26-27/000001')''').lastrowid
        finally:
            conn.close()
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.login('owner', '12345678')

    def login(self, username, pin):
        result = self.client.post('/staff-login', data={'username':username,'pin':pin})
        self.assertEqual(result.status_code,302,result.get_data(as_text=True))

    def test_print_test_and_current_spool_queue(self):
        with patch.object(tp,'send_image',return_value='TM-T81') as spool, \
             patch.object(tp,'default_printer',return_value='TM-T81'):
            info = self.client.get('/api/printing/info')
            self.assertEqual(info.status_code,200)
            self.assertEqual(info.json['default_printer'],'TM-T81')
            resp = self.client.post('/api/printing/test',json={})
        self.assertEqual(resp.status_code,200,resp.get_data(as_text=True))
        self.assertEqual(resp.json['status'],'spooled')
        self.assertEqual(spool.call_count,1)

    def test_printing_repeated_auto_is_once_and_manual_is_explicit(self):
        uri = f'/api/printing/sales/{self.sale_id}'
        with patch.object(tp,'send_image',return_value='TM-T81') as spool:
            first = self.client.post(uri,json={'manual':False})
            again = self.client.post(uri,json={'manual':False})
            manual = self.client.post(uri,json={'manual':True})
        self.assertEqual(first.status_code,200,first.get_data(as_text=True))
        self.assertEqual(again.json['status'],'already_requested')
        self.assertEqual(manual.json['status'],'spooled')
        self.assertEqual(spool.call_count,2)

    def test_denied_cross_site_post_does_not_print(self):
        with patch.object(tp,'send_image') as spool:
            resp=self.client.post(f'/api/printing/sales/{self.sale_id}',
                                  headers={'Origin':'http://evil.example'},json={})
        self.assertEqual(resp.status_code,403)
        spool.assert_not_called()

    def test_invalid_sale_and_manual_type(self):
        self.assertEqual(self.client.post('/api/printing/sales/999999',json={}).status_code,404)
        self.assertEqual(self.client.post(f'/api/printing/sales/{self.sale_id}',
                                          json={'manual':'true'}).status_code,400)

    def test_regular_staff_cannot_test_print_or_change_printer(self):
        self.login('cashier', '87654321')
        self.assertEqual(self.client.post('/api/printing/test',json={}).status_code,403)
        self.assertEqual(self.client.get('/api/printing/info').status_code,403)
        self.assertEqual(self.client.put('/api/settings',json={'receipt_printer_name':'OTHER'}).status_code,403)


if __name__ == '__main__':
    unittest.main()
