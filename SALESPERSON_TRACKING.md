# Optional salesperson tracking

Apply this update **after** the Retail Hardening and Staff Menu Toggle patches. Extract changed files at the repository root. Back up `data/` first. Restart pyPOS to add the non-destructive `sales.salesperson` and `parked_sales.salesperson` columns.

In Register → Current Sale, type a salesperson name (optional). It is independent of Staff/PIN login, is saved with the sale, and is visible in Sales and the sales CSV export. You can search the Sales screen by salesperson name. It never appears on printed receipts or invoice snapshots.

The value persists in the active cart and in parked sales, and is cleared after checkout or clearing the cart. Existing sales have an empty salesperson field. The original customer association, tax, payment, return, inventory, loyalty and cash-drawer logic is unchanged. Free-text names are not identity-verified: for reliable per-person totals, use consistent spelling.

This is an attribution field, not a staff permission control. A retrospective correction UI and dedicated salesperson aggregation report are not included.
