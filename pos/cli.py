import argparse
import getpass
import ipaddress
import socket
import threading
import webbrowser
import os
import time
from pathlib import Path
import sys
import logging

from .launch_dialog import configure_logging, show_error, unexpected_failure
from .db import connect, init_db
from .demo import seed_demo_data
from . import auth
from . import backups
from . import settings as settings_mod
from .app import create_app


def _is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in ("localhost",)


class PortBusyError(RuntimeError):
    """A live till or another service already owns the requested port."""


def main(argv=None):
    """GUI-capable entry point. Native Windows builds never depend on cmd.exe."""
    frozen = getattr(sys, "frozen", False)
    # pythonw.exe for a source checkout is also console-free on Windows.
    graphical = frozen or (sys.platform == "win32" and sys.stdout is None)
    if graphical:
        logfile = configure_logging()
        # PyInstaller --windowed supplies no stdout/stderr on Windows. Keep
        # diagnostic output in the user-specific log instead of crashing.
        if sys.stdout is None:
            sys.stdout = open(logfile, "a", buffering=1, encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(logfile, "a", buffering=1, encoding="utf-8")
    try:
        return _run(argv)
    except PortBusyError as exc:
        if graphical:
            logging.getLogger("pypos").warning("Port conflict: %s", exc)
            show_error("pyPOS is already running or the port is occupied", str(exc))
        else:
            print(f"pyPOS: {exc}", file=sys.stderr)
        return 73
    except Exception as exc:
        if graphical:
            unexpected_failure("Starting the register", exc)
            return 1
        raise


def _run(argv=None):
    parser = argparse.ArgumentParser(
        prog="pyPOS", description="pyPOS — simple point of sale")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1 — this machine only)")
    parser.add_argument("--lan", action="store_true",
                        help="serve other devices on the LAN (unencrypted HTTP — "
                             "only on a trusted network; implies --host 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8420, help="port (default 8420)")
    parser.add_argument("--db", default=None,
                        help="database file path "
                             "(default: $PYPOS_HOME/pypos.db, ./pypos-data/pypos.db, "
                             "or data/ inside a source checkout)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--seed-demo", action="store_true",
                        help="load sample catalog and sales history on first run")
    parser.add_argument("--set-password", action="store_true",
                        help="set or replace the Settings admin password, then exit "
                             "(web only — the CLI itself never needs a password)")
    parser.add_argument("--version", action="version",
                        version=f"pyPOS {_get_version()}")
    args = parser.parse_args(argv)

    db_path = init_db(args.db)

    if args.lan:
        args.host = "0.0.0.0"
    elif not _is_loopback(args.host):
        parser.error(
            f"--host {args.host} would expose the register to the network.\n"
            "The default is isolated to this computer (127.0.0.1). To serve\n"
            "other devices deliberately, pass --lan and understand that the\n"
            "connection is unencrypted HTTP.")

    if args.set_password:
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            raise RuntimeError("Use the Settings page to change the administrator password. "
                               "The graphical application has no interactive console.")
        _set_password(db_path)
        return 0

    if args.seed_demo:
        conn = connect(db_path)
        try:
            print(seed_demo_data(conn))
        except Exception as e:
            print(f"demo data: {e}")
        finally:
            conn.close()

    def _scheduler_settings():
        conn = connect(db_path)
        try:
            return settings_mod.get_all_settings(conn)
        finally:
            conn.close()

    if not _port_free(args.host, args.port):
        raise PortBusyError(
            f"Port {args.port} is already in use on {args.host}.\n\n"
            "Another pyPOS window may already be running. First check your "
            "browser at http://127.0.0.1:8420/. If pyPOS is not running, "
            "close the program using this port and start pyPOS again.\n\n"
            "For advanced use: python main.py --port 8421")

    scheduler = backups.BackupScheduler(_scheduler_settings, source_path=db_path)
    scheduler.start()

    app = create_app(db_path, extra_hosts=("*",) if args.lan else ())
    url = f"http://{args.host}:{args.port}"


    print(r"  ___               ____  _____  ___ ")
    print(r" | _ \_ _ ___ ___ |   _\|_   _||_ _|")
    print(r" |  _/ '_/ -_|_-< |  _|   | |   | | ")
    print(r" |_| |_| \___/__/ |___|   |_|  |___|")
    print(f"\n  pyPOS running at {url}")
    print(f"  Data: {db_path}")
    if args.lan:
        print("  ⚠ LAN MODE: traffic is unencrypted HTTP — trusted networks only.")
        print("  ⚠ Anyone on this network can reach the register at this address.")
    else:
        print("  Isolated to this computer (127.0.0.1) — no outside access.")
    print("  Press Ctrl+C to stop.\n")

    from waitress import create_server
    from .server_lifecycle import serve_register
    # Bind before opening the browser: it must never open an older instance
    # after this server failed to acquire its listener.
    channel_map = {}
    try:
        server = create_server(app, host=args.host, port=args.port, threads=8,
                               map=channel_map)
    except OSError as exc:
        raise PortBusyError(f"Could not bind {args.host}:{args.port}: {exc}") from exc

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    serve_register(server, channel_map, os.environ.get("PYPOS_STOP_FILE"))
    return 0


def _port_free(host, port):
    """True when we can bind host:port — i.e. no other process owns it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def _set_password(db_path):
    """Set/replace the Settings admin password from the terminal."""
    conn = connect(db_path)
    try:
        stored = settings_mod.get_all_settings(conn).get("admin_password") or ""
        if auth.is_configured(stored):
            current = getpass.getpass("Current password: ")
            if not auth.verify_password(current, stored):
                conn.close()
                raise SystemExit("Wrong current password — nothing changed.")
        while True:
            pw1 = getpass.getpass("New admin password (min {} chars): ".format(
                auth.MIN_PASSWORD_LEN))
            if len(pw1) < auth.MIN_PASSWORD_LEN:
                print(f"Too short — at least {auth.MIN_PASSWORD_LEN} characters.")
                continue
            pw2 = getpass.getpass("Repeat: ")
            if pw1 != pw2:
                print("Passwords don't match, try again.")
                continue
            break
        settings_mod.save_settings(
            conn, {"admin_password": auth.hash_password(pw1)})
        print("Admin password set. The Settings area of the web UI now "
              "requires it (the CLI never does).")
    finally:
        conn.close()


def _get_version():
    from . import __version__
    return __version__


if __name__ == "__main__":
    main()
