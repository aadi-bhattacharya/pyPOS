# pyPOS — Additive stock import (India Edition)

This patch adds a **Catalog → Import stock** action and a reusable Excel import
workbook. It changes neither the existing stock database nor existing customers,
sales, loyalty, HSN details, or receipts by itself. It never deletes products.

## Apply the patch

1. **Back up your database** from Settings first.
2. Apply `pyPOS-Stock-Import-Patch.zip` to the root of your *existing loyalty +
   insights + blank-screen hotfix* version, overwriting only files in the ZIP.
3. Restart pyPOS and hard refresh the browser (`Ctrl+Shift+R`).
4. Open **Catalog → Import stock**.
5. Download a fresh editable template from the import dialog or use one of the
   two prepared workbooks supplied with this response.
6. Select the file, choose a SKU conflict policy, **Preview import**, and inspect
   the new/existing/price/GST totals before **Import now**.

Accepts `.xlsx` files (sheet named `Import`) and UTF-8 `.csv` files. The first
row must use the column names in the supplied template. Columns prefixed
`attr:` are imported as ordinary **editable product attributes**; add any
number of extra attributes such as `attr:Fabric` or `attr:Supplier`.

## Modes (none replaces existing inventory wholesale)

- **Create new, skip matching SKUs (default):** safe to re-import the same file;
  existing quantities, prices and attributes stay exactly as they are.
- **Add stock:** create new products, then *increase* the quantity of an
  existing SKU by the quantity in the import row. Prices and attributes on
  matched items stay unchanged. **Repeat imports will add stock again.**
- **Add stock + merge:** also update an existing SKU's *supplied nonblank*
  price/cost/GST/barcode/HSN/attributes. Other attributes remain untouched.
  No existing attributes or products are removed. Repeat imports will add stock again.

**All products and all imported attributes remain editable** in the ordinary
Catalog editor; a SKU can also be changed later. No sheets or database records
are protected or locked. Catalog also paginates results in sets of 100 and
searches imported attributes, so larger catalogues remain navigable. Import is an atomic operation: if one row conflicts
with the database, the transaction is rolled back, rather than leaving a half-
imported catalogue.

## Source workbook transformation and counts

Source: `STOCK_UPDATE_29092026.xlsx`.

| Prepared workbook | Source tabs | Rows | Quantity total | Price source |
|---|---|---:|---:|---|
| `pyPOS-Stock-Sep2026-Import.xlsx` | 29092026-VH Men, 30092026-VHWomen, Others, Accesories, Thermals | **595** | **1,140** | Source `MRP` |
| `pyPOS-Stock-Nov2026-Import.xlsx` | 03112026 | **775** | **1,141** | **Not supplied; blank** |

All 775 November rows are missing a price. They are previewable but **cannot
be imported as new products by default**. Fill real selling prices first, or
select the explicit option to create them at **₹0 with a Price Review Required
attribute**, and correct prices **before any sale**.

Original data did not give reliable cost prices, GST percentages, HSN codes or
barcodes. Those fields are left blank. pyPOS stores missing costs/GST as its
legacy 0 defaults and attaches editable **Cost Review Required** and
**GST Review Required** attributes. A missing GST rate is **not** evidence of
a 0%-rated product. Confirm the correct classification/rate and your store's
GST-inclusive/exclusive pricing mode before billing.

On the September rows, `price` is populated from the source **MRP**, not from
verified transaction selling prices. Do not assume the MRP is the intended sale
price or use it as cost price. The source `Total Amt` column was a multiplication
of quantity × MRP; it is not needed by pyPOS, which calculates totals itself.

### Duplicate handling

- The source includes repeated *original SKUID* strings for different styles.
  Those are **not** treated as unique identifiers or barcodes.
- Deterministic `STK-*` SKU values have been derived from the exact source
  category, style, colour and size, **not claimed to be manufacturer SKUs**.
- There are **10 repeated variants** across the September sheets and **37**
  among the November rows. Their occurrences receive distinct `-02`, `-03`, etc.
  SKU suffixes to avoid silently merging quantities or prices. Rows are tagged
  for review in their `notes` column.
- Original identifiers, source sheet and row are preserved as editable `attr:`
  fields, so the source record remains traceable.
- A trailing summary/footer line in the women's sheet and blank thermal lines
  are excluded, not converted into products.

## Extremely important: the stock snapshots are not purchase receipts

The two workbooks show inventory snapshots from different dates/product groups.
They are **not automatically additive to each other**. Using **Add stock** with
an already-imported snapshot increases inventory and may double-count it. Start
with **Create new, skip existing**, confirm products/quantities, and use
**Add stock** only with genuinely new stock receipts or independently
reconciled adjustments.

## Technical limits and verification

- CSV must be UTF-8; maximum 8 MB and 12,000 rows, 80 columns.
- XLSX parsing uses Python standard-library ZIP/XML, no spreadsheet library is
  required at runtime. It reads stored values; it does not evaluate Excel formulas.
- Duplicate SKUs within one file or invalid quantities/prices block that import.
- Existing customer, sales, loyalty and GST data is untouched by this feature.
- The Python business-logic tests and the frontend module parser were run here.
  Flask route and real-browser end-to-end tests could not run because Flask is
  absent from this test environment.
