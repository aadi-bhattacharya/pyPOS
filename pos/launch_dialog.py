"""User-visible startup failures for GUI/native builds without a console."""
import logging
import os
import subprocess
import sys
import traceback
from logging.handlers import RotatingFileHandler

from .platform_paths import log_dir


def configure_logging():
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pypos.log"
    root = logging.getLogger("pypos")
    if not root.handlers:
        h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    return path


def show_error(title, message):
    """Do not spawn a command window while showing an error."""
    message = str(message)[:3500]
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    elif sys.platform == "darwin":
        # AppleScript is executed directly, not via a shell. Arguments are not
        # concatenated into the script (prevents AppleScript injection).
        script = 'on run argv\n display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button "OK" with icon stop\nend run'
        try:
            subprocess.run(["osascript", "-e", script, title, message],
                           timeout=20, check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            print(f"{title}: {message}", file=sys.stderr)
    else:
        # GUI dialog if the system has one; terminal remains usable for Linux.
        try:
            subprocess.run(["zenity", "--error", "--title", title, "--text", message],
                           timeout=20, check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            print(f"{title}: {message}", file=sys.stderr)


def unexpected_failure(stage, exc):
    path = configure_logging()
    logging.getLogger("pypos").error("%s failed:\n%s", stage, traceback.format_exc())
    show_error("pyPOS could not start", f"{stage}: {exc}\n\nDetails saved to:\n{path}")


def confirm_stop(title, message):
    """Require an explicit operator confirmation before stopping the till."""
    if sys.platform == "win32":
        import ctypes
        # MB_YESNO | MB_ICONWARNING; YES == IDYES (6)
        return ctypes.windll.user32.MessageBoxW(None, message, title, 0x34) == 6
    if sys.platform == "darwin":
        script = 'on run argv\n set r to display dialog (item 2 of argv) with title (item 1 of argv) buttons {"Cancel", "Stop"} default button "Cancel"\n return button returned of r\nend run'
        try:
            result = subprocess.run(["osascript", "-e", script, title, message],
                                    timeout=30, capture_output=True, text=True)
            return result.returncode == 0 and result.stdout.strip() == "Stop"
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        return subprocess.run(["zenity", "--question", "--title", title,
                               "--text", message], timeout=30,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
