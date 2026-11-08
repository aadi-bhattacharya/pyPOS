#!/usr/bin/env python3
"""Explicit one-time migration from a source-checkout data folder.

Run *before* opening the new managed native build. Preserves the original and
refuses to overwrite an existing destination. Only uses stdlib / SQLite.

    python migrate_legacy_data.py --from ./data
"""
import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from pos.db import default_data_dir


def migrate(old_folder, target_folder=None):
    source_dir = Path(old_folder).expanduser().resolve()
    target_dir = (Path(target_folder).expanduser().resolve() if target_folder else
                  default_data_dir(checkout=None, cwd="/").expanduser().resolve())
    source = source_dir / "pypos.db"
    destination = target_dir / "pypos.db"
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Original database not found or is a symlink: {source}")
    if source == destination:
        raise ValueError("Source and destination are the same; nothing to migrate")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing store database: {destination}")
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".pypos-migrate-", suffix=".db", dir=target_dir)
    os.close(fd)
    temp = Path(temp_name)
    try:
        original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            snapshot = sqlite3.connect(temp)
            try:
                original.backup(snapshot, pages=256, sleep=0.1)
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Migrated database failed SQLite quick_check")
            finally:
                snapshot.close()
        finally:
            original.close()
        os.chmod(temp, 0o600)
        if destination.exists():
            raise FileExistsError("Destination appeared during migration; refusing overwrite")
        # Atomically publish verified data. This tool must only run after both
        # pyPOS processes are stopped; it does not erase its input or existing DB.
        temp.rename(destination)
    finally:
        temp.unlink(missing_ok=True)
    old_backups = source_dir / "backups"
    if old_backups.is_dir() and not old_backups.is_symlink():
        new_backups = target_dir / "backups"
        new_backups.mkdir(exist_ok=True)
        for item in old_backups.iterdir():
            if (item.is_file() and not item.is_symlink() and item.name.startswith("pypos-")
                    and item.name.endswith(".db") and not (new_backups / item.name).exists()):
                shutil.copy2(item, new_backups / item.name)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="old_folder", required=True,
                        help="existing source data directory (e.g. ./data)")
    args = parser.parse_args()
    print("Transferred verified SQLite database to:", migrate(args.old_folder))
    print("Original data was not deleted. Check stock, invoices and backups before use.")
