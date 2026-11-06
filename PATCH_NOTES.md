# pyPOS India Edition: Customer, Sales and Reporting Patch

**Baseline:** `pyPOS-India-Edition-Loyalty.zip` (the previous loyalty-enabled release). This is a **patch, not a complete application**. No database file, uploaded logos, or other private data are included.

## Install

1. **Back up your current database and modified code**, preferably by committing/stashing your local changes.
2. In the *root of your existing pyPOS repository* (the folder with `main.py` and `pos/`), extract the contents of `pyPOS-India-Insights-Patch.zip`. File paths start with `pos/` and `tests/`, **not** `pyPOS-main/`. For example:

   ```bash
   unzip -o /path/to/pyPOS-India-Insights-Patch.zip -d /path/to/your/pyPOS-main
   ```

   If you use Git, instead check and apply the standalone unified patch:

   ```bash
   git apply --check /path/to/pyPOS-India-Insights.patch
   git apply /path/to/pyPOS-India-Insights.patch
   ```

   **Choose one installation method**, not both. If `git apply --check` reports conflicts, review the changed files; do not overwrite your newer local edits blindly.
3. Restart the Flask application so the server code changes load. Hard-refresh the browser (`Ctrl+Shift+R`) to reload the updated JavaScript and CSS.
4. Open Register, Customers, Sales and Reports to verify the UI. Take a test sale and return before using the reports for actual accounting.

## What changed

- **Register:** The fixed-amount discount option now uses the shop's configured currency (`₹` by default), instead of a hard-coded `$`.
- **Customers:** Prominent loyalty points column (click the points to manage them with admin authentication); totals for all registered customers, points outstanding, customers holding points, and store credit; filter by points or store credit; search by name/phone/email across the database; sort displayed results. Customer list results are capped at **500 per search**, with a visible warning and narrower search guidance. The summary totals still cover all customers.
- **Sales:** Search by invoice number, receipt ID, customer name, phone or payment method; 100-document pages with Load More; today/7-day/30-day/custom/all-time filters using **IST**; completed/refund/void filters; net-effect values shown as negative for refunds and zero for voids; direct invoice/receipt access; visible loaded-row totals. Date links from Reports open exactly that day in Sales. CSV exports include **all documents for the selected dates**, and are not restricted to the loaded rows, local search or status filter.
- **Reports:** Clear Overview / Revenue analysis / Operations sections; 7/14/30/90-day ranges; today's net figure; net sales, gross completed sales, refunds and average checkout; comparison with the immediately preceding equal-length period; selectable chart for gross, net and refunds (handles negative days); daily IST drill-down to Sales; top products net of returns with recorded line-level discount and tax; net payment methods; low stock and end-of-day controls.
- **Accounting consistency:** All revenue and report calculations use recorded documents and IST day boundaries. Refunded line quantities and amounts are deducted; void invoices are excluded from net sales. Net tax reflects recorded tax less refunds. Refunded sales appear in the period the refund document was issued, not the period of the original sale.
- **Refund UX:** Gross value in the return selector is identified as an estimate rather than falsely displaying an exact refund figure. The server's recorded invoice allocations determine the amount on the final refund receipt.

## Revenue definitions

- **Gross completed sales**: Sum of invoice totals for completed sales, **including GST**, before recorded refunds.
- **Refunds**: Sum of separate refund documents created during the selected period.
- **Net sales**: Gross completed sales minus refund documents. This is **not** profit, bank deposits, or a filed GST return; refund timing can cause negative individual days.
- **Average checkout**: Gross completed sales divided by completed invoice count; not affected by later refunds.
- **Top-item net value**: Net sold/returned item amounts with the original discount and tax allocation, including GST.
- **Payment mix**: Net recorded cash/card/UPI payments (store credit is excluded). Refunds to store credit are not bank/card reversals.
- **Voids**: The original invoice is excluded, regardless of when the void action was performed. This means historical periods can change if an older sale is voided.

## Technical notes and validation

Modified files include `pos/app.py`, `pos/customers.py`, `pos/reports.py`, `pos/sales.py`, four JavaScript modules, one stylesheet, and the register discount selector. A new `tests/test_reports_patch.py` covers accounting and search regressions. **No schema migration** is required for this patch over the loyalty-enabled version.

Tests can be run from the repo root with an environment that has dependencies installed:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests
node --test tests/frontend_test.mjs  # if npm dev dependencies are installed
```

The test environment used for this patch did not have Flask or jsdom, and browser navigation was blocked. As a result, Flask-dependent API suites and live-browser UI tests remain **unverified** here. Python backend regressions and JavaScript syntax checks were run.
