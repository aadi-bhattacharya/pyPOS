# pyPOS: Shared EAN / Multiple-MRP stock

**Base:** pyPOS 2.0 with the latest Returns & Refunds patch applied (which follows the receipt-layout and cash-drawer restoration patches).

This ZIP is **patch-only**. It contains no database, invoice files, or full app. Back up the `data/` directory before deploying.

## What changes

- A barcode can now refer to **multiple price-edition SKUs** for the same underlying style/size/colour.
- On a scan, the Register offers **MRP ₹X · N units available** for each edition. Cashier verifies the printed MRP on the physical package before selecting. Out-of-stock choices are disabled.
- Each edition retains its **own SKU, current MRP, quantity and product ID**. Selecting one deducts *that* edition's stock, and partial refunds/voids restore *that* edition's stock using the existing sale-item/product-ID linkage.
- Searching by exact SKU still adds that exact SKU. Manual product browsing also shows each price edition separately.
- Catalogue XLSX/CSV imports allow the same barcode on multiple rows **only when the MRP differs and the product identity agrees** (name, known colour/size/style). A repeated identical barcode+MRP refers to the same edition and is skipped or has stock added according to import mode.
- The existing unique barcode in `products.barcode` is preserved; a small `barcode_price_options` table records secondary EAN assignments. This avoids rebuilding core product tables or modifying existing invoice rows.
- Product prices, barcodes, categories and attributes remain editable under the existing Catalog controls. No cash-drawer, loyalty, branding or reporting functionality is removed.

## Recommended import workbook

`Scanned-pyPOS-Multi-MRP-Import.xlsx` is prepared from the uploaded `Scanned.xlsx`:

- **Import**: 586 price-edition SKU rows, 1,018 units.
- **Price_Editions**: the five EANs with multiple confirmed prices. Their chronological age is **unknown**; check the marked MRP rather than assuming higher means newer.
- **EAN_Conflicts**: six source rows/eight units from three EANs with contradictory size/colour information, held for manual reconciliation, not automatically imported.
- **Source_Audit**: all original 630 rows and 1,026 recorded units.
- **Read_Me**: usage instructions and data assumptions.

All supplied MRPs are below ₹2,500. The workbook uses a **provisional 5% GST** based on the previous classification assumption; confirm category and actual GST applicability. Selling at MRP requires the `inclusive` price-tax mode in Settings. Purchase costs and HSN descriptions remain blank; no invented source data.

## Installation

1. Stop pyPOS. Back up `data/` in a separate folder. Preferably create a manual SQLite backup too.
2. Extract the **patch-only** ZIP into the app root, overwriting matching `pos/` and `tests/` files.
3. Restart pyPOS. The new secondary-barcode table is created automatically when the app initialises its existing SQLite database.
4. Hard-refresh the browser with Ctrl+Shift+R. In Catalog → Import, preview the new Excel file. For **existing stock counts**, choose **Create new / Skip existing**, not Add Stock.
5. Confirm the five multi-MRP barcodes; scan one and select the actual package MRP. Test a sale and refund on a copy of your database before taking live payments.

### Git alternative

`git apply --check pyPOS-Multi-MRP.patch` then `git apply pyPOS-Multi-MRP.patch` (only if your working tree matches the base). Do **not** apply both ZIP and `.patch`.

## Important rules and limitations

- A repeated scan does **not** automatically prefer the lower or higher price. This is intentional; the physical item and its printed MRP decide.
- Duplicate EANs with mismatched colour/size/style are **rejected for review** to avoid selling one variant as another.
- Import in **Add Stock** mode is additive even on repeat upload. Repeating a stock snapshot in this mode doubles counted quantities; do not do so.
- The app preserves historical invoice prices; it does **not** backdate old purchase batches or infer manufacturing dates.
- An EAN assigned to two different styles cannot be registered automatically.
- The price versions are represented by separate existing-style product/SKU rows (not by an accounting stock-lot costing model). There is no supplier-level batch valuation or FIFO ageing in this patch.
- Full Flask HTTP/authentication and physical scanner/printer testing should be performed on the deployed workstation. Local backend tests and isolated Chromium UI tests are included.
