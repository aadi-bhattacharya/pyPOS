"""Verified, staged Github release downloads. Never touch a running version.

The trust root is the GitHub repository compiled into a tagged build. SHA-256
sidecars prevent partial/corrupt downloads; GitHub TLS and release permissions
are the publisher-authenticity boundary. Do not treat a SHA-256 sidecar as an
independent digital signature.
"""
import hashlib
import io
import json
import logging
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .platform_paths import binary_names, platform_key

LOG = logging.getLogger("pypos")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_DOWNLOAD = 300 * 1024 * 1024


class UpdateError(Exception):
    pass


def validate_version(version):
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise UpdateError("Unsupported release version")
    return version


def newer(candidate, installed):
    return tuple(map(int, validate_version(candidate).split("."))) > tuple(
        map(int, validate_version(installed).split(".")))


def safe_fetch(url, limit, *, opener=None):
    if not url.startswith("https://"):
        raise UpdateError("Updates require HTTPS")
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": "pyPOS-Updater/1", "Accept": "application/vnd.github+json"})
    with opener(req, timeout=10) as response:
        result = bytearray()
        while True:
            block = response.read(min(65536, limit + 1 - len(result)))
            if not block:
                return bytes(result)
            result.extend(block)
            if len(result) > limit:
                raise UpdateError("Release download exceeded the size limit")


def release_assets(repo, key, *, fetch=safe_fetch):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise UpdateError("No trusted release repository is configured")
    info = json.loads(fetch(f"https://api.github.com/repos/{repo}/releases/latest", 512_000))
    version = validate_version(str(info.get("tag_name", "")).removeprefix("v"))
    if info.get("draft") or info.get("prerelease"):
        raise UpdateError("Cannot auto-install draft/prerelease builds")
    basename = f"pypos-{key}.zip"
    assets = {asset["name"]: asset.get("browser_download_url", "")
              for asset in info.get("assets", []) if "name" in asset}
    if basename not in assets or basename + ".sha256" not in assets:
        raise UpdateError("Release does not contain this platform and its checksum")
    for name in (basename, basename + ".sha256"):
        if not assets[name].startswith(f"https://github.com/{repo}/releases/download/"):
            raise UpdateError("Untrusted asset address in release metadata")
    return version, assets[basename], assets[basename + ".sha256"]


def validate_archive(data, expected_version, expected_key, expected_checksum, *, system=None):
    expected_version = validate_version(expected_version)
    if not SHA_RE.fullmatch(expected_checksum):
        raise UpdateError("Invalid release checksum")
    if hashlib.sha256(data).hexdigest() != expected_checksum:
        raise UpdateError("Release checksum does not match")
    return extract_validated_archive(data, expected_version, expected_key, system=system)


def extract_validated_archive(data, expected_version, expected_key, *, system=None):
    """Return verified binaries (name -> bytes); never use extractall()."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) != 3:
                raise UpdateError("Unexpected release archive contents")
            if len(set(archive.namelist())) != 3:
                raise UpdateError("Duplicate release archive entries")
            names = set(binary_names(system=system))
            if set(archive.namelist()) != names | {"release-manifest.json"}:
                raise UpdateError("Invalid release file names")
            for item in archive.infolist():
                # symlinks and very large decompressed files are not allowed.
                mode = (item.external_attr >> 16) & 0o170000
                if mode not in (0, 0o100000) or item.file_size > MAX_DOWNLOAD:
                    raise UpdateError("Invalid file type or size in archive")
            mf = json.loads(archive.read("release-manifest.json"))
            if mf.get("version") != expected_version or mf.get("platform") != expected_key:
                raise UpdateError("Release metadata does not match requested version/platform")
            checks = mf.get("sha256")
            if not isinstance(checks, dict) or set(checks) != names:
                raise UpdateError("Release manifest has missing or extra files")
            result = {}
            for name in names:
                digest = checks[name]
                if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
                    raise UpdateError("Invalid binary checksum")
                contents = archive.read(name)
                if hashlib.sha256(contents).hexdigest() != digest:
                    raise UpdateError("Binary checksum does not match manifest")
                result[name] = contents
            return result
    except (zipfile.BadZipFile, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Corrupt or incompatible release: {exc}") from exc


def install_version(root, version, contents, *, system=None):
    """Immutable per-version installation, atomic directory publication.

    Never delete `root/versions/<active>` or any data directory. Creation is
    staged in the same filesystem, and existing version paths are never written.
    """
    validate_version(version)
    names = set(binary_names(system=system))
    if set(contents) != names:
        raise UpdateError("Refusing to install unexpected application files")
    versions = Path(root) / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / version
    if target.exists():
        # An existing version is immutable: accept it only if byte-identical.
        hashes = target / "sha256.json"
        expected = {n: hashlib.sha256(contents[n]).hexdigest() for n in names}
        if (target.is_symlink() or hashes.is_symlink() or not hashes.is_file() or
                json.loads(hashes.read_text(encoding="ascii")) != expected or
                any(not (target / n).is_file() or (target / n).is_symlink() or
                    hashlib.sha256((target / n).read_bytes()).hexdigest() != expected[n]
                    for n in names)):
            raise UpdateError("Installed version differs from verified release")
        return target
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
    try:
        for name in names:
            file = staging / name
            file.write_bytes(contents[name])
            file.chmod(0o700)
        (staging / "sha256.json").write_text(
            json.dumps({n: hashlib.sha256(contents[n]).hexdigest() for n in sorted(names)}),
            encoding="ascii")
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return target


def atomic_version_file(path, version):
    validate_version(version)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".pypos-state-", dir=path.parent,
                                     delete=False, encoding="ascii") as f:
        temp = Path(f.name)
        f.write(version + "\n")
        f.flush()
        import os
        os.fsync(f.fileno())
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_version_file(path):
    path = Path(path)
    if not path.exists() or path.is_symlink():
        return None
    try:
        return validate_version(path.read_text(encoding="ascii").strip())
    except (UpdateError, OSError, UnicodeError):
        return None


def clean_versions(root, *, keep):
    """Delete only non-active version folders; leave all unrecognized entries.

    This runs only after a successful launch and *never* deletes a database,
    data directory, staged/pending update or unknown user-managed folder.
    """
    root = Path(root)
    versions = root / "versions"
    if not versions.is_dir() or versions.is_symlink():
        return
    protected = set(filter(None, keep))
    protected.update(filter(None, [read_version_file(root / "current.txt"),
                                   read_version_file(root / "previous.txt"),
                                   read_version_file(root / "pending.txt")]))
    candidates = []
    for p in versions.iterdir():
        if p.is_symlink() or not p.is_dir() or not VERSION_RE.fullmatch(p.name) or p.name in protected:
            continue
        candidates.append(p)
    for p in candidates:
        # Filesystem race checked again. Never follow a symlink or remove a
        # folder containing anything other than our two versioned binaries.
        if p.is_symlink() or set(x.name for x in p.iterdir()) != set(binary_names()) | {"sha256.json"}:
            continue
        if any(x.is_symlink() or not x.is_file() for x in p.iterdir()):
            continue
        try:
            shutil.rmtree(p)
            LOG.info("Removed obsolete app version %s", p.name)
        except OSError:
            LOG.warning("Unable to remove old version %s", p.name)


def update_once(root, current, repository, *, system=None, fetch=safe_fetch):
    """Attempt one update; caller runs this off the UI/server thread."""
    key = platform_key(system=system)
    latest, release_url, checksum_url = release_assets(repository, key, fetch=fetch)
    if not newer(latest, current):
        return False
    # Exactly 64 hex characters + optional newline, no arbitrary shell syntax.
    checksum = fetch(checksum_url, 256).decode("ascii").strip()
    if not SHA_RE.fullmatch(checksum):
        raise UpdateError("Release checksum sidecar is invalid")
    data = fetch(release_url, MAX_DOWNLOAD)
    files = validate_archive(data, latest, key, checksum, system=system)
    install_version(root, latest, files, system=system)
    atomic_version_file(Path(root) / "pending.txt", latest)
    LOG.info("Installed %s for next launch; current register remains running", latest)
    return True
