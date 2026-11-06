# pyPOS 2.0 — Cash Drawer visibility restoration

Applies **after** `pyPOS-2.0-Branding-Patch.zip` on top of Milestone 1.

## What went wrong

The Insights/Reports redesign kept the cash-session backend and X/Z report UI but
moved them under **Reports → Operations & close-out**, leaving the overview as
the default tab. This hid essential Start Day, End Day, Cash in Hand and X/Z
controls from the main till workflow. This patch restores direct access.

## What this patch does

- Adds a visible **Cash Drawer** menu item (independent of Reports navigation).
- Displays the current cash session state at the top of **Register**.
- Provides **Start Day** when no session is open, and **Cash in Hand (expected)**,
  **X Report**, and **End Day** when a session is open.
- Refreshes the Register's cash summary after successful checkout.
- Provides a dedicated Cash Drawer screen with opening status, expected balance,
  cash tender received/refunded, closing count, printable reports and history.
- Adds a visible Cash Drawer shortcut to the Reports overview.
- Reuses the **existing** open/close endpoints, cash-session report functions,
  opening-float and closing-count dialogs, and X/Z print logic. Nothing financial
  is recalculated by new client-side code.

**Expected cash is not physical cash counted.** It equals opening float + cash
received − cash refunded within the open session. Use End Day to record the
actual counted drawer amount and the over/short difference. Cash transactions
entered outside an open session are not retroactively assigned to it.

## Files

Only files present in the patch archive are changed. No database, sales,
SKU, customer, stock, settings or loyalty data is included. There are **no
schema migrations** and no changes to `pos/cash_sessions.py` or financial API
routes.

## Apply

1. Back up your application and `data/` directory.
2. Stop pyPOS.
3. Extract the patch ZIP into the application repository root (alongside
   `main.py` and `pos/`), overwriting matching files. Or use the Git patch,
   **not both**.
4. Restart and hard-refresh the browser (`Ctrl+Shift+R`).
5. Verify **Register → Start Day → Cash in Hand → X Report → End Day → Z Report**;
   review Cash Drawer history and Reports → Operations & close-out.

## Verification

- `node tests/check_frontend_modules.mjs`
- `node tests/check_branding.mjs`
- `PYTHONPATH=tests:. python -m unittest test_cash_sessions -v`
- Optional Chromium test (requires Playwright): `python tests/smoke_cash_drawer.py`

The browser smoke test uses mock responses, so it does **not** create a real
cash session or modify your database. Test on your actual POS installation
before relying on the day-end figures.
