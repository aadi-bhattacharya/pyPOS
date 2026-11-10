"""Persistent automatic-update preference, shared by Flask and the launcher.

The launcher is a separate process, so browser state and a Flask in-memory flag
cannot control updates. The source of truth is the normal pyPOS settings table
in the persistent store database (outside immutable application versions).

Missing setting / first installation defaults to enabled for compatibility.
Unreadable or corrupt databases fail closed: a billing app must not update
itself when the operator's preference cannot be checked.
"""
import logging
import sqlite3
from pathlib import Path

from .db import default_data_dir

LOG = logging.getLogger("pypos")


def automatic_updates_enabled(database_path=None):
    path = Path(database_path) if database_path is not None else (
        default_data_dir(checkout=None, cwd="/") / "pypos.db")
    if not path.is_file():
        return True  # Fresh installation; no preference has ever been saved.
    if path.is_symlink():
        LOG.warning("Skipping automatic updates: settings database is a symlink")
        return False
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                               uri=True, timeout=1)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'auto_updates_enabled'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table: settings" in str(exc).lower():
            return True  # Legacy database not yet initialized.
        LOG.warning("Skipping automatic updates: cannot read preference: %s", exc)
        return False
    except (OSError, sqlite3.DatabaseError) as exc:
        LOG.warning("Skipping automatic updates: cannot read preference: %s", exc)
        return False
    return row is None or row[0] == "true"
