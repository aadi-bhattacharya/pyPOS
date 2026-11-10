"""Opting out must stop the launcher, not merely hide update notices."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pos import db, settings, update_core as upd, managed_launcher as launcher
from pos.update_policy import automatic_updates_enabled
from tests.test_managed_updates import archive


class UpdatePreferenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "store" / "pypos.db"
        db.init_db(self.db_path)

    def save(self, value):
        conn = db.connect(self.db_path)
        try:
            settings.save_settings(conn, {"auto_updates_enabled": value})
        finally:
            conn.close()

    def test_fresh_install_defaults_on_and_admin_change_persists(self):
        self.assertTrue(automatic_updates_enabled(self.root / "uninitialized.db"))
        self.assertTrue(automatic_updates_enabled(self.db_path))
        self.save("false")
        self.assertFalse(automatic_updates_enabled(self.db_path))
        conn = db.connect(self.db_path)
        try:
            self.assertEqual(settings.get_settings(conn)["auto_updates_enabled"], "false")
            with self.assertRaises(ValueError):
                settings.save_settings(conn, {"auto_updates_enabled": "FALSE"})
        finally:
            conn.close()
        self.assertFalse(automatic_updates_enabled(self.db_path))
        self.save("true")
        self.assertTrue(automatic_updates_enabled(self.db_path))

    def test_unreadable_or_invalid_value_does_not_override_opt_out(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                         ("auto_updates_enabled", "invalid"))
        self.assertFalse(automatic_updates_enabled(self.db_path))
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not a sqlite database")
        self.assertFalse(automatic_updates_enabled(corrupt))

    def test_disabled_prevents_any_network_request(self):
        def never_fetch(*_):
            self.fail("An opted-out launcher must not contact the network")
        result = upd.update_once(self.root, "1.0.0", "seller/pypos", system="linux",
                                 allowed=lambda: False, fetch=never_fetch)
        self.assertFalse(result)
        self.assertFalse((self.root / "pending.txt").exists())

    def test_opt_out_during_download_never_stages_release(self):
        data, sha = archive(version="1.1.0")
        repo = "seller/pypos"
        url = f"https://github.com/{repo}/releases/download/v1.1.0/pypos-linux-x86_64.zip"
        urls = {
            f"https://api.github.com/repos/{repo}/releases/latest": json.dumps({
                "tag_name": "v1.1.0", "assets": [
                    {"name": "pypos-linux-x86_64.zip", "browser_download_url": url},
                    {"name": "pypos-linux-x86_64.zip.sha256", "browser_download_url": url + ".sha256"},
                ]}).encode(),
            url + ".sha256": (sha + "\n").encode(),
            url: data,
        }
        enabled = {"on": True}
        def fetch(src, _limit):
            if src == url:
                enabled["on"] = False  # Admin opts out while the ZIP is downloading.
            return urls[src]
        with patch.object(upd, "platform_key", return_value="linux-x86_64"):
            changed = upd.update_once(self.root, "1.0.0", repo, system="linux",
                                      fetch=fetch, allowed=lambda: enabled["on"])
        self.assertFalse(changed)
        self.assertFalse((self.root / "pending.txt").exists())
        self.assertFalse((self.root / "versions" / "1.1.0").exists())

    def test_staged_release_remains_inactive_until_opt_back_in(self):
        contents = {"pypos-app": b"one", "pypos-launcher": b"two"}
        for version in ("1.0.0", "1.1.0"):
            upd.install_version(self.root, version, contents, system="linux")
        upd.atomic_version_file(self.root / "current.txt", "1.0.0")
        upd.atomic_version_file(self.root / "pending.txt", "1.1.0")
        with patch.object(launcher, "binary_names", return_value=("pypos-app", "pypos-launcher")), \
             patch.object(launcher, "automatic_updates_enabled", return_value=False), \
             patch.object(launcher, "_start_child") as start:
            launcher.stable_launch(self.root)
            start.assert_called_once()
        self.assertEqual(upd.read_version_file(self.root / "current.txt"), "1.0.0")
        self.assertEqual(upd.read_version_file(self.root / "pending.txt"), "1.1.0")
        with patch.object(launcher, "binary_names", return_value=("pypos-app", "pypos-launcher")), \
             patch.object(launcher, "automatic_updates_enabled", return_value=True), \
             patch.object(launcher, "_pre_update_backup", return_value=None), \
             patch.object(launcher, "_start_child"):
            launcher.stable_launch(self.root)
        self.assertEqual(upd.read_version_file(self.root / "current.txt"), "1.1.0")
        self.assertFalse((self.root / "pending.txt").exists())


if __name__ == "__main__":
    unittest.main()
