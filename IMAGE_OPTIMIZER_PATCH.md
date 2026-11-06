# pyPOS 2.0: Image Optimiser Patch

Patch target: latest **EAN Display Repair** application (`pyPOS-EAN-Repair-Patch` applied after Multi-MRP).

## Included changes

- `pos/static/js/imageOptimizer.js`: reusable browser-side PNG/JPEG/WebP downscaling and compression. No third-party services or runtime dependencies.
- `pos/static/js/views/settingsview.js`: automatic image optimisation when uploading the **separate POS/menu logo** and **receipt logo**. Large images (up to 24 MiB input) are reduced to **200 KiB or less** before the existing settings API stores them. The menu and receipt logos remain independent. Each has adjustable maximum pixel dimension and quality. A preview displays the resulting dimensions and before/after sizes.
- `pos/static/js/views/settingsview.js`: standalone **Settings → Image downscaler & compressor**. Process up to 20 files, choose maximum dimension, output format and quality. Download each optimised file; existing files are never changed. No server upload for this tool.
- `pos/static/css/app.css`: responsive controls and per-file results.
- `tests/smoke_image_optimizer.py`: real Chromium test for compression, transparent PNGs, Settings upload, and batch UI.

## Safety and compatibility

- No schema migration or changes to checkout, sales, refunds, invoices, stock, GST, loyalty, cash drawer, scanner or branded invoice layout.
- Existing logo values are retained unless someone deliberately uploads a replacement.
- Transparent logos remain transparent with Auto, PNG or WebP. **JPEG deliberately flattens transparency to white** if chosen in the standalone utility.
- The server's existing 200 KiB image-size and file-signature validation remains enabled.
- Unsupported/undecodable images and input files larger than 24 MiB are rejected. Browser bitmap decoding is limited to 32 megapixels.
- A compressed image may omit embedded EXIF/ICC metadata. This is appropriate for POS visual assets but not a lossless archival workflow.
- Image quality controls affect **lossy WebP/JPEG**; PNG is lossless. For a small already-accepted logo, the original is preserved exactly.
- Standalone utility outputs must be downloaded **individually**. There is no ZIP/bulk download feature.
- The current pyPOS catalogue does **not** support attaching product photos to SKUs. This patch prepares files for that eventual feature, but does not invent a product-image database or claim to display images in the register.

## Installation

1. Back up the existing `data/` directory and stop pyPOS.
2. Extract the ZIP at the existing repo root, preserving directory structure, and overwrite **only the supplied files**. Alternatively use the unified Git patch. **Do not apply both**.
3. Restart pyPOS and hard-refresh the browser (Ctrl+Shift+R).
4. In Settings upload a receipt or menu logo larger than 200 KiB. Verify preview and saved image, print a test slip, then check the sidebar image.
5. For local batch optimisation, use Settings → Image downscaler & compressor. These downloads do not modify stored POS assets.

## Testing

Browser smoke: `python tests/smoke_image_optimizer.py` requires Pillow and Playwright/Chromium in **the test environment only**, not for running the POS.

Frontend module check: `node tests/check_frontend_modules.mjs`.

Backend tests without Flask are unchanged. Flask-dependent endpoint tests require Flask (not available in this build environment).
