"""Per-user locations for managed application binaries, state and logs.

Store data retains the existing pos.db resolution rules in pos.db; it is never
stored under an application-version directory. These paths use only stdlib.
"""
import os
import platform as host_platform
import sys
from pathlib import Path


def install_root(*, system=None, env=None, home=None):
    system = sys.platform if system is None else system
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if system == "win32":
        local = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(local) / "Programs" / "pyPOS"
    if system == "darwin":
        return home / "Library" / "Application Support" / "pyPOS" / "Application"
    data = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(data) / "pyPOS" / "Application"


def log_dir(*, system=None, env=None, home=None):
    system = sys.platform if system is None else system
    env = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    if system == "win32":
        local = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(local) / "pyPOS" / "logs"
    if system == "darwin":
        return home / "Library" / "Logs" / "pyPOS"
    state = env.get("XDG_STATE_HOME") or str(home / ".local" / "state")
    return Path(state) / "pyPOS"


def platform_key(*, system=None, machine=None):
    system = sys.platform if system is None else system
    machine = host_platform.machine() if machine is None else machine
    machine = machine.lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    os_name = {"win32": "windows", "darwin": "macos"}.get(system, "linux" if system.startswith("linux") else None)
    if os_name is None or arch not in {"x86_64", "arm64"}:
        raise ValueError(f"Unsupported platform for managed releases: {system}/{machine}")
    return f"{os_name}-{arch}"


def binary_names(*, system=None):
    system = sys.platform if system is None else system
    suffix = ".exe" if system == "win32" else ""
    return ("pypos-app" + suffix, "pypos-launcher" + suffix)
