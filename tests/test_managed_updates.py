"""Isolated tests: require no network access and never touch a shop database."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pos import managed_launcher as ml
from pos import update_core as upd
from pos.platform_paths import binary_names, install_root, log_dir, platform_key
from pos.install_links import create_shortcut


class PathsTests(unittest.TestCase):
    def test_windows_app_and_data_are_separate(self):
        env = {"LOCALAPPDATA": r"C:\Users\Cashier\AppData\Local"}
        p = install_root(system="win32", env=env, home=r"C:\Users\Cashier")
        self.assertEqual(str(p).replace("\\", "/"),
                         "C:/Users/Cashier/AppData/Local/Programs/pyPOS")
        self.assertEqual(str(log_dir(system="win32", env=env)).replace("\\", "/"),
                         "C:/Users/Cashier/AppData/Local/pyPOS/logs")

    def test_macos_and_linux_install_paths(self):
        self.assertEqual(install_root(system="darwin", home="/Users/seller"),
                         Path("/Users/seller/Library/Application Support/pyPOS/Application"))
        self.assertEqual(install_root(system="linux", env={"XDG_DATA_HOME": "/data"}),
                         Path("/data/pyPOS/Application"))
        self.assertEqual(log_dir(system="linux", env={"XDG_STATE_HOME": "/state"}),
                         Path("/state/pyPOS"))

    def test_platform_release_architecture(self):
        self.assertEqual(platform_key(system="win32", machine="AMD64"), "windows-x86_64")
        self.assertEqual(platform_key(system="darwin", machine="arm64"), "macos-arm64")
        self.assertEqual(platform_key(system="linux", machine="aarch64"), "linux-arm64")
        with self.assertRaises(ValueError):
            platform_key(system="unknown", machine="x86_64")


def archive(version="1.2.3", platform="linux-x86_64", mutated=False):
    files = {"pypos-app": b"EXE 1", "pypos-launcher": b"EXE 2"}
    manifest = {"version": version, "platform": platform,
                "sha256": {name: hashlib.sha256(b).hexdigest() for name, b in files.items()}}
    if mutated:
        files["pypos-app"] = b"ATTACK"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as f:
        for name, data in files.items():
            f.writestr(name, data)
        f.writestr("release-manifest.json", json.dumps(manifest))
    payload = buffer.getvalue()
    return payload, hashlib.sha256(payload).hexdigest()


class UpdateIntegrityTests(unittest.TestCase):
    def test_valid_payload_and_checksum(self):
        data, checksum = archive()
        files = upd.validate_archive(data, "1.2.3", "linux-x86_64", checksum,
                                     system="linux")
        self.assertEqual(files["pypos-app"], b"EXE 1")

    def test_digest_mismatch_is_rejected(self):
        data, checksum = archive()
        with self.assertRaises(upd.UpdateError):
            upd.validate_archive(data, "1.2.3", "linux-x86_64", "0" * 64,
                                         system="linux")

    def test_internal_binary_mismatch_is_rejected(self):
        data, checksum = archive(mutated=True)
        with self.assertRaises(upd.UpdateError):
            upd.validate_archive(data, "1.2.3", "linux-x86_64", checksum,
                                         system="linux")

    def test_wrong_platform_and_version_rejected(self):
        data, checksum = archive()
        for version, platform in [("1.2.4", "linux-x86_64"), ("1.2.3", "macos-x86_64")]:
            with self.assertRaises(upd.UpdateError):
                upd.validate_archive(data, version, platform, checksum, system="linux")

    def test_path_traversal_is_rejected(self):
        data, checksum = archive()
        with zipfile.ZipFile(io.BytesIO(data)) as f:
            entries = [(x, f.read(x)) for x in f.namelist()]
        result = io.BytesIO()
        with zipfile.ZipFile(result, "w") as f:
            for name, val in entries:
                f.writestr("../" + name if name == "pypos-app" else name, val)
        changed = result.getvalue()
        with self.assertRaises(upd.UpdateError):
            upd.validate_archive(changed, "1.2.3", "linux-x86_64",
                                         hashlib.sha256(changed).hexdigest(), system="linux")

    def test_no_downgrades_or_malformed_versions(self):
        self.assertTrue(upd.newer("1.10.0", "1.9.9"))
        self.assertFalse(upd.newer("1.0.0", "1.0.1"))
        for v in ["../datadir", "1.2.3-rc1", "1.2", "1.2.3/secret"]:
            with self.assertRaises(upd.UpdateError):
                upd.validate_version(v)

    def test_fetch_is_size_bounded_and_https_only(self):
        with self.assertRaises(upd.UpdateError):
            upd.safe_fetch("http://github.com/", 2)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, n): return b"12345" if n else b""
        with self.assertRaises(upd.UpdateError):
            upd.safe_fetch("https://github.com/", 2, opener=lambda *a, **kw: Response())


class ManagedInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {"pypos-app": b"executable one", "pypos-launcher": b"executable two"}

    def install(self, ver):
        return upd.install_version(self.root, ver, self.files, system="linux")

    def test_install_is_immutable_and_verified(self):
        version = self.install("1.0.0")
        self.assertTrue((version / "sha256.json").exists())
        with patch.object(ml, "binary_names", return_value=("pypos-app", "pypos-launcher")):
            self.assertTrue(ml.verify_installed(self.root, "1.0.0"))
            (version / "pypos-app").write_bytes(b"tamper")
            self.assertFalse(ml.verify_installed(self.root, "1.0.0"))
        with self.assertRaises(upd.UpdateError):
            self.install("1.0.0")

    def test_cleanup_never_deletes_data_or_active_or_previous(self):
        for v in ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]:
            self.install(v)
        upd.atomic_version_file(self.root / "current.txt", "1.3.0")
        upd.atomic_version_file(self.root / "previous.txt", "1.2.0")
        (self.root / "data").mkdir()
        (self.root / "data" / "pypos.db").write_bytes(b"important sales")
        with patch.object(upd, "binary_names", return_value=("pypos-app", "pypos-launcher")):
            upd.clean_versions(self.root, keep=set())
        self.assertFalse((self.root / "versions" / "1.0.0").exists())
        self.assertFalse((self.root / "versions" / "1.1.0").exists())
        self.assertTrue((self.root / "versions" / "1.2.0").exists())
        self.assertTrue((self.root / "versions" / "1.3.0").exists())
        self.assertEqual((self.root / "data" / "pypos.db").read_bytes(), b"important sales")

    def test_legacy_directories_and_unrecognized_files_are_safe(self):
        self.install("0.9.0")
        old = self.root / "versions" / "0.9.0"
        (old / "customers.db").write_text("unsafe to delete")
        with patch.object(upd, "binary_names", return_value=("pypos-app", "pypos-launcher")):
            upd.clean_versions(self.root, keep=set())
        self.assertTrue(old.exists())

    def test_pending_activation_happens_only_during_stable_launch(self):
        self.install("1.0.0")
        self.install("1.1.0")
        upd.atomic_version_file(self.root / "current.txt", "1.0.0")
        upd.atomic_version_file(self.root / "pending.txt", "1.1.0")
        with (patch.object(ml, "binary_names", return_value=("pypos-app", "pypos-launcher")),
              patch.object(ml, "_start_child") as start):
            ml.stable_launch(self.root)
            start.assert_called_once()
        self.assertEqual(upd.read_version_file(self.root / "current.txt"), "1.1.0")
        self.assertEqual(upd.read_version_file(self.root / "previous.txt"), "1.0.0")
        self.assertFalse((self.root / "pending.txt").exists())

    def test_failed_pending_version_does_not_switch(self):
        self.install("1.0.0")
        upd.atomic_version_file(self.root / "current.txt", "1.0.0")
        upd.atomic_version_file(self.root / "pending.txt", "1.1.0")
        with (patch.object(ml, "binary_names", return_value=("pypos-app", "pypos-launcher")),
              patch.object(ml, "_start_child") as start):
            ml.stable_launch(self.root)
            start.assert_called_once()
        self.assertEqual(upd.read_version_file(self.root / "current.txt"), "1.0.0")

    def test_background_update_stages_only_verified_files(self):
        data, checksum = archive(version="1.1.0")
        repo = "seller/pypos"
        urls = {
            f"https://api.github.com/repos/{repo}/releases/latest":
                json.dumps({"tag_name": "v1.1.0", "assets": [
                    {"name": "pypos-linux-x86_64.zip",
                     "browser_download_url": f"https://github.com/{repo}/releases/download/v1.1.0/pypos-linux-x86_64.zip"},
                    {"name": "pypos-linux-x86_64.zip.sha256",
                     "browser_download_url": f"https://github.com/{repo}/releases/download/v1.1.0/pypos-linux-x86_64.zip.sha256"}]}).encode(),
            f"https://github.com/{repo}/releases/download/v1.1.0/pypos-linux-x86_64.zip.sha256": (checksum + "\n").encode(),
            f"https://github.com/{repo}/releases/download/v1.1.0/pypos-linux-x86_64.zip": data,
        }
        self.assertTrue(upd.update_once(self.root, "1.0.0", repo, system="linux",
                                        fetch=lambda url, limit: urls[url]))
        self.assertEqual(upd.read_version_file(self.root / "pending.txt"), "1.1.0")
        self.assertIsNone(upd.read_version_file(self.root / "current.txt"))
        self.assertTrue((self.root / "versions" / "1.1.0" / "sha256.json").exists())

    def test_atomic_version_file_refuses_traversal(self):
        with self.assertRaises(upd.UpdateError):
            upd.atomic_version_file(self.root / "current.txt", "../../danger")


if __name__ == "__main__":
    unittest.main()

class PreUpdateDatabaseSafetyTests(unittest.TestCase):
    def test_backup_captures_wal_commits_without_changing_live_db(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "shopdata"
            data.mkdir()
            live = data / "pypos.db"
            c = sqlite3.connect(live)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, amount INTEGER)")
            c.execute("INSERT INTO invoices VALUES (1, 400)")
            c.commit()
            with patch.object(ml, "default_data_dir", return_value=data):
                backup = ml._pre_update_backup("1.0.0", "1.1.0")
            b = sqlite3.connect(backup)
            self.assertEqual(b.execute("SELECT amount FROM invoices WHERE id=1").fetchone()[0], 400)
            b.close()
            c.execute("INSERT INTO invoices VALUES (2, 700)")
            c.commit()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 2)
            c.close()
            b = sqlite3.connect(backup)
            self.assertEqual(b.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
            b.close()

class DesktopShortcutTests(unittest.TestCase):
    def test_linux_creates_private_app_shortcut_without_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "code folder" / "pypos"
            target.parent.mkdir()
            target.write_bytes(b"binary")
            shortcut = create_shortcut(target, system="linux", env={"XDG_DATA_HOME": str(root / "xdg")})
            content = shortcut.read_text()
            self.assertIn('Terminal=false', content)
            self.assertIn(str(target), content)
            self.assertEqual(create_shortcut(target, system="linux", env={"XDG_DATA_HOME": str(root / "xdg")}), shortcut)

    def test_mac_creates_app_bundle_without_touching_store(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stable = root / "Library" / "Application Support" / "pyPOS" / "Application" / "pypos"
            stable.parent.mkdir(parents=True)
            stable.write_bytes(b"binary")
            result = create_shortcut(stable, system="darwin", home=root)
            self.assertTrue((result / "Contents" / "Info.plist").exists())
            self.assertIn(str(stable), (result / "Contents" / "MacOS" / "pyPOS").read_text())

class NativeReleasePackagingTests(unittest.TestCase):
    def test_release_zip_matches_expected_updater_manifest(self):
        from package_native import package
        from pos import __version__
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)
            a, b = binary_names()
            # The packager consumes dist/pypos and dist/pypos-launcher and
            # emits their versioned/release names, preserving SHA-256 content.
            (output / ("pypos.exe" if sys.platform == "win32" else "pypos")).write_bytes(b"app")
            (output / ("pypos-launcher.exe" if sys.platform == "win32" else "pypos-launcher")).write_bytes(b"launcher")
            archive_path, hash_path = package(output)
            data = archive_path.read_bytes()
            expected = hash_path.read_text().strip()
            result = upd.validate_archive(data, __version__, platform_key(), expected)
            self.assertEqual(result[a], b"app")
            self.assertEqual(result[b], b"launcher")
