"""Per-user shortcuts to the permanent launcher, no administrator privileges."""
import base64
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger("pypos")


def create_shortcut(target, *, system=None, env=None, home=None):
    system = sys.platform if system is None else system
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    target = Path(target).resolve()
    if system == "win32":
        roaming = env.get("APPDATA") or str(home / "AppData" / "Roaming")
        menu = Path(roaming) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        menu.mkdir(parents=True, exist_ok=True)
        # Two explicit actions: start (or open) the register, and safely stop it.
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        for label, arguments in (("pyPOS.lnk", ""), ("Stop pyPOS.lnk", "--stop")):
            link = menu / label
            code = ("$w = New-Object -ComObject WScript.Shell; "
                    f"$s = $w.CreateShortcut({quote(link)}); "
                    f"$s.TargetPath = {quote(target)}; "
                    f"$s.Arguments = {quote(arguments)}; "
                    f"$s.WorkingDirectory = {quote(target.parent)}; "
                    f"$s.IconLocation = {quote(str(target) + ',0')}; "
                    "$s.Save()")
            encoded = base64.b64encode(code.encode("utf-16le")).decode("ascii")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                     "-EncodedCommand", encoded], timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode:
                raise OSError(f"Windows Start-menu shortcut could not be created: {label}")
        return menu / "pyPOS.lnk"
    if system == "darwin":
        app_root = home / "Applications"
        for app_name, arg in (("pyPOS.app", ""), ("Stop pyPOS.app", " --stop")):
            app = app_root / app_name
            executable = app / "Contents" / "MacOS" / "pyPOS"
            info = app / "Contents" / "Info.plist"
            executable.parent.mkdir(parents=True, exist_ok=True)
            if not executable.exists():
                executable.write_text("#!/bin/sh\nexec " + shlex.quote(str(target)) + arg + "\n", encoding="utf-8")
                executable.chmod(0o700)
            if not info.exists():
                info.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>org.pypos.register</string>
  <key>CFBundleName</key><string>pyPOS</string>
  <key>CFBundleDisplayName</key><string>pyPOS</string>
  <key>CFBundleExecutable</key><string>pyPOS</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
''', encoding="utf-8")
        return app_root / "pyPOS.app"
    data = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    escaped = str(target).replace("\\", "\\\\").replace('"', '\\"')
    for label, arg in (("pypos.desktop", ""), ("pypos-stop.desktop", " --stop")):
        desktop = Path(data) / "applications" / label
        desktop.parent.mkdir(parents=True, exist_ok=True)
        if not desktop.exists():
            name = "pyPOS" if not arg else "Stop pyPOS"
            desktop.write_text(
                f"[Desktop Entry]\nType=Application\nName={name}\n"
                f'Exec="{escaped}"{arg}\nTerminal=false\nCategories=Office;\n',
                encoding="utf-8")
            desktop.chmod(0o700)
    return Path(data) / "applications" / "pypos.desktop"
