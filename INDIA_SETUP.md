# pyPOS for Indian shops: setup and operating notes

This build targets small Indian retail counters, with local database storage and a browser-based register. This is a **billing/POS tool, not a certified GST accounting, IRP/e-invoicing, GSTR-filing, e-way-bill or ERP system**. Have your CA/GST practitioner review your invoice format and tax treatment before issuing statutory invoices.

## 1. First-time setup

1. Make a **manual backup** of any existing pyPOS database before upgrading, using **Settings > Backups**. Keep a second copy on an external drive. Existing databases are migrated when the new version starts; old sales are not retrospectively assigned invoice numbers or new HSN data.
2. Launch as before: Windows `run.bat`, Linux/macOS `./run.sh`, or `python main.py` once `pip install -r requirements.txt` has completed. Open `http://127.0.0.1:8420` if the browser did not open automatically.
3. In **Settings > Store details**, enter the exact **legal trade name, address and phone**. Enter **GSTIN only if registered**. The 15-character format is checked, but the app cannot verify an active registration or GST portal status. Set the supplier State/UT and two-digit GST code to match the GSTIN.
4. For a food business, enter a valid **FSSAI licence number** if applicable. Enter the shop **UPI ID** if you want it printed as plain text on slips. This does *not* generate a payment QR or verify a transfer.
5. In **Settings > Receipt printing & logo**, choose whether catalogue prices are **GST-inclusive (MRP)** or **tax-exclusive**. **The original pyPOS behaviour was tax-exclusive and remains the upgrade default.** Changing modes never rewrites catalogue prices, so check the prices yourself before your next sale.
6. In the catalogue, check every **GST rate**, **HSN/SAC**, **unit of quantity (UQC)**, selling price and stock count. The software will not guess classifications or rates. An empty HSN, an incorrectly chosen unit or a zero tax rate is **not** evidence that a sale is exempt.
7. In the customer editor, add buyer **GSTIN and billing address** when required for your type of sale.

### Billing choices

The checkout asks whether the supply is **within the same State/UT** or **inter-State**. For a shop with GSTIN, it presents a CGST + SGST/UTGST split for intra-State supplies or IGST for inter-State supplies. **This is a user-selected classification, not automatic place-of-supply legal reasoning**; special rules for services, composition taxpayers, exports, exempt supplies, reverse charge and other exceptions need separate handling. For inter-State supply, enter the **place of supply** as the State/UT.

Each new completed sale has a sequential invoice reference with an **Indian April–March financial-year** prefix, for example `26-27/000001`. It is an app-generated reference and may not match your existing statutory invoice series. **Do not run two independent shop databases using the same invoice series**; coordinate an official numbering policy with your accountant. Changing a completed sale to void will not reuse its number. Older sales retain their existing receipt IDs.

At checkout, the app snapshots the shop details, customer tax details, product name/SKU/HSN/UQC, tax mode and calculated tax breakup. A later edit to a product or Settings does not rewrite the recorded details of a newly issued receipt. **The stored shop logo is a current global setting**, so reprints may show the new logo even when the historical business identity fields are fixed.

## 2. Printing your shop logo

1. Prepare a **clear PNG, JPEG or WebP** file no larger than **200 KiB**. A monochrome high-contrast logo prints more cleanly on thermal paper; PNG with a transparent background works if the printer supports it.
2. Go to **Settings > Receipt printing & logo > Upload shop logo**. Preview it, set **Print logo on receipts**, and select a width of **12–65 mm**. Click **Save print settings**.
3. Choose **58 mm thermal**, **80 mm thermal**, or **A4/PDF**. For 58 mm receipts the printable content area is ~48 mm; for 80 mm receipts it is ~72 mm. The layout does not change the physical paper in the printer.
4. Click **Print test slip** to check logo size, alignment, margins and clipping. The slip is clearly marked **TEST PRINT - NOT A SALE** and creates no transaction.
5. At checkout or from sales history, click **Print** and select the actual printer in the browser dialogue. For A4, you can choose the browser's **Save as PDF** destination. The chosen paper/media size and margins in the operating-system printer settings should agree with the app's selection. Disable browser header/footer text if necessary.

The print option includes the logo on **sales receipts, refund slips and X/Z drawer reports** if it is enabled. pyPOS uses the browser print dialogue; it does **not** silently select or drive an ESC/POS printer, open a cash drawer, or command a cutter. Hardware-specific direct printing would need a separate adapter and driver support.

## 3. Reports and audits

- **Sales CSV** exports transaction IDs, invoice references, dates (explicitly marked UTC), statuses, parent sale IDs and **signed net effect**. A refund has a negative effect, while a voided sale has zero effect. Do not sum the ordinary unsigned `total` column as net revenue.
- **Sale lines CSV** exports the **recorded** SKU, product description, HSN/SAC, UQC, quantity, selling price, tax rate, discounts and tax amounts. Older records can lack historical classifications or recorded line tax; the app does not fabricate them.
- **Inventory CSV** includes HSN/SAC and UQC, plus cost-based stock valuation.
- **X/Z drawer reports** remain operational cash/tender reports, not tax filings or audited financial statements.
- Figures use Indian digit grouping (`₹1,23,456.78`), with receipts shown in **IST (Asia/Kolkata)** and CSV timestamps explicitly labelled as **UTC**.

### Returns, credit and accounting

Refunds restore only returned stock and are recorded separately from the original sale. **Once a sale has any refund, it cannot be voided**, avoiding double-restocking. Voids of unrefunded sales preserve the original item rows and invoice reference for audit. A refund to store credit is logged in the store-credit journal. Refunds to original payment sources preserve mixed cash/card/UPI/store-credit proportions and repeated small partial refunds reconcile to the original bill in paise. Refund slips are **not GST credit notes** with statutory numbering and cross-document fields, and they do not automatically adjust GSTR filing records. Similarly, voids and discounts must be reviewed against your accounting practice.

## 4. Important limits and safety

- **Not implemented:** IRN generation, officially signed e-invoice QR, GST return filing or reconciliation, automated tax-rate/HSN lookup, e-way bills, digital signing, GST credit/debit notes, supplier purchase accounts, purchase orders, bank reconciliations, multi-branch shared inventory, staff-specific audit roles, and direct ESC/POS printer/cash drawer control. These should not be assumed to work just because a printed bill displays a GSTIN or has a "Tax invoice" heading.
- The GSTIN check is **structural only**; FSSAI validates length only. Actual registrations/validity are not independently confirmed.
- **Security:** the register runs on localhost by default. **Do not expose the server unprotected to the Internet.** Follow the README security and backup instructions if sharing it across a LAN.
- **Backups:** shop logos are stored in the SQLite database and included in backup snapshots. Keep off-device backups and verify restores on a copy before any major upgrade.
- **Historical receipt details** are preserved only for sales created by this version. Older sales have their pre-existing amount/receipt data; missing older metadata will remain blank.

For registered businesses or those above the applicable e-invoicing turnover threshold, have a qualified professional verify whether the app's current billing scope is sufficient **before using it for official tax invoices**.
