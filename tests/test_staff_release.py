import unittest
from base import POSTestCase
from pos import staff


class StaffPermissionsModelTests(POSTestCase):
    def test_first_operator_must_be_admin_and_pin_hashes(self):
        with self.assertRaises(staff.StaffError):
            staff.add_user(self.conn,'worker1','Cashier','cashier','123456')
        u=staff.add_user(self.conn,'owner1','Owner','admin','123456')
        self.assertTrue(staff.configured(self.conn))
        self.assertNotIn('pin_hash',u)
        self.assertNotEqual(self.conn.execute('SELECT pin_hash FROM staff_users WHERE id=?',(u['id'],)).fetchone()[0],'123456')
        self.assertIsNone(staff.verify_login(self.conn,'owner1','000000'))
        self.assertEqual(staff.verify_login(self.conn,'owner1','123456')['id'],u['id'])

    def test_last_admin_cannot_be_disabled(self):
        first=staff.add_user(self.conn,'owner1','Owner','admin','123456')
        with self.assertRaises(staff.StaffError):
            staff.set_active(self.conn,first['id'],False)
        second=staff.add_user(self.conn,'owner2','Other owner','admin','654321')
        staff.set_active(self.conn,first['id'],False)
        self.assertIsNone(staff.verify_login(self.conn,'owner1','123456'))
        self.assertEqual(staff.verify_login(self.conn,'owner2','654321')['id'],second['id'])
