"""Permanent, console-free launcher for versioned native pyPOS installations.

Downloadable archives contain pypos-launcher and pypos-app. The first launch
copies both to the per-user program directory. The fixed launcher forwards to
the versioned launcher; the versioned launcher supervises the app and stages
verified new releases in the background. A sale is NEVER interrupted to update.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import release_build
from .install_links import create_shortcut
from .launch_dialog import confirm_stop
from .launch_dialog import configure_logging, show_error, unexpected_failure
from .platform_paths import binary_names, install_root, platform_key
from .db import default_data_dir
from .update_core import (UpdateError, atomic_version_file, clean_versions,
                          install_version, read_version_file, update_once,
                          validate_version)

LOG = logging.getLogger("pypos")


def _native_binary():
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def _start_child(executable, *args, cwd=None):
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = os.environ.copy()
    # A managed executable must not use its current working directory, a ZIP
    # extraction folder, or its version directory as the SQLite data home.
    env.setdefault("PYPOS_HOME", str(default_data_dir(checkout=None, cwd="/")))
    if Path(executable).name == binary_names()[0]:
        env["PYPOS_STOP_FILE"] = str(install_root() / "stop.request")
    return subprocess.Popen([str(executable), *args], cwd=str(cwd) if cwd else None,
                            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=creationflags,
                            close_fds=True)


def verify_installed(root, version):
    """Verify local immutable binary hashes before ever launching a version."""
    validate_version(version)
    folder = Path(root) / "versions" / version
    if folder.is_symlink() or not folder.is_dir():
        return False
    hashes = folder / "sha256.json"
    if hashes.is_symlink() or not hashes.is_file():
        return False
    try:
        expected = json.loads(hashes.read_text(encoding="ascii"))
        if set(expected) != set(binary_names()):
            return False
        for name in binary_names():
            file = folder / name
            if file.is_symlink() or not file.is_file():
                return False
            if hashlib.sha256(file.read_bytes()).hexdigest() != expected[name]:
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def _read_local_package(folder):
    mf_path = folder / "release-manifest.json"
    if not mf_path.is_file() or mf_path.is_symlink():
        raise UpdateError("Installation files missing. Extract the complete release ZIP first.")
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    version = validate_version(mf.get("version"))
    if mf.get("platform") != platform_key():
        raise UpdateError("This build is for a different operating system or CPU.")
    names = set(binary_names())
    if set(mf.get("sha256", {})) != names:
        raise UpdateError("Incomplete release manifest")
    contents = {}
    for name in names:
        if (folder / name).is_symlink():
            raise UpdateError("Refusing a symbolic link in the installation")
        contents[name] = (folder / name).read_bytes()
        if hashlib.sha256(contents[name]).hexdigest() != mf["sha256"][name]:
            raise UpdateError(f"Installation file failed verification: {name}")
    return version, contents


def first_install(folder, root):
    """Install a release extracted from a ZIP. Data folder is never touched."""
    version, files = _read_local_package(folder)
    installed = install_version(root, version, files)
    if not verify_installed(root, version):
        raise UpdateError("Installed application did not pass integrity verification")
    current = read_version_file(root / "current.txt")
    if current and current != version and verify_installed(root, current):
        atomic_version_file(root / "previous.txt", current)
    if not current or (tuple(map(int, version.split("."))) >=
                       tuple(map(int, current.split(".")))):
        atomic_version_file(root / "current.txt", version)
    stable = root / ("pyPOS.exe" if os.name == "nt" else "pypos")
    if not stable.exists():
        # Only the stable forwarder is copied here, never SQLite files.
        shutil.copy2(installed / binary_names()[1], stable)
        if os.name != "nt":
            stable.chmod(0o700)
    try:
        link = create_shortcut(stable)
        LOG.info("Added user launcher shortcut at %s", link)
    except (OSError, subprocess.SubprocessError):
        LOG.exception("Could not create optional launcher shortcut")
    LOG.info("Installed %s at %s; data remains separate", version, root)
    return stable


def _pre_update_backup(current, pending):
    """Create an SQLite-consistent restore point before a new version can migrate.

    Never replace the live DB. If a backup cannot be completed, postpone the
    update and start the existing register rather than risk the only copy.
    """
    import sqlite3
    from datetime import datetime, timezone
    data_dir = default_data_dir(checkout=None, cwd="/")
    db_file = data_dir / "pypos.db"
    if not db_file.is_file():
        return None
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"pre-update-{current}-to-{pending}-{stamp}.db"
    target = backup_dir / name
    if target.exists():
        raise UpdateError("Pre-update backup name already exists; update postponed")
    # SQLite's online backup API copies a transaction-consistent snapshot,
    # including committed transactions in WAL. No raw .db file copying.
    source = sqlite3.connect(db_file.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        destination = sqlite3.connect(str(target), timeout=10)
        try:
            source.backup(destination, pages=256, sleep=0.1)
            status = destination.execute("PRAGMA quick_check").fetchone()[0]
            if status != "ok":
                raise UpdateError("Pre-update SQLite backup did not pass quick_check")
        finally:
            destination.close()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    try:
        target.chmod(0o600)
    except OSError:
        pass
    LOG.info("Created pre-update database backup at %s", target)
    return target


def stable_launch(root):
    current = read_version_file(root / "current.txt")
    pending = read_version_file(root / "pending.txt")
    if pending and verify_installed(root, pending):
        activate = True
        if current and current != pending:
            try:
                _pre_update_backup(current, pending)
            except Exception:
                activate = False
                LOG.exception("Update to %s postponed: cannot secure database backup", pending)
        if activate:
            if current and verify_installed(root, current) and current != pending:
                atomic_version_file(root / "previous.txt", current)
            atomic_version_file(root / "current.txt", pending)
            (root / "pending.txt").unlink(missing_ok=True)
            current = pending
            LOG.info("Activated version %s on launch", current)
    if not current or not verify_installed(root, current):
        previous = read_version_file(root / "previous.txt")
        if previous and verify_installed(root, previous):
            current = previous
            atomic_version_file(root / "current.txt", previous)
            LOG.warning("Fell back to previous verified version %s", previous)
        else:
            raise UpdateError("No verified installed version found. Reinstall from an official release.")
    folder = root / "versions" / current
    _start_child(folder / binary_names()[1], "--managed-run", cwd=folder)


def request_stop(root):
    running = root / "running.txt"
    if not running.is_file():
        show_error("pyPOS", "No managed register appears to be running on this computer.")
        return 0
    if confirm_stop("Stop pyPOS?", "Stop the register on this computer? Finish all sales and receipts first."):
        (root / "stop.request").write_text("stop\n", encoding="ascii")
        LOG.info("Operator requested a graceful pyPOS shutdown")
    return 0


def managed_run(root, version_dir):
    version = version_dir.name
    if not verify_installed(root, version):
        raise UpdateError("Application integrity verification failed")
    if read_version_file(root / "current.txt") != version:
        raise UpdateError("This is not the active application version")
    app_file = version_dir / binary_names()[0]
    (root / "stop.request").unlink(missing_ok=True)
    child = _start_child(app_file, cwd=version_dir)
    (root / "running.txt").write_text(str(child.pid), encoding="ascii")
    LOG.info("Started pyPOS %s (PID %s)", version, child.pid)
    started = time.monotonic()

    def updater():
        # Keep all HTTP/download and disk work away from the billing process.
        try:
            if not release_build.ENABLED or not release_build.REPOSITORY:
                return
            # Do not spend time on updates if the register failed to start.
            if child.poll() is not None:
                return
            changed = update_once(root, version, release_build.REPOSITORY)
            if changed:
                LOG.info("A verified update is ready for the next pyPOS launch")
        except Exception:
            LOG.exception("Background update check failed; continuing offline")

    # Never wait for the internet before starting the till.
    thread = threading.Thread(target=updater, daemon=True, name="pypos-update")
    thread.start()
    while child.poll() is None:
        # An older install is only cleaned once the new app has survived 15s.
        if time.monotonic() - started >= 15:
            clean_versions(root, keep={version})
            break
        time.sleep(0.25)
    result = child.wait()
    running = root / "running.txt"
    if running.is_file() and running.read_text(encoding="ascii").strip() == str(child.pid):
        running.unlink(missing_ok=True)
    if result != 0 and result != 73 and time.monotonic() - started < 15:
        prior = read_version_file(root / "previous.txt")
        if prior and prior != version and verify_installed(root, prior):
            atomic_version_file(root / "current.txt", prior)
            LOG.error("New version exited early (%s); rolled back to %s", result, prior)
            show_error("pyPOS update rolled back",
                       "The new version could not start. The previous working version "
                       "has been restored for the next launch. See the pyPOS log.")
    LOG.info("pyPOS %s exited with code %s", version, result)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    configure_logging()
    root = install_root()
    try:
        if argv == ["--managed-run"]:
            return managed_run(root, _native_binary().parent)
        if argv == ["--stop"]:
            return request_stop(root)
        if argv:
            raise UpdateError("The graphical launcher does not accept command-line options")
        here = _native_binary().parent
        if here == root.resolve():
            stable_launch(root)
        else:
            stable = first_install(here, root)
            _start_child(stable, cwd=root)
        return 0
    except Exception as exc:
        unexpected_failure("Launching pyPOS", exc)
        return 1
