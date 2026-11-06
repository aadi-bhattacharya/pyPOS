# pyPOS India Edition: blank-screen hotfix

Apply this **on top of** `pyPOS-India-Insights-Patch.zip` (the version with
Revenue/Reports/Sales improvements). No database changes and no change to
loyalty balances, sales or invoices.

## Cause

`pos/static/js/views/reports.js` contained two extra closing parentheses,
one in the overview stock-alert cards and one in the daily-sales table. This
made the Reports ES module invalid. Because `app.js` imports all views
statically, **every screen** stayed blank, including Register and Customers.
The earlier syntax-check statement was incorrect: the prior check did not
force ES-module parsing. This patch adds a check that does.

## Install (only changed files)

1. Stop pyPOS.
2. Extract this ZIP into the project root (`main.py` and `pos/` live here).
3. Replace `pos/static/js/views/reports.js` with the included file.
4. Restart pyPOS and use Ctrl+Shift+R (hard refresh) in the browser.
5. Click Register, Customers, Sales, Reports, and Settings to confirm pages load.

Or use the separate `.patch` file with `git apply --check ...` and
`git apply ...`; use **one method only**.

## Regression check

From your project root, with Node.js installed:

    node tests/check_frontend_modules.mjs

This checks browser module syntax across all app JavaScript files, then links
all six page imports. It does not require npm packages or a running server.

If you are running a **packaged executable** rather than source, the executable
must be rebuilt because simply changing source files cannot replace embedded
frontend resources.
