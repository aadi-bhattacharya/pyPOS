import os
import shutil
import sqlite3
import tempfile
import unittest

from pos.app import create_app
from pos import auth
from pos.db import connect, init_db
from pos import settings as settings_mod


class AuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="posauth-")
        cls.db_path = os.path.join(cls.tmpdir, "auth-test.db")
        init_db(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def client(self):
        return self.app.test_client()

    def set_stored_password(self, password):
        """Write the hash straight into the DB (tests are on-machine)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('admin_password', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (auth.hash_password(password) if password else "",))
        conn.commit()
        conn.close()

    def setUp(self):
        # every test starts from a known state: no password, no session
        self.set_stored_password("")
        self.client().get("/logout")

    def test_settings_open_when_no_password_configured(self):
        c = self.client()
        r = c.put("/api/settings", json={"store_name": "Open Store"})
        self.assertEqual(r.status_code, 200)
        a = c.get("/api/settings/auth").get_json()
        self.assertFalse(a["configured"])
        self.assertFalse(a["authenticated"])

    def test_bootstrap_set_password(self):
        c = self.client()
        r = c.post("/api/settings/password",
                   json={"current_password": "", "new_password": "hunter22"})
        self.assertEqual(r.status_code, 200)
        a = c.get("/api/settings/auth").get_json()
        self.assertTrue(a["configured"])
        self.assertTrue(a["configured"] and a["authenticated"])

    def test_password_never_stored_plaintext(self):
        self.set_stored_password("hunter22")
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT value FROM settings WHERE key='admin_password'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertTrue(row[0].startswith("pbkdf2_sha256$"))
        self.assertNotIn("hunter22", row[0])

    def test_locked_endpoints_return_401_when_logged_out(self):
        self.set_stored_password("hunter22")
        c = self.client()
        c.get("/logout")
        for method, path, kwargs in [
            ("put", "/api/settings", {"json": {"store_name": "X"}}),
            ("post", "/api/backups", {}),
            ("get", "/api/backups", {}),
            ("get", "/api/backups/status", {}),
            ("post", "/api/demo-data", {}),
        ]:
            with self.subTest(endpoint=path):
                r = getattr(c, method)(path, **kwargs)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.get_json().get("code"), "auth_required")

    def test_login_wrong_then_right(self):
        self.set_stored_password("hunter22")
        c = self.client()
        c.get("/logout")
        r = c.post("/login", data={"password": "nope", "next": "#/settings"})
        self.assertEqual(r.status_code, 200)          # re-rendered page
        self.assertIn(b"Wrong password", r.data)
        self.assertNotIn(b"settings_auth", r.data)

        r = c.post("/login", data={"password": "hunter22", "next": "#/settings"})
        self.assertEqual(r.status_code, 302)          # redirect on success
        self.assertIn("#/settings", r.headers["Location"])

        r = c.put("/api/settings", json={"store_name": "Locked Store"})
        self.assertEqual(r.status_code, 200)

    def test_change_password_requires_current(self):
        self.set_stored_password("hunter22")
        c = self.client()
        c.post("/login", data={"password": "hunter22"})
        r = c.post("/api/settings/password",
                   json={"current_password": "wrong", "new_password": "newpass1"})
        self.assertEqual(r.status_code, 400)

        r = c.post("/api/settings/password",
                   json={"current_password": "hunter22", "new_password": "newpass1"})
        self.assertEqual(r.status_code, 200)
        # old password no longer works, new one does
        c.get("/logout")
        self.assertEqual(c.post("/login",
                                data={"password": "hunter22"}).status_code, 200)
        self.assertIn(b"Wrong password", c.post(
            "/login", data={"password": "hunter22"}).data)
        r = c.post("/login", data={"password": "newpass1"})
        self.assertEqual(r.status_code, 302)

    def test_too_short_password_rejected(self):
        c = self.client()
        c.post("/login", data={"password": "newpass1"})
        r = c.post("/api/settings/password",
                   json={"current_password": "newpass1", "new_password": "ab"})
        self.assertEqual(r.status_code, 400)

    def test_open_redirect_blocked(self):
        self.set_stored_password("hunter22")
        c = self.client()
        r = c.post("/login", data={"password": "hunter22",
                                   "next": "https://evil.example"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil.example", r.headers["Location"])

    def test_login_page_when_not_configured(self):
        # fresh install: login page explains settings are open
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "fresh.db")
            init_db(p)
            app = create_app(p)
            app.config["TESTING"] = True
            r = app.test_client().get("/login")
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
        import io
        import builtins
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "cli.db")
            init_db(p)
            answers = iter(["pw12345", "pw12345"])
            real_getpass = __import__("getpass").getpass
            fake = lambda _prompt="": next(answers)
            builtins_getpass = __import__("getpass")
            builtins_getpass.getpass = fake
            try:
                from pos.cli import _set_password
                _set_password(p)
            finally:
                builtins_getpass.getpass = real_getpass
            conn = connect(p)
            stored = settings_mod.get_all_settings(conn)["admin_password"]
            conn.close()
            self.assertTrue(auth.verify_password("pw12345", stored))


if __name__ == "__main__":
    unittest.main()
