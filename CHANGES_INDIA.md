# India-focused changes (November 2026)

## Register, tax and history

- Indian rupee and `en-IN` grouped totals; checkout and on-screen reporting use Indian Standard Time.
- Configurable **GST-inclusive or GST-exclusive** catalogue pricing. Inclusive-mode tax is backed out of discounted sale prices; existing installations retain the tax-exclusive default.
- Cash denominations in ₹, intra/inter-State supply selector and place-of-supply entry; per-sale tax breakup follows CGST + SGST/UTGST or IGST for GSTIN-configured shops. Tax treatment is still a merchant decision, not a rule-engine determination.
- Atomic invoice references by Indian April–March FY. Invoice series and newly captured tax/account details migrate into older SQLite databases.
- Fields for supplier GSTIN, State/UT, FSSAI and UPI ID; buyer GSTIN and billing address; product HSN/SAC and unit code.
- Immutable captured names/SKUs, billing fields, selected tax mode and tax breakup on **newly created** bills, allowing reprints after settings or catalogue edits.
- Improved multi-part refund cent allocation, including proportional returns when a payment combined store credit with cash/card/UPI.
- Voids preserve original sold-item rows and disallow voiding any sale after a refund, preventing double-restocking.

## Receipts and print

- Configurable **58 mm**, **80 mm** and **A4** printable layouts with optional logo (PNG/JPEG/WebP, <=200 KiB), adjustable logo width, saved visibility and a non-transactional test print.
- Store logo also prints on X/Z reports; receipt includes optional GSTIN, HSN/SAC, UQC, buyer GSTIN, place of supply, taxable value and item GST split.
- Browser print/dialog controls output; there is no raw ESC/POS driver or automatic cutter/drawer support.

## Data exports and correctness

- CSV transactions include invoice reference, refund parent and signed net effect; CSV line export reports recorded quantities, GST rates and amounts without filling missing legacy fields with guesses.
- Spreadsheet formula injection protection for user-controlled CSV string cells.
- Restored backup path now uses database init + schema migration rather than passing a file path into a connection migration helper.
- Existing database schemas migrate idempotently; older sales do not receive invented invoice numbers or tax identities.

## Verification performed in this build

- Backend unit suites not requiring Flask passed (109 tests as of packaging).
- Original distributed schema from uploaded ZIP migrated **twice**, then a live SQLite checkout completed on the upgraded schema.
- All JavaScript module syntax and Python module syntax checks passed.
- Full Flask HTTP and headless browser end-to-end tests **could not be run in the packaging environment** because Flask and jsdom were not installed. Run them on the shop PC/staging setup before production rollout.

For full limitations and a step-by-step printer setup, see `INDIA_SETUP.md`.

## Loyalty points addition (November 2026)

- Added `loyalty_events` audit journal and customer `loyalty_points` balance with idempotent SQLite migration.
- Automatic earning on eligible customer-linked paid purchases with configurable ₹ threshold and integer point rate; no rewards on spend settled with store credit.
- Proportional point clawback for partial refunds and full reversals for voids; settled refund is never blocked by an insufficient point balance.
- Admin-authenticated add / deduct / set / convert-to-store-credit actions; mandatory audit reasons for edits and atomic redemption transactions.
- Customer point column, register customer point badges, detail history, receipt point lines and admin-only CSV export.
- Configuration in Settings and a worked examples / compliance note in `LOYALTY_GUIDE.md`.
