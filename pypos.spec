# PyInstaller spec — builds a single-file native binary:
#   pip install flask pyinstaller
#   pyinstaller pypos.spec
# Produces dist/pypos[-<platform>] with Python, Flask and the whole UI baked in.
# No Python installation is needed on the target machine.
import os

block_cipher = None
HERE = os.path.abspath(SPECPATH)  # SPECPATH is injected by PyInstaller

a = Analysis(
    ["main.py"],
    pathex=[HERE],
    binaries=[],
    datas=[
        ("pos/schema.sql", "pos"),
        ("pos/templates", "pos/templates"),
        ("pos/static", "pos/static"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="pypos",
    console=True,
    upx=False,
)
