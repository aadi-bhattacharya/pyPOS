import sqlite3
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

from base import POSTestCase

from pos import backups, gdrive, settings as settings_mod

SCHEMA = Path(__file__).resolve().parent.parent / "pos" / "schema.sql"


def gdrive_err(message):
    return gdrive.GoogleDriveError(message)


class BackupsTests(POSTestCase):
    def setUp(self):
        super().setUp()
        # Snapshots land next to the live db file, so pointing --db at a
        # temp dir isolates all backup IO for this test.
        self.tmp = Path(tempfile.mkdtemp(prefix="posbak-"))
        self.addCleanup(self._cleanup_tmp)

        # Materialize a real database file from the schema.
        self.live_db = self.tmp / "pypos.db"
        dst = sqlite3.connect(str(self.live_db))
        dst.executescript(SCHEMA.read_text(encoding="utf-8"))
        dst.commit()
        dst.close()

        self.bdir = self.tmp / "backups"

    def _cleanup_tmp(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        return settings_mod.get_settings(self.conn)

    def test_create_backup_makes_valid_snapshot(self):
        result = backups.run_backup_cycle(
            {**self._settings(), "backup_keep": "10", "backup_remote": ""},
            source_path=self.live_db)
        path = self.bdir / result["name"]
        self.assertTrue(path.exists())
        backups.validate_database(path)  # must not raise
        self.assertFalse(result["pushed"])

    def test_list_and_prune(self):
        for _ in range(4):
            name = backups.create_backup(self.live_db)
            self.assertRegex(name, r"^pypos-\d{4}-\d{2}-\d{2}-\d{6}-\d{3}\.db$")
        self.assertEqual(len(backups.list_backups(self.bdir)), 4)
        removed = backups.prune_backups(keep=2, bdir=self.bdir)
        self.assertEqual(removed, 2)
        remaining = backups.list_backups(self.bdir)
        self.assertEqual(len(remaining), 2)

    def test_restore_roundtrip(self):
        from pos import catalog
        gid = self.mkgroup("Keepers")
        # Write the product into the FILE database (what gets snapshotted),
        # not the in-memory test connection.
        file_conn = sqlite3.connect(str(self.live_db))
        catalog.add_product_to_group(file_conn, gid, "KEEP-ME", None, 5, 5, 1, 0)
        file_conn.close()

        name = backups.create_backup(self.live_db)

        # Mutate live data AFTER the snapshot.
        conn2 = sqlite3.connect(str(self.live_db))
        conn2.execute("DELETE FROM products")
        conn2.commit()
        conn2.close()

        backups.restore_backup(name, live_path=self.live_db)
        check = sqlite3.connect(f"file:{self.live_db}?mode=ro", uri=True)
        rows = check.execute("SELECT SKU FROM products").fetchall()
        check.close()
        self.assertIn(("KEEP-ME",), rows)

    def test_invalid_files_rejected(self):
        bad = self.tmp / "notapos.db"
        bad.write_text("this is not a database")
        with self.assertRaises(backups.BackupError):
            backups.validate_database(bad)

        wrong_schema = self.tmp / "wrong.db"
        c = sqlite3.connect(str(wrong_schema))
        c.execute("CREATE TABLE other(x)")
        c.commit()
        c.close()
        with self.assertRaises(backups.BackupError):
            backups.validate_database(wrong_schema)

    def test_bad_names_rejected(self):
        with self.assertRaises(backups.BackupError):
            backups.restore_backup("../../etc/passwd", live_path=self.live_db)
        with self.assertRaises(backups.BackupError):
            backups.restore_backup("../escape.db", live_path=self.live_db)
        with self.assertRaises(backups.BackupError):
            backups.restore_backup("pypos-9999-99-99-999999.db",
                                   live_path=self.live_db)

    def test_scheduler_next_run_logic(self):
        sched = backups.BackupScheduler(
            lambda: {"auto_backup_enabled": "true",
                     "auto_backup_interval_hours": "24"})
        import time as time_mod
        now = time_mod.time()
        self.assertIsNone(sched.next_run({"auto_backup_enabled": "false"}, None))
        self.assertAlmostEqual(
            sched.next_run({"auto_backup_enabled": "true"}, None),
            now + 15, delta=30)
        overdue = sched.next_run({"auto_backup_enabled": "true"},
                                 now - 3600 * 25)
        self.assertLess(overdue, now)

    def test_status_shape(self):
        st = backups.scheduler_status(self._settings, source_path=self.live_db)
        for key in ("enabled", "interval_hours", "keep",
                    "gdrive", "last_auto", "next_auto", "count", "storage"):
            self.assertIn(key, st)
        self.assertIn("connected", st["gdrive"])
        self.assertIn("used_human", st["storage"])

    # ---------- power-cut safety ----------
    def test_snapshot_is_atomic_no_partial_finals(self):
        name = backups.create_backup(self.live_db)
        backups.validate_database(self.bdir / name)  # complete & valid
        leftovers = [f.name for f in self.bdir.iterdir()
                     if f.name.startswith(backups.TMP_PREFIX)]
        self.assertEqual(leftovers, [], "temp file left behind after success")

    def test_failed_copy_leaves_only_temp_garbage(self):
        with mock.patch.object(backups.sqlite3, "connect",
                               side_effect=sqlite3.OperationalError("disk I/O error")):
            with self.assertRaises(backups.BackupError):
                backups.create_backup(self.live_db)
        finals = [f for f in self.bdir.glob("pypos-*.db")]
        self.assertEqual(finals, [], "a final snapshot appeared from a failed run")

    def test_cleanup_debris_removes_crash_leftovers(self):
        import os as _os
        stale_tmp = self.bdir / ".tmp-pypos-old.db"
        backups._ensure_dir(self.bdir)
        stale_tmp.write_bytes(b"junk")
        old = __import__("time").time() - 25 * 3600
        _os.utime(stale_tmp, (old, old))
        fresh_tmp = self.bdir / ".tmp-pypos-new.db"
        fresh_tmp.write_bytes(b"junk")  # young — must be kept

        zero_byte = self.bdir / "pypos-2026-01-01-000000-000.db"
        zero_byte.touch()

        orphan_synced = self.bdir / "pypos-2026-01-02-000000-000.synced"
        orphan_synced.write_text("ok")

        removed = backups.cleanup_debris(bdir=self.bdir)
        self.assertFalse(stale_tmp.exists(), "stale tmp must be removed")
        self.assertTrue(fresh_tmp.exists(), "fresh tmp must survive")
        self.assertFalse(zero_byte.exists(), "empty snapshot must be removed")
        self.assertFalse(orphan_synced.exists(), "orphan marker must be removed")
        self.assertGreaterEqual(removed, 3)

    def test_size_budget_prunes_oldest_first(self):
        for _ in range(4):
            backups.create_backup(self.live_db)
        listing = backups.list_backups(self.bdir)
        self.assertEqual(len(listing), 4)

        # Fractional-MB budget small enough that only ONE snapshot fits.
        removed = backups.prune_backups(keep=10, settings={"backup_max_mb": "0.001"},
                                        bdir=self.bdir)
        remaining = backups.list_backups(self.bdir)
        self.assertEqual(len(remaining), 1)
        self.assertGreaterEqual(removed, 3)
        # newest survives
        self.assertEqual(remaining[0]["name"], listing[0]["name"])

    def test_disk_guard_blocks_when_space_is_short(self):
        with mock.patch.object(backups, "_free_bytes", return_value=1024):  # 1 KB free
            with self.assertRaises(backups.BackupError) as ctx:
                backups.run_backup_cycle(self._gdrive_settings(),
                                         source_path=self.live_db)
            self.assertIn("disk space", str(ctx.exception))
        # nothing was written under a final name
        self.assertEqual([f for f in self.bdir.glob("pypos-*.db")], [])

    def test_disk_guard_emergency_prunes_then_succeeds(self):
        for _ in range(3):
            backups.create_backup(self.live_db)
        self.assertEqual(len(backups.list_backups(self.bdir)), 3)

        freed_after_one_delete = backups._live_db_size(self.live_db) * 2 + \
            backups.MIN_FREE_MARGIN_BYTES + 1

        state = {"calls": 0}

        def fake_free(*_a):
            state["calls"] += 1
            # First check: not enough. After emergency pruning: plenty.
            if state["calls"] <= 2:
                return 512
            return freed_after_one_delete

        with mock.patch.object(backups, "_free_bytes", side_effect=fake_free):
            result = backups.run_backup_cycle(self._gdrive_settings(),
                                              source_path=self.live_db)

        self.assertIn(result["name"],
                      [b["name"] for b in backups.list_backups(self.bdir)])
        self.assertGreaterEqual(len(backups.list_backups(self.bdir)), 1)

    def test_gdrive_push_marks_synced_and_errors_are_recorded(self):
        with mock.patch("pos.gdrive.refresh_access", return_value="tok"), \
             mock.patch("pos.gdrive.upload_file", return_value={"id": "x"}), \
             mock.patch("pos.gdrive.list_backups_on_drive", return_value=[]):
            result = backups.run_backup_cycle(
                self._gdrive_settings(), source_path=self.live_db)
        self.assertTrue(result["pushed"])
        pushed_path = self.bdir / result["name"]
        self.assertTrue(pushed_path.with_suffix(".synced").exists())
        self.assertFalse((self.bdir / ".remote_error").exists())

        with mock.patch("pos.gdrive.refresh_access",
                        side_effect=gdrive_err("quota exceeded")):
            result = backups.run_backup_cycle(
                self._gdrive_settings(), source_path=self.live_db)
        self.assertFalse(result["pushed"])
        self.assertIn("quota", result["remote_error"])
        marker = self.bdir / backups.ERROR_MARKER
        self.assertTrue(marker.exists())
        self.assertIn("quota", marker.read_text())

    def _gdrive_settings(self):
        s = dict(self._settings())
        s.update({
            "backup_keep": "5",
            "gdrive_client_id": "cid",
            "gdrive_client_secret": "sec",
            "gdrive_refresh_token": "rtok",
        })
        return s


if __name__ == "__main__":
    unittest.main()
