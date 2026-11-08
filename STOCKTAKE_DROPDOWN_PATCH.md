# pyPOS — Stocktake & Purchasing dropdown update

**Baseline:** pyPOS with Retail Hardening, Staff menu toggle, Salesperson tracking, Production Audit Fixes, and the Layout Refinement patch installed.

## Changes

- **Physical Stocktake:** Existing stocktake sessions are selected with one dropdown instead of a wrapping wall of session chips. A draft remains editable; posted stocktakes remain read-only.
- **Physical Stocktake / exact SKU:** Search or scan by EAN, exact SKU, product name or customer-facing variant. The dropdown displays the **exact SKU**, the available stock, selling price and relevant size/colour. Selecting a record saves its *product ID* and never an ambiguous EAN.
- **Supplier goods receiving:** The same searchable SKU selector replaces its unreliable browser datalist. Each supplier line is tied to the chosen SKU/MRP edition. Existing supplier choice and payment-method dropdowns remain unchanged.
- **Multiple MRPs:** Scanning an EAN shared by several price editions shows every matching SKU; `Enter` **cannot silently select an arbitrary price**. Scan a unique EAN or exact SKU and press Enter to select immediately; or use arrows/Enter or the pointer to select from multiple results.
- **Cashier ergonomics:** Stocktake advances to quantity after a valid selection and returns focus to the SKU box after a saved count. Display only the first 40 matches, with prompts to narrow searches of large catalogues.
- **Input safeguards:** Stocktake counts must be non-negative integers; receipt quantities must be positive integers, and landed unit costs must have at most two decimal places.
- **UI:** The dropdown uses the existing accent colour, dark-mode tokens and field styling; it works on narrow windows and within the scrolling purchase modal.

## Installation

1. Stop pyPOS and back up `data/`.
2. Extract the update-only ZIP into the existing pyPOS root so that `pos/static/js/skuPicker.js` and the other paths are placed correctly. Overwrite matching files.
3. Restart pyPOS and hard-refresh your browser (`Ctrl+Shift+R`).
4. In Stocktake, scan an EAN with two MRPs and verify that both options appear. Count a draft SKU, and verify the entered quantity. In Purchasing, use the same EAN and select the intended price edition before receiving stock.

Do **not** apply both the file ZIP and the Git patch. For Git installation, run `git apply --check <patch>` first.

## Scope & safety

- No SQLite schema or stored business data changes.
- No changes to checkout, sales totals, refund calculations, loyalty, tax, staff permissions, or cash drawer logic.
- No automatic inventory merges; price editions remain distinct.
- Existing supplier invoice and stocktake posting safeguards remain enforced by the backend.
- Browser checks are isolated Chromium UI tests, not complete Flask HTTP or physical scanner/printer acceptance tests.

## Checks performed

- `node tests/check_frontend_modules.mjs` — 22 ES modules parsed, page and Cash Drawer links resolved.
- `python tests/smoke_sku_dropdowns.py` — product picker, Stocktake, and supplier receipt flows checked at 1440px and 500px, including ambiguous EAN handling.
- `python tests/test_ui_layout_smoke.py` — existing Register, Purchasing and Stocktake layouts checked at three viewport widths.
- Existing Python backend tests remain separate and do not require this UI-only patch to change any data logic. The Flask-dependent tests cannot be executed without Flask.
