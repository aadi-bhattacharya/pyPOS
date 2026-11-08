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
        print("Vendoring Flask, Waitress and offline UPI QR generator...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install",
             "--target", str(staging), "--ignore-installed",
             "--no-compile", "--quiet", "--upgrade",
             "Flask>=3.0", "waitress>=3.0", "qrcode>=7.4"],
            check=True, capture_output=True, text=True)

        # 3. Entry point executed when the .pyz is run. The shebang makes
        #    chmod +x pypos.pyz work directly on unix.
        (staging / "__main__.py").write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "from pathlib import Path\n"
            "portable_data = Path(sys.argv[0]).resolve().parent / 'pypos-data'\n"
            "if portable_data.is_dir():\n"
            "    os.environ.setdefault('PYPOS_HOME', str(portable_data))\n"
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

        # 5. GUI-mode Windows launcher: WScript + pyw/pythonw, no cmd.exe.
        #    Zipapps bundle dependencies, so no pip/venv install is necessary.
        vbs = out_dir / "Start pyPOS.vbs"
        vbs.write_text(
            "Option Explicit\n"
            "Dim shell, fso, folder, cmd, rc\n"
            "Set shell = CreateObject(\"WScript.Shell\")\n"
            "Set fso = CreateObject(\"Scripting.FileSystemObject\")\n"
            "folder = fso.GetParentFolderName(WScript.ScriptFullName)\n"
            + ('cmd = "pyw -3 """ & folder & "\\' + target.name + '"""\n')
            + "On Error Resume Next\n"
            + "rc = shell.Run(cmd, 0, False)\n"
            + "If Err.Number <> 0 Then\n"
            + "  Err.Clear\n"
            + ('  cmd = "pythonw """ & folder & "\\' + target.name + '"""\n')
            + "  rc = shell.Run(cmd, 0, False)\n"
            + "  If Err.Number <> 0 Then\n"
            + "    MsgBox \"Python 3.10+ was not found. Install Python and try again.\", 16, \"pyPOS\"\n"
            + "  End If\n"
            + "End If\n", encoding="utf-8")
        print(f"Also wrote {vbs} — double-click to run without a command prompt.")
        print("Portable data: create a pypos-data folder beside the .pyz. "
              "Otherwise the standard OS data location is used.")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
