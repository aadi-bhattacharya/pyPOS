# pyPOS

A simple, practical point-of-sale system for small shops. Runs entirely on
your machine — one Python dependency, no internet needed, data stays local.

![stack](https://img.shields.io/badge/python-3.10+-blue) ![ui](https://img.shields.io/badge/ui-browser%20(offline)-purple)

## Features

- **Register** — fast checkout: barcode scanner support (scan SKU/barcode +
  Enter), searchable product tiles, quantity steppers, order discounts (% or
  fixed), split payments (Cash / Card / UPI), store-credit tendering, change
  calculation, printable receipts. Park an in-progress sale with one key and
  recall it when the customer comes back.
- **Customers & store credit** — customer list with phone/email lookup,
  attach a customer to any sale, top up or deduct store credit, pay with
  credit at checkout, refund into credit, full credit + purchase history per
  customer.
- **Catalog** — product groups, product variants with attributes (Size, Color,
  …), cost/price/tax per product, audited stock adjustments.
- **Sales history** — filter by date/status, view any past receipt and reprint
  it, partial refunds (dial back quantities line by line — only returned units
  restock) as well as full refunds and voids.
- **Reports** — today's revenue & transactions, 14-day revenue chart, top
  products, payment mix, low-stock alerts — plus end-of-day close-outs:
  open the day with a drawer float, watch live X read-outs, then count the
  drawer and print a Z report that records counted cash vs expected.
- **Settings** — store name/address/phone, currency symbol, tax label, receipt
  footer, low-stock threshold, and GST-style **tax breakup** on bills: toggle
  it on and each product's tax rate prints as components (e.g. 5% →
  CGST 2.5% + SGST 2.5%), with custom component names and rate shares.
  Optionally **lock Settings behind an admin password** (hashed with
  PBKDF2-SHA256, never stored in plaintext) while the register stays open
  for cashiers. Light/dark theme.
- **Backups** — one-click snapshots, scheduled auto-backups (configurable
  interval + retention), download/restore from the app, and automatic
  **Google Drive sync** via native Google sign-in (no extra tools needed).
- **CSV exports** — sales history and inventory valuation, Excel-friendly.
- **Safety** — every sale is computed server-side in exact decimal arithmetic;
  totals can't be tampered with from the UI; stock is validated atomically at
  checkout so two registers can't oversell the last unit; store credit can
  never go negative and every credit movement is journalled.

## Install & run

pyPOS is a single small app that keeps all data on your machine. Pick whichever
route fits you:

**Option A — one file, no installation (recommended for a shop PC)**

Build once, copy anywhere:

```bash
python build_portable.py          # produces dist/pypos-<version>.pyz (~2 MB)
                                  # plus "Start pyPOS.bat" for Windows
```

Then on any machine that has Python 3.10+ (Windows/macOS/Linux):

```bash
python pypos-1.0.0.pyz            # starts the register, opens the browser
```

**On Windows, double-click `Start pyPOS.bat`** (sits right next to the `.pyz`)
— no typing needed; it finds Python for you.

No venv, no pip, no admin rights. Data appears in `./pypos-data` next to where
you run it — put the `.pyz` on a USB stick together with that folder and your
whole till travels with it.

**Option B — native binary (no Python needed at all)**

PyInstaller can't cross-compile, so each OS builds its own binary. Three ways:

```bash
# on the target OS (needs Python + pip):
pip install "Flask>=3.0" pyinstaller
pyinstaller pypos.spec            # -> dist/pypos  (dist\pypos.exe on Windows)
```

Or let GitHub do it: push a `v*` tag (or use the Actions tab → **build-binaries**
→ Run workflow) and collect `pypos-linux`, `pypos-windows`, `pypos-macos`
from the run's Artifacts. The workflow lives at
`.github/workflows/build-binaries.yml`.

The resulting single executable runs standalone — copy it to the shop PC and
double-click.

**Option C — proper pip / pipx package**

```bash
pip install .                     # or: pipx install .
pypos                             # console command, same flags as main.py
```

**Option D — straight from this checkout**

```bash
./run.sh                          # Windows: run.bat  (auto-creates .venv)
# or manually:
pip install -r requirements.txt   # just Flask
python main.py                    # starts server + opens your browser
```

The app runs at http://127.0.0.1:8420. On first run a fresh database is created.
Want to explore with sample data first? Add `--seed-demo`.

### Where the data lives

| Priority | Situation | Location |
|---|---|---|
| 1 | `--db PATH` flag or `$PYPOS_HOME` | exactly there (`$PYPOS_HOME/pypos.db`) |
| 2 | running this source checkout | `<checkout>/data/pypos.db` |
| 3 | a `pypos-data/` folder exists in the working directory | that folder (USB-stick portable mode) |
| 4 | otherwise — the OS-standard per-user data dir | see below |

Platform-standard locations (priority 4):

| OS | Path |
|---|---|
| Linux / BSD | `~/.local/share/pyPOS` (or `$XDG_DATA_HOME/pyPOS`) |
| macOS | `~/Library/Application Support/pyPOS` |
| Windows | `%LOCALAPPDATA%\pyPOS` |

Backups live in a `backups/` folder next to whichever database is active.
To carry the till on a USB stick: put the `.pyz` (or binary) and an empty
`pypos-data/` folder together — pyPOS will keep its data beside itself.

**Shipping a clean copy?** The app creates its database fresh on first run —
the artifacts (`dist/*.pyz`, the binary, the wheel) contain no store data. To
ship with zero history, just don't copy any data folder over: leave out
`data/`, `dist/pypos-data/`, and `.venv` if you're zipping a checkout (they're
all in `.gitignore`). On the receiving PC the first launch starts a brand-new
register.

You can also load samples later from **Settings → Sample data** (only while
the catalog is empty).

### Options

| Flag | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to serve other devices on your LAN (e.g. a tablet register). |
| `--port` | `8420` | HTTP port |
| `--db` | see table above | Database file location |
| `--no-browser` | off | Don't auto-open the browser |
| `--seed-demo` | off | Load sample data on first run |
| `--set-password` | off | Set/replace the Settings admin password, then exit (prompts securely; the CLI itself never needs a password) |
| `--version` | — | Print the version and exit |

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F2` or `/` | Focus the register search (barcode scanners just work) |
| `Enter` | Add exact SKU/barcode match to cart |
| `F4` | Open payment dialog |
| `F6` | Park the current sale / manage parked sales |
| `+` / `-` | Adjust selected cart line quantity |
| `Esc` | Close dialogs / clear search |
| `?` | Shortcut help |

Click a cart line to select it for `+`/`-`.

## How money works

- Product prices are **pre-tax**; each product has a tax rate in percent
  (`5` = 5%).
- Order-level discounts are allocated proportionally across lines in exact
  cents; tax is applied to each discounted line and rounded half-up.
- Payments are validated server-side: non-cash tenders apply first, cash
  absorbs the remainder and produces change. Card/UPI overpayment is refused.
- Store credit is held per customer in exact integer cents with a full event
  journal (top-ups, spends, refunds); a sale can split between credit and any
  tender, but the balance can never go negative.
- Partial refunds recompute each returned unit from the line's recorded
  discount/tax cents, return money across the original tenders in exact
  proportion (or into store credit), and restock only what came back.

## End of day

Reports → **End of day**: open the day with your drawer float, check the live
X read-out (transactions, net revenue, expected cash) any time, then close the
day by counting the drawer — pyPOS records counted vs expected, keeps a
history of past days, and prints a classic Z report slip for your records.

## Backing up to Google Drive

Settings → Backups handles everything. For the Drive sync, do the one-time
Google setup (2 minutes):

1. Go to [console.cloud.google.com](https://console.cloud.google.com) →
   **APIs & Services** → enable **Google Drive API**
2. **OAuth consent screen** → External → add your own Google account as a
   test user
3. **Credentials → Create credentials → OAuth client ID** → type
   *Web application*
4. Add this exact Authorized redirect URI (also shown in pyPOS):
   `http://127.0.0.1:8420/gdrive/callback`
5. Paste the Client ID + Secret in Settings → Backups → click
   **Connect Google Drive** and approve.

After that, every backup (manual or scheduled) is uploaded to your Drive and
old copies are pruned with the same retention rule as local ones. Access uses
the minimal `drive.file` scope — pyPOS can only touch files it created, never
the rest of your Drive. Revoke anytime from the app (Disconnect) or from your
[Google account permissions page](https://myaccount.google.com/permissions).

## Design

The UI follows the **"Windows Business Classic"** design system — see
[`DESIGN.md`](DESIGN.md). It is intentionally reminiscent of the Windows
XP/7-era register software cashiers already know: toolbar strips,
function-key hints in a status bar, dense grids, and one blue action key.
Typography uses self-hosted **Selawik** (Microsoft's open-source Segoe UI
kin, SIL license) with a genuine Segoe UI/Tahoma fallback on Windows, so
the app looks native on the cashier's machine — fully offline, no CDNs.

## Security & isolation

pyPOS is built to run **entirely on the cashier's computer**, and the
isolation is enforced, not promised:

- **Loopback only by default** — the server binds to `127.0.0.1`. Other
  devices on the network cannot even connect. Serving tablets over Wi-Fi is
  possible with the explicit `--lan` flag (unencrypted HTTP warning included);
  a non-loopback `--host` without it refuses to start.
- **Host allow-list** — every request must carry a loopback `Host` header.
  This kills DNS-rebinding attacks (a malicious website mapping a domain to
  127.0.0.1 to reach local apps).
- **Cross-site request shield** — mutating requests (POST/PATCH/DELETE) whose
  `Origin`/`Referer` belong to another website are refused, so a page you
  visit in another tab cannot create sales or wipe backups while your browser
  holds an open connection to the register.
- **Strict CSP & headers** — no external script/CSS/font is ever loaded
  (works fully offline); framing other sites is impossible; API responses are
  never cached.
- **File permissions** — `data/` and all database files are chmod'ed to the
  current OS user only (`700`/`600`) where the OS supports it.
- **Secrets stay put** — Google Drive tokens never leave the server; public
  settings responses have them masked; SQL is parameterized everywhere;
  backup filenames are validated against a strict pattern (no path tricks).

## Backups that can't hurt you

Snapshots are written to a temp name, integrity-checked, fsynced, then
atomically renamed — **a power cut mid-backup can never leave a corrupt file
pretending to be a good snapshot** (only an unnamed temp file, which is
cleaned up automatically). Before each cycle pyPOS checks free disk space and,
if tight, prunes the oldest snapshots first; if space still can't be made it
refuses to write rather than filling your drive. Retention runs on two rails:
count ("keep last N") and a hard size cap in MB — total backup storage can
never exceed what you configured.

Everything lives in one SQLite file (see the table above) plus its snapshots
in a `backups/` folder beside it. The in-app backup system makes manual copying
unnecessary, but an offline copy on a USB stick is still a good idea for
fire/flood protection. The schema (with triggers for stock and product
history) ships inside the package as `pos/schema.sql`.

Products with sales history can't be deleted (accounting trail); instead edit
them or run stock to zero.

## Development

```bash
python -m unittest discover -s tests   # backend test suite (139 tests)
```

There is also a headless end-to-end test that drives the real UI in a virtual
browser (jsdom) against a running server — it clicks through register →
checkout with split payment → catalog → customers & store credit → park/recall
→ partial refunds → sales → reports → end-of-day Z report → settings:

```bash
npm install                            # dev-only (jsdom)
python main.py --no-browser --seed-demo &   # in one shell
node tests/frontend_test.mjs           # in another
```

Layout:

```
main.py            entry shim -> pos.cli (argparse, opens browser)
pos/               the whole application
  cli.py           command-line interface
  app.py           Flask routes (JSON API + serves the SPA)
  db.py            connections, schema loading, portable data-dir rules
  catalog.py / sales.py / customers.py / parked.py / cash_sessions.py /
  reports.py / settings.py / backups.py / gdrive.py / demo.py / security.py
  templates/       single-page app shell
  static/js/       ES-module frontend (no build step, no CDNs)
  schema.sql       database schema (+ idempotent migrations on start)
tests/             unittest suite for the business logic
build_portable.py  builds the single-file .pyz (app + Flask bundled)
pypos.spec         PyInstaller spec for a native single-file binary
```

The frontend talks to the API under `/api/*`; all money math lives in the
backend, so any other client could drive the same endpoints.
