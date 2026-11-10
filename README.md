# pyPOS

**An offline-first, browser-based point-of-sale system for local retail stores.**

pyPOS runs a local Python server and opens the register in your browser. It uses SQLite for sales, stock, customers and audit records; ordinary checkout does not need an internet connection. It supports Indian retail workflows, including GST-inclusive MRPs, UPI, cash drawers, multiple MRPs against one EAN, staff accounts and printable receipts.

> **Scope:** pyPOS is a retail billing and operational record-keeping tool, **not** certified GST accounting, GSTR filing, IRN/e-invoicing, a bank settlement system or a substitute for an accountant. Verify your invoice format, tax treatment and reporting obligations before issuing statutory documents.

**Documentation:** This single README covers daily operation, installation, architecture, security, developer workflows and release acceptance. Obsolete patch-specific documents are preserved in Git history; the current code and tests take precedence over historical implementation notes.

## Contents

- [Get started](#get-started)
- [First-time shop setup](#first-time-shop-setup)
- [Daily counter workflow](#daily-counter-workflow)
- [Products, barcodes and stock import](#products-barcodes-and-stock-import)
- [Billing, GST and refunds](#billing-gst-and-refunds)
- [Staff accounts and permissions](#staff-accounts-and-permissions)
- [Backups and store data](#backups-and-store-data)
- [Multiple checkout terminals](#multiple-checkout-terminals)
- [Updates, shutdown and recovery](#updates-shutdown-and-recovery)
- [Troubleshooting](#troubleshooting)
- [For developers](#for-developers)
- [Technical reference](#technical-reference)

## What pyPOS does

| Area | Capabilities |
| --- | --- |
| **Register** | Barcode/SKU scanning, searchable catalogue, quantity editing, discounts, Cash/Card/UPI/split payments, store credit, parked sales, receipt printing |
| **Cash Drawer** | Start Day with opening float; current cash/X report; End Day with physical cash count and printable Z report; session history |
| **Catalogue & Inventory** | Product groups and variants, SKU/EAN, multiple MRP editions for one EAN, positive receipts, audited corrections, CSV/XLSX import, physical stocktakes |
| **Purchasing** | Supplier receipts and cash-out workflows, subject to staff permissions |
| **Customers** | Customer IDs, purchase history, store-credit ledger, loyalty points and audit history |
| **Sales & Returns** | Invoice references, reprints, partial/full refunds, voids, payment and GST exports; original sale-item history preserved |
| **Reports** | Revenue, tender mix, product performance, low-stock alerts, stock valuation and X/Z reporting |
| **Administration** | Named operator PINs, configurable permissions, protected owner settings, branding, backups, audit entries and managed native releases |

## Get started

### Recommended: native release for a shop PC

1. From [GitHub Releases](https://github.com/aadi-bhattacharya/pyPOS/releases), download the ZIP **for your actual operating system and CPU**:
   - `pypos-windows-x86_64.zip`
   - `pypos-linux-x86_64.zip`
   - `pypos-macos-x86_64.zip`
   - `pypos-macos-arm64.zip`
2. Download the matching `.zip.sha256` file if you want to check the archive manually. **Extract the whole ZIP**. Keep `release-manifest.json` beside both native executables.
3. On Windows, run `pypos-launcher.exe` from the extracted folder **once** to install. It installs per-user shortcuts **pyPOS** (Start) and **Stop pyPOS**. Use these shortcuts afterward, rather than running the versioned application executable directly.
4. On macOS or Linux, run the extracted `pypos-launcher` and then use the app launchers it creates. macOS builds may require a Gatekeeper exception because they are not signed/notarized.
5. Open the local register at <http://127.0.0.1:8420/> if a browser did not open automatically.

The two executables have different jobs: **`pypos-app`** runs the Flask/Waitress server and processes transactions; **`pypos-launcher`** installs, starts, stops and updates the versioned application. On Windows, they have `.exe` extensions. A permanent launcher in the per-user install directory forwards to the current verified version. See [Updates, shutdown and recovery](#updates-shutdown-and-recovery).

> **Existing store?** Before installing or upgrading, back up the real SQLite database and confirm where it lives. A source-checkout database and a managed-install database may be **different files**. Starting a blank database does not migrate your existing store automatically.

### Run from source

Requires **Python 3.10+**. No Node.js or frontend build pipeline is required for normal operation.

```bash
python -m pip install -r requirements.txt
python main.py
```

On Linux/macOS you can use `./run.sh`; on Windows use `Start pyPOS.vbs` (or `run.bat`, which delegates to it). These are **source-launch methods**: they do not receive native managed auto-updates.

Other supported distribution methods:

```bash
python -m pip install .                 # or: pipx install .
pypos                                 # installed console entry point

python build_portable.py               # builds dist/pypos-<version>.pyz
python "dist/pypos-VERSION.pyz"          # replace VERSION with the built version
```

The `.pyz` is manually updated; it is not the managed native launcher. For a portable data location, create a `pypos-data/` directory beside it before starting. Never put your only live database in a folder you plan to replace or delete.

### Command-line options

| Option | Meaning |
| --- | --- |
| `--port 8420` | HTTP port (default `8420`) |
| `--host 127.0.0.1` | Bind address (loopback by default) |
| `--lan` | Explicitly listen on the LAN (`0.0.0.0`); **unencrypted HTTP**, trusted networks only |
| `--db PATH` | Use an explicit SQLite database file |
| `--no-browser` | Start the server without opening a browser |
| `--seed-demo` | Seed a new database with demonstration data; **never use on a live register** |
| `--set-password` | Change the administrator Settings password interactively from a console (not from the windowed Windows binary) |
| `--version` | Show installed application version |

## First-time shop setup

1. **Protect your data.** If this store has prior records, make an independent backup first and choose **one authoritative database**. Do not initialize a second database and start issuing overlapping invoice numbers.
2. **Create the owner account.** First-run setup is local-only. Establish the separate administrator Settings password and the owner's personal operator PIN. Do not share the owner PIN with cashiers.
3. **Enter shop details** in Settings: legal/trading name, address, phone, State/UT and applicable GSTIN; optional FSSAI and merchant UPI ID. A valid-looking GSTIN/FSSAI value is not proof that a registration is active.
4. **Configure pricing and receipts separately**: choose GST-inclusive pricing for MRP-based retail (or GST-exclusive when appropriate) under **Settings → Store & tax**, then select printer queue, paper, logo and footer under **Settings → Receipts & printer**. Switching pricing modes does **not** reprice existing catalogue entries.
5. **Review your catalogue**: selling price/MRP, GST rate, HSN/SAC, UQC, SKU, barcode, variants and quantity. Imported missing cost or GST values are *review flags*, not trustworthy zero amounts.
6. **Create staff accounts** under Staff & permissions; assign only the capabilities each person needs. Test the operator login before handing the till to a cashier.
7. **Test on a separate copy**: one sale, its receipt, a partial refund, the correct stock edition, start/end day, backup/restore, scanner and physical printer. Only then operate against live store data.

### Logo and printing

In **Settings → Receipts & printer**, upload PNG/JPEG/WebP logo
(up to **200 KiB**), choose direct Epson TM-T81 printing or browser/PDF
printing, set the 80 mm paper layout and select the OS printer queue. Click
**Print test slip** to verify the actual device. Direct printing uses ESC/POS
through Windows Print Spooler or Linux/macOS CUPS, and does not display a
browser dialog. For configuration, see [Direct Epson TM-T81 receipt printing](#direct-epson-tm-t81-receipt-printing-windows-linux-macos). Printing does not automatically open the cash drawer.

## Daily counter workflow

### Open, sell and close the day

1. Start pyPOS using the installed **pyPOS** shortcut, then sign in with **your own username and PIN**.
2. Go to **Cash Drawer → Start Day** and enter the counted opening cash float. Staff may open/close the day when their individual permission switches allow it; an owner does not need to attend every opening.
3. Go to **Register**. Scan an EAN/SKU or search products. For an EAN shared by different MRPs, **select the edition printed on the physical item**; stock is tracked separately by edition.
4. Press **F4 / Charge**, choose cash, card, UPI, store credit or a valid split, and confirm the customer's payment. pyPOS checks the real open cash session **on the server** when recording a sale. Do not enter the sale twice if a browser response is delayed.
5. Use **Cash Drawer** to inspect the current X report and cash-in-hand information. Expected cash is the **calculated** opening float plus cash received, less cash refunded/payouts; it is *not* the physical count.
6. At closing, complete pending transactions, count the real drawer, select **End Day**, enter the counted cash, review the difference and print/retain the Z report. **End Day closes the cash session; it does not stop the server.**
7. Use the installed **Stop pyPOS** shortcut for graceful application shutdown after transactions and receipts are finished. Closing the browser tab alone **does not stop the server**.

**Switch operator without stopping the app:** Select **Log out / Switch** at the **bottom of the left sidebar**, then sign in with another account. This clears the operator session and any Settings unlock. The control remains visible even if the Staff management page is hidden.

### Register keyboard shortcuts

| Key | Action |
| --- | --- |
| `F2` or `/` | Focus product/barcode search |
| `Enter` | Add exact SKU/EAN match or open the correct MRP choice |
| `F4` | Open payment |
| `F6` | Park/recall sale |
| `+` / `-` | Adjust selected cart line |
| `Esc` | Close dialog / clear search |
| `?` | Keyboard help |

## Products, barcodes and stock import

- **Separate SKU, barcode and MRP.** A SKU identifies a product/price edition; the manufacturer's EAN can be shared by editions with **different** MRPs for the **same** size/style/colour. Scanning that EAN asks the cashier to choose the actual package MRP. Do not guess the newest edition from the higher price.
- **Stock and refunds follow the selected edition's product ID.** A refund restocks the recorded edition, not every SKU with that barcode.
- **Staff-created products** can be saleable immediately, but are marked for manager review. Unknown cost is recorded as a placeholder, not asserted purchase cost. Existing price/cost edits and stock decreases are permission-gated.
- **Stock movements use positive quantities in the normal receive-stock flow.** Shrinkage is an explicit, reasoned operation requiring the corresponding permission; no ordinary one-click `-1` stock button exists.
- **Import CSV (UTF-8) or XLSX** from **Catalog → Import stock**, with a preview before commit. Use the supplied template and `attr:` columns for editable attributes such as size, fabric or source references.

| Import mode | Existing SKU | Repeating the file |
| --- | --- | --- |
| Create new / skip existing | Leave existing rows unchanged | Generally safe for existing rows |
| Add stock | Increase matched SKU quantity; do not replace its price | **Adds the quantity again** |
| Add stock + merge (privileged) | Add quantity and update explicitly supplied nonblank fields | **Adds the quantity again** |

**A stock snapshot is not a delivery.** Never use additive import simply to load an older counted inventory snapshot; doing so double-counts stock. Check SKU/EAN/MRP/variant conflicts and reconcile rows with missing prices, GST, purchase cost or HSN before taking payment. Imports do not infer an item's correct tax rate from its MRP or product name. Review flags such as **Price Review Required**, **GST Review Required** and **Cost Review Required** must be resolved with verified information. Certain review flags are enforced at checkout; an unreviewed GST rate is not equivalent to GST exemption.

## Billing, GST and refunds

- Catalogue prices can be **inclusive** or **exclusive** of GST. The upgrade default for older installations is **tax-exclusive**. Confirm which mode matches your SKU/MRP values.
- A sale records historical product description, SKU, price, discount, tax details, store identity and applicable customer billing details. Editing catalogue or Settings later does not recalculate completed bills; historical records may have fewer captured fields.
- For registered shops, intra-State supply can show CGST + SGST/UTGST and inter-State supply IGST. The user selects supply type/place of supply; pyPOS **does not determine legal place of supply or rate automatically**.
- Invoice numbers use an April–March financial-year sequence, for example `26-27/000001`. Reusing the same series across separate databases is unsafe. Voided invoices do not reuse their number.
- Discounts and split tenders are checked on the server. Money and tax allocations are rounded to paise with controlled arithmetic. Card/UPI overpayment is rejected; eligible cash tender may produce change.
- **UPI QR and POS reference are not payment confirmation.** The cashier must verify payment in the merchant bank/payment application. A bank UTR is distinct from pyPOS's own reference; a nonempty UTR cannot be reused for another recorded UPI payment. Changing amount/method requires reconfirming payment.
- **Returns and store credit:** Open **Returns & refunds**, find the original invoice, choose the returned quantities and a reason, then select **Original payment methods** or **Customer store credit**. Credit is issued immediately to a named customer and recorded in their credit ledger, not paid through a bank. If the original sale was anonymous, select or create a customer before issuing credit. The original invoice remains unchanged.
- **Exchanges, including anonymous walk-ins:** Select **Exchange** beside the original invoice, choose returned quantities and replacement products using SKU/barcode search. pyPOS calculates the original return value from the saved receipt and charges only the extra difference through Cash/Card/UPI. **No name, phone or customer account is required** for a permitted anonymous equal-value or higher-value exchange. The return is applied as a **direct exchange offset**, not as a cash refund or a temporary customer-credit wallet. If a customer is selected and store credit is permitted, the return uses that customer's existing wallet ledger; unused value from a cheaper replacement remains as credit. Both linked invoices and all their stock movements commit **atomically**; failed payment or insufficient stock records neither side. The operator must have both refund and checkout permissions.
- **Return rules (administrator Settings):** Toggle direct cash/card/UPI refund recording, named-customer store credit, exchanges, anonymous exchanges and (separately) forfeiture of an unused exchange difference. Defaults preserve existing refund methods and allow anonymous exchanges; **forfeiture is OFF by default**. If an anonymous replacement is cheaper, pyPOS blocks it unless the operator selects a customer eligible for credit or the administrator has enabled forfeiture. Any forfeiture requires an explicit per-exchange acknowledgement, is shown on the linked receipt and in the refund export, and creates **no claimable store credit or payment**. Verify the legality and tax/accounting treatment of that shop policy before enabling it. The application does not provide anonymous bearer-credit vouchers.
- Each exchange creates **two linked documents**: an operational refund receipt and a **new, separately numbered sales invoice**. These must be retained for accounting and GST review. A pyPOS refund receipt **is not a statutory GST credit note**. Cash/card/UPI refunds still require external payout and reconciliation; the application does not send money to payment gateways.
- Each new refund has its own reference, e.g. `26-27/R000001-000123` (FY refund sequence plus original internal sale ID). Historical refunds keep their original identifiers; a **refund receipt is not a statutory GST credit note**.
- A sale with any completed refund cannot also be voided. Customer loyalty earned on refunded value is reversed proportionally; a past redemption can temporarily leave the points balance negative.
- Loyalty defaults are **1 point per full ₹100 of eligible spend** and **₹1 of store credit per redeemed point**, subject to administrator-configured policy. Store credit settles a payment; it does not silently change an invoice's taxable amount. Confirm the accounting treatment with your GST advisor.

CSV exports are operational audit inputs. A refunded sale has a **negative net effect**, while voided invoices have zero net effect; do not sum an unsigned `total` column to calculate net sales. CSV timestamps are labelled UTC; receipt times display IST. X/Z cash reports are **not** GST returns or audited financial statements.

## Staff accounts and permissions

- **Owner/administrator:** protected administrative functions and individual staff permissions. Staff administration also requires unlocking Settings with the separate administrator password.
- **Staff:** individual permission switches. The **Everyday staff** and **Supervisor** presets are starting configurations, not hardcoded shared roles. By default ordinary staff may bill, open/close the day, add new products, receive/import positive stock, manage customers within granted limits and view reports.
- **High-risk operations** (stock reductions, refunds/voids, cash payouts, customer-credit changes, financial exports and privileged purchase actions) require explicit grants or owner access. Staff cannot acquire owner-only Settings/backups/staff-management powers through a switch.
- Permissions are enforced in API routes as well as the UI. Revoking a permission or disabling an operator is effective on subsequent requests. Every operator should use their **own** PIN for accountability.

## Backups and store data

### Where the database is stored

| Installation | Typical database directory |
| --- | --- |
| Managed Windows release | `%LOCALAPPDATA%\pyPOS\` |
| Managed macOS release | `~/Library/Application Support/pyPOS/` |
| Managed Linux release | `${XDG_DATA_HOME:-~/.local/share}/pyPOS/` |
| Source checkout | `<repository>/data/` |
| Explicit override | `PYPOS_HOME/` or the path passed with `--db` |
| Portable marker | `pypos-data/` alongside the chosen working-directory launch |

The usual database filename is **`pypos.db`**. Backups live in the sibling **`backups/`** directory; logs are under the platform's pyPOS logs/state path. The managed native launcher deliberately uses the per-user data directory, **not the extracted ZIP folder**, so changing application versions should not move customer or sales records.

**Migrating from a source checkout:** With every pyPOS process stopped, inspect your old `<checkout>/data/pypos.db`. From the checkout with the migration script, run:

```bash
python migrate_legacy_data.py --from /path/to/old/pyPOS/data
```

The script uses SQLite's backup API and will **not overwrite** an existing managed-install database. If both databases contain transactions, stop: **do not delete either, replace one with the other or start billing until the records are reconciled**.

### Local and off-device backups

In **Settings → Backups & recovery**, automatic backups are **enabled every 10 minutes** by default. The 30-day history keeps all snapshots from the last 24 hours, an hourly snapshot for the next six days, then a daily snapshot through 30 days. Additional snapshots are made **after End Day commits** and **before the first schema migration for a version**. Each snapshot is a SQLite online backup including committed WAL entries, checked for integrity and atomically published. Pre-migration snapshots are created before modifying existing tables; an unsuccessful safety backup prevents that migration.

**Capacity is not unlimited.** The default 500-snapshot count ceiling and 4 GiB disk budget may shorten the 30-day history for larger stores. The newest local snapshot is protected against ordinary pruning, and backups are skipped safely when there is too little disk space. Verify the visible snapshot history regularly. Day-end backup failures report a warning while preserving the already-closed day.

**Syncthing replication support (enabled by default):** pyPOS stages the **latest three completed snapshots only** in the `backups/sync-outbox/` subfolder and verifies each copied file. Install/configure Syncthing yourself; in Syncthing, share *that folder* from the shop server as **Send Only**, and configure a second PC/NAS as **Receive Only**. Syncthing copies and deletes staged files according to its own sync status. pyPOS cannot verify that the other device received them: the UI reports only *locally staged* snapshots. Test an actual restore on the second device; maintain independent historical/off-site backups and protect customer data in transit and at rest. **Never synchronize live `pypos.db`, `pypos.db-wal` or `pypos.db-shm`**. These are not independently safe replicas.

The outbox itself is a second local copy, *not* an independent off-site backup until Syncthing has delivered it. You can disable outbox staging, day-end backups or pre-migration backups separately; disabling pre-migration protection increases upgrade risk. For a proper disaster recovery plan maintain an additional off-device and/or cloud backup and periodically restore to a test database. Restore the live database only with operators stopped and the original protected.

For optional Google Drive sync, enable the **Google Drive API**, create a Google OAuth web-client credential with redirect URI `http://127.0.0.1:8420/gdrive/callback`, then enter its client ID/secret in **Settings → Backups & recovery** and authorize the chosen Google account. The app requests the limited `drive.file` scope. OAuth credentials and access tokens must remain private. Sync is optional; sales stay local if the network or Drive is unavailable.

## Multiple checkout terminals

Use **one pyPOS server as the authoritative register**. On the computer holding the real shop database, sign in as the administrator and enable **Settings → App & updates → Shared register (multiple terminals) → Allow other terminals on this trusted LAN**. Save, **stop pyPOS gracefully and start it again**. This setting makes managed native shortcuts bind to the LAN; `python main.py --lan --no-browser` is an alternative for a source installation. By default, LAN access is **off**. The host PC must remain running and connected.

On each additional terminal, open `http://SERVER-IP:8420` in a browser, replacing `SERVER-IP` with the host PC's **trusted LAN address** (such as `192.168.x.x`). Each operator logs in with their own PIN. Allow inbound TCP port `8420` through the **host's firewall only for trusted devices**. Every connected browser uses the *same* sales, invoice sequences, cash-session record and stock in the central SQLite database. **Do not run another independent pyPOS database on the other terminals.** The existing Cash Drawer represents a *shared store-wide day*, not separate physical tills per counter; use a single reconciled cash drawer until per-terminal sessions exist. Direct thermal receipts print through the **server computer's configured printer**, not a separate printer attached to each browser-only terminal.

**Security:** pyPOS LAN mode uses **unencrypted HTTP**. Credentials and transaction data are therefore visible to attackers on an untrusted network. Use only a physically isolated/trusted LAN, ideally wired, or place the service behind an appropriate HTTPS/TLS gateway. Do not expose TCP `8420` to the Internet, forward the router port, or use public/shared Wi-Fi. No cloud service is required for local multi-terminal billing, but if the server PC or LAN is unavailable the other terminals cannot check out until it recovers.

Syncthing is for **completed backup snapshots only**. It is *not* a synchronization mechanism for multiple independently writable tills: simultaneous SQLite writers on separate replicas would generate conflicts and diverging invoice/stock/credit records.

## Updates, shutdown and recovery

**Managed native releases only:** When **Settings → App & updates → Enable automatic software updates** is on (the default), the launcher checks the pinned GitHub Releases source in the background. A strictly newer stable release with a matching platform ZIP, manifest and SHA-256 checksums is downloaded and staged separately. **It is not activated during an open session or while the app is running**; the next valid launcher start selects it after making a pre-update SQLite snapshot. Network failure does not block ordinary sales.

To **opt out**, sign in as owner, open **Settings → App & updates**, switch off **Enable automatic software updates**, then select **Save update preference**. This preference is stored in the persistent shop settings database, not in browser storage. The launcher stops scheduling automatic checks/downloads and will not activate an already-staged `pending.txt` release at restart while opted out. The running version, database and staged files remain untouched. Re-enabling allows a verified staged release to activate at the next normal launch; to update manually instead, download an official release for your OS from [GitHub Releases](https://github.com/aadi-bhattacharya/pyPOS/releases) and install it deliberately. Turning auto-updates off also disables unattended security fixes.

**Settings organization:** use the seven categories: **Store & tax**, **Receipts & printer**, **Appearance**, **Customers & returns**, **Staff & security**, **Backups & recovery**, and **App & updates**. Each group has its own Save control. Printer preferences no longer silently save GST pricing changes.

The managed launcher retains the previous version for recovery. A failed new binary can trigger **binary** rollback, but **schema/data rollback is not automatic**: restoring yesterday's database could lose today's sales. Never treat a version rollback as authorization to overwrite the transaction database.

For a normal shutdown: **finish the sale → End Day as appropriate → Stop pyPOS → wait for the server to exit**. A plain browser close is not shutdown. The launcher uses a per-installation operating-system instance lock and a graceful stop request; it does not kill every process whose filename resembles pyPOS. The permanent installed launcher is refreshed from a verified release on supported updates.

> **One-time legacy cleanup:** Older releases could leave stale application processes. After ensuring no transaction is in progress, confirm the active installation and its database path before removing any orphaned process. Several Task Manager entries may also be normal subprocesses of one packaged executable. **Do not blindly use `taskkill /F /IM ...` or delete app-data folders.**

Auto-updates require a release published by the tagged GitHub Actions build. **Pushing to `main` alone does not update the cashier's executable.** Source checkouts, pip installs and `.pyz` builds remain manual-update installations.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Register says **Start Day** even though the drawer is open | Hard-refresh the browser (`Ctrl+Shift+R`). Verify the *installed* version was activated after stopping/restarting. While signed in, inspect `http://127.0.0.1:8420/api/cash-sessions/current`: `null` means the **server** sees no open session; an object with `closed_at: null` means it does. Do **not** open duplicate days or bypass server checkout checks. |
| Closing the browser doesn't close pyPOS | Expected. Use **Stop pyPOS** from the installed app shortcuts. |
| Stop shortcut does nothing | Verify you're using the **managed installation's** Stop shortcut, not a source-checkout `.vbs`. Check the application log; do not force-kill a process during a sale. |
| Several pyPOS tasks in Task Manager | Confirm which process owns port `8420` and the installed app path; one launcher can use subprocesses. Do not assume every process is a separate live register. |
| Login as admin while staff is signed in | Click **Log out / Switch** at the bottom of the sidebar; sign in with the owner's own PIN. Unlock Settings separately if required. |
| Barcode offers several prices | Choose the price printed on that item. Each MRP edition has separate stock. |
| SKU cannot be sold | Review price/GST flags, physical stock, role permission and actual open cash session. |
| Old stock suddenly doubled | Check whether a historical snapshot was re-imported in **Add stock** mode. Stop further imports and reconcile before corrections. |
| UPI QR displayed but payment uncertain | Verify actual credit in the merchant payment app; QR display/reference generation proves nothing. |
| Black/blank screen after an update | Fully stop/restart the installed app and force-reload the browser. Confirm release version and inspect logs before deleting any files. |

## For developers

### Repository layout

```text
main.py                 source entry point -> pos.cli
launcher_main.py        managed native launcher entry point
pos/app.py              Flask HTTP routes, API and SPA delivery
pos/auth_pages.py       staff login and owner onboarding HTML builders
pos/cli.py              CLI, startup, server binding
pos/server_lifecycle.py graceful Waitress shutdown
pos/managed_launcher.py version selection, installation and supervision
pos/instance_lock.py   per-installation OS process lock
pos/update_core.py     verified update checking, download and staging
pos/db.py, schema.sql   SQLite schema, connections and idempotent migrations
pos/sales.py            sales, tenders, void/refund transactions
pos/catalog.py          product/variant and stock operations
pos/cash_sessions.py    Start Day, X/Z reports and End Day
pos/staff.py            user accounts and capability checks
pos/printer_transport.py  native RAW spool submission (Windows/CUPS)
pos/thermal_print.py    ESC/POS receipt renderer and print-job ledger
pos/static/js/          browser ES-module UI, no frontend compilation
pos/static/js/views/settingsBackups.js      backup configuration UI
pos/static/js/views/settingsImageTools.js   image optimization UI
pos/static/js/views/settingsReturnRules.js return/exchange policy UI
pos/templates/          application and login HTML
tests/                  Python tests and optional UI smoke checks
pypos.spec              PyInstaller app configuration
launcher.spec           PyInstaller launcher configuration
package_native.py       platform release ZIP + checksums
migrate_legacy_data.py  guarded migration from an older source checkout
```

### Test and build

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
node tests/check_frontend_modules.mjs

# Optional browser integration tests; install dev-only JS dependencies first:
npm ci
PYPOS_HOME=/tmp/pypos-ui-test python main.py --no-browser --seed-demo
# Linux/macOS example: use an empty test-only directory. In another terminal:
node tests/frontend_test.mjs

# Build native binaries on the target operating system:
python -m pip install pyinstaller
pyinstaller --clean --noconfirm pypos.spec
pyinstaller --clean --noconfirm launcher.spec
python package_native.py
```

The workflow in `.github/workflows/build-binaries.yml` runs Python and JS checks before packaging Linux x86_64, Windows x86_64, macOS x86_64 and macOS ARM64. Tagged `vMAJOR.MINOR.PATCH` builds publish archives and SHA-256 sidecars; manual builds intentionally have automatic updates disabled.

**Release acceptance is more than green unit tests:** verify the Windows/macOS launcher lifecycle, a real cash session and sale, a printed receipt, exact MRP edition stock, staff permissions, refund reconciliation, backup/restore and data migration against a copy of the shop database. [Technical reference](#technical-reference) includes internal invariants, security boundaries and a release checklist.

## Security and legal limits

Default loopback binding protects against casual LAN access. The API uses host/origin checks, session-based staff authentication, permission enforcement and restricted settings operations. Optional `--lan` is **unencrypted HTTP**, not suitable for exposure to the public Internet; use a trusted and segmented local network only. PINs and admin credentials must remain private. Reports, refund references, loyalty balances, cash drawer entries and UPI references are operational records that still require physical/bank reconciliation and, where applicable, statutory accounting.

**License:** MIT (per `pyproject.toml`).

## Direct Epson TM-T81 receipt printing (Windows, Linux, macOS)

pyPOS can **automatically queue completed sale, refund and exchange receipts** on the
server machine's thermal printer without showing a browser print dialog.
The original Epson TM-T81 accepts ESC/POS; it prints 512 dots (72.2 mm) across
nominal 80 mm receipt rolls. Epson specifies **79.5 ± 0.5 mm** physical
roll width; rolls marketed as "78 mm" should be measured and test-fed before
assuming they meet this tolerance.
The printer must be connected and installed in the **operating system print
service** before pyPOS can send jobs to it.

1. Install the correct Epson printer on the computer running the pyPOS server.
   On Windows, make the Epson printer available in **Printers & scanners** (the
   application sends `RAW` bytes via the Windows Print Spooler). On Linux or
   macOS, configure a CUPS queue that passes ESC/POS data **unmodified**, and
   verify that `lp -o raw -d QUEUE` works. A driver queue that rasterizes PDF
   or filters arbitrary bytes will not work with this transport.
2. In **Settings → Receipts & printer**, choose **Epson TM-T81 (direct,
   no dialog)**, **80 mm** paper, and enter the installed **printer queue name**.
   Leave it blank to use the OS default printer. Save settings before testing.
3. Click **Print test slip** and physically verify paper width, logo, legible
   text and cutter behavior. If it does not print, check the OS queue or use
   **Browser / PDF** mode as a deliberate fallback.
4. Enable **Automatically print completed sales, returns and exchanges**.
   After a completed transaction, the receipt is submitted once. The
   **Print** button always requests a separately audited reprint.

The Windows/macOS/Linux **server** selects the queue. Browser clients connected
from another PC over LAN do not print on their own machine unless the printer
is shared/configured as an OS queue on the server. Queued/spooled means the OS
accepted the job, **not** that paper was successfully delivered. A failed
print never rolls back a sale; the UI reports an error and permits a manual
reprint. Cash-drawer opening is not triggered by receipt printing.

Browser/PDF printing remains available as an **opt-in manual** fallback for A4
or unsupported hardware; it is not used for automatic receipts. Barcode-label
sheets are a separate workflow and still use browser printing.

---

## Technical reference

This section is for maintainers, auditors and operators responsible for migration or recovery. It documents the invariants behind the interface. The active source, database schema and release tests remain authoritative if historical details differ.

### Architecture and execution model

```text
OS Start shortcut
    |
    v
Permanent launcher -> verified, versioned launcher
                          |-- OS instance lock
                          |-- optional GitHub release check / staged download
                          |-- starts versioned pypos-app
                                     |
                                     v
                          Flask + Waitress (127.0.0.1:8420)
                           |          |
                     browser UI    /api/*
                       (ES modules)  |
                                     v
                        SQLite (WAL, transactions)
                           |-- sales / sale_items / payments
                           |-- products / barcodes / stock movements
                           |-- cash sessions / purchasing / stocktakes
                           |-- customers / loyalty / store credit
                           |-- staff sessions / permissions / audit
                           +-- online backup snapshots
```

- `main.py` enters `pos.cli`; `launcher_main.py` enters `pos.managed_launcher`. A source-checkout server and a managed native server are **different launch modes** and can use different data paths.
- `pos/app.py` serves the Flask routes and bundled single-page app. `pos/static/js/app.js` provides routing; views live under `pos/static/js/views/`. Browser modules are loaded natively, with **no frontend compilation or runtime CDN**.
- `pos/db.py` opens SQLite with `foreign_keys=ON`, a busy timeout and explicit transaction control. On startup, `init_db()` applies idempotent schema migrations and enables WAL. High-risk checkout/stock operations use write transactions and database invariants rather than trusting UI calculations.
- `pos/schema.sql` defines core tables, indexes and history triggers. Modules such as `sales.py`, `catalog.py`, `customers.py`, `cash_sessions.py`, `staff.py`, `purchasing.py`, `stocktake.py` and `backups.py` own domain logic.
- Browser state is **not** the accounting system of record. The server verifies stock, payments, permissions, open cash session, discount and quantities before committing a sale. HTTP operations must be safe against duplicate clicks, stale responses and unauthorized clients.

#### Runtime requirements and packaging

Python **3.10+**, Flask **3.0+**, Waitress **3.0+**, and `qrcode` **7.4+** are declared in both `requirements.txt` and `pyproject.toml`. The native packaging workflow uses Python **3.12** and PyInstaller. `npm`/jsdom are optional **development-test tooling only**.

| Distribution | Python on shop PC? | Automatic native updates? | Startup |
| --- | --- | --- | --- |
| Tagged native ZIP | No | Optional (on by default; owner can opt out) | Installed pyPOS shortcut / managed launcher |
| `.pyz` zipapp | Yes | No | `python pypos-<version>.pyz` |
| pip/pipx | Yes | No | `pypos` |
| source checkout | Yes | No | `python main.py`, `run.sh` or Windows VBS |

Native binaries are built **on each target OS/CPU**, not cross-compiled from one Linux job. Manual, branch and ad-hoc builds intentionally lack a pinned update identity. The launcher reads `auto_updates_enabled` from the persistent SQLite settings table in the managed install data directory; missing preferences default to enabled, but unreadable/corrupt settings prevent update activity. The opt-out is checked before network use, after a download and again before staged activation. Manual release installation is unaffected.

### Data boundaries and migrations

#### Canonical paths

| Environment | Program files | Persistent database |
| --- | --- | --- |
| Managed Windows | `%LOCALAPPDATA%\Programs\pyPOS\versions\<version>\` | `%LOCALAPPDATA%\pyPOS\pypos.db` |
| Managed macOS | `~/Library/Application Support/pyPOS/Application/versions/<version>/` | `~/Library/Application Support/pyPOS/pypos.db` |
| Managed Linux | `${XDG_DATA_HOME:-~/.local/share}/pyPOS/Application/versions/<version>/` | `${XDG_DATA_HOME:-~/.local/share}/pyPOS/pypos.db` |
| Source checkout | `<checkout>/pos/` and scripts | `<checkout>/data/pypos.db` |
| Explicit override | unchanged | `PYPOS_HOME/pypos.db`, or `--db PATH` |

Managed logs: Windows `%LOCALAPPDATA%\pyPOS\logs\pypos.log`; macOS `~/Library/Logs/pyPOS/pypos.log`; Linux `${XDG_STATE_HOME:-~/.local/state}/pyPOS/pypos.log`. Backups live next to the active database in `backups/`. An explicit `PYPOS_HOME` overrides the default data directory; use it intentionally and consistently.

**Migrate carefully.** A checkout's `data/pypos.db` is not silently picked up by a newly installed native release. With **all** processes stopped, use `python migrate_legacy_data.py --from /path/to/old/pyPOS/data`. It copies through SQLite's online backup API, checks results and copies snapshots; it does not erase the original or overwrite an existing managed database. If the destination already contains sales or stock, **do not choose one to delete**: reconcile invoices, quantities, customer balances and invoice sequences first.

Avoid raw-copying a live `pypos.db` while SQLite may have uncheckpointed `-wal` data. Prefer a verified online snapshot. If doing an offline directory copy, stop all database writers first and include relevant SQLite sidecar files. Neither a binary rollback nor a backup restore automatically merges later transactions.

#### Historical migration decisions

The schema evolves additively and preserves existing IDs/audit records wherever possible:

- New invoice metadata stores historical shop and buyer billing details, per-line HSN/UQC, recorded tax mode/breakdown and April–March FY sequences for **new** bills. Older records are not retroactively given invented invoice numbers or classifications.
- Product `barcode_price_options` records additional EAN-to-product associations for distinct MRP editions without rebuilding the original unique primary barcode field. Historical sale items retain the actual product ID.
- Cash session attribution uses explicit `cash_session_id` for new transactions; older events may still require timestamp-based interpretation. Ambiguous historical same-second events cannot be reconstructed by guessing.
- Loyalty adds a customer point balance and an append-only `loyalty_events` journal; store credit retains its own ledger.
- `staff_permissions` holds individually granted booleans without rebuilding the existing `staff_users` table. Legacy `cashier`/`manager` database roles are preserved for compatibility and mapped to the current staff UI/presets. A historic staff user lacking a saved permission record inherits the nearest legacy preset until explicitly updated.
- Refund numbering adds `sales.refund_number` and `refund_sequences`. Older refunds keep their original identifiers; new partial refunds get distinct serials.
- Migrations and old source data must **never** assert that missing cost/GST information is verified zero. Where the legacy schema requires numeric zero, mark and review it as a placeholder.

### Sales invariants and accounting semantics

#### Checkout and money

1. Product quantities must be positive integers within bounds; booleans, fractional values and silent truncation are rejected.
2. Non-finite numbers, malformed money and fractions smaller than ₹0.01 are rejected for day-opening floats, day-end counts and tenders instead of silently rounded.
3. Catalogues have a configured inclusive/exclusive tax-price mode. Changing it does not edit price records or historical invoices.
4. Discounts must use a recognized mode and valid bounds. The server allocates line discounts and tax in paise, using recorded sales-line values for later refunds.
5. Card/UPI cannot overpay the amount due; cash may produce calculated change. Cash, noncash tenders and store-credit portions must reconcile to the invoice.
6. Sale requests support idempotent checkout retry keys so accidental retransmission does not deduct the same stock twice. Atomic inventory checks prevent overselling under concurrent requests.
7. A genuinely closed cash session blocks checkout at the **server**, with a transactional recheck. The Register frontend should never treat an unreliable secondary status lookup as accounting authority.
8. The browser must not claim a sale completed unless the server has committed it; a network failure after POST needs invoice lookup/retry handling before resubmission.

#### India-specific invoices and tax

- The configured supplier GSTIN, state, trade details, product HSN/SAC and UQC, buyer GSTIN/address where applicable and selected supply type are recorded on **new** documents as available. GSTIN checks are **format-only**, not registration validation.
- Registered shop invoices may show CGST + SGST/UTGST for intra-State or IGST for inter-State sales. The operator decides place/supply type; pyPOS is not a comprehensive GST place-of-supply determination engine.
- Invoice references follow the April–March FY, e.g. `26-27/000001`. Voiding does not recycle numbers. Maintain one authoritative invoice series per shop, not one uncontrolled series per computer.
- Invoice snapshots preserve business/product/tax descriptions; older documents can lack new fields, and the shop logo is a current setting that may differ on a historical reprint.
- Import review flags are particularly significant: `Price Review Required` may block sale until reviewed; `GST Review Required` requires explicit confirmation under applicable checkout conditions, including the store's GST registration setting. A genuine zero-rate product must still be verified rather than inferred from an absent rate.
- **Not included:** statutory GST credit/debit note issuance, IRN/e-invoicing, GST return filing/reconciliation, automatic rate/HSN classification, e-way bills, reverse-charge/composition tax workflows, tax audit certification or a verified TallyPrime voucher export. A receipt headed “Tax Invoice” is not independent evidence of statutory compliance.

#### Returns, voids and reconciliation

- Refunds are linked child documents, not destructive edits to the original sale. A partial return selects **specific units of recorded lines**, and only those units are restocked. Mixed original tenders and store-credit contributions are allocated proportionally in paise.
- A repeated refund cannot return more items or money than the source invoice permits. A sale that already has refunds cannot also be voided; a completed Z-closed session's original sale is handled through Returns rather than a retroactive void.
- Cash/card/UPI refunds are **recorded**, not sent to payment providers. Actual cash payout, card reversal and UPI bank transfer require external action and reconciliation. Store-credit refunds update the internal wallet.
- New refund codes, e.g. `26-27/R000001-000123`, contain the **refund's IST financial-year sequence** and original internal sale ID. Each partial refund has a unique number assigned in the same SQLite transaction; failed refunds do not consume a number, and retry keys retain the first result. These are **operational refund references, not Section 34 GST credit notes or IRNs**.
- Refund previews should derive the original recorded allocations from the server, not recompute historical amounts from today’s catalogue. Old receipts may be displayed with cleaned-up import metadata **without altering stored historical line descriptions**.
- **Store-credit refund recipient:** a completed sale with a registered buyer can only return credit to that original buyer. For anonymous sales, an operator may explicitly select an existing/new customer when issuing credit. This does not alter `sales.customer_id` on the original invoice; the new refund document and positive `credit_events` entry identify the recipient. Existing refund idempotency keys remain valid.
- **Exchange policy keys:** `refund_allow_original`, `refund_allow_credit`, `exchange_allow`, `exchange_allow_anonymous`, and `exchange_allow_forfeit_difference` are administrator-only Settings toggles. The last defaults to `false`. The server validates these rules in both refund preview/commit and exchange planning; changing a setting never rewrites a historical document. Store policies are not proof that a forfeiture is legally permissible; consult qualified GST/consumer-law counsel.
- **Two settlement models:** `pos/exchanges.py` creates a return receipt and replacement invoice in one `BEGIN IMMEDIATE` transaction. For named buyers with store credit enabled, it uses only the new return's wallet credit, never older balances. For anonymous buyers or stores that disable credit, it uses **direct exchange settlement**: `refund_sale(exchange_only=True, manage_transaction=False)` creates a return without a cash tender or `credit_event`; `checkout(exchange_offset_cents=N, manage_transaction=False)` reduces the amount to collect but records the full taxable invoice plus `exchange_offset_cents` in its immutable snapshot. These caller-owned internal parameters are deliberately NOT HTTP request parameters on ordinary checkout/refund routes. Both stock legs, GST, loyalty, invoice/refund sequences and settlement roll back together on failure.
- **Cheaper no-credit replacements:** By default, a no-credit exchange with value left over is rejected. Optional `exchange_allow_forfeit_difference=true` permits one only when the cashier explicitly sends `accept_forfeit=true` on that exact request. `exchanges.forfeited_cents` captures the difference, receipts disclose it, and the refund export identifies it. This amount is **not** a customer credit, cash payout, or automatically classified statutory taxable revenue; accounting reconciliation must explicitly review it. Anonymous bearer vouchers are not implemented. `exchanges.customer_id` is nullable, `settlement_mode` distinguishes `wallet` and `offset`, and offset/remainder amounts are stored in paise with an idempotent request fingerprint.
- **No hidden wallet spend:** an exchange's credit application is capped at the value of *its own* return. Older customer credit balances remain untouched. A cheaper replacement leaves a balance only when a named customer can receive store credit; otherwise it is blocked or explicitly forfeited under policy. A more expensive replacement requires explicit cash/card/UPI payment of the exact difference. Same-SKU swaps can reuse returned stock. An anonymous original invoice can name a customer on the new transaction without rewriting history. The full refund export distinguishes `PAYOUT`, `CREDIT`, `EXCHANGE_WALLET` and `EXCHANGE_OFFSET`, with offset, forfeiture and replacement invoice references.
- Both `refund_sales` and `checkout` capabilities are enforced for exchange endpoints. The backend recalculates the preview under a write lock before committing; browser quantities, payment amounts and prices are not authoritative. The operator verifies returned goods and external card/UPI settlement; no statutory GST credit note is generated.

#### Reporting definitions

| Name | Operational meaning |
| --- | --- |
| Gross completed sales | Sum of completed-sale invoice totals in the selected period, **including GST** |
| Refunds | Separate refund documents issued in that period, not backdated to the original invoice date |
| Net sales | Gross completed sales minus refund documents; **not profit**, bank deposits or a GST return |
| Average checkout | Gross completed sales divided by number of completed invoices |
| Top item value | Sold line values net of returned lines, including originally recorded discount/tax allocations |
| Tender mix | Net recorded Cash/Card/UPI payments; store credit is internal tender, not a bank transaction |
| Voids | Original voided invoices are excluded from recognized sales; historical comparisons may change after voids |

Reports support IST date boundaries and operational drill-down; selected CSV exports may cover **all** records within the date range, not just currently displayed page rows or a local search. Sales CSV should be interpreted by its **signed net-effect** field (refunds negative, voids zero); summing an unsigned `total` is wrong. CSV timestamps are explicitly UTC. Exported user-controlled text is escaped to reduce spreadsheet formula injection. Recorded historical HSN/GST detail may be missing; do not fill it with extrapolated data.

#### Store credit and loyalty

- Credit balances are stored in integer paise with a movement journal and cannot be spent below zero. Customers with relevant historical ledger records are restricted from destructive deletion.
- Default loyalty: 1 point for each completed ₹100 of eligible non-credit-funded spend; 1 point converts to ₹1 credit (100 paise), configurable in Settings. A ₹549 eligible sale yields 5 points under defaults; paying ₹20 of a ₹100 bill using credit makes eligible new tender ₹80, which earns 0 points.
- Conversion is an atomic decrease in points and increase in customer credit. Manual add/deduct/set requires administrator authentication and a reason. Policy changes are prospective.
- Refunds/voids reverse earned points proportionally. If the customer already redeemed them, the point balance can become negative; later accruals offset it. Other discretionary edits are separately journalled, not silently undone.
- Loyalty and store credit are treated as operational tenders; they do **not** automatically reduce the GST-bearing sale value. Their tax and liability accounting requires independent review. Point histories and CSV exports require appropriate access.

### Inventory, EANs, imports, purchasing

#### Product identity and multiple MRP editions

- Exact SKU searches select the corresponding product, even when an EAN maps to multiple products. Shared EAN matches are allowed **only for distinct MRP price editions of the same verified product identity**, including size, colour and style.
- The UI prompts for the physical package's MRP; in-stock counts are displayed and unavailable editions cannot be chosen. It must **never guess** which edition is “newest”, cheaper or more expensive.
- Product IDs, on-hand quantities, printed MRPs and refund stock are segregated by edition. This is not a full FIFO supplier-batch or inventory-costing/COGS engine.
- EAN-13 labels are rendered locally as SVG, with invalid check digits rejected. The app can print a 50 × 30 mm barcode label for a selected SKU/MRP/variant.

#### Stock changes and staff security

- The normal receipt form is positive-only; shrinkage is a **separate** action requiring an explicit reason and a granted stock-reduction capability. Stocktake shortages require independent variance review and shortage reason. All movements log the operator.
- Unauthorized browser/API clients cannot use negative quantities, unbounded counts, fake movement categories or hidden endpoints to bypass permission checks. A manager with appropriate privileges is still responsible for reconciling physical goods to system stock.
- Cashiers may create new products/groups and receive positive quantities if assigned those capabilities. Such items are flagged **Manager Review Required**; unknown purchase cost remains `0` with **Cost Review Required**, rather than a fabricated vendor cost. Product creation is not necessarily evidence that price/tax identity has been independently approved.
- The original UI and early patches once described binary `cashier_allow_stock_reduction`/`cashier_allow_stock_import` switches. The **current individual staff-permission system supersedes these old global switches** for access decisions. Owner-only and privileged actions remain protected on the server.
- Removing a product with nonzero stock or historical sales is denied; adjustments should be recorded with traceable reasons rather than deleting history.

#### CSV/XLSX import contracts

- Import accepts UTF-8 CSV or an XLSX workbook with an `Import` sheet. Header names come from the exported template; additional `attr:<Field>` columns are editable product attributes. Product list/search can use these attributes and catalog pagination handles large imports.
- **Create new / skip:** ignore existing matching SKUs and preserve their data.
- **Add stock:** receive the supplied positive quantity for existing SKUs without overwriting price/attributes.
- **Add stock + merge:** receive quantity and update explicitly supplied nonblank price, cost, tax, barcode, HSN and attributes when the operator has appropriate privileges. Existing attributes are not wholesale deleted.
- Backend validation treats the whole import as one transaction. Duplicate-content fingerprints help detect accidental repeated goods receipts; an explicitly authorised new receipt may require its own reason. **Never treat a prior counted stock snapshot as a supplier delivery.**
- A repeated barcode/price on a matched edition is not permission to merge an unrelated size/style. Conflicting EAN/variant combinations must be held for review.
- Missing purchase cost/GST/HSN/price must be flagged for review, never interpolated from unrelated rows. An MRP is not necessarily the cost price or an evidenced actual sale price. `Price Review Required` prevents an invented selling price from being taken at the till.
- For historical source workbooks, old preparation notes described 595 September rows/1,140 units and 775 November rows/1,141 units, plus an independently scanned 630-row source. These numbers were **patch-era provenance notes, not a current verified master inventory**. Never substitute them for a fresh, row-level audit or use them to overwrite the shop database. Rows excluded for conflicting EAN identity require human reconciliation.

#### Purchasing and stocktake

- Supplier records, purchase receipts, supplier-invoice uniqueness, exact SKU/MRP receipt lines, recorded costs, payment/outstanding tracking and cash supplier payouts belong to the purchasing domain. They are **not** a complete accounting/ERP or GST input-tax-credit workflow.
- Stocktake drafts count only selected products, provide variance previews and detect changed on-hand quantities before posting. Uncounted SKUs must remain unchanged; deficit posting requires the shortage reason and permission.
- Category/style dropdowns, supplier/product selectors and positive quantity controls should offer exact matching and prevent accidental cross-variant receipts.

#### Images and branding

- Separate **app/menu logo** and **receipt logo** settings; logo upload validates file signature and caps the stored result at **200 KiB**. Browser-side PNG/JPEG/WebP optimization can reduce uploaded images up to **24 MiB input** (subject to pixel/decode limits) while preserving transparent PNG/WebP where appropriate.
- Settings also includes a standalone image downscaler/compressor for up to **20** local files; file results are downloaded individually. JPEG loses transparency (white background); lossy output may strip EXIF/ICC metadata. The tool does not upload a batch to an external service.
- The app does **not** currently attach product photos to individual SKU records. A downscaler is not evidence that catalogue image management is implemented.
- Direct receipt printing uses `pos/thermal_print.py` to render committed sale/refund/exchange records as 512-pixel raster ESC/POS `GS v 0` commands with a partial-cut command (`GS V 1`). This targets the **original** Epson TM-T81, 180 dpi / 512-dot printable area on 79.5 mm nominal stock. Pillow renders the stored logo and glyphs; the client cannot inject raw printer commands or alter recorded invoice amounts. Windows sends RAW jobs through Winspool; Linux/macOS call CUPS `lp -o raw` without a shell. The CUPS queue must actually support raw pass-through. `receipt_print_mode=browser` is an explicit manual fallback for A4/58 mm.
- The `receipt_print_jobs` table records each automatic spool attempt at most once per sale (SQLite unique partial index) and logs manual reprints separately. `CLAIMED`/`FAILED` may be ambiguous if OS acceptance or HTTP reply was lost, so the system never silently resubmits automatically: the operator must confirm and manually reprint. `SPOOLED` means OS accepted bytes, not that printing physically completed. Print failures never undo sales or stock. Admin-only `/api/printing/test` prints a calibration slip without inventing a sale. X/Z reports have server-side print endpoints. No automatic cash-drawer pulse is sent. Browser label sheets still require browser printing.

### Cash drawer, operations and UPI

#### Cash-session model

- `cash_sessions.current_session()` selects the latest `closed_at IS NULL` entry. `GET /api/cash-sessions/current` returns either **`null`** or the session object **directly**, including `id`, `opened_at`, `closed_at` and `report` (it is *not* wrapped under `session`).
- `POST /api/cash-sessions/open` returns a *different shape*: `{ "session": ..., "report": ... }`. Frontend code must not confuse these contracts.
- Opening float and closing count require finite, nonnegative amounts with at most two decimal places. A second Start Day with one already open is rejected.
- Expected cash is the opening float plus recorded cash receipts, minus cash refunds and audited cash-out/bank deposit/petty-cash movements attributable to the session. It is not automatically equal to physically counted money.
- X reports represent the in-progress session; Z reports record the closed-session reconciliation. Explicit `cash_session_id` is authoritative for new sale/refund attribution; old records may use timestamp fallback.
- A cashier may open/close the day with the appropriate personal permission. **End Day is not Exit Application.** The server can remain running after closing the drawer. Finishing an active sale and safe receipt printing takes precedence over an OS shutdown request.

#### UPI references and settlement boundaries

- When UPI is selected, pyPOS creates an **internal POS reference** to help match a customer payment note. A QR may encode the configured merchant VPA and exact due amount. This is **not a bank-generated UTR** and the payer's app may truncate/ignore a note.
- Operators can enter the **actual UTR** observed in the bank/merchant app. Nonblank references are normalized and checked against previous UPI payments and other split rows; duplicates are rejected without changing stock/payments. A blank UTR is allowed for a manually verified payment but is not itself verification.
- Changing the amount/payment method clears an earlier “payment received” confirmation and previous UTR; fractions of a paisa are rejected. If reference generation fails temporarily, the UI supports retry without discarding the payment row.
- pyPOS cannot poll NPCI or the bank for independent success. A QR scan, customer screenshot, typed UTR or “payment received” checkbox **does not prove settlement**. Check the merchant account before committing the payment.
- UPI operational CSV exports can contain POS reference and recorded UTR; they are not bank statements or reconciliation certificates.

### Staff authentication, authorization and security

#### Operator sessions

1. First-run owner setup is local-only. An administrator Settings password and the owner's **separate operator PIN** serve different purposes; there is no shared default PIN.
2. Named operator sessions use server-side status checks for business API operations; the current process's boot nonce invalidates old sessions after restart. Staff can sign out via `GET /staff-logout` (sidebar **Log out / Switch**), which clears the operator, Settings unlock and session/CSRF data. The next user authenticates at `/staff-login`.
3. The owner can manage accounts and grant exact boolean permissions; presets **Everyday staff** and **Supervisor** are conveniences, not immutable roles. Existing `cashier`/`manager` rows remain in SQLite for compatibility but display as staff accounts.
4. Revocation or disabling takes effect on subsequent API requests, not just after browser refresh. Unknown, nonboolean, incomplete or out-of-schema permission updates are rejected.
5. Staff account creation/permission changes require the owner's authenticated operator identity **and** an unlocked administrator Settings session. Staff cannot grant themselves owner-only settings, backups, Drive/OAuth access or account administration.
6. Financial exports, refund, void, cash payouts, inventory shrinkage, costs and privilege escalations are permission-gated. A successful UI operation does not imply a modified request can bypass API enforcement.
7. Operator audit history is operational evidence, **not a cryptographically tamper-evident ledger**. Physical stock controls, bank reconciliation and limited device access are still required.

#### Local web security

- Default binding is `127.0.0.1`; nonloopback listen requires `--lan` or an administrator-enabled `lan_access_enabled` preference followed by restart, which exposes **unencrypted HTTP** and should be restricted to a trusted, segmented LAN, not the public internet.
- Request Host validation mitigates DNS rebinding. Mutating requests require permitted same-origin context: matching scheme and port; intended loopback hostname variants (`localhost`, `127.0.0.1`, IPv6 loopback) are handled carefully. Never broadly trust opaque `Origin: null`, arbitrary external sites or another port.
- Session/CSRF protections remain in place; privileged mutations require authenticated owner identity and appropriate settings unlock. Script/CSS/font assets are bundled and CSP/security headers restrict remote injection and framing; sensitive API responses are not cached.
- Where available, app-data permissions are restricted to the running OS user. Google OAuth secrets and tokens stay server-side; backups enforce validated filenames and use safe paths. SQL uses parameterized queries.
- An open web browser or localhost exposure is **not** protection against anyone with full access to the logged-in operating-system account. Do not leave PINs/admin credentials visible at an unattended counter.

### Backups, recovery and managed updates

#### SQLite snapshots

- The SQLite **online backup API** creates transaction-consistent snapshots including committed WAL content, without force-checkpointing the live register before copying.
- Snapshots are staged under temporary filenames, verified with SQLite integrity checks, durably written (with a **writable descriptor for Windows `fsync`**) and atomically renamed to their final names. A failed backup is not presented as a valid final snapshot.
- The 10-minute scheduler retains 24-hour high-frequency, seven-day hourly and 30-day daily history, subject to **count and total size** ceilings. Disk-space safeguards can prune the oldest eligible snapshots or refuse a backup; an off-device independent copy is still essential. A restore is an operator-controlled recovery procedure, **not** a way to erase disagreement between two live tills.
- Restoring to the active SQLite database uses the online backup mechanism into the same target file to avoid splitting existing SQLite connections across a replaced inode. Even so, stop other operators and verify a disposable restoration before touching real transactions.
- Optional Drive integration uses Google OAuth (`drive.file` scope, validated `state`) and callback `http://127.0.0.1:8420/gdrive/callback`. The permission covers files pyPOS creates, not the user's entire Drive. Token failures must never interrupt the local sale path.

#### Native updater internals

1. A tagged workflow produces platform-specific release ZIPs, SHA-256 sidecars and `release-manifest.json`. A release identity pinned at build time defines the GitHub repository; ad-hoc builds do not enable updates.
2. A native ZIP contains the main app **and** graphical launcher. A persistent per-user launcher forwards to a verified versioned launcher; the latter supervises `pypos-app` and holds an installation-wide OS-backed lock throughout the child lifetime.
3. A second Start request while a managed instance owns the lock reopens the browser rather than launching another managed server. A newly extracted installer must not replace the installed version underneath an active register.
4. The updater checks in a background thread; a newer stable version is downloaded to an immutable version folder only after matching platform/manifest names and validating checksums. Path traversal, suspicious names and incomplete packages are rejected.
5. On the **next** Start, it creates a pre-update SQLite backup. A failed backup postpones activation; the old version runs. The prior verified binary remains for rollback. Data/schema rollback **never** happens automatically.
6. Early failure of the replacement binary within the configured startup window can revert to the previous binary for the next launch (except deliberate port conflict). Stable versions are cleaned only after successful startup, preserving unknown folders and all data/backups.
7. The permanent installed forwarder is refreshed from the **verified** active release; Windows executable-lock retries postpone rather than dangerously overwrite a still-running launcher.
8. Stop writes a request to the managed install's `stop.request` only after operator confirmation. The app's Waitress shutdown coordinates worker dispatcher and network connections; the supervisor waits for exit, then releases the OS instance lock. Stop **must not** blindly kill processes or interrupt an in-flight sale.

**Security boundary:** SHA-256 protects against accidental mismatch with published checksums; it does not independently prove publisher authenticity if the release and sidecar are compromised. Release repository permissions and HTTPS matter. Wide production distribution should sign Windows executables (Authenticode) and sign/notarize macOS builds. Native runtimes must be tested on physical target OS hardware.

**Existing stale processes:** The new lock controls **new managed instances**, not arbitrary orphaned processes started by historical binaries or source scripts. Task Manager counts can include PyInstaller children. Confirm the exact running executable, port owner, active DB path and current invoice activity before a one-time cleanup. Do not kill every `pypos*.exe` by filename.

#### Recovery decision table

| Situation | Safe first action | Do **not** do |
| --- | --- | --- |
| Browser closed, server running | Use installed **Stop pyPOS**; check log if still live | Assume closing a tab stops the server |
| Old release continues after push | Publish an actual tagged release; stop/restart managed install | Assume `git push main` updates packaged binaries |
| Browser shows old UI | Hard reload, confirm installed version, restart app safely | Delete data/ or database to force an update |
| Open drawer but checkout says Start Day | While signed in check `/api/cash-sessions/current`, active port/version and DB path | Start duplicate days or bypass the backend day gate |
| Rollback after failed binary | Launch retained verified previous binary and inspect backup | Restore pre-update DB over newly recorded sales without reconciliation |
| Conflicting source and managed DB | Stop both; inspect, back up and reconcile | Overwrite either DB, merge via filesystem copy, issue duplicate invoice sequences |
| Unknown orphan processes | Identify actual OS process/port and data files before termination | Blanket force-kill during a live checkout |

### UI design system and accessibility

**Theme:** “Windows Business Classic”. The target is a dense, predictable retail interface inspired by older Windows business registers, without unnecessary decoration or an internet dependency.

#### Core visual tokens

| Token | Light | Dark / behavior |
| --- | --- | --- |
| Workspace | `#EAEAE8` | `#202020` |
| Panels | `#FFFFFF` | `#292929` |
| Chrome | `#FCFCFC` → `#F1F1F0` | `#333333` → `#2B2B2B` |
| Hairline border | `#D9D9D9` | `#3F3F3F` |
| Control border | `#ADADAD` | `#565656` |
| Primary text | `#1A1A1A` | `#F1F1F1` |
| Secondary/muted | `#444444` / `#767676` | `#CCCCCC` / `#969696` |
| Hover | `#E5F3FB` | Translucent blue `rgba(76,144,224,.16)` |
| Selected | `#CCE4F7` | Muted blue selection |
| Primary action | Blue gradient `#5F9DE9` → `#3F7FD6` → `#2F66C4`, border `#245BB5` | Same blue family |
| Danger | `#E58A8A` → `#BB2F2F` | Semantic red only |

**Typography:** Self-hosted **Selawik** (SIL Open Font License 1.1, Microsoft) with Segoe UI/Tahoma/system fallback. Amounts use Consolas/Courier-like tabular monospaced figures. Baseline sizing: UI 13px, secondary 12px, micro-labels 11px, page title 17px/600, totals 19px/700.

**Components:** 3px-radius Windows-like chrome buttons, 32px standard / 26px small / 46px main charge key, blue focused form fields, sticky-gradient table heads, right-aligned amounts, boxed settings sections, informative empty states, skeletons in place of spinner overlays and title-strip modals. Show inline error messages rather than silently failing.

**Layout:** ~200px left sidebar with compact/icon mode under ~940px; Register uses two panes (catalogue and cart) plus function-key status bar and clock. Dense tables scroll within fixed chrome. On narrow screens the content collapses to one column; statistics can become a 2 × 2 block. The permanent Logout/Switch action stays at the bottom of sidebar independently of Staff-menu visibility.

**Interaction:** 100–200 ms restrained feedback (`ease-out`, opacity/transform only), physical key press metaphor, no scroll hijacking. Avoid emojis in application copy, neon/glow, glassmorphism, black-on-black, large pill panels, generic circular spinners, parallax, runtime web fonts/CDNs and marketing jargon. The design spec is a guide; source CSS controls actual current colors and dimensions.

### Testing and release acceptance

#### Automated checks

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py'
node tests/check_frontend_modules.mjs
python -m compileall -q pos
```

For optional full browser tests install the development-only `npm ci` dependencies, run the server on a **disposable** database, then run `node tests/frontend_test.mjs`. Some Python smoke scripts require Chromium/Playwright and/or Pillow and are **not mandatory runtime dependencies**. Tests must not point at a live cashier database.

The release workflow `.github/workflows/build-binaries.yml` runs tests before package creation across **Ubuntu x86_64, Windows x86_64, macOS Intel and macOS ARM64**. The release job depends on all build jobs. A successful package job is not proof that receipt hardware, Drive OAuth, operator permissions or GST workflow passed integration acceptance.

#### Practical acceptance checklist (before a live till upgrade)

- [ ] Back up the actual database and make a staging copy; run SQLite `PRAGMA integrity_check` and check foreign-key integrity.
- [ ] Verify no other process is writing to the same SQLite file; identify the active application version and data path.
- [ ] Run the full Python suite and frontend import check with all dependencies installed; require **zero failures and errors**.
- [ ] Install/stop/restart native binaries on the actual supported operating system. Confirm no duplicate independent servers and no stale fixed forwarder.
- [ ] Test named owner/staff PINs, sidebar logout, admin Settings unlock, role revocation, forbidden stock reductions and rejected privileged API calls.
- [ ] Open Day as an authorized operator; complete Cash, Card, UPI and split-tender test sales on staging data; verify UPI in a real merchant test flow where feasible.
- [ ] Verify failed/duplicate sale submissions do not create duplicate invoices, charge twice or decrement stock twice.
- [ ] Scan a known EAN with multiple MRPs; sell and refund one edition only, then verify exact inventory balances.
- [ ] Check flagged imported GST/price data; do not accept missing costs/taxes as genuine zeros.
- [ ] Test a partial refund, its new refund number, historical receipt reprint, loyalty reversal, store-credit entry and Z-closed return rules.
- [ ] Receive genuine stock twice under separate supplier receipts, test repeated-upload protection, then perform a limited stocktake and verify uncounted SKUs are unchanged.
- [ ] Review Start Day float, cash-in/out/petty cash, cash supplier payout, X report, counted End Day difference and printed Z report.
- [ ] Print actual 58mm/80mm thermal slips (including long names, logo, GST breakdown, rupee symbol and payment method); verify barcode scanner and physical printer settings.
- [ ] Create a local online backup; restore **to a copy**; reconcile inventory, cash, customer credit, invoices and loyalty. Validate any optional Google OAuth sync separately.
- [ ] Check invoice/refund FY rollover around March/April, timezone consistency (IST receipt vs UTC CSV) and statutory document treatment with the responsible accountant.
- [ ] Only deploy after the operator responsible for cash, stock and accounting has accepted the staging result.

#### Historical QA evidence is not current release certification

Past isolated tests included Python unit suites, JavaScript import/link validation, browser smoke tests with simulated APIs, discount rounding cases, concurrent checkout retry cases, SQLite restore with live connections, and import/stocktake concurrency checks. Some patch environments lacked Flask, Playwright, actual Windows hardware or a physical printer. Consequently, older “passed N tests” statements applied **only to that patch's specific environment and revision**. Do not aggregate those counts or represent them as a current all-platform pass. CI and real till acceptance are required for each new release.
