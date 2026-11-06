# pyPOS 2.0 — Returns & Refunds patch

This is an **update-only patch** for the version that already has the Billing/Refund Repair and Receipt Layout Cleanup changes installed. It is not the complete application and it does **not** include any shop database.

## New workflow

1. Open **Returns & Refunds** in the left navigation (or **Sales → Returns & Refunds**).
2. Search by invoice number, sale ID, customer name or phone. This searches historical completed invoices without restricting the date.
3. Select **Return items**, then use the quantity controls for the **exact original SKUs** being returned. No items are selected initially.
4. Choose a return reason and optionally enter details.
5. Select the original payment methods or customer store credit (only available when the sale has an attached customer).
6. Review the server-calculated exact refund total and payout split. The original recorded discount and tax allocations are preserved.
7. Confirm. pyPOS records a refund receipt linked to the original sale, restores returned stock, reverses applicable loyalty points and records cash/card/UPI or credit allocation.
8. Print the refund receipt. Existing refunds remain visible in **Returns & Refunds**, and an all-time **Refunds CSV** export includes original invoice, reason and payment split.

### Critical accounting distinction

**Cash/card/UPI refunds are recorded, not paid automatically.** The cashier must physically hand over cash or initiate card/UPI repayment, and reconcile these actual payouts externally. Store credit is booked immediately in the POS customer ledger. There is no payment-gateway refund API or bank settlement confirmation.

A refund receipt is an operational record; it is **not** an independently numbered GST credit note. This patch does not automate statutory credit notes, GST return adjustments or IRN/e-invoice actions. Review GST correction obligations with your accountant.

## Data safety and compatibility

- **No existing table or column is replaced or dropped**. The only new table is `refund_requests`, used to make browser retries idempotent (a repeated request returns the original refund rather than restocking or repaying twice). It is created automatically during application DB startup.
- Invoice numbers, product IDs, saved original tax/discount amounts, store credit ledger and loyalty history remain intact.
- Original sale payment methods are used for a proportional repayment allocation. Split cash/card/UPI and original store-credit portions are handled.
- An explicitly empty `lines: []` request is rejected to prevent accidental full-sale refunds. Legacy API callers that omit `lines` entirely retain their previous "refund all remaining" behaviour.
- Reasons are stored on **new** refund documents and exported in `GET /api/export/refunds.csv`. Existing historical refund documents remain untouched.
- The Returns page requires a reason; the API reason is optional for backward compatibility.

## Install

1. **Make a verified copy of your existing database and `data/` folder.**
2. Stop the application.
3. Extract `pyPOS-Refunds-Patch-Files.zip` into the existing application root (where `main.py` and `pos/` live), replacing matching source files; or apply `pyPOS-Refunds.patch` with `git apply --check` then `git apply`.
4. Restart pyPOS; `init_db` creates `refund_requests` automatically. Hard-refresh the browser (`Ctrl+Shift+R`).
5. On a disposable test invoice, perform a one-unit cash refund, then a mixed-tender refund, and reconcile the printed receipt, physical cash, stock and loyalty transactions before using live business records.

## Verification performed

- 177 non-Flask tests passed plus 18 subtests; 1 Flask-related test deselected. Authentication and Flask route tests could not be run because Flask is unavailable in this environment.
- Chromium isolated-UI test passed for Returns navigation, searching an invoice, selecting a quantity, getting a precise refund preview, confirming, and rendering the receipt.
- Backend preview-vs-posted tender split, store-credit, remaining quantity, refund reason, CSV export, idempotent retries and SQLite concurrency tests passed.
- All 15 frontend ES modules parse and link, including Cash Drawer navigation.
- This patch does **not** fix previously identified unrelated cash-session timestamp attribution, checkout overpayment UI, or large-discount allocation defects. Those require a separate reliability patch.
