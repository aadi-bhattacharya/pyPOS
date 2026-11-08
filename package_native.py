#!/usr/bin/env python3
"""Create verified per-platform release ZIP + SHA-256 sidecar for GitHub.

Input: dist/pypos[.exe] and dist/pypos-launcher[.exe]. The extracted ZIP is
self-installing by double-clicking pypos-launcher; it never bundles shop data.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from pos import __version__
from pos.platform_paths import binary_names, platform_key

ROOT = Path(__file__).resolve().parent


def package(directory=None):
    dist = Path(directory or ROOT / "dist")
    names = binary_names()
    inputs = {names[0]: dist / ("pypos.exe" if sys.platform == "win32" else "pypos"),
              names[1]: dist / ("pypos-launcher.exe" if sys.platform == "win32" else "pypos-launcher")}
    for src in inputs.values():
        if not src.is_file():
            raise FileNotFoundError(f"Native executable missing: {src}")
    files = {name: src.read_bytes() for name, src in inputs.items()}
    mf = {"version": __version__, "platform": platform_key(),
          "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    archive = dist / f"pypos-{mf['platform']}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for name, content in files.items():
            item = zipfile.ZipInfo(name)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100755 << 16
            zf.writestr(item, content)
        zf.writestr("release-manifest.json", json.dumps(mf, indent=2) + "\n")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar = Path(str(archive) + ".sha256")
    sidecar.write_text(digest + "\n", encoding="ascii")
    print(f"Created {archive} ({archive.stat().st_size:,} bytes), SHA-256 {digest}")
    return archive, sidecar


if __name__ == "__main__":
    package()
