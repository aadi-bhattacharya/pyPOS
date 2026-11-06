# pyPOS Receipt Layout Patch — 5 November 2026

**Apply after:** `pyPOS-Billing-Refunds-Repair-Files.zip` (the latest billing/refund repair), including the preceding cash drawer and branding updates.

This patch changes only how existing receipts are **rendered and printed**. It does not recalculate invoices, change stored financial values, modify stock, create migrations, or change customer records.

## Changed files

- `pos/static/js/receipt.js`: each product displays item name, quantity and unit price, then the saved line total, with variant and HSN details underneath. Date/time now has its own single line. Retail receipts show the existing numeric **Customer ID** instead of the customer's name. GST-registered buyers retain their legal name in the separate buyer-information block when a GSTIN exists. Replaces the wordy subtotal caption with `Subtotal`. Refund receipts use the same item structure.
- `pos/static/css/app.css`: item-column styling for 80 mm and A4 printing; a readable stacked layout for 58 mm printing; receipt-specific styles only.
- `tests/smoke_receipt_layout.py`: standalone Chromium smoke test for printed layouts (no data writes).

## Installation

1. Back up your application folder and especially `data/`.
2. Stop pyPOS.
3. Extract the files from this update-only ZIP **into your existing pyPOS repository root**, overwriting only the matching paths above.
4. Restart pyPOS and refresh your browser using Ctrl+Shift+R.
5. Print a sample receipt and check your specific printer margins/paper driver.

Alternatively, from the pyPOS repository root:

```sh
git apply --check /path/to/pyPOS-Receipt-Layout-Cleanup.patch
git apply /path/to/pyPOS-Receipt-Layout-Cleanup.patch
```

Use **either** ZIP or Git patch, not both. The Git patch is based on the exact Billing & Refund Repair release. If you edited either affected file locally, compare changes before overwriting.

## Testing and limits

- JavaScript syntax and cross-module imports checked.
- Chromium smoke-tested at 58 mm, 80 mm and A4 print widths for inline timestamps, layout overflow, customer ID and line totals, plus refund receipts.
- The existing receipt smoke test passes.
- 155 available non-Flask backend tests pass. The full Flask API/integration test suite was not runnable because Flask is not installed in the testing environment.
- **No records were altered**, and receipt `line_total` still comes from the original sale details, not a fresh recalculation. This patch does not fix previously incorrect GST rates, issue statutory credit notes or alter payment/refund processing.
