# pyPOS — stock-shrinkage and cashier permission hardening

**Patch baseline:** the combined UPI/Till patch **followed by** `pyPOS-Windows-Null-Origin-Login-Fix.patch`, i.e. the post-login-fix source. An optional cumulative patch is also provided against the original uploaded `pypos.zip`.

**What this patch does not do:** replace a database, erase customer/stock data, introduce a new database migration, call a bank API, or claim to prevent physical theft.

## Role / attack surface review

| Action | Cashier | Manager | Admin |
| --- | --- | --- | --- |
| Sell actual stocked goods at catalogue prices | Yes, open-day and payment rules apply | Yes | Yes |
| Apply sale discount | **0% default**; admin can set whole-sale cap | Yes | Yes |
| Record positive manual stock movement | Yes, reason required | Yes, reason required | Yes, reason required |
| Remove stock via one-click catalogue stepper | **No** | **No** | **No** |
| Record manual negative stock correction | **No by default**. Only when admin explicitly enables high-risk toggle; reason always required | Yes, reason required | Yes, reason required |
| Import new products, modify catalogue prices, merge import fields | No | Yes | Yes |
| Add stock using a CSV/XLSX import | Existing approved SKU only, exact SKU/EAN/MRP/variant; no duplicate override | Yes, normal validated import | Yes |
| Edit/delete product, group and price | No | Yes, but stocked SKUs cannot be deleted | Yes, but stocked SKUs cannot be deleted |
| Post a stocktake that reduces balance | No | Yes, mandatory shortage reason | Yes, mandatory shortage reason |
| Issue refunds/voids, pay suppliers, open/close day | No | Yes | Yes |
| Delete customer record or export financial/inventory CSV | No | Yes (subject to existing extra auth) | Yes |

## Concrete fixes

1. **Catalogue controls:** removes the single-click `−1` stock action for all staff. Cashier screens hide product editing, deleting, catalogue creation, group edits, stock-valued CSV exports and cost values; movements use a form with a reason and a preview. An old/cached or modified browser cannot bypass backend role rules.
2. **Authoritative stock changes:** validates signed whole numbers (no booleans, decimal truncation, zero or excessively large deltas), requires a 4–200-character reason, and only allows `RECEIPT` / `ADJUSTMENT` manual categories. It prefixes audit reasons with the actual logged-in staff username/role; the caller cannot masquerade as a sale, stocktake or import in the stock ledger.
3. **CSV/XLSX imports:** cashier mode is *only* additive receipt for already-approved exact SKUs. A new SKU, wrong EAN, MRP, GST rate, product style or specified variant rejects the entire import. The check runs again inside the locked transaction, not only in Preview. No `force_repeat` or zero-price override. A file digest and operator are written in the import stock-movement reason.
4. **Stocktake shortages:** negative differences require a separately-entered shortage reason before Post; the posting operator and reason appear on the stock movement. The existing stale-count protections still apply.
5. **Deletion:** product deletion fails while stock is nonzero, even for managers/admins. A legitimate zeroing correction must be recorded first. Existing transaction-history foreign-key protections continue to apply.
6. **Discount abuse:** cashier discount limit defaults to **0%** and is calculated server-side from the live stocked merchandise price, after locking the database. An administrator can set a whole-number percentage (0–100). Any discounted checkout above that limit fails atomically, without decreasing stock. Cashier sales attribution is taken from the signed-in account, not an editable free-text field.
7. **Sensitive exports:** cashiers cannot use CSV export endpoints. Cost-price fields are masked for cashier product lists, product details, SKU lookup and multi-MRP lookup. The missing-cost overview metric is hidden, too. Manager/admin export access is unchanged.
8. **Operator audit:** successful API write actions now include the actual resource path (e.g. product ID), rather than only a parameterised route pattern. Existing staff audit tables and logs are reused; no schema changes.

## Safeguards *not* automatically solved by software

- **UPI:** a POS-generated reference and a customer-entered UTR do not prove settlement. A dishonest cashier could still manually mark an unpaid UPI tender as paid. Daily UPI bank reconciliation and restricted access to merchant credentials are essential. Reliable automatic verification requires a supported bank/payment-provider integration.
- **Physical theft:** an employee can steal merchandise without interacting with the POS, or inflate received stock to hide shrinkage if permitted to record inbound stock. Compare supplier invoices against goods actually delivered; supervise adjustments; carry out independent cycle counts.
- **Manager/admin authority:** authorised managers can still post a documented correction, refund, supplier payment or stocktake. For higher assurance, institute two-person approval for write-offs, refunds and cash payouts. This patch does not implement a new two-person approval system.
- **Database file access:** someone who can directly modify or delete the SQLite file can tamper with local records outside these API controls. Restrict operating-system file permissions, protect Windows accounts, back up off-device and independently retain daily closing/reconciliation reports.
- **Session sharing:** staff must sign out or lock the workstation. PINs should be individual; do not share admin credentials.

## Testing (disposable databases only)

Available in this environment:

```bash
PYTHONPATH=.:tests python -m pytest -q tests --ignore=tests/test_auth.py \
  --ignore=tests/test_security.py --ignore=tests/test_till_http.py \
  --ignore=tests/test_till_upgrade.py -k 'not loopback_detection'
node tests/check_frontend_modules.mjs
python tests/smoke_stock_security_ui.py
python tests/smoke_new_workflows.py
python tests/smoke_sku_dropdowns.py
```

Required on a machine with Flask installed (not runnable in the current sandbox):

```bash
python -m pip install -e .
PYTHONPATH=.:tests python -m pytest -q tests/test_stock_hardening_http.py \
  tests/test_till_http.py tests/test_auth.py tests/test_security.py
```

Before shop-floor deployment: log in as an actual cashier, check Catalogue controls, try direct `POST /api/products/<id>/stock` with a negative quantity, test a wrong-MRP import, test a 100%-discount checkout on an open day, and verify all return clear rejections without a stock change. Then test a real Manager correction and confirm the SKU, before/after quantity, operator and reason are in Stock History. Confirm UPI against your merchant bank feed. Maintain a tested database backup.

## Installation and patch ordering

1. Shut down pyPOS. Back up its `data/` directory (not included in these patches).
2. If your source already has the **Windows Null-Origin Login Fix**, apply `pyPOS-Inventory-Shrinkage-Hardening.patch` from your repository root.
3. If your source is still the **original `pypos.zip`** uploaded in this chat, apply the separate cumulative patch instead — **never both**.
4. Run `git apply --check <file.patch>` before `git apply <file.patch>`. Do not use `--reject`, `--3way`, or `--unsafe-paths` on mismatched local source.
5. Restart the server, hard-refresh the browser, and run the HTTP tests shown above before live use.
