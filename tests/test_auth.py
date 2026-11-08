"""End-to-end Settings password tests with the mandatory staff sign-in.

Staff authentication and the Settings password are separate security layers.
All HTTP tests use a disposable database and real Flask request handlers.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from pos.app import create_app
from pos import auth, staff
from pos.db import connect, init_db
from pos import settings as settings_mod


class AuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="posauth-")
        cls.db_path = os.path.join(cls.tmpdir, "auth-test.db")
        init_db(cls.db_path)
        db = connect(cls.db_path)
        try:
            staff.add_user(db, "owner1", "Owner", "admin", "12345678")
        finally:
            db.close()
        cls.app = create_app(cls.db_path)
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def client(self):
        return self.app.test_client()

    def admin_client(self):
        c = self.client()
        r = c.post("/staff-login", data={"username": "owner1", "pin": "12345678"})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True))
        self.assertEqual(c.get("/api/staff/status").json["operator"]["role"], "admin")
        return c

    def set_stored_password(self, password):
        """Write the hash straight into the disposable test database."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('admin_password', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (auth.hash_password(password) if password else "",))
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        # Every test starts with staff configured, no Settings password,
        # and a new browser session. Only admin_client() signs staff in.
        self.set_stored_password("")

    def test_settings_open_for_signed_in_admin_when_no_password_configured(self):
        c = self.admin_client()
        r = c.put("/api/settings", json={"store_name": "Open Store"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(c.get("/api/settings").json["store_name"], "Open Store")
        a = c.get("/api/settings/auth").get_json()
        self.assertFalse(a["configured"])
        self.assertFalse(a["authenticated"])

    def test_bootstrap_set_password(self):
        c = self.admin_client()
        r = c.post("/api/settings/password",
                   json={"current_password": "", "new_password": "hunter22"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        a = c.get("/api/settings/auth").get_json()
        self.assertTrue(a["configured"])
        self.assertTrue(a["authenticated"])

    def test_password_never_stored_plaintext(self):
        self.set_stored_password("hunter22")
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='admin_password'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith("pbkdf2_sha256$"))
        self.assertNotIn("hunter22", row[0])

    def test_locked_endpoints_return_401_without_settings_password(self):
        self.set_stored_password("hunter22")
        c = self.admin_client()
        for method, path, kwargs in [
            ("put", "/api/settings", {"json": {"store_name": "X"}}),
            ("post", "/api/backups", {}),
            ("get", "/api/backups", {}),
            ("get", "/api/backups/status", {}),
            ("post", "/api/demo-data", {}),
        ]:
            with self.subTest(method=method, endpoint=path):
                r = getattr(c, method)(path, **kwargs)
                self.assertEqual(r.status_code, 401, r.get_data(as_text=True))
                self.assertEqual(r.get_json().get("code"), "auth_required")

    def test_no_staff_session_cannot_access_business_apis_even_without_admin_password(self):
        c = self.client()
        for method, path, kwargs in [
            ("get", "/api/settings", {}),
            ("put", "/api/settings", {"json": {"store_name": "Unauthorized"}}),
            ("post", "/api/settings/password", {"json": {"new_password": "attack"}}),
            ("post", "/api/groups", {"json": {"name": "Unauthorized"}}),
            ("get", "/api/backups", {}),
        ]:
            with self.subTest(method=method, endpoint=path):
                r = getattr(c, method)(path, **kwargs)
                self.assertEqual(r.status_code, 401, r.get_data(as_text=True))
                self.assertEqual(r.get_json().get("code"), "staff_login_required")

    def test_login_wrong_then_right(self):
        self.set_stored_password("hunter22")
        c = self.admin_client()
        r = c.post("/login", data={"password": "nope", "next": "#/settings"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Wrong password", r.data)
        self.assertFalse(c.get("/api/settings/auth").json["authenticated"])

        r = c.post("/login", data={"password": "hunter22", "next": "#/settings"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/#/settings", r.headers["Location"])
        r = c.put("/api/settings", json={"store_name": "Locked Store"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_change_password_requires_current(self):
        self.set_stored_password("hunter22")
        c = self.admin_client()
        self.assertEqual(c.post("/login", data={"password": "hunter22"}).status_code, 302)
        r = c.post("/api/settings/password",
                   json={"current_password": "wrong", "new_password": "newpass1"})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        r = c.post("/api/settings/password",
                   json={"current_password": "hunter22", "new_password": "newpass1"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        c.get("/logout")
        # /logout removes both the Settings and the staff session.
        self.assertEqual(c.get("/api/settings").status_code, 401)
        self.assertEqual(c.post("/staff-login", data={"username": "owner1", "pin": "12345678"}).status_code, 302)
        r = c.post("/login", data={"password": "hunter22"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Wrong password", r.data)
        r = c.post("/login", data={"password": "newpass1"})
        self.assertEqual(r.status_code, 302)

    def test_too_short_password_rejected(self):
        c = self.admin_client()
        r = c.post("/api/settings/password",
                   json={"current_password": "", "new_password": "ab"})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertFalse(c.get("/api/settings/auth").json["configured"])
        self.set_stored_password("hunter22")
        self.assertEqual(c.post("/login", data={"password": "hunter22"}).status_code, 302)
        r = c.post("/api/settings/password",
                   json={"current_password": "hunter22", "new_password": "ab"})
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        db = connect(self.db_path)
        try:
            self.assertTrue(auth.verify_password(
                "hunter22", settings_mod.get_all_settings(db)["admin_password"]))
        finally:
            db.close()

    def test_open_redirect_blocked(self):
        self.set_stored_password("hunter22")
        c = self.admin_client()
        r = c.post("/login", data={"password": "hunter22",
                                   "next": "https://evil.example"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example", r.headers["Location"])

    def test_login_page_when_not_configured(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "fresh.db")
            init_db(p)
            app = create_app(p)
            app.config["TESTING"] = True
            c = app.test_client()
            self.assertEqual(c.get("/").status_code, 302)
            self.assertIn("/staff-setup", c.get("/").headers["Location"])
            self.assertEqual(c.get("/api/settings").status_code, 403)
            r = c.get("/login")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"No admin password is set", r.data)


class HashingTests(unittest.TestCase):
    def test_hash_verify_roundtrip(self):
        h = auth.hash_password("s3cret!")
        self.assertTrue(auth.verify_password("s3cret!", h))
        self.assertFalse(auth.verify_password("wrong", h))
        self.assertFalse(auth.verify_password("s3cret!", "garbage"))
        self.assertFalse(auth.verify_password("s3cret!", ""))

    def test_salts_are_unique(self):
        self.assertNotEqual(auth.hash_password("x"), auth.hash_password("x"))

    def test_cli_set_password_stores_hash(self):
        import getpass
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cli.db")
            init_db(p)
            answers = iter(["pw12345", "pw12345"])
            real_getpass = getpass.getpass
            getpass.getpass = lambda _prompt="": next(answers)
            try:
                from pos.cli import _set_password
                _set_password(p)
            finally:
                getpass.getpass = real_getpass
            conn = connect(p)
            try:
                stored = settings_mod.get_all_settings(conn)["admin_password"]
            finally:
                conn.close()
            self.assertTrue(auth.verify_password("pw12345", stored))


if __name__ == "__main__":
    unittest.main()
