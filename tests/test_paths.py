import shutil
import tempfile
import unittest
from pathlib import Path

from pos import backups, db


class DataDirResolutionTests(unittest.TestCase):
    """The rules that make pyPOS portable AND well-behaved:

    1. $PYPOS_HOME wins over everything
    2. source checkout  -> <checkout>/data       (historic layout preserved)
    3. ./pypos-data in cwd exists -> use it       (USB-stick portable mode)
    4. otherwise the platform-standard location (XDG / Library / LOCALAPPDATA)
    """

    def test_env_var_wins_over_everything(self):
        d = db.default_data_dir(env={"PYPOS_HOME": "/shop/data"},
                                cwd="/anywhere", checkout="/repo")
        self.assertEqual(d, Path("/shop/data"))

    def test_source_checkout_uses_data_subdir(self):
        d = db.default_data_dir(env={}, cwd="/elsewhere", checkout="/home/me/pyPOS")
        self.assertEqual(d, Path("/home/me/pyPOS/data"))

    def test_existing_pypos_data_marker_is_portable_mode(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pypos-data").mkdir()
            d = db.default_data_dir(env={}, cwd=td, checkout=None)
            self.assertEqual(d, Path(td) / "pypos-data")

    def test_platform_locations(self):
        import os
        cases = [
            ("linux",   "/home/u", {},
             "/home/u/.local/share/pyPOS"),
            ("linux",   "/home/u", {"XDG_DATA_HOME": "/xdg"},
             "/xdg/pyPOS"),
            ("darwin",  "/Users/u", {},
             "/Users/u/Library/Application Support/pyPOS"),
            ("win32",   r"C:\Users\u", {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
             r"C:\Users\u\AppData\Local\pyPOS"),
            ("win32",   r"C:\Users\u", {},
             r"C:\Users\u\AppData\Local\pyPOS"),
        ]
        for platform, home, env, expected in cases:
            with self.subTest(platform=platform):
                d = db.default_data_dir(env=env, cwd="/nowhere",
                                        checkout=None, platform=platform,
                                        home_dir=home)
                # compare separator-agnostically (tests may run on any OS)
                self.assertEqual(str(d).replace("\\", "/").replace("//", "/"),
                                 expected.replace("\\", "/"))

    def test_blank_env_value_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            d = db.default_data_dir(env={"PYPOS_HOME": "   "}, cwd=td,
                                    checkout=None, platform="linux",
                                    home_dir="/home/u")
            self.assertEqual(d, Path("/home/u/.local/share/pyPOS"))

    def test_default_db_file_follows_data_dir(self):
        f = db.default_database_file(env={}, cwd="/w", checkout="/repo")
        self.assertEqual(f, Path("/repo/data/pypos.db"))

    def test_this_repo_is_detected_as_checkout(self):
        checkout = db.source_checkout_dir()
        self.assertIsNotNone(checkout)
        self.assertTrue((Path(checkout) / "main.py").exists())


class BackupDirTests(unittest.TestCase):
    def test_backup_dir_sits_next_to_the_live_db(self):
        self.assertEqual(backups._backup_dir("/somewhere/shop.db"),
                         Path("/somewhere/backups"))

    def test_explicit_live_path_beats_default(self):
        # restoring into a custom --db must look for snapshots next to it,
        # never in the default location
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "custom" / "pypos.db"
            bdir = backups._backup_dir(live)
            name = "pypos-2026-01-01-000000-000.db"
            bdir.mkdir(parents=True, exist_ok=True)
            (bdir / name).write_bytes(b"x")
            path = backups._backup_path(name, bdir)
            self.assertTrue(path.exists())
            other = Path(tempfile.mkdtemp()) / "backups"
            try:
                with self.assertRaises(backups.BackupError):
                    backups._backup_path(name, other)
            finally:
                shutil.rmtree(other.parent, ignore_errors=True)


class CliGuardTests(unittest.TestCase):
    def test_loopback_detection(self):
        from pos.cli import _is_loopback
        self.assertTrue(_is_loopback("127.0.0.1"))
        self.assertTrue(_is_loopback("localhost"))
        self.assertTrue(_is_loopback("::1"))
        self.assertFalse(_is_loopback("0.0.0.0"))
        self.assertFalse(_is_loopback("192.168.1.10"))


if __name__ == "__main__":
    unittest.main()


class LegacyUpgradeTests(unittest.TestCase):
    """init_db must upgrade databases made by older pyPOS versions
    (pre-customers, pre-partial-refunds) instead of crashing."""

    LEGACY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS product_groups (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER REFERENCES product_groups(id),
      SKU TEXT NOT NULL UNIQUE,
      barcode TEXT UNIQUE,
      price REAL NOT NULL CHECK (price >= 0),
      quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
      cost_price REAL CHECK (cost_price >= 0) NOT NULL DEFAULT 0,
      tax_rate REAL NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
      updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS sales (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      subtotal REAL NOT NULL, tax_total REAL NOT NULL,
      discount_total REAL NOT NULL DEFAULT 0, total REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'COMPLETED',
      parent_sale_id INTEGER REFERENCES sales(id));
    CREATE TABLE IF NOT EXISTS sale_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
      product_id INTEGER NOT NULL REFERENCES products(id),
      quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
      tax_rate_at_sale REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
      method TEXT NOT NULL, amount REAL NOT NULL,
      is_partial BOOLEAN NOT NULL DEFAULT 0,
      parent_payment_id INTEGER REFERENCES payments(id));
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def test_init_db_upgrades_legacy_database(self):
        import sqlite3 as s3
        with tempfile.TemporaryDirectory() as td:
            legacy = Path(td) / "pypos.db"
            c = s3.connect(legacy)
            c.executescript(self.LEGACY_SCHEMA)
            c.execute("INSERT INTO settings(key, value) VALUES('store_name','Old')")
            c.commit()
            c.close()

            db.init_db(legacy)  # must not raise

            c = s3.connect(legacy)
            cols = {r[1] for r in c.execute("PRAGMA table_info(sales)")}
            self.assertIn("customer_id", cols)
            item_cols = {r[1] for r in c.execute("PRAGMA table_info(sale_items)")}
            self.assertIn("discount_cents", item_cols)
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for expected in ("customers", "credit_events", "parked_sales",
                             "cash_sessions"):
                self.assertIn(expected, tables)
            # old data survives
            name = c.execute(
                "SELECT value FROM settings WHERE key='store_name'").fetchone()[0]
            self.assertEqual(name, "Old")
            c.close()

            db.init_db(legacy)  # idempotent

    def test_init_db_fresh_then_again(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fresh.db"
            db.init_db(p)
            db.init_db(p)
