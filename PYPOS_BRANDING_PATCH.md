# pyPOS 2.0 — Store Branding Patch

This is an **update-only patch** for **pyPOS 2.0 Milestone 1**, not a complete POS app. It does not include a database, customer data, or product inventory. The existing `settings` key-value table stores the additional appearance fields, so there are no database table or column changes.

## Installation

1. Stop the running POS and back up the entire `data/` directory (especially the SQLite database).
2. Extract `pyPOS-2.0-Branding-Patch.zip` into the current repository root, where `main.py` and the `pos/` folder are located. Overwrite matching files **only**. Do not delete any other existing files.
3. Alternatively, from an **unmodified pyPOS 2.0 Milestone 1** installation, use the Git patch:
   ```sh
   git apply --check pyPOS-2.0-Branding.patch
   git apply pyPOS-2.0-Branding.patch
   ```
   **Do not use both methods.** If you edited the same files locally, inspect the diff before applying.
4. Restart pyPOS and use Ctrl+Shift+R (hard refresh) in the browser.
5. Open **Settings → POS appearance & branding** to set your display name, colour and logo.

## Features

- **Sidebar display name** independent of the legal/store name printed on receipts and invoices. Changes the browser-tab title and admin settings-login heading.
- **Custom accent colour** with preset swatches and a `#RRGGBB` picker. Applied to primary actions, filters, focus indicators and navigation in both light and dark themes; text/interactive colours are adjusted for contrast where necessary.
- **Menu logo source:** default initial; reuse saved receipt logo; upload a separate PNG/JPEG/WebP menu logo (max 200 KiB); or hide the logo.
- **Menu logo size** from 24 to 48 pixels, automatically compacted on narrow screens.
- **Live preview**, **reset to default appearance**, and **remove separately uploaded logo**. Resetting does not delete saved logo assets or change receipt settings.
- Browser-tab icon and admin settings-login page reflect stored menu logo / name.
- Appearance settings are stored locally in SQLite and travel with your usual POS database backups.

## Separation from legal/invoice details

The `Store name` in the **Store** settings card remains the name printed on receipts and used for invoice snapshots. The `Name in sidebar and browser tab` is a UI label only. Custom POS logo uploads do **not** replace the existing receipt logo unless you separately change it in **Receipt printing & logo**.

Previously issued invoice snapshots, sales, payments, stock, customer loyalty and customer data are untouched by this patch.

## Validation & access

The API validates colour syntax, logo bytes/type and upload size, logo mode and size, and display name length. Invalid changes are rejected together without writing partial settings. Settings changes use the application's existing administrator authentication. If no admin password has been configured, Settings remains open as before.

## Tests performed

- 147 available non-Flask Python backend tests passed (including four new branding tests).
- JavaScript module parsing and seven page imports checked; 13 modules passed.
- A headless Chromium Settings smoke test exercised save, reset, custom image upload, title/sidebar updates and light/dark accent changes without page errors.
- Login HTML formatting/escaping was also tested independently.

**Limitation:** Flask is unavailable in the build environment, so full Flask HTTP/API integration and real hardware receipt printing are not verified. Test on your local machine before deploying to a live till.
