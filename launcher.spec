"""Build versioned, console-free launcher without Flask or POS web assets."""
import os
HERE = os.path.abspath(SPECPATH)
a = Analysis(
    ["launcher_main.py"],
    pathex=[HERE],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["flask", "waitress", "qrcode", "tkinter"],
    hookspath=[],
    runtime_hooks=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="pypos-launcher",
          console=False, upx=False)
