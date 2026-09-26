import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import default_database_file

NAME_RE = re.compile(r"^pypos-\d{4}-\d{2}-\d{2}-\d{6}-\d{3}\.db$")
TMP_PREFIX = ".tmp-"
REQUIRED_TABLES = {
    "product_groups", "products", "sales", "sale_items", "payments", "settings",
}
# Never let backups eat the disk: refuse to start a snapshot unless this much
# space remains free beyond what the copy itself needs.
MIN_FREE_MARGIN_BYTES = 64 * 1024 * 1024  # 64 MB

_cycle_lock = threading.Lock()


class BackupError(Exception):
    """Raised when a backup/restore operation cannot be completed."""


def _backup_dir(source_path=None):
    """Snapshots live next to the live database, wherever that is."""
    src = Path(str(source_path)) if source_path else default_database_file()
    return src.parent / "backups"


def _ensure_dir(bdir=None):
    (bdir or _backup_dir()).mkdir(parents=True, exist_ok=True)


def _fsync_dir(path):
    """Best-effort directory fsync so renames survive a power cut (POSIX)."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_write_text(path, text):
    tmp = Path(str(path) + ".tmpwrite")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


ERROR_MARKER = ".remote_error"
SYNC_SUFFIX = ".synced"


def _gdrive_connected(settings):
    return all([
        settings.get("gdrive_refresh_token"),
        settings.get("gdrive_client_id"),
        settings.get("gdrive_client_secret"),
    ])


def push_to_gdrive(path, settings):
    """Upload one backup to Google Drive and prune old remote copies.

    Returns (True, None) on success or (False, reason) — never raises;
    local backups must succeed even when the network is down.
    """
    from . import gdrive
    if not _gdrive_connected(settings):
        return False, "Google Drive is not connected"
    try:
        token = gdrive.refresh_access(
            settings["gdrive_refresh_token"],
            settings["gdrive_client_id"],
            settings["gdrive_client_secret"],
        )
        gdrive.upload_file(token, path.name, path.read_bytes())
        # Mirror retention on Drive: delete oldest beyond keep count.
        keep = max(int(settings.get("backup_keep") or 14), 1)
        remote = gdrive.list_backups_on_drive(token)
        for stale in remote[keep:]:
            try:
                gdrive.delete_file(token, stale["id"])
            except gdrive.GoogleDriveError:
                pass
        return True, None
    except gdrive.GoogleDriveError as e:
        return False, str(e)[:200]


def _mark_remote_result(path, pushed, error=None):
    marker = path.parent / ERROR_MARKER
    if pushed:
        marker.unlink(missing_ok=True)
        path.with_suffix(SYNC_SUFFIX).write_text("ok")
    else:
        marker.write_text(error or "unknown error")


def _free_bytes(bdir):
    try:
        return shutil.disk_usage(str(bdir)).free
    except OSError:
        return None  # unknown — assume there is room rather than blocking sales


def _live_db_size(source_path):
    src = Path(str(source_path)) if source_path else default_database_file()
    total = src.stat().st_size if src.exists() else 0
    for suffix in ("-wal", "-shm"):
        extra = Path(str(src) + suffix)
        if extra.exists():
            total += extra.stat().st_size
    return total


def _ensure_disk_room(settings, source_path=None):
    """Guarantee headroom for a new snapshot; prune oldest if space is tight.

    A power cut or a runaway disk can never be caused by backups: we check
    before writing and delete old snapshots first when space runs low.
    """
    bdir = _backup_dir(source_path)
    _ensure_dir(bdir)
    needed = int(_live_db_size(source_path) * 1.2) + MIN_FREE_MARGIN_BYTES
    free = _free_bytes(bdir)
    if free is None or free >= needed:
        return 0

    # Emergency pruning: drop oldest snapshots (count-retention still applies,
    # but the newest snapshot is never touched).
    removed = 0
    for b in reversed(list_backups(bdir)):
        if _free_bytes(bdir) >= needed or len(list_backups(bdir)) <= 1:
            break
        delete_backup(b["name"], bdir)
        removed += 1

    if _free_bytes(bdir) is not None and _free_bytes(bdir) < needed:
        raise BackupError(
            f"Not enough disk space for a backup "
            f"({_human_size(_free_bytes(bdir))} free, need ~{_human_size(needed)}). "
            "Free up space or lower the storage budget in Settings.")
    return removed


def create_backup(source_path=None):
    """Create a consistent, power-cut-safe snapshot of the live database.

    The copy is written to a temp name, integrity-checked, fsynced and then
    atomically renamed — a crash mid-write can only ever leave an unnamed
    temp file behind, never a broken 'final' snapshot.
    """
    src = str(source_path) if source_path else str(default_database_file())
    if not Path(src).exists():
        raise BackupError("Database file not found")
    bdir = _backup_dir(source_path)
    _ensure_dir(bdir)

    now = datetime.now()
    stamp = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{now.microsecond // 1000:03d}"
    final = bdir / f"pypos-{stamp}.db"
    tmp = bdir / f"{TMP_PREFIX}pypos-{stamp}.db"

    dest_conn = None
    source_conn = None
    try:
        source_conn = sqlite3.connect(src)
        try:
            # Fold the WAL into the main db so the snapshot is small & fast.
            source_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dest_conn = sqlite3.connect(str(tmp))
            source_conn.backup(dest_conn)
            dest_conn.execute("PRAGMA journal_mode = DELETE")  # self-contained file
            result = dest_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("Snapshot failed its integrity check")
        finally:
            if dest_conn is not None:
                dest_conn.close()

        # Durable rename: fsync file, replace, fsync directory.
        fd = os.open(str(tmp), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
        _fsync_dir(bdir)

        from .db import harden_permissions
        harden_permissions(final.parent)
    except BackupError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise BackupError("Backup failed while copying the database") from e
    finally:
        if source_conn is not None:
            source_conn.close()
    return final.name


def validate_database(path):
    """Check that a .db file is a plausible pyPOS database before restoring."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        raise BackupError("That file is empty or missing")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("Database integrity check failed")
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise BackupError(
                "Not a pyPOS backup (missing: " + ", ".join(sorted(missing)) + ")"
            )
    except sqlite3.DatabaseError as e:
        raise BackupError(f"Not a valid SQLite database: {e}") from e
    finally:
        conn.close()


def list_backups(bdir=None):
    bdir = bdir or _backup_dir()
    _ensure_dir(bdir)
    out = []
    for f in sorted(Path(bdir).glob("pypos-*.db"), reverse=True):
        if NAME_RE.match(f.name):
            st = f.stat()
            created_utc = datetime.fromtimestamp(
                st.st_mtime, timezone.utc
            ).replace(tzinfo=None).isoformat(timespec="seconds")
            out.append({
                "name": f.name,
                "size": st.st_size,
                "size_human": _human_size(st.st_size),
                "created_at": created_utc,
                "pushed_remote": (f.with_suffix(".synced")).exists(),
            })
    return out


def delete_backup(name, bdir=None):
    path = _backup_path(name, bdir)
    path.unlink(missing_ok=True)
    path.with_suffix(SYNC_SUFFIX).unlink(missing_ok=True)


def restore_backup(name, live_path=None):
    """Replace the live database with a stored snapshot."""
    src = _backup_path(name, _backup_dir(live_path))
    validate_database(src)
    _swap_live(src, live_path)


def save_upload_and_restore(fileobj, live_path=None):
    """Restore from a user-supplied .db file (validated before touching live data)."""
    import tempfile

    bdir = _backup_dir(live_path)
    _ensure_dir(bdir)
    fd, tmp = tempfile.mkstemp(suffix=".db", dir=str(bdir))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        fileobj.save(tmp_path)
        validate_database(tmp_path)
        _swap_live(tmp_path, live_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _backup_path(name, bdir=None):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise BackupError("Invalid backup name")
    path = Path(bdir or _backup_dir()) / name
    if not path.exists():
        raise BackupError("Backup not found")
    return path


def _swap_live(new_db, live_path=None):
    target = Path(live_path) if live_path else default_database_file()

    # Checkpoint the live WAL into the main file so nothing is lost, then swap.
    conn = sqlite3.connect(str(target))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    for suffix in ("-wal", "-shm"):
        stale = Path(str(target) + suffix)
        stale.unlink(missing_ok=True)
    os.replace(new_db, target)


def cleanup_debris(max_age_hours=24, bdir=None):
    """Remove leftovers from crashed runs and orphans.

    Safe to call any time, from anywhere — it only touches files that are
    provably garbage:
      * temp copies (*.tmp-*.db) older than max_age_hours
      * zero-byte snapshots (power cut during an ancient non-atomic version)
      * .synced markers whose snapshot no longer exists
    Returns the number of files removed.
    """
    bdir = Path(bdir or _backup_dir())
    _ensure_dir(bdir)
    now = time.time()
    removed = 0
    for f in bdir.iterdir():
        try:
            if f.name.startswith(TMP_PREFIX) and f.name.endswith(".db"):
                if now - f.stat().st_mtime > max_age_hours * 3600:
                    f.unlink(missing_ok=True)
                    removed += 1
            elif NAME_RE.match(f.name) and f.stat().st_size == 0:
                f.unlink(missing_ok=True)
                removed += 1
            elif f.name.endswith(SYNC_SUFFIX):
                if not f.with_suffix(".db").exists():
                    f.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            continue  # file vanished mid-scan — fine
    return removed


def prune_backups(keep, settings=None, bdir=None):
    """Enforce BOTH retention rules. Always keeps at least one snapshot.

    keep     — max number of snapshots (count-based retention)
    settings — when given, also enforces backup_max_mb (size-based budget),
               deleting oldest-first until total size fits the budget.
    """
    keep = max(int(keep or 0), 1)
    removed = 0

    backups_list = list_backups(bdir)
    for b in backups_list[keep:]:
        delete_backup(b["name"], bdir)
        removed += 1

    if settings is not None:
        budget_mb = float(settings.get("backup_max_mb") or 0)
        if budget_mb > 0:
            budget = int(budget_mb * 1024 * 1024)
            remaining = list_backups(bdir)
            total = sum(b["size"] for b in remaining)
            while total > budget and len(remaining) > 1:
                oldest = remaining.pop()  # list is newest-first
                delete_backup(oldest["name"], bdir)
                total -= oldest["size"]
                removed += 1
    return removed


def run_backup_cycle(settings, source_path=None):
    """One scheduled/manual cycle: cleanup -> disk guard -> snapshot -> prune.

    Guarded by a lock so a manual click and the scheduler can never run two
    cycles at once.
    """
    with _cycle_lock:
        bdir = _backup_dir(source_path)
        cleanup_debris(bdir=bdir)
        emergency_pruned = 0
        try:
            emergency_pruned = _ensure_disk_room(settings, source_path)
        except BackupError:
            raise

        name = create_backup(source_path)
        pruned = prune_backups(
            settings.get("backup_keep", "14"), settings=settings, bdir=bdir)

        path = bdir / name
        pushed, err = False, None
        if _gdrive_connected(settings):
            pushed, err = push_to_gdrive(path, settings)
            _mark_remote_result(path, pushed, err)
        return {"name": name, "pruned": pruned + emergency_pruned,
                "pushed": pushed, "remote_error": err}


# ---------------- scheduler ----------------

class BackupScheduler(threading.Thread):
    """Checks every minute whether an auto-backup is due; runs it if so."""

    def __init__(self, get_settings_fn, source_path=None):
        super().__init__(daemon=True, name="pos-backup-scheduler")
        self.get_settings = get_settings_fn
        self.source_path = source_path
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def next_run(self, settings, last_ts):
        hours = float(settings.get("auto_backup_interval_hours") or 24)
        if settings.get("auto_backup_enabled") != "true" or hours <= 0:
            return None
        interval = timedelta(hours=hours)
        if last_ts is None:
            return time.time() + 15  # first auto-run shortly after boot
        return last_ts + interval.total_seconds()

    def run(self):
        marker_file = _backup_dir(self.source_path) / ".last_auto_backup"
        # Backwards-compatible: also honor newest manual/auto snapshot on disk.
        while not self._stop.wait(60):
            try:
                settings = self.get_settings()
                last_ts = self._last_run(marker_file, settings)
                nxt = self.next_run(settings, last_ts)
                if nxt is None or time.time() < nxt:
                    continue
                result = run_backup_cycle(self.get_settings(), self.source_path)
                marker_file.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(
                    marker_file,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
                if not result["pushed"] and result["remote_error"]:
                    print(f"[backups] remote sync failed: {result['remote_error']}")
            except Exception as e:
                print(f"[backups] auto-backup failed: {e}")

    def _last_run(self, marker_file, settings):
        try:
            ts = datetime.fromisoformat(marker_file.read_text().strip())
            return ts.timestamp()
        except Exception:
            pass
        backups = list_backups(_backup_dir(self.source_path))
        if backups:
            return datetime.fromisoformat(backups[0]["created_at"]).timestamp()
        return None


def scheduler_status(get_settings_fn, source_path=None):
    """For the UI: schedule state + Google Drive connection info.

    Timestamps are returned as naive UTC strings ('YYYY-MM-DD HH:MM:SS') to
    match how sale timestamps are stored and rendered by the frontend.
    """
    settings = get_settings_fn()
    bdir = _backup_dir(source_path)
    marker = bdir / ".last_auto_backup"
    enabled = settings.get("auto_backup_enabled") == "true"
    hours = float(settings.get("auto_backup_interval_hours") or 24)
    backups = list_backups(bdir)
    last_iso = None
    if marker.exists():
        try:
            dt = datetime.fromisoformat(marker.read_text().strip())
            last_iso = dt.astimezone(timezone.utc).replace(
                tzinfo=None).isoformat(timespec="seconds").replace("T", " ")
        except Exception:
            pass
    if last_iso is None and backups:
        dt = datetime.fromisoformat(backups[0]["created_at"])
        last_iso = dt.isoformat(timespec="seconds")
    next_iso = None
    if enabled and hours > 0 and last_iso:
        dt = datetime.fromisoformat(last_iso) + timedelta(hours=hours)
        next_iso = dt.isoformat(timespec="seconds")

    error_marker = bdir / ERROR_MARKER
    last_error = None
    try:
        if error_marker.exists():
            last_error = error_marker.read_text().strip()[:200]
    except Exception:
        pass

    client_id = settings.get("gdrive_client_id", "")

    total_bytes = sum(b["size"] for b in backups)
    free = _free_bytes(bdir)
    return {
        "enabled": enabled,
        "interval_hours": hours,
        "keep": int(settings.get("backup_keep") or 14),
        "storage": {
            "used_human": _human_size(total_bytes),
            "used_bytes": total_bytes,
            "max_mb": float(settings.get("backup_max_mb") or 0),
            "free_human": _human_size(free) if free is not None else "unknown",
        },
        "gdrive": {
            "connected": _gdrive_connected(settings),
            "client_id": (client_id[:12] + "…") if len(client_id) > 12 else client_id,
            "last_error": last_error,
        },
        "last_auto": last_iso,
        "next_auto": next_iso,
        "count": len(backups),
    }


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
