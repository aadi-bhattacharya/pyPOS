import os
import stat
import sqlite3
import sys
from importlib import resources
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_FILE = PACKAGE_DIR / "schema.sql"

_AUTO = object()  # sentinel: detect automatically


def schema_sql() -> str:
    """Load the schema in a way that works from a source checkout, a wheel,
    and a zipapp (where __file__ points inside the archive)."""
    try:
        return resources.files(__package__).joinpath("schema.sql").read_text(
            encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return SCHEMA_FILE.read_text(encoding="utf-8")


def source_checkout_dir():
    """Directory of the source checkout (contains main.py), or None when
    running from an installed package / zipapp."""
    parent = PACKAGE_DIR.parent
    return parent if (parent / "main.py").exists() else None


def platform_data_dir(platform=None, home_dir=None, env=None):
    """The OS-conventional per-user application data directory.

    Linux/BSD:  $XDG_DATA_HOME/pyPOS  or  ~/.local/share/pyPOS
    macOS:      ~/Library/Application Support/pyPOS
    Windows:    %LOCALAPPDATA%/pyPOS  or  %APPDATA%/pyPOS
    """
    platform = (platform or sys.platform) if platform is not None else sys.platform
    env = os.environ if env is None else env
    home = Path(home_dir) if home_dir else Path.home()

    if platform == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        return Path(base) / "pyPOS" if base else home / "AppData" / "Local" / "pyPOS"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "pyPOS"
    # XDG Base Directory spec (Linux, BSD, everything else unix-ish)
    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    return Path(xdg).expanduser() / "pyPOS" if xdg else home / ".local" / "share" / "pyPOS"


def default_data_dir(env=None, cwd=None, checkout=_AUTO, platform=None, home_dir=None):
    """
    Where pyPOS keeps its database and backups, in priority order:

    1. $PYPOS_HOME if set — explicit, wins over everything
    2. a source checkout:  <checkout>/data  (matches the historic layout)
    3. an existing ./pypos-data in the current directory — portable marker;
       a USB stick with the .pyz and a pypos-data folder just works
    4. the platform-standard location (XDG ~/.local/share/pyPOS,
       ~/Library/Application Support/pyPOS, %LOCALAPPDATA%/pyPOS)
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else Path(cwd)
    if checkout is _AUTO:
        checkout = source_checkout_dir()

    marker = cwd / "pypos-data"

    home = (env.get("PYPOS_HOME") or "").strip()
    if home:
        return Path(home).expanduser()
    if checkout:
        return Path(checkout) / "data"
    if marker.is_dir():
        return marker
    return platform_data_dir(platform=platform, home_dir=home_dir, env=env)


def default_database_file(**kw):
    return default_data_dir(**kw) / "pypos.db"


def _resolve(path):
    if path:
        return str(path)
    # Lazy so launchers/tests can set PYPOS_HOME before this matters.
    return str(default_data_dir() / "pypos.db")


# Kept for backward compatibility with code that imported the old constant.
DEFAULT_DATABASE_FILE = _resolve(None)


def connect(db_path=None):
    """Open a connection with sane defaults (Row rows, FK enforcement, busy timeout).

    isolation_level=None gives manual transaction control so service code can
    use explicit BEGIN IMMEDIATE for atomic multi-statement writes.
    """
    path = str(db_path) if db_path else _resolve(None)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path=None):
    """Create the database file and apply the schema. Safe to run repeatedly
    and safe on databases created by older pyPOS versions."""
    path = str(db_path) if db_path else _resolve(None)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        sql = schema_sql()
        # 1) Tables only — gives brand-new installs their skeleton and lets
        #    old databases pick up tables that didn't exist when they were made.
        #    (Indexes below would fail on not-yet-migrated legacy columns.)
        for stmt in sql.split(";"):
            if stmt.strip().upper().startswith("CREATE TABLE"):
                conn.execute(stmt)
        # 2) Bring legacy tables up to current columns.
        migrate(conn)
        # 3) Full schema — indexes, views and triggers are now all valid,
        #    and every statement is IF NOT EXISTS / idempotent.
        conn.executescript(sql)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.commit()
    finally:
        conn.close()
    harden_permissions(Path(path).parent)
    return path


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """Bring databases created by older pyPOS versions up to the current
    schema. Idempotent; only touches columns that are missing."""
    changed = False

    sales_cols = _columns(conn, "sales")
    if "customer_id" not in sales_cols:
        conn.execute(
            "ALTER TABLE sales ADD COLUMN customer_id INTEGER REFERENCES customers(id)")
        changed = True

    item_cols = _columns(conn, "sale_items")
    for col in ("discount_cents", "tax_cents"):
        if col not in item_cols:
            conn.execute(
                f"ALTER TABLE sale_items ADD COLUMN {col} REAL NOT NULL DEFAULT 0")
            changed = True

    if changed:
        conn.commit()


def harden_permissions(directory):
    """Best-effort POSIX permission tightening: only the current OS user may
    read the store's data. No-op on filesystems that don't support chmod."""
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    for pattern in ("*.db", "*.db-wal", "*.db-shm", "*.sqlite"):
        for f in Path(directory).glob(pattern):
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
