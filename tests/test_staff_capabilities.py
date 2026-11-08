"""Authorisation regression tests for unified staff permissions.

Uses disposable SQLite databases; never reads production records.
"""
import os
import tempfile
import unittest

from pos import auth, staff, settings
from pos.app import create_app
from pos.db import init_db, connect


class StaffCapabilitiesHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'test.db')
        init_db(self.path)
        db = connect(self.path)
        try:
            settings.save_settings(db, {'admin_password': auth.hash_password('owner-password-test')})
            self.owner = staff.add_user(db, 'owner01', 'Shop owner', 'admin', '111111')
            self.staff = staff.add_user(db, 'counter01', 'Counter operator', 'staff', '222222')
            self.other = staff.add_user(db, 'counter02', 'Second operator', 'staff', '333333')
            db.execute("INSERT INTO product_groups(name) VALUES('Test')")
            db.execute("INSERT INTO products(group_id,SKU,price,cost_price,tax_rate,quantity) VALUES(1,'SKU-1',100,50,0,20)")
        finally:
            db.close()
        self.app = create_app(db_path=self.path)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def signin(self, username, pin):
        self.client.get('/staff-logout')
        self.assertEqual(self.client.post('/staff-login', data={'username': username, 'pin': pin}).status_code, 302)

    def owner_unlock(self):
        self.signin('owner01', '111111')
        self.assertEqual(self.client.post('/login', data={'password': 'owner-password-test'}).status_code, 302)

    def grant(self, user_id, **changes):
        self.owner_unlock()
        db = connect(self.path)
        try:
            payload = dict(staff.get_user(db, user_id)['permissions'], **changes)
        finally:
            db.close()
        return self.client.put(f'/api/staff/users/{user_id}/permissions', json={'permissions': payload})

    def test_unified_role_and_default_cash_session_permissions(self):
        self.signin('counter01', '222222')
        user = self.client.get('/api/staff/status').json['operator']
        self.assertEqual(user['role'], 'staff')
        self.assertTrue(user['permissions']['open_day'])
        self.assertTrue(user['permissions']['close_day'])
        self.assertFalse(user['permissions']['reduce_stock'])
        self.assertEqual(self.client.post('/api/cash-sessions/open', json={'opening_float': 200}).status_code, 201)
        self.assertEqual(self.client.post('/api/cash-sessions/close', json={'counted_cash': 200}).status_code, 200)
        self.assertEqual(self.client.get('/api/staff/users').status_code, 403)

    def test_owner_password_required_for_permission_changes(self):
        self.signin('owner01', '111111')
        payload = dict(self.staff['permissions'], reduce_stock=True)
        self.assertEqual(self.client.put(f'/api/staff/users/{self.staff["id"]}/permissions',
            json={'permissions': payload}).status_code, 401)
        self.assertEqual(self.client.post('/login', data={'password': 'owner-password-test'}).status_code, 302)
        self.assertEqual(self.client.put(f'/api/staff/users/{self.staff["id"]}/permissions',
            json={'permissions': payload}).status_code, 200)
        self.signin('counter01', '222222')
        self.assertTrue(self.client.get('/api/staff/status').json['operator']['permissions']['reduce_stock'])

    def test_one_operator_grant_does_not_grant_other_operator(self):
        self.assertEqual(self.grant(self.staff['id'], reduce_stock=True).status_code, 200)
        self.signin('counter01', '222222')
        self.assertEqual(self.client.post('/api/products/1/stock',
            json={'delta': -1, 'reason': 'Damaged item'}).status_code, 200)
        self.signin('counter02', '333333')
        self.assertEqual(self.client.post('/api/products/1/stock',
            json={'delta': -1, 'reason': 'Damaged item'}).status_code, 403)

    def test_revocation_applies_to_existing_signed_in_session(self):
        self.signin('counter01', '222222')
        self.assertEqual(self.client.post('/api/cash-sessions/open', json={'opening_float': 0}).status_code, 201)
        db = connect(self.path)
        try:
            staff.set_permissions(db, self.staff['id'], dict(self.staff['permissions'], checkout=False,
                close_day=False, reduce_stock=False))
        finally:
            db.close()
        self.assertEqual(self.client.post('/api/cash-sessions/close', json={'counted_cash': 0}).status_code, 403)
        self.assertEqual(self.client.post('/api/sales', json={}).status_code, 403)

    def test_incomplete_unknown_and_nonboolean_payloads_fail_atomically(self):
        self.owner_unlock()
        endpoint = f'/api/staff/users/{self.staff["id"]}/permissions'
        for payload in ({'reduce_stock': True},
                        dict(self.staff['permissions'], root=True),
                        dict(self.staff['permissions'], reduce_stock='true')):
            self.assertEqual(self.client.put(endpoint, json={'permissions': payload}).status_code, 400)
        db = connect(self.path)
        try:
            self.assertFalse(staff.get_user(db, self.staff['id'])['permissions']['reduce_stock'])
        finally:
            db.close()

    def test_granted_everything_still_cannot_edit_owner_or_backups(self):
        self.assertEqual(self.grant(self.staff['id'], **{p: True for p in staff.PERMISSIONS}).status_code, 200)
        self.signin('counter01', '222222')
        self.assertEqual(self.client.post('/api/staff/users', json={'username':'baduser', 'role':'admin', 'pin':'444444'}).status_code, 403)
        self.assertEqual(self.client.put('/api/settings', json={'store_name': 'Unauthorized'}).status_code, 403)
        self.assertEqual(self.client.get('/api/backups').status_code, 403)
        self.assertEqual(self.client.put(f'/api/staff/users/{self.owner["id"]}/permissions',
            json={'permissions': self.staff['permissions']}).status_code, 403)

    def test_owner_permissions_not_editable(self):
        self.owner_unlock()
        self.assertEqual(self.client.put(f'/api/staff/users/{self.owner["id"]}/permissions',
            json={'permissions': dict(self.staff['permissions'])}).status_code, 400)

    def test_export_and_cost_are_independent_permissions(self):
        self.signin('counter01', '222222')
        self.assertEqual(self.client.get('/api/export/inventory.csv').status_code, 403)
        self.assertIsNone(self.client.get('/api/products/1').json['cost_price'])
        self.assertEqual(self.grant(self.staff['id'], view_cost=True).status_code, 200)
        self.signin('counter01', '222222')
        self.assertEqual(self.client.get('/api/products/1').json['cost_price'], 50)
        self.assertEqual(self.client.get('/api/export/inventory.csv').status_code, 403)

    def test_foreign_origin_rejected_even_if_permission_granted(self):
        self.signin('counter01', '222222')
        self.assertEqual(self.client.post('/api/cash-sessions/open', json={'opening_float': 0},
            headers={'Origin': 'https://evil.example'}).status_code, 403)

    def test_legacy_manager_and_cashier_permissions_migrate_without_loss(self):
        db = connect(self.path)
        try:
            manager = staff.add_user(db, 'supervisor', 'Old supervisor', 'manager', '444444')
            row = db.execute('SELECT role FROM staff_users WHERE id=?', (manager['id'],)).fetchone()
            self.assertEqual(row['role'], 'manager')
            # Historic rows without explicit custom grants use the matching preset.
            db.execute('DELETE FROM staff_permissions WHERE staff_id IN (?,?)',
                (self.staff['id'], manager['id']))
            old_manager = staff.get_user(db, manager['id'])
            old_cashier = staff.get_user(db, self.staff['id'])
            self.assertEqual(old_manager['role'], 'staff')
            self.assertTrue(old_manager['permissions']['refund_sales'])
            self.assertTrue(old_manager['permissions']['reduce_stock'])
            self.assertFalse(old_cashier['permissions']['reduce_stock'])
            self.assertTrue(old_cashier['permissions']['open_day'])
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
