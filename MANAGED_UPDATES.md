# pyPOS managed native deployment and automatic updates

This file documents the **native release path**. Source checkouts (`python main.py`,
`run.sh`, `Start pyPOS.vbs`), `pip install` and old `.pyz` zipapps do not silently
self-update. Only a release built from a `vMAJOR.MINOR.PATCH` GitHub tag contains
a pinned update source. The release repository is stamped from the publishing
GitHub Actions job, not selected by a cashier or a runtime environment variable.

## Install

1. **Take a verified shop database backup**, and finish all open sales.
2. From your GitHub release, download the ZIP matching the OS/CPU (`pypos-windows-x86_64.zip`,
   `pypos-linux-x86_64.zip`, `pypos-macos-x86_64.zip`, or `pypos-macos-arm64.zip`).
   The corresponding `.zip.sha256` is published separately. Check it if installing manually.
3. Extract the **entire** ZIP, including `release-manifest.json`.
4. Windows: double-click `pypos-launcher.exe`. This copies verified executables
   to the standard user application directory and installs **pyPOS** and **Stop pyPOS**
   Start-menu shortcuts. Use those shortcuts on subsequent days. No Python,
   administrator privileges or command prompt are required.
5. macOS: run `pypos-launcher` from the extracted archive (may require Gatekeeper
   approval: builds are currently unsigned/not notarized). It creates
   `~/Applications/pyPOS.app` and `Stop pyPOS.app` shortcuts. Use those afterward.
6. Linux: run `./pypos-launcher` from the extracted directory. The installer
   creates application launchers `pypos.desktop` and `pypos-stop.desktop` under
   `$XDG_DATA_HOME/applications` (or `~/.local/share/applications`).

**Install before the first sale.** The application uses a browser to display the
till UI, but a separate program keeps the local server running. Use **Stop pyPOS**
when closing the till for the day; it requests a graceful stop, never taskkill.
If port 8420 is already occupied, the native application shows an error dialog
and logs the failure; it does not start a second instance or silently connect to
an unrelated server. Windows native PyInstaller binaries are built without a console.
The legacy source `run.bat` briefly opens a command interpreter to delegate to
`Start pyPOS.vbs`; double-click the `.vbs` directly for entirely console-free
source launching.

## Platform paths

| OS | Installed app versions | Database and persistent backups | Logs |
| --- | --- | --- | --- |
| Windows | `%LOCALAPPDATA%\Programs\pyPOS\versions\<version>\` | `%LOCALAPPDATA%\pyPOS\pypos.db` and `backups\` | `%LOCALAPPDATA%\pyPOS\logs\pypos.log` |
| macOS | `~/Library/Application Support/pyPOS/Application/versions/<version>/` | `~/Library/Application Support/pyPOS/pypos.db` and `backups/` | `~/Library/Logs/pyPOS/pypos.log` |
| Linux | `${XDG_DATA_HOME:-~/.local/share}/pyPOS/Application/versions/<version>/` | `${XDG_DATA_HOME:-~/.local/share}/pyPOS/pypos.db` and `backups/` | `${XDG_STATE_HOME:-~/.local/state}/pyPOS/pypos.log` |

`PYPOS_HOME` continues to override the data directory deliberately. Program
files, release ZIPs and expired executables **never** contain the live database.
Native executables use a fixed data location regardless of where the file was
extracted or which working directory the program was launched from.

### Existing source-checkout database

If you ran `python main.py` or `run.bat` from an older checkout, your current
shop may be in `<old checkout>/data/pypos.db`, NOT the OS location above. **Do
not delete the old folder** or start billing in the managed installation until
this has been resolved. With *all* pyPOS processes stopped, run from the
checkout that contains this script:

```
python migrate_legacy_data.py --from /path/to/old/pyPOS/data
```

The migration uses SQLite's online backup API, checks the copy and copies
existing snapshot files. It does **not** delete the original or overwrite an
existing destination database. If there is already a managed-install database,
the tool stops and requires an explicit merge/migration decision: do not delete
or overwrite either database casually. Compare invoices, stock, customers,
loyalty and opening balances before live billing.

## Update and recovery behaviour

- When a managed register starts, the versioned launcher checks GitHub releases
  in a **background thread**. Network failure is logged and **never blocks billing**.
- The release must have a strictly newer `vMAJOR.MINOR.PATCH` tag, a matching
  platform ZIP and SHA-256 sidecar. The archive and each executable are checked
  against SHA-256 digests. Unsupported names and path traversal are rejected.
- Downloads are **staged to a new immutable version directory**. Running code
  and the active database are never replaced. The candidate is selected **only
  when pyPOS is next launched**.
- Before activating a downloaded version, pyPOS uses SQLite's backup API to
  create a `pre-update-*` restore point in the persistent data `backups/` folder.
  If this fails, activation is postponed and the old version starts.
- The previously active executable version is kept. If the new version exits
  unsuccessfully within 15 seconds (except a port conflict), the next launch
  reverts to the previous version and shows an error.
- Once the new version runs for at least 15 seconds, the updater removes other
  obsolete version folders **created by this manager only**. It preserves the
  current, previous and pending versions, unknown folders, and ALL data/backups.
- **Database rollback is not automatic.** If a new version applies a migration,
  reverting just the binary may not undo it. Preserve the `pre-update-*` backup
  and consult your inventory/accounting reconciliation before restoring data;
  an automatic restore could erase newly recorded transactions.
- Old standalone binaries and historical source checkouts elsewhere on disk
  are intentionally **not** deleted: their location and live-data status cannot
  be safely inferred. Retire them manually after migrating and validating data.

SHA-256 sidecars protect against corruption, but they are not independent code
signatures: publisher security depends on the HTTPS connection and GitHub
release permissions. Production Windows binaries should be Authenticode-signed
and macOS builds signed/notarized before wide distribution.

## Release maintainer

GitHub Actions now builds `pypos` (server) and `pypos-launcher` (permanent
forwarder), bundles them with a manifest, uploads platform ZIPs and hashes,
and publishes on `v*` tags. All regression tests and frontend import checks
must pass **before** a release can be created; do not bypass these checks to
hide a regression. Any branch/manual build is intentionally update-disabled.

Manual offline build with installed PyInstaller:

```
pyinstaller --clean --noconfirm pypos.spec
pyinstaller --clean --noconfirm launcher.spec
python package_native.py
```

A manually built archive is usable, but its pinned automatic update identity
is disabled. To enable auto-updates, publish through the tagged workflow.

**Operational limitation:** Windows and macOS native runtime/installer flows
must be acceptance-tested on actual supported hardware, with live scanner and
thermal printer tests. Linux simulation/unit tests do not prove an installer
behaves correctly on another operating system.
