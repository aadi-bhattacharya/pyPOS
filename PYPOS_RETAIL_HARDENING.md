# pyPOS Retail Hardening — update-only release

**Base:** pyPOS India Edition with loyalty, Insights, cash-drawer restoration,
receipt-layout repair, Returns, multi-MRP barcode support, EAN validation, and
the Image Optimiser update. This patch contains only changed/new application
files. It contains **no database**, customer records or inventory workbooks.

## Installation, safely

1. Stop pyPOS. Make two separate copies of the **entire** `data/` folder to an
   external safe location. Verify that the database and WAL sidecar files (if
   present) are included; preferably use pyPOS's normal database backup action
   while running, and then shut it down before copying the directory.
2. On a separate test copy of the app and its database, apply this patch.
   The installer expects the **Image Optimiser** release as its baseline.
3. Run the application's documented setup/startup procedure. Its existing
   `init_db()` adds the columns and tables non-destructively. Do not manually
   apply SQL against an open production database.
4. Test a normal sale, cash+card split payment, a partial return, a multi-MRP
   barcode, X/Z and Start Day/End Day, a stock count, and a backup restore.
5. Only switch the shop to the patched copy once the trial data and till
   balances reconcile. Reverting source code alone is **not** a valid DB rollback:
   restore the corresponding pre-upgrade backup if reverting.

Alternatively, use `git apply --check pyPOS-Retail-Hardening.patch` and then
`git apply pyPOS-Retail-Hardening.patch` from the exact baseline. Do **not**
apply both ZIP overlay and Git patch.

## Implemented

- **Checkout:** transaction retry keys (`sale_requests`), cent-bounded discount
  allocation, GST-import review enforcement even without a shop GSTIN,
  and a browser tender preview that rejects noncash overpayment and reports
  cash change correctly. Receipt figures still come from the server.
- **Daily cash drawer:** new sales and refunds record their session ID, and
  explicit attribution prevents outside-session sales from being pulled into
  a newly opened session. Existing records retain legacy half-open timestamp
  handling, which cannot retroactively resolve ambiguous same-second events.
  Z-closed sales cannot be voided; use Returns instead.
- **Cash movements:** audited cash-in, cash-out, petty cash and bank deposits
  affect expected drawer cash, with reasons and optional references.
- **Additive stock imports:** a repeat content digest stops accidental double
  receiving; an explicit second-shipment override requires a reason.
- **Purchasing:** supplier registry, unique supplier invoice, exact SKU/MRP
  receipt lines with recorded cost, payments and outstanding balances.
  Cash payments from an open drawer are recorded as drawer withdrawals.
- **Stocktakes:** draft counts for individual SKUs, variance preview, and
  transactional posting only for counted items when their expected quantities
  have not changed in the meantime.
- **Staff:** optional hashed PINs and role-based API write access; administrators
  can manage operators. Nothing is enabled until a previously authenticated
  administrator enrols the first admin. Staff audit records successful API
  writes but is **not tamper-proof**.
- **Labels:** on-device EAN-13 SVG encoding and a printable 50×30 mm label for
  the selected barcode/MRP/variant. Invalid check digits are rejected.

All existing Store Branding, Register, Catalogue, Inventory, Customers,
Sales, Returns, Cash Drawer, Reports, Settings, Loyalty and receipt printing
controls are retained.

## Important boundaries

**Not claimed complete in this patch:** a single atomic product-exchange
transaction, formal Indian GST credit-note numbering/filing, audited
GSTR-1/GSTR-3B export, bank-settlement reconciliation, a verified TallyPrime
voucher mapping, historical inventory valuation/COGS, production-grade
multi-terminal authorization, or tamper-evident staff event storage.
Do not invent purchase costs or input tax credits for past stock.

**Live validation not available in the creation environment:** Flask was not
installed here, so app HTTP/auth/CSRF integration tests could not be run.
Chromium navigation to a local POS server was blocked; GUI module-load/syntax
checks are **not** equivalent to browser/hardware testing. Printer and scanner
compatibility, backup restore on your machine, and GST classifications need
local verification. This is a candidate update for staging, **not a certificate
of production readiness**.

## Test commands

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
node tests/check_frontend_modules.mjs
python -m compileall -q pos
```

Run these inside an environment with the dependencies listed by the project.
Use a **copy** of the database for any manual acceptance tests.
