# pyPOS release audit — 6 November 2026

## Release status: NOT YET PRODUCTION SIGNED OFF

This is a corrective, update-only patch against the latest salesperson-enabled
pyPOS source. It preserves the existing catalogue, invoices, loyalty, branding,
refunds, cash-drawer controls, multi-MRP workflow, and salesperson tracking.
No database files are included. No migrations or existing records are rewritten
by this patch.

### Confirmed defects corrected

| Area | Reproduced before | Corrective action |
|---|---|---|
| Checkout units | Quantity `1.9` silently sold 1 unit; `true` sold 1 unit | Reject noninteger/bool or out-of-range product quantities |
| Returns | Partial return quantity could be truncated | Same strict integer validation in preview, fingerprint, and commit |
| Parked carts | Fractional quantities could be parked and then rounded | Reject invalid IDs and quantities before parking |
| Day-end count | `NaN` could close a day without a valid count | Require finite, nonnegative rupee amount, <=2 decimals |
| Day start | ₹10.001 silently became ₹10.00 | Reject fractional paise and nonfinite floats |
| Tender | Fractional-paise or huge tenders silently rounded | Reject, rather than round, malformed payment amounts |
| Discounts | Unknown discount mode ignored; >100% rounded down to 100% | Reject invalid mode / percentages |
| Backup restore | Replacing a live DB file split already-open SQLite connections into divergent databases | Use SQLite online backup API into the same target file; verified two live connections |
| Same-origin protection | Opaque Origin `null` and different localhost ports were treated as trusted | Require exact same scheme, host, port on browser mutation requests |
| Google Drive OAuth | Authorization-code callback had no CSRF `state` verification | Random per-session state, checked before exchanging token |
| Frontend | Dynamic relative `import()` broke isolated browser execution for cash and multi-MRP screens | Reuse static API imports; both UI smoke tests now pass |
| Google error pages | OAuth error detail was interpolated into HTML without escaping | HTML-escape diagnostic text |

### Automated checks actually completed here

- 217 non-Flask Python unittest cases passed (zero failures in executable subset).
- Three unittest cases could not execute: `test_auth`, `test_security`, and `test_loopback_detection` (Flask is absent).
- All 21 ES modules parse and link.
- Isolated Chromium smoke tests passed: receipts, receipt width 58/80mm/A4, refunds, cash drawer/X-Z, multi-MRP barcode selection, image compression and Settings image uploads.
- Live SQLite restore tested with two open connections; both observe the restored state and subsequent writes.
- Unit tests for invalid counts, fractional quantities, invalid tender precision and correct rejections added.
- A clean Git patch-application check is required during packaging; final package verification recorded separately.

### Not executed / not certified

1. Full Flask HTTP tests (auth, staff roles, CSRF, password flow, API errors, backup upload/restore) — Flask cannot be installed in this environment. **Release blocker.**
2. Actual till hardware: barcode scanner keyboard wedge, 58mm/80mm thermal printer, card machine, UPI terminal, cash drawer — not available here. **Release blocker for that specific deployment.**
3. User's actual SQLite database with live invoices/customers — not provided for this release audit; only test/disposable databases used. **Migration acceptance required.**
4. Live Google OAuth account flow, external backups and recoverability — not tested. Live credential/redirect validation is required before enabling remote sync.
5. India statutory compliance: independently numbered GST credit notes, complete return exports/e-invoice where applicable, tax classification and external settlements remain outside this patch. **Not GST/accounting certified.**
6. Cash/card/UPI refunds remain **recorded bookkeeping actions**, not automatically paid out through provider APIs.
7. Online restore improves SQLite connection consistency, but should still be performed only when other operators have stopped transacting. Verify data after restoration.

### Production deployment safeguards

- Do not expose pyPOS to the public Internet; prefer localhost. LAN mode serves unencrypted HTTP; use it only on a trusted segmented network with staff PINs and a strong admin password.
- Set a strong admin password before operating on non-demo data; activate named staff accounts for accountability where relevant.
- Turn off demo data and keep one authoritative database path; confirm that backup/restore points to that same path.
- Inventory receipt imports and physical stocktakes are **not** interchangeable. Never add snapshot quantities as deliveries.
- Verify GST classifications and whether MRP prices are tax inclusive; do not treat a 0% placeholder as a confirmed tax rate.
- Physically verify distinct MRP editions of the same EAN and their lot-wise quantities.
- Never delete historical sales, GST records or refund entries when resetting test data on a live shop.

## Minimum acceptance tests on a real shop copy

1. Create a staging copy of `data/` and verify SQLite `PRAGMA integrity_check` and foreign keys.
2. Run `python -m unittest discover -s tests -p 'test_*.py' -q` with dependencies installed. Require **zero errors, zero failures**.
3. Run `node tests/check_frontend_modules.mjs`, plus the six optional Chromium smoke scripts if available.
4. Test a full sale and a partial return with verified GST, inclusive MRP, discounts, store credit and loyalty points; reconcile to nearest paisa.
5. Test mixed Cash/Card/UPI payments and cash change; card/UPI excess must be rejected.
6. Open a day, sell, refund, post petty cash, pay a supplier cash amount, close with a physical counted amount, and reconcile X/Z totals.
7. Scan a shared EAN with two MRPs, sell one edition, refund it and ensure only its inventory changes.
8. Re-import an already-received file and supplier invoice; verify no duplication.
9. Exercise each role's **forbidden** as well as permitted operations; cashiers must not alter protected stock or settings.
10. Test opaque/cross-origin POST is blocked, while same-origin requests and authenticated staff flow work.
11. Test local backup then restore (including after app restart); compare original invoice count, customer credit and stock totals.
12. Print on both configured thermal widths and check long descriptions, INR signs, rounding, customer ID and payment allocations.
13. Validate date/time in Asia/Kolkata and invoice FY numbering around the March/April boundary.
14. Only promote to live after sign-off by the operator responsible for accounting and stock data.

### Installation

1. **Back up the complete `data/` directory outside the app folder.**
2. Stop pyPOS and ensure no other instance is writing to the database.
3. Extract the update-only ZIP over the *Salesperson Tracking + Staff Menu Toggle* build, or use the corresponding Git diff after `git apply --check`.
4. Restart, hard-refresh, run the acceptance checks on a staging copy, then promote only if all pass.

This audit is a best-effort source and test review, not a claim of perfect software or production certification.
