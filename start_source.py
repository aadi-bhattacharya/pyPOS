"""Console-free first-run setup for a Windows source checkout.

Run through 'Start pyPOS.vbs', which uses pythonw; no cmd.exe is spawned.
"""
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def alert(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, str(message), "pyPOS", 0x10)


def hidden(*cmd):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(list(map(str, cmd)), cwd=ROOT, creationflags=flags,
                          capture_output=True, text=True, timeout=180, check=False)


def start():
    python = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not python.is_file():
        created = hidden(sys.executable, "-m", "venv", ROOT / ".venv")
        if created.returncode:
            raise RuntimeError("Python environment setup failed:\n" + created.stderr[-1500:])
    check = hidden(ROOT / ".venv" / "Scripts" / "python.exe", "-c", "import flask,waitress,qrcode")
    if check.returncode:
        result = hidden(ROOT / ".venv" / "Scripts" / "python.exe", "-m", "pip", "install", "-r", ROOT / "requirements.txt")
        if result.returncode:
            raise RuntimeError("Could not install Python dependencies:\n" + result.stderr[-1500:])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # No shell, no command prompt. A port conflict is reported by the
    # console-free CLI through a Windows dialog.
    state_dir = ROOT / ".pypos-run"
    state_dir.mkdir(exist_ok=True)
    stop = state_dir / "stop.request"
    stop.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYPOS_STOP_FILE"] = str(stop)
    subprocess.Popen([str(python), str(ROOT / "main.py")], cwd=ROOT,
                     env=env, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    try:
        start()
    except Exception as exc:
        alert(f"pyPOS could not start: {exc}\n\n{traceback.format_exc()[-900:]}")
