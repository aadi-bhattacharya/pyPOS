import os
import hashlib
import logging
from contextlib import closing
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
REASON_SUFFIX = ".reason"
OUTBOX_NAME = "sync-outbox"
OUTBOX_KEEP = 3
LOG = logging.getLogger("pypos.backups")



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


def create_backup(source_path=None, reason=None):
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
            # SQLite's online backup API already copies a consistent snapshot
            # including committed WAL data. Do NOT force a checkpoint here:
            # other active register readers/writers can make it busy, and the
            # backup should not interfere with live sales.
            dest_conn = sqlite3.connect(str(tmp))
            source_conn.backup(dest_conn, pages=256, sleep=0.1)
            dest_conn.execute("PRAGMA journal_mode = DELETE")  # self-contained file
            result = dest_conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("Snapshot failed its integrity check")
        finally:
            if dest_conn is not None:
                dest_conn.close()

        # On Windows, os.fsync() uses the CRT _commit(), which requires a
        # writable file descriptor. O_RDONLY works on POSIX but raises EBADF
        # on Windows, leaving every backup (including API backups) broken.
        # SQLite has already closed the snapshot before this durability step.
        fd = os.open(str(tmp), os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
        _fsync_dir(bdir)
        if reason:
            # Informational metadata; the snapshot itself is already durable.
            try:
                _atomic_write_text(final.with_suffix(REASON_SUFFIX), str(reason)[:40])
            except OSError:
                LOG.exception("Snapshot is safe, but reason metadata could not be written")

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
            try:
                reason = f.with_suffix(REASON_SUFFIX).read_text(encoding="utf-8").strip()
            except OSError:
                reason = ""
            out.append({
                "reason": reason,
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
    path.with_suffix(REASON_SUFFIX).unlink(missing_ok=True)


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
    """Restore *into* the existing SQLite database without unlinking its file.

    Replacing the pathname while the web server is running split active SQLite
    connections across two distinct inodes. The online SQLite backup API
    coordinates the restore with database locks and preserves a single target
    identity for existing and future connections (including on Windows).
    """
    target = Path(live_path) if live_path else default_database_file()
    validate_database(new_db)
    if not target.exists():
        raise BackupError("Live database is missing; stop pyPOS and recover from a backup")
    try:
        # Keep WAL mode: a connection active at restore time must be able to
        # read the new content instead of continuing to modify an unlinked DB.
        with closing(sqlite3.connect(f"file:{Path(new_db).resolve()}?mode=ro", uri=True,
                                     timeout=10)) as source:
            with closing(sqlite3.connect(str(target), timeout=10)) as dest:
                source.backup(dest, pages=128, sleep=0.05)
                check = dest.execute("PRAGMA integrity_check").fetchone()
                if not check or check[0] != "ok":
                    raise BackupError("Restored database failed integrity check")
    except sqlite3.Error as exc:
        raise BackupError("Could not safely restore the live database; original backup remains available") from exc



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
            elif f.name.endswith((SYNC_SUFFIX, REASON_SUFFIX)):
                if not f.with_suffix(".db").exists():
                    f.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            continue  # file vanished mid-scan — fine
    return removed


def _sync_outbox(source_path=None):
    """Only immutable completed snapshots, NEVER pypos.db or WAL files."""
    return _backup_dir(source_path) / OUTBOX_NAME


def replicate_snapshot(path, source_path=None):
    """Atomically publish an integrity-checked copy for Syncthing Send Only.

    This stages a local outbox; it does not claim Syncthing is connected or
    that an independent machine has received the file.
    """
    path = Path(path)
    if not NAME_RE.fullmatch(path.name):
        raise BackupError("Only finalized pyPOS snapshots may be replicated")
    validate_database(path)
    outbox = _sync_outbox(source_path)
    _ensure_dir(outbox)
    target = outbox / path.name
    if target.exists():
        validate_database(target)
        return target
    if _free_bytes(outbox) is not None and _free_bytes(outbox) < path.stat().st_size + MIN_FREE_MARGIN_BYTES:
        raise BackupError("Not enough disk space to stage a replication copy")
    temp = outbox / (TMP_PREFIX + path.name)
    h_src = hashlib.sha256()
    try:
        with path.open("rb") as src, temp.open("xb") as dest:
            while chunk := src.read(1024 * 1024):
                h_src.update(chunk)
                dest.write(chunk)
            dest.flush()
            os.fsync(dest.fileno())
        h_dst = hashlib.sha256()
        with temp.open("rb") as check:
            while chunk := check.read(1024 * 1024):
                h_dst.update(chunk)
        if h_src.digest() != h_dst.digest():
            raise BackupError("Replicated snapshot checksum mismatch")
        validate_database(temp)
        os.replace(temp, target)
        _fsync_dir(outbox)
        # Bounded outbox: remote mirror contains the latest three snapshots.
        for old in sorted(outbox.glob("pypos-*.db"), reverse=True)[OUTBOX_KEEP:]:
            if NAME_RE.fullmatch(old.name):
                old.unlink(missing_ok=True)
        return target
    finally:
        temp.unlink(missing_ok=True)


def _retention_candidates(backups_list, settings):
    """Keep 10-minute history for 24h, hourly to 7d, daily to 30d.

    Day-end and pre-migration snapshots are protected against *tiering*, not
    against the hard byte/count caps or emergency disk-space pruning.
    """
    if "backup_retention_days" not in settings:
        return backups_list
    days = max(1, int(settings["backup_retention_days"]))
    now = datetime.now(timezone.utc)
    kept = []
    seen = set()
    for index, item in enumerate(backups_list):
        created = datetime.fromisoformat(item["created_at"]).replace(tzinfo=timezone.utc)
        age = max(0., (now - created).total_seconds())
        if index != 0 and age > days * 86400:
            continue
        if age <= 86400 or item.get("reason") in ("day_end", "pre_migration"):
            kept.append(item)
            continue
        if age <= 7 * 86400:
            bucket = ("hour", created.strftime("%Y-%m-%d-%H"))
        else:
            bucket = ("day", created.strftime("%Y-%m-%d"))
        if bucket not in seen:
            seen.add(bucket)
            kept.append(item)
    return kept


def prune_backups(keep, settings=None, bdir=None):
    """Enforce BOTH retention rules. Always keeps at least one snapshot.

    keep     — max number of snapshots (count-based retention)
    settings — when given, also enforces backup_max_mb (size-based budget),
               deleting oldest-first until total size fits the budget.
    """
    keep = max(int(keep or 0), 1)
    removed = 0

    backups_list = list_backups(bdir)
    chosen = _retention_candidates(backups_list, settings or {})
    # The newest snapshot is always protected from normal retention pruning.
    kept_names = {b["name"] for b in chosen[:keep]}
    for b in backups_list:
        if b["name"] not in kept_names:
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


def run_backup_cycle(settings, source_path=None, *, reason=None, upload_gdrive=True):
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

        name = create_backup(source_path, reason=reason)
        pruned = prune_backups(
            settings.get("backup_keep", "14"), settings=settings, bdir=bdir)

        path = bdir / name
        replication_staged, replication_error = False, None
        if settings.get("backup_replication_enabled") == "true":
            try:
                replicate_snapshot(path, source_path)
                replication_staged = True
            except (OSError, sqlite3.Error, BackupError) as exc:
                replication_error = str(exc)[:200]
                LOG.warning("Replication staging failed: %s", exc)

        pushed, err = False, None
        if upload_gdrive and _gdrive_connected(settings):
            pushed, err = push_to_gdrive(path, settings)
            _mark_remote_result(path, pushed, err)
        return {"name": name, "pruned": pruned + emergency_pruned,
                "pushed": pushed, "remote_error": err,
                "replication_staged": replication_staged,
                "replication_error": replication_error}


# ---------------- scheduler ----------------

class BackupScheduler(threading.Thread):
    """Checks every minute whether an auto-backup is due; runs it if so."""

    def __init__(self, get_settings_fn, source_path=None):
        super().__init__(daemon=True, name="pos-backup-scheduler")
        self.get_settings = get_settings_fn
        self.source_path = source_path
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def next_run(self, settings, last_ts):
        minutes = float(settings.get("auto_backup_interval_minutes") or
                        float(settings.get("auto_backup_interval_hours") or 24) * 60)
        if settings.get("auto_backup_enabled") != "true" or minutes <= 0:
            return None
        interval = timedelta(minutes=minutes)
        if last_ts is None:
            return time.time() + 15  # first auto-run shortly after boot
        return last_ts + interval.total_seconds()

    def run(self):
        marker_file = _backup_dir(self.source_path) / ".last_auto_backup"
        # Backwards-compatible: also honor newest manual/auto snapshot on disk.
        while not self._stop_event.wait(60):
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
            return datetime.fromisoformat(backups[0]["created_at"]).replace(
                tzinfo=timezone.utc).timestamp()
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
    minutes = float(settings.get("auto_backup_interval_minutes") or
                    float(settings.get("auto_backup_interval_hours") or 24) * 60)
    hours = minutes / 60
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
    outbox = _sync_outbox(source_path)
    replicated = [p for p in sorted(outbox.glob("pypos-*.db"), reverse=True)
                  if NAME_RE.fullmatch(p.name)] if outbox.is_dir() else []
    return {
        "enabled": enabled,
        "interval_minutes": minutes,
        "interval_hours": hours,  # Compatible with old Settings clients.
        "retention_days": int(settings.get("backup_retention_days") or 30),
        "day_end_enabled": settings.get("backup_day_end_enabled") == "true",
        "pre_migration_enabled": settings.get("backup_pre_migration_enabled") == "true",
        "replication": {
            "enabled": settings.get("backup_replication_enabled") == "true",
            "outbox": str(outbox),
            "staged_count": len(replicated),
            "last_staged": replicated[0].name if replicated else None,
            "remote_verified": False,  # Syncthing remote state is external.
        },
        "keep": int(settings.get("backup_keep") or 500),
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
