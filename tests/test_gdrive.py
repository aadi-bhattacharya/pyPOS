import json
import unittest
from io import BytesIO
from unittest import mock

from base import POSTestCase

from pos import gdrive, settings as settings_mod


def _resp(payload):
    """A fake urllib response (context manager) serving one JSON body."""
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return Resp()


def fake_urlopen(payload):
    """Patch target: callable matching urlopen(req, timeout=...) signature."""
    return lambda *args, **kwargs: _resp(payload)


class GDriveUnitTests(unittest.TestCase):
    def test_auth_url_contains_required_params(self):
        url = gdrive.build_auth_url("my-id.apps.googleusercontent.com",
                                    "http://127.0.0.1:8420/gdrive/callback")
        self.assertIn("accounts.google.com/o/oauth2/v2/auth?", url)
        self.assertIn("client_id=my-id.apps.googleusercontent.com", url)
        self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8420%2Fgdrive%2Fcallback",
                      url)
        self.assertIn("access_type=offline", url)
        self.assertIn("drive.file", url)

    def test_exchange_code_parses_tokens(self):
        with mock.patch.object(gdrive, "urlopen",
                               fake_urlopen({"access_token": "at", "refresh_token": "rt",
                                             "expires_in": 3599})):
            result = gdrive.exchange_code("code123", "cid", "csec", "http://x/cb")
        self.assertEqual(result["refresh_token"], "rt")
        self.assertEqual(result["access_token"], "at")

    def test_refresh_is_cached(self):
        gdrive._access_cache.clear()
        opener = mock.MagicMock(side_effect=iter([
            _resp({"access_token": "t1", "expires_in": 3600}),
            _resp({"access_token": "t2", "expires_in": 3600}),
        ]))
        with mock.patch.object(gdrive, "urlopen", opener):
            first = gdrive.refresh_access("rt", "cid", "sec")
            second = gdrive.refresh_access("rt", "cid", "sec")
        self.assertEqual(first, "t1")
        self.assertEqual(second, "t1")  # served from cache
        self.assertEqual(opener.call_count, 1)

    def test_upload_sends_multipart_with_filename(self):
        captured = {}

        class Resp(BytesIO):
            def __init__(self, data):
                super().__init__(b'{"id": "file-1"}')

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def recorder(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return Resp(b"")

        with mock.patch.object(gdrive, "urlopen", recorder):
            result = gdrive.upload_file("tok", "pypos-test.db", b"\x53\x51\x4c")

        self.assertEqual(result["id"], "file-1")
        self.assertIn("uploadType=multipart", captured["url"])
        body = captured["body"]
        self.assertIn(b'"name": "pypos-test.db"', body)
        self.assertIn(b"pyPOS", body)  # boundary marker present
        ct = captured["headers"].get("Content-type") or \
             captured["headers"].get("Content-Type")
        self.assertIn("multipart/related", ct)
        self.assertIn("Bearer tok", captured["headers"].get("Authorization"))

    def test_http_error_surfaces_google_message(self):
        import urllib.error

        def raise_http(req, timeout=None):
            raise urllib.error.HTTPError("u", 400,
                                         "Bad Request", {}, BytesIO(
                                             b'{"error_description": "expired code"}'))

        with mock.patch.object(gdrive, "urlopen", raise_http):
            with self.assertRaises(gdrive.GoogleDriveError) as ctx:
                gdrive.exchange_code("bad", "cid", "sec", "http://x/cb")
        self.assertIn("expired code", str(ctx.exception))


class SettingsMaskingTests(POSTestCase):
    def test_secrets_never_returned_publicly(self):
        settings_mod.save_settings(self.conn, {
            "gdrive_client_id": "cid-123",
            "gdrive_client_secret": "sekret",
            "gdrive_refresh_token": "rtok",
        })
        public = settings_mod.get_settings(self.conn)
        internal = settings_mod.get_all_settings(self.conn)

        self.assertEqual(public["gdrive_client_secret"], "")
        self.assertEqual(public["gdrive_refresh_token"], "")
        self.assertNotIn("sekret", json.dumps(public))

        self.assertEqual(internal["gdrive_client_secret"], "sekret")
        self.assertEqual(internal["gdrive_refresh_token"], "rtok")
        self.assertEqual(public["gdrive_client_id"], "cid-123")  # id is fine to show

    def test_empty_secret_write_preserves_existing(self):
        settings_mod.save_settings(self.conn, {"gdrive_client_secret": "keep-me"})
        settings_mod.save_settings(self.conn, {"store_name": "X"})
        internal = settings_mod.get_all_settings(self.conn)
        self.assertEqual(internal["gdrive_client_secret"], "keep-me")


if __name__ == "__main__":
    unittest.main()
