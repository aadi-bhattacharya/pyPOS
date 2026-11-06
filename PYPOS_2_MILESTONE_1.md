# pyPOS 2.0: register and inventory milestone

This is an **update-only patch** built against the following existing installation:

1. `pyPOS-India-Edition-Loyalty.zip` (base)
2. `pyPOS-India-Insights-Patch.zip`
3. `pyPOS-Blank-Screen-Hotfix.zip`
4. `pyPOS-Stock-Import-Patch.zip`

There is **no database file, customer record, transaction data or full application** in this patch.

## Install

1. **Stop pyPOS.** Make a copy of your existing `data/pypos.db` and, ideally, the whole `data/` directory. Never import over your only copy of the database.
2. Unzip the patch in the existing repository root (the folder containing `main.py` and `pos/`). Overwrite matching files and keep all other files.
3. Alternatively, from a clean checkout of the exact baseline above, check and apply the Git diff:

   ```sh
   git apply --check pyPOS-2.0-Milestone-1.patch
   git apply pyPOS-2.0-Milestone-1.patch
   ```

   Use **one** installation method, not both. For locally modified files, examine the diff first.
4. Restart the app and hard-refresh the browser (`Ctrl+Shift+R`). No new runtime packages are needed.
5. The next app launch adds new tables/columns automatically. Database migration is idempotent. Check existing balances and completed sales before processing real transactions.

## What is included

### Register
- One browse card per existing `product_groups` style instead of hundreds of SKU cards.
- Search by style, exact SKU/barcode, brand, category, size, colour or any editable attribute.
- **Exact barcode/SKU scans add that specific SKU.** Unknown codes never auto-add a different similar item.
- Multiple-variant styles open a selector with editable-attribute filters and exact SKU/stock/price detail. Single-SKU styles add immediately.
- Favourites, recently selected styles, in-stock filter, horizontal category filter and progressive Show more list.
- Original checkout engine, payments, GST, discounts, parked sales, customer loyalty and receipts are unchanged.

### Inventory
- Dedicated Inventory navigation, searchable/paginated SKU table and SKU-level stock overview.
- Per-SKU reorder point; blank inherits the existing global Settings threshold.
- Receive goods (positive quantity), record manual stock adjustments (+/-), attach an explicit reason or supplier invoice reference.
- Record and inspect movements for each SKU or recent movements across products.
- Create brands/categories and classify an existing style without changing any products' SKU, attributes, quantities or sale IDs.
- Existing additive stock importer and catalogue editor remain available from the inventory screen.

### Database & audit
- Adds `brands`, `categories`, `stock_movements`.
- Adds nullable `product_groups.brand_id`, `product_groups.category_id`, `products.reorder_point`.
- All stock updates (including direct quantity updates) are captured by SQLite triggers. Known operations label events `SALE`, `REFUND`, `VOID`, `RECEIPT`, `ADJUSTMENT`, `IMPORT_RECEIPT`. Other updates remain labelled `CHANGE` rather than guessing their origin.
- On first upgrade, existing positive stock quantities become `OPENING` balances. No historical deliveries, stock losses or movements are invented.
- Existing `sales`, `sale_items`, `payments`, SKU IDs, loyalty events, invoices and prices are preserved.
- Category and brand assignments are intentionally left blank until explicitly classified. No guesses from product names.

## Important distinctions
- A product **style** is an existing `product_groups` record. Each variant remains a separate existing `products.id` and SKU.
- Stock `RECEIPT` means a real delivery. Never use it to import an earlier stock snapshot; that would double-count units. For existing spreadsheets use the existing catalogue import workflow deliberately.
- Stock `OPENING` means the measured database balance at migration, not the beginning of trading activity.
- A zero cost in the inventory screen means no recorded purchase cost in the current schema; it does **not** establish profitability.

## Not yet included
The full longer-term architecture called for batch physical stocktake/reconciliation, full purchase receiving with supplier/bill records, operator roles and per-user audit attribution, brand/size performance reports, and historical cost-of-goods snapshots. Those require separate implementation and testing. The new Inventory screen already supports individual movements and classification, not batch stocktake.

## Verification performed
- The prior project's **129** selected non-Flask backend regressions and **7** new inventory/migration regressions passed (136 total).
- Real latest-previous-schema upgrade tested with two products and a completed sale: product IDs/quantities, sale lines and completed sale totals unchanged; two opening-balance rows created once.
- 12 JavaScript modules parse and all seven page imports link.
- Actual Chromium UI smoke test with isolated fixture API (no Flask server) rendered grouped styles, added the selected exact variant to the cart and displayed three SKU rows on Inventory without runtime errors.
- **Limitation:** Flask-dependent API tests and a real connected-server browser test are not available in this environment. Perform a backup-first local verification of the stock and payment workflows before live use.
