"""Offline store-branding persistence checks: no Flask or network dependency."""
import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pos.db import connect, init_db
from pos.settings import get_settings, save_settings


class BrandingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.file = str(Path(self.dir.name) / "store.db")
        init_db(self.file)
        self.db = connect(self.file)

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    @staticmethod
    def logo():
        sample = b"\x89PNG\r\n\x1a\n" + b"test-logo"
        return "data:image/png;base64," + base64.b64encode(sample).decode()

    def test_appearance_defaults_and_no_schema_reset(self):
        s = get_settings(self.db)
        self.assertEqual(s["pos_display_name"], "pyPOS")
        self.assertEqual(s["pos_accent_color"], "#1B6BB5")
        self.assertEqual(s["pos_logo_mode"], "default")
        self.assertEqual(s["pos_logo_size"], "28")
        init_db(self.file)
        self.assertEqual(get_settings(self.db)["pos_display_name"], "pyPOS")

    def test_independent_sidebar_and_receipt_branding(self):
        original = save_settings(self.db, {"store_name": "Seller GST Name",
            "store_logo": self.logo(), "pos_display_name": "   राधा  फैशन   ",
            "pos_accent_color": "#a5b6c7", "pos_logo_mode": "receipt", "pos_logo_size": "40"})
        self.assertEqual(original["pos_display_name"], "राधा फैशन")
        self.assertEqual(original["pos_accent_color"], "#A5B6C7")
        self.assertEqual(original["store_name"], "Seller GST Name")
        self.assertEqual(original["pos_logo_mode"], "receipt")
        self.assertEqual(original["pos_logo_size"], "40")
        custom = save_settings(self.db, {"pos_logo": self.logo(), "pos_logo_mode": "custom"})
        self.assertEqual(custom["pos_logo"], self.logo())
        self.assertEqual(custom["store_logo"], original["store_logo"])
        reset = save_settings(self.db, {"pos_logo_mode": "default", "pos_display_name": "pyPOS",
                                        "pos_accent_color": "#1B6BB5"})
        self.assertEqual(reset["store_name"], "Seller GST Name")
        self.assertEqual(reset["pos_logo"], self.logo())

    def test_invalid_branding_is_atomic(self):
        for bad in ["red", "#ABC", "#000000; color:red", "#FFFFFFFF", "#GG0000", ""]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    save_settings(self.db, {"pos_display_name": "BROKEN", "pos_accent_color": bad})
                self.assertEqual(get_settings(self.db)["pos_display_name"], "pyPOS")
        for key, value in [
            ("pos_display_name", " "), ("pos_display_name", "x" * 49),
            ("pos_logo_size", "23"), ("pos_logo_size", "49"),
            ("pos_logo_mode", "script"), ("pos_logo", "javascript:alert(1)"),
            ("pos_logo", "data:image/png;base64,aW52YWxpZA=="),
        ]:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    save_settings(self.db, {"pos_display_name": "BROKEN", key: value})
                self.assertEqual(get_settings(self.db)["pos_display_name"], "pyPOS")
        with self.assertRaises(ValueError):
            save_settings(self.db, {"pos_logo_mode": "custom"})
        self.assertEqual(get_settings(self.db)["pos_logo_mode"], "default")

    def test_remove_custom_logo_requires_switch_mode_together(self):
        save_settings(self.db, {"pos_logo": self.logo(), "pos_logo_mode": "custom"})
        with self.assertRaises(ValueError):
            save_settings(self.db, {"pos_logo": ""})
        self.assertEqual(get_settings(self.db)["pos_logo_mode"], "custom")
        after = save_settings(self.db, {"pos_logo": "", "pos_logo_mode": "default"})
        self.assertEqual(after["pos_logo"], "")


if __name__ == "__main__":
    unittest.main()
