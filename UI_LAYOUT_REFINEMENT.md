# pyPOS layout refinement

## Scope

UI-only patch against the **Production Audit Fixes** release. Does not change tax, invoices, sales, staff, database schema or cash drawer logic.

- Register header: Inventory and Parked are grouped at the right, rather than distributed across the page.
- Register empty state: distinguish an empty database from no search/filter results and link to the existing Catalogue import flow.
- Purchasing: indented section heading, helpful empty state and CTA buttons; receipt table aligns to the padded content area.
- Purchasing goods receipt: responsive SKU/quantity/cost/remove row alignment.
- Stocktake: consistent input/button alignment.
- Shared `form-grid` now actually has a layout, improving Settings and other forms.
- Reports: table and breakdown cards have consistent internal padding.
- Desktop and narrow-screen action groups no longer sprawl across the page.

## Installation

Back up your installation. Extract this patch archive into the current application root, replacing matching files, and hard-refresh. Alternatively apply the supplied unified diff. Do not apply both.

## Compatibility

No Python or SQL files changed. The existing application routes, data records, receipts, refunds, inventory, branding, staff and cash drawer capabilities are retained.
