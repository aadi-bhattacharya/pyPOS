import os
import shutil
import tempfile
import unittest

from pos.app import create_app
from pos.db import connect, init_db
from pos import settings as settings_mod


class SecurityIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="posec-")
        cls.db_path = os.path.join(cls.tmpdir, "security-test.db")
        init_db(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def get(self, path, host="127.0.0.1", **kw):
        return self.client.get(path, headers={"Host": host}, **kw)

    def post(self, path, host="127.0.0.1", origin=None, **kw):
        headers = {"Host": host}
        if origin:
            headers["Origin"] = origin
        return self.client.post(path, headers=headers, **kw)

    # ---------- Host allow-list (DNS rebinding shield) ----------
    def test_loopback_hosts_allowed(self):
        for host in ("127.0.0.1:8420", "localhost:8420", "127.0.0.1"):
            resp = self.get("/api/settings", host=host)
            self.assertEqual(resp.status_code, 200, f"host {host} rejected")

    def test_foreign_host_blocked(self):
        for host in ("evil.com", "evil.com:8420", "192.168.1.50", "internal.attacker.io"):
            resp = self.get("/api/settings", host=host)
            self.assertEqual(resp.status_code, 403, f"host {host} allowed!")

    def test_empty_host_rejected(self):
        resp = self.get("/api/settings", host="")
        self.assertEqual(resp.status_code, 403)

    # ---------- Origin checks on mutating requests ----------
    def test_post_without_origin_allowed(self):
        # curl / scripts / local tooling send no Origin — must keep working.
        resp = self.post("/api/groups", json={"name": "curl-made"})
        self.assertEqual(resp.status_code, 201)

    def test_post_from_same_origin_allowed(self):
        resp = self.post("/api/groups", origin="http://127.0.0.1:8420",
                         json={"name": "same-origin"})
        self.assertEqual(resp.status_code, 201)

    def test_cross_site_post_blocked(self):
        # A malicious page POSTing to http://127.0.0.1:8420 from the victim's
        # browser carries its own Origin — that request must be refused.
        resp = self.post("/api/groups", origin="http://evil.example",
                         json={"name": "csrf"})
        self.assertEqual(resp.status_code, 403)
        # and nothing was written
        listing = self.client.get("/api/groups", headers={"Host": "127.0.0.1"})
        self.assertNotIn(b"csrf", listing.data)

    def test_referer_fallback_checked_on_mutations(self):
        resp = self.client.post(
            "/api/groups",
            headers={"Host": "127.0.0.1",
                     "Referer": "http://attacker.example/overlay.html"},
            json={"name": "referer-csrf"})
        self.assertEqual(resp.status_code, 403)

    def test_get_not_subject_to_origin_check(self):
        resp = self.client.get(
            "/api/settings",
            headers={"Host": "127.0.0.1", "Origin": "http://evil.example"})
        self.assertEqual(resp.status_code, 200)  # reads are same-data-local anyway

    # ---------- security headers ----------
    def test_security_headers_present(self):
        resp = self.get("/")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")

    def test_api_responses_not_cached(self):
        resp = self.get("/api/settings")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_secrets_never_in_public_settings_response(self):
        conn = connect(self.db_path)
        try:
            settings_mod.save_settings(conn, {
                "gdrive_client_secret": "super-secret-value"})
            resp = self.get("/api/settings")
            self.assertNotIn(b"super-secret-value", resp.data)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()


class LanModeTests(unittest.TestCase):
    """--lan must actually serve LAN clients: any Host/Origin is accepted
    when the operator opts in, while the default stays loopback-only."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="poslan-")
        cls.db_path = os.path.join(cls.tmpdir, "lan-test.db")
        init_db(cls.db_path)
        cls.app = create_app(cls.db_path, extra_hosts=("*",))
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_lan_host_and_origin_allowed(self):
        r = self.client.get("/", headers={"Host": "192.168.1.50:8420"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/backups",
                             headers={"Host": "192.168.1.50:8420",
                                      "Origin": "http://192.168.1.50:8420"})
        self.assertIn(r.status_code, (200, 201))

    def test_loopback_still_allowed(self):
        r = self.client.get("/", headers={"Host": "127.0.0.1:8420"})
        self.assertEqual(r.status_code, 200)


class NullOriginTests(unittest.TestCase):
    """Privacy browsers and extensions (Brave strict mode, uBlock-style)
    send 'Origin: null' on mutations. It names no site, so it must not be
    treated as a cross-site attack."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="posnull-")
        cls.db_path = os.path.join(cls.tmpdir, "null-test.db")
        init_db(cls.db_path)
        cls.app = create_app(cls.db_path)
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_null_origin_mutation_allowed(self):
        r = self.client.post("/api/customers",
                             data='{"name":"Privacy User"}',
                             headers={"Host": "127.0.0.1:8420",
                                      "Origin": "null",
                                      "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 201)

    def test_null_origin_case_insensitive(self):
        r = self.client.post("/api/customers",
                             data='{"name":"Null NUL"}',
                             headers={"Host": "127.0.0.1:8420",
                                      "Origin": "NULL",
                                      "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 201)

    def test_foreign_origin_still_blocked(self):
        r = self.client.post("/api/customers",
                             data='{"name":"Evil"}',
                             headers={"Host": "127.0.0.1:8420",
                                      "Origin": "https://evil.example",
                                      "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 403)
