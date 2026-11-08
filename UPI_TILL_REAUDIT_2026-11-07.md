# pyPOS — UPI and till-operations re-audit (7 November 2026)

## Patch scope

Apply **after** `pyPOS-UPI-Till-Operations-Patch.zip` / its matching Git patch. This archive contains only changed/new files. It does not include or touch a SQLite database. All previous billing, refunds, staff menus, branding, catalogue and receipt functionality remains in place.

### Fixes confirmed during this re-audit

1. **Runtime packaging:** The application uses Flask, Waitress and `qrcode`; the pip/pipx installation, Windows and POSIX launchers, portable ZIP-app builder, and GitHub native-binary build now install/check all three. Previously, some installation methods omitted `waitress` and/or `qrcode`, which could prevent launching the till or showing a UPI QR.
2. **UPI bank UTR reuse:** A non-empty bank UTR cannot be recorded on a second UPI payment, even with a different POS reference, nor reused twice in one split transaction. Duplicate submissions fail atomically without changing stock, sales or payments. Alphanumeric UTRs are normalised to uppercase for comparison. Empty UTRs remain allowed so a cashier can record a bank-verified payment when the UTR has not been supplied; this **does not** establish automated payment verification.
3. **UPI amount changes:** Changing the method or amount clears an earlier UPI "payment received" confirmation and previously typed UTR, requiring the cashier to confirm the *current* transfer details again. Amounts with fractional paise are rejected before QR generation.
4. **UPI reference retry:** If the local POS reference endpoint temporarily fails, the cashier can retry it using Copy Reference or Show QR, without deleting the payment row.
5. **Double-click parking:** The Park Sale action is disabled while its request is in flight, stopping accidental duplicate tickets from rapid clicks. (Server-side idempotency for manually repeated HTTP Park requests remains future work.)

## Checks actually executed

- Reassembled source from the India/Loyalty release and every successive update through the 7 November UPI/till-operations release.
- Python unittest discovery: **234 tests run; 230 pass; 4 errors caused by missing Flask, not test assertions.** Flask cannot be installed in the current execution environment. This must not be presented as a completely passed HTTP suite.
- `node tests/check_frontend_modules.mjs`: **22 ES modules parse and link**.
- `python tests/smoke_new_workflows.py`: isolated Chromium test passed for closed-day UI gate, Park double-click, UPI reference failure/retry, amount-change reconfirmation, UTR entry and QR display.
- `python tests/smoke_cash_drawer.py`, `smoke_multi_mrp.py`, `smoke_sku_dropdowns.py`: isolated Chromium tests passed.
- New disposable-DB tests confirm UTR uniqueness across sales and split payment rows, atomic rollback, blank-UTR compatibility and package-dependency consistency.

## Remaining requirements before production deployment

These were **not** verified here: complete Flask HTTP/authorization tests, actual Windows localhost login and origin handling, physical scanner and thermal-printer operation, true bank UPI settlement confirmation, real-data migration/restore, backup recovery, live GST invoice and credit-note compliance.

UPI POS references and UTRs are **not equivalent**. A QR or typed UTR never proves money was received; staff must check the merchant bank/payment application. The client-side manual-confirmation control is an operational reminder and is not a cryptographic verification mechanism.

### On the actual Windows till

1. Stop pyPOS, copy its complete `data/` directory to a separate safe location.
2. Apply this patch over the **UPI/Till Operations** release; use ZIP extraction or Git, not both.
3. In a virtual environment run `python -m pip install -e .` (or run the revised `run.bat`).
4. Open the address emitted by the app (normally `http://127.0.0.1:8420`) in a regular browser, not a `file://` page; keep host and port consistent.
5. Check first-run admin setup, restart/re-login, manager and cashier roles; verify cashier stock increases succeed but stock decreases fail by default, and changing the admin toggle affects server-side authorization.
6. Open Day, Park/Recall, and sell via cash and mixed tender. Check that UPI QR shows the configured **merchant** VPA and the **exact** amount. Check any UTR in the merchant/bank application. Try to reuse a recorded UTR; the sale must be rejected.
7. Verify partial refund, stock restoration for exact MRP edition, X/Z reports and closing the day.
8. Print an actual 58/80-mm receipt; verify backup and restore against a disposable copy, then repeat the full Flask suite: `python -m unittest discover -s tests -p 'test_*.py'`.

**Release verdict:** code-reviewed and staging tested. **Not production-certified** until the outstanding HTTP/Windows/hardware/security checks pass.
