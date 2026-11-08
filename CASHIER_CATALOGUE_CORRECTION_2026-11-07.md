# pyPOS cashier catalogue + positive-only stock control correction

**Applies after:** `pyPOS-Inventory-Shrinkage-Hardening.patch` on a repository already carrying the UPI/Till and Windows/login fixes. A separate cumulative patch is provided for the original `pypos.zip` baseline.

## Store-floor behaviour

- Cashiers **can** create product groups and new products. New SKUs may include variants (size, colour, etc.), a positive selling price, an explicit tax rate and non-negative opening stock. Products are available to sell immediately.
- Cashiers can use **Import / Receive stock** for two non-destructive workflows: **Create new, skip existing** (`skip`), and **Create new + add stock to existing** (`add_stock`).
- Editing the price, barcode, tax, variants or identity of an *existing* SKU, deleting products, and merge imports remain manager-only. Existing SKUs receiving stock are checked against their stored identity, price and tax rate.
- Cashier-created products cannot reuse an existing EAN as a new price edition; a manager handles those cases. Case-only duplicates of existing SKUs are also rejected.
- Cashier-created products are clearly marked **Manager Review Required** for later MRP/tax/identity checks; they can still be sold. Their inventory cost is stored as `0` (unknown, not an asserted true cost) and marked **Cost Review Required** for manager review.
- Cashier manual stock movements are goods receipts by default. They cannot reduce stock unless an admin has expressly enabled the legacy cashier-reduction override in Settings. No one-click stock subtraction exists regardless of role.
- The stock movement popups in both Catalogue and Inventory now take **positive quantities only**, with `+1`, `+5`, `+10` shortcuts in Catalogue. Manager-authorised shrinkage is a separate dropdown choice that uses a positive quantity as the count to write off, requires an explanation and asks for confirmation. The backend still independently enforces permissions and logs the authenticated operator.

## Behaviour that is intentionally not automated

- A new cashier-created product is **not automatically approved as correctly priced or taxed**. It is placed in the manager-review queue but remains saleable for fast counter service.
- A cashier can still under-price a deliberately falsified *new* SKU or misrepresent goods without a manager's review. Operator attribution, review flags and later physical counts help detection; they cannot guarantee prevention. Restrict admin credentials and reconcile sales, bank receipts and stock daily.
- Existing cost prices are not exposed to cashiers. They can enter a new SKU and stock, but cannot edit manager-held purchase costs or see cost-valued CSV exports.

## Deployment

1. Back up the actual shop `data/` directory outside the project tree.
2. Confirm the selected patch's expected baseline (incremental = **previous inventory hardening release**; cumulative = **original uploaded pypos.zip**).
3. Run `git apply --check <chosen-patch>`; only apply if the check passes.
4. Run `git apply <chosen-patch>`, restart pyPOS, hard-refresh the browser.
5. Check login as cashier: Add Product, New Group, import new, import existing, receive stock; check manager review and that negative quantity entry is not available. Check that PATCH/DELETE of an existing SKU and cashier stock reduction are denied from the API.
6. Install runtime dependencies and run `PYTHONPATH=.:tests python -m pytest -q tests` with Flask available. Run `python tests/smoke_stock_security_ui.py` if Chromium/Playwright are installed.

No SQL migration, database, or credentials are bundled in the patch. The previously delivered patches are not re-applied when using the incremental version.
