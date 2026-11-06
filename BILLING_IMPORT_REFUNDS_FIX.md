# pyPOS: clean receipts, import review, and return handling

**Patch base:** pyPOS 2.0 + Branding + Cash Drawer Restoration (including the older Insights, Loyalty, and Stock Import patches). **This is a changed-files patch only.** The SQLite database, inventory IDs, sales, cash sessions, customers, loyalty and report data are not replaced.

## What was wrong

Imported source references and review warnings (`Source Sheet`, `Source Row`, `Original SKU`, `Cost Review Required`, `GST Review Required`, `Import Notes`) were saved as editable product attributes. Checkout concatenated **all** attributes into the sale description, so thermal receipts printed internal record-keeping instead of clean product variants.

Missing imported GST was persisted as `tax_rate=0` due to the existing numeric schema. **That is a placeholder, not a verified legal zero rate.** Applying this patch does not retrospectively change a completed invoice's original financial amount, tax record, or audit description.

## What changes

- **Receipts and Register** show only product name plus customer-facing variants: Colour/Color, Size, Fit, Fabric, Pattern, Pack, Length and Waist. HSN (when supplied), quantity, UQC and recorded price remain. Other attributes are still editable in Catalog.
- **Existing sale receipts:** saved descriptions are preserved verbatim in the database; old reprints receive a display-only cleanup. Old transactions whose original descriptions show an unverified GST rate receive a visible warning instead of being silently presented as tax-reviewed.
- **Catalog and Inventory:** compact customer-facing variant labels, a clear GST/price review indication; the Catalog has a `Billing review` filter with the number of flagged SKUs.
- **Import:** new products without GST continue to import as drafts with a review marker. Numeric zero remains only as a database placeholder. An explicit GST rate supplied in `merge` mode clears the GST review marker; a blank never clears it. Existing stock increment and SKU matching rules remain unchanged.
- **Checkout safeguard:** a SKU with `Price Review Required` cannot be sold until the price is reviewed. If the store has a GSTIN in Settings, a SKU with `GST Review Required` cannot be billed until its tax rate has been explicitly confirmed. The catalogue editor has checkboxes to confirm the rate/price; even an actual 0% rate can be confirmed after independent review. This is a checkout validation, **not a permanent attribute edit lock**. All product attributes remain editable.
- **Refund preview:** selecting quantities now asks the backend for the actual prorated discount and GST from the original invoice. The final refund still recomputes amounts atomically when confirmed.
- **Refund receipt:** distinguishes the amount refunded from a sale payment; references the original invoice number; identifies cash/card/UPI refunds or returned store credit; shows any loyalty points reversed.

## Handling refunds

Existing `sales.refund_sale` creates a separate `REFUNDED` child record, with item-level quantities and the source invoice's historical unit prices, taxes and discounts. It prevents exceeding available refundable quantities. Insertion of return lines restores stock through the existing database trigger. Recorded cash/card/UPI payments or original store credit are allocated back to those sources; alternatively, a return may be credited to a customer's store-credit wallet. Loyalty earnings are proportionally reversed, even when earlier redemption makes the points balance temporarily negative. A sale that has already been partially refunded cannot also be voided.

**Important Indian compliance boundary:** the printed *Refund receipt* is an operational proof of return, not a fully compliant GST credit note. GST-registered sellers may need a separately numbered tax credit note under CGST Section 34 and Rule 53, and corresponding return reporting. This patch does **not** claim to create those. Confirm the correct document and tax treatment with the business's accountant before relying on refund slips for GST returns.

## How to review old imported inventory

1. Back up your entire `data/` folder before patching.
2. Install the changed files (or Git diff) into the existing repo and restart pyPOS. Hard refresh your browser (`Ctrl+Shift+R`).
3. In **Catalog → Billing review**, inspect all flagged SKUs. For each, edit the actual GST rate and confirm it, or prepare a corrected **additive merge** file with explicit rates. A zero rate must be explicitly verified, not assumed from the import default.
4. Review imported items with `Price Review Required` and confirm selling prices before selling them. Missing cost prices remain flagged for margin reporting but do not affect customer receipt descriptions.
5. Verify Settings → price tax mode (inclusive vs exclusive), business GST registration/details, place of supply, receipt heading, and 58/80mm printer settings.
6. Make a sample sale and partial refund in a **copy** of your database before using the patch live. Ensure the receipt matches the original historical tax allocations and refund tender.
7. **Do not re-import a historical stock snapshot as a goods receipt.** It will add quantities again. Use `skip` for safe catalogue creation and `merge` only when you intend additive stock effects and/or explicit field updates.

## Data preservation and testing

The patch adds no migration and no destructive database query. It changes how receipts and register views display data, plus explicit validation around recorded review flags. Existing product IDs, quantities, payments, refunds, credit and loyalty ledgers, invoice amounts and cash sessions stay intact.

Validated with 155 non-Flask backend tests, 14 frontend-module parse/link checks, a Chromium receipt/refund print preview smoke test, and a Chromium smoke test for Start Day, Cash in Hand, X, End Day, Z and history. Flask-dependent API/full HTTP tests could not run here because Flask is unavailable in this environment. The price/tax flags are added only for existing imported rows that already carry their original review metadata; the patch does not reclassify unrelated legacy SKUs.
