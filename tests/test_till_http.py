"""Flask HTTP integration checks. Requires: pip install -e .

These are intended for a machine with Flask installed; they cannot be
executed in environments lacking Flask. Every test uses a disposable DB.
"""
import os
import tempfile
import unittest

from pos.app import create_app
from pos.db import connect, init_db
from pos import staff, settings, cash_sessions


class TillHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "http-test.db")
        init_db(self.path)
        db = connect(self.path)
        try:
            db.execute("INSERT INTO product_groups(name) VALUES('Test garment')")
            db.execute("INSERT INTO products(group_id,SKU,price,cost_price,tax_rate,quantity) "
                       "VALUES(1,'TEST-SKU',100,30,0,5)")
        finally:
            db.close()
        self.app = create_app(db_path=self.path)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _bootstrap(self):
        res = self.client.post('/staff-setup', data={
            'username': 'owner', 'display_name': 'Shop owner',
            'pin': '12345678', 'admin_password': 'long-safe-test-password',
        }, follow_redirects=False)
        self.assertEqual(res.status_code, 302, res.data[:500])

    def _cashier(self):
        self._bootstrap()
        db = connect(self.path)
        try:
            staff.add_user(db, 'cashier01', 'Sales associate', 'cashier', '87654321')
        finally:
            db.close()
        self.client.get('/logout')
        res = self.client.post('/staff-login', data={
            'username': 'cashier01', 'pin': '87654321',
        })
        self.assertEqual(res.status_code, 302)

    def test_bootstrap_mandatory_and_relogin_after_restart(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertIn('/staff-setup', self.client.get('/').headers['Location'])
        self.assertEqual(self.client.post('/api/sales', json={}).status_code, 403)
        self._bootstrap()
        self.assertEqual(self.client.get('/').status_code, 200)
        # Fresh process nonce invalidates the previous authenticated cookie.
        new_app = create_app(db_path=self.path)
        new_app.config.update(TESTING=True)
        old_cookie = self.client.get_cookie('session')
        replacement = new_app.test_client()
        replacement.set_cookie('session', old_cookie.value)
        self.assertIn('/staff-login', replacement.get('/').headers['Location'])

    def test_closed_day_blocks_sales_even_when_staff_logged_in(self):
        self._cashier()
        payload = {'items': [{'product_id': 1, 'quantity': 1}],
                   'payments': [{'method': 'CASH', 'amount': 100}]}
        res = self.client.post('/api/sales', json=payload)
        self.assertEqual(res.status_code, 409)
        db = connect(self.path)
        try:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sales').fetchone()[0], 0)
            cash_sessions.open_session(db, 0)
        finally:
            db.close()
        res = self.client.post('/api/sales', json=payload)
        self.assertEqual(res.status_code, 201, res.json)
        sid = res.json['id']
        self.assertEqual(self.client.post(f'/api/sales/{sid}/refund',
                                         json={'lines': [{'product_id': 1,'quantity': 1}]}
                                         ).status_code, 403)  # Cashier may not refund.

    def test_cashier_stock_permissions_and_admin_toggle(self):
        self._cashier()
        url = '/api/products/1/stock'
        self.assertEqual(self.client.post(url, json={'delta': -1, 'reason': 'stock correction'}).status_code, 403)
        self.assertEqual(self.client.post(url, json={'delta': 2, 'reason': 'new delivery'}).status_code, 200)
        db = connect(self.path)
        try:
            self.assertEqual(db.execute('SELECT quantity FROM products WHERE id=1').fetchone()[0], 7)
            # A global legacy setting must NOT silently grant a sensitive
            # permission to every staff member.
            settings.save_settings(db, {'cashier_allow_stock_reduction': 'true'})
        finally:
            db.close()
        self.assertEqual(self.client.post(url, json={'delta': -1, 'reason': 'approved correction'}).status_code, 403)
        db = connect(self.path)
        try:
            operator = db.execute("SELECT id FROM staff_users WHERE username='cashier1'").fetchone()
            if operator is None:
                operator = db.execute("SELECT id FROM staff_users WHERE role='cashier' LIMIT 1").fetchone()
            user = staff.get_user(db, operator[0])
            grant = dict(user['permissions'], reduce_stock=True)
            staff.set_permissions(db, operator[0], grant)
        finally:
            db.close()
        self.assertEqual(self.client.post(url, json={'delta': -1, 'reason': 'approved correction'}).status_code, 200)
        self.assertEqual(self.client.post('/api/stocktakes', json={}).status_code, 403)

    def test_origin_alias_accepted_but_cross_origin_rejected(self):
        self._cashier()
        api = '/api/parked'
        body = {'items': [{'product_id': 1, 'quantity': 1}], 'label': 'Hold'}
        safe = self.client.post(api, json=body,
            headers={'Origin': 'http://127.0.0.1'}, base_url='http://localhost')
        self.assertIn(safe.status_code, (200, 201), safe.get_data(as_text=True))
        bad = self.client.post(api, json=body, headers={'Origin':'https://evil.example'},
                               base_url='http://localhost')
        self.assertEqual(bad.status_code, 403)


if __name__ == '__main__':
    unittest.main()

class OpaqueOriginLoginTests(unittest.TestCase):
    """HTTP regression: Windows/embedded browsers can send Origin:null.

    The session CSRF proof is required; we never trust null itself.
    """

    def setUp(self):
        import re
        self._re = re
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "opaque-origin.db")
        init_db(self.path)
        self.app = create_app(db_path=self.path)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _token(self, route):
        page = self.client.get(route)
        self.assertEqual(page.status_code, 200)
        html = page.data.decode()
        field = (r'name="_csrf" value="([A-Za-z0-9_-]+)"' if route != "/"
                 else r'name="pypos-csrf-token" content="([A-Za-z0-9_-]+)"')
        match = self._re.search(field, html)
        self.assertIsNotNone(match, html[:800])
        return match.group(1)

    def test_staff_setup_null_origin_requires_rendered_token(self):
        data = {'username': 'owner', 'display_name': 'Shop owner',
                'pin': '12345678', 'admin_password': 'long-safe-test-password'}
        self.assertEqual(self.client.post('/staff-setup', data=data,
            headers={'Origin': 'null'}).status_code, 403)
        token = self._token('/staff-setup')
        self.assertEqual(self.client.post('/staff-setup', data={**data, '_csrf': token},
            headers={'Origin': 'null'}).status_code, 302)

    def test_operator_login_and_json_writes_with_null_origin(self):
        setup = {'username': 'owner', 'display_name': 'Shop owner',
                 'pin': '12345678', 'admin_password': 'long-safe-test-password'}
        self.client.post('/staff-setup', data=setup)
        self.client.get('/staff-logout')
        token = self._token('/staff-login')
        login = self.client.post('/staff-login', data={
            'username': 'owner', 'pin': '12345678', '_csrf': token},
            headers={'Origin': 'null'})
        self.assertEqual(login.status_code, 302)
        api_token = self._token('/')
        self.assertEqual(self.client.post('/api/cash-sessions/open',
            json={'opening_float': 0}, headers={'Origin': 'null'}).status_code, 403)
        allowed = self.client.post('/api/cash-sessions/open',
            json={'opening_float': 0}, headers={
                'Origin': 'null', 'X-pyPOS-CSRF': api_token})
        self.assertEqual(allowed.status_code, 201, allowed.data[:500])
        # Even a valid token never overrides an explicitly foreign origin.
        rejected = self.client.post('/api/cash-sessions/close',
            json={'counted_cash': 0}, headers={
                'Origin': 'http://evil.example', 'X-pyPOS-CSRF': api_token})
        self.assertEqual(rejected.status_code, 403)

    def test_stale_token_from_previous_session_is_rejected(self):
        self.client.post('/staff-setup', data={'username': 'owner',
            'display_name': 'Shop owner', 'pin': '12345678',
            'admin_password': 'long-safe-test-password'})
        old_token = self._token('/')
        self.client.get('/staff-logout')
        # Explicitly clear the cookie/session: token must not be reusable.
        with self.client.session_transaction() as sess:
            sess.clear()
        result = self.client.post('/staff-login', data={'username': 'owner',
            'pin': '12345678', '_csrf': old_token}, headers={'Origin': 'null'})
        self.assertEqual(result.status_code, 403)
