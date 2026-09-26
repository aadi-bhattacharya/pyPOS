#!/usr/bin/env python3
"""
Build a single-file portable pyPOS:  python build_portable.py

Produces dist/pypos-<version>.pyz — a zipapp that bundles the app *and*
Flask, so any machine with Python 3.10+ can run it with zero installation:

    python pypos-1.0.0.pyz            # starts the register
    python pypos-1.0.0.pyz --seed-demo

Data lands in ./pypos-data next to wherever you run it (or set PYPOS_HOME),
which makes USB-stick use work naturally.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def version():
    ns = {}
    exec((ROOT / "pos" / "__init__.py").read_text(encoding="utf-8"), ns)
    return ns["__version__"]


def clean_tree(src: Path) -> None:
    """Remove __pycache__ dirs so we don't ship stale bytecode."""
    for pycache in src.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "dist"),
                    help="output directory (default dist/)")
    args = ap.parse_args()

    ver = version()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix="pypos-portable-"))
    try:
        # 1. The application package itself.
        shutil.copytree(ROOT / "pos", staging / "pos")
        clean_tree(staging / "pos")

        # 2. Vendored runtime deps at the archive ROOT — top-level modules of
        #    a zipapp are importable only from its root, not subfolders.
        #    --ignore-installed keeps pip from cross-checking unrelated
        #    packages in the host environment (jupyter etc.).
        print("Vendoring Flask...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--target", str(staging), "--ignore-installed",
             "--no-compile", "--quiet", "--upgrade",
             "Flask>=3.0"],
            check=True, capture_output=True, text=True)

        # 3. Entry point executed when the .pyz is run. The shebang makes
        #    chmod +x pypos.pyz work directly on unix.
        (staging / "__main__.py").write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pos.cli import main\n"
            "sys.exit(main())\n",
            encoding="utf-8")

        target = out_dir / f"pypos-{ver}.pyz"
        if target.exists():
            target.unlink()

        # 4. Zip it up with the shebang preserved so chmod +x makes it
        #    directly runnable on unix; on Windows run `python pypos-x.y.z.pyz`.
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(staging.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(staging))
        import os
        os.chmod(target, 0o755)
        size_mb = target.stat().st_size / (1024 * 1024)
        print(f"Built {target} ({size_mb:.1f} MB)")

        # 5. Windows double-click launcher for people without a Python file
        #    association for .pyz. Prefers the `py` launcher, then `python`.
        bat = out_dir / "Start pyPOS.bat"
        bat.write_text(
            "@echo off\r\n"
            "title pyPOS\r\n"
            "setlocal\r\n"
            "cd /d \"%~dp0\"\r\n"
            "set ARG=\"" + target.name + "\"\r\n"
            "where py >nul 2>nul\r\n"
            "if not errorlevel 1 (\r\n"
            "  py -3 %ARG% %*\r\n"
            "  if errorlevel 1 goto :missing\r\n"
            ") else (\r\n"
            "  where python >nul 2>nul\r\n"
            "  if errorlevel 1 goto :missing\r\n"
            "  python %ARG% %*\r\n"
            "  if errorlevel 1 goto :missing\r\n"
            ")\r\n"
            "goto :eof\r\n"
            ":missing\r\n"
            "echo.\r\n"
            "echo  pyPOS needs Python 3.10+, but none was found on this PC.\r\n"
            "echo  Install it from https://www.python.org/downloads/  then\r\n"
            "echo  double-click this file again.\r\n"
            "echo.\r\n"
            "pause\r\n",
            encoding="utf-8")
        print(f"Also wrote {bat} — double-click it on Windows to start pyPOS.")
        print("Store data appears in ./pypos-data (or set PYPOS_HOME).")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
