# pyPOS · UPI and till operations patch (7 November 2026)

This **update-only** patch is for the latest *Stocktake Dropdowns* release. It does not contain a database or replace any existing product, invoice, customer, GST, loyalty, or stock quantity.

## Changes

1. **UPI payments**: When UPI is selected at payment, generate a random **POS reference** and show it to the cashier for the payer's payment note/comment. A UPI QR can be shown for the configured `Settings → Store UPI ID` and the outstanding amount. The cashier can separately enter the **real bank UTR / transaction reference**, which comes from the customer's or merchant's payment app. The POS reference is **NOT a bank-generated UTR**. QR generation is not payment verification, and payment apps may truncate or ignore the payment note. Cashiers must confirm receipt in the merchant account before completing the transaction. Store the POS reference and entered UTR for internal reconciliation; neither is forced onto the tax invoice.
2. **Start Day enforcement**: The Register refuses to launch checkout when the day is closed; sales, refunds and voids are also denied at the API, with a second transactional check inside SQLite to prevent a close/reopen race. A manager or administrator opens/closes day as before. No data is deleted or silently rolled forward.
3. **Parking**: Fixes a JavaScript ReferenceError where Park referenced a payment-dialog-only `checkoutRequestId`. Parking remains separate from payment submission and retains the chosen salesperson/customer/cart.
4. **Windows login / origin shielding**: Allow `localhost`, `127.0.0.1`, and IPv6 loopback to be used interchangeably on **the same scheme and TCP port** for request Origin checks. Cross-site requests are still rejected. Always open pyPOS through its local `http://localhost:<port>` address, not `file://` or another port. A Windows browser/launcher integration check remains necessary.
5. **Startup sales login**: If no staff account exists, the app redirects to **first-run administrator setup** (localhost only). Existing admin password must be entered, or a new password created if none exists. No default staff PIN is generated. Once enrolled, operators must log in with username and PIN at startup; a new server process invalidates the previous staff-login session. Roles are inherited from the existing staff system. **This is an intentional change** from optional staff enrolment; make sure the administrator credentials are safely recorded before using live data. It does not clear existing data.
6. **Cashier stock permissions**: Admin Settings now include **Allow cashiers to reduce stock** (default **OFF**) and **Allow cashiers to import stock** (default **ON**, additive/skip modes only). Cashiers may add stock by positive manual movement or authorised additive CSV/XLSX import. Negative adjustments require the administrator to turn on that toggle. Cashiers still cannot post stocktakes, edit core product attributes, delete products, process refunds or perform manager-only actions. Server-side checks enforce this even if a user bypasses the interface.
7. **Reconciliation**: Internal UPI CSV exports include POS reference and entered UTR. They are not bank settlement reports and do not prove receipt.

## Setup

1. Stop pyPOS. Back up the entire `data/` directory (including any configured SQLite file).
2. Apply the ZIP to your repository root, replacing matching files, **or** run `git apply --check pyPOS-UPI-Till-Operations.patch` then `git apply pyPOS-UPI-Till-Operations.patch`. Do **not** use both methods.
3. Install Python project dependencies: `python -m pip install -e .` (includes `Flask` and `qrcode`). The portable build script was also updated to package `qrcode`.
4. Restart. On the same PC, open `http://localhost:8420` (or your configured port). Create the first administrator account if prompted, then login. Log in as a cashier to test restricted actions.
5. Set the **store UPI ID** and store name in Settings, preferably using your merchant VPA.
6. Open the day with an authorised manager/admin account, scan a product, choose UPI, confirm the on-screen payment reference and optional QR, and **verify the payment in the actual bank/merchant app**. Enter the real bank UTR when available.
7. Test Park → Resume, closed-day checkout rejection, cash/card change, cashier stock increase, cashier stock decrease denied, cashier additive import allowed/disabled, and a refund approved by a manager with day open.

## Quality checks / limitations

- **225 of 228 Python test cases passed** in this environment. Three Flask-import-dependent cases could not execute because Flask is unavailable and network package installation is blocked here. Eight new local service tests passed, including database day-gate checks.
- 22 JS modules parse/link; isolated Chromium browser smoke passes checkout gating, Park, UPI reference and QR interface. This test uses mocked API results and **is not a real bank payment test**.
- `tests/test_till_http.py` is provided for an environment with Flask installed; it has not been run here. Run `python -m unittest discover -s tests -p 'test_*.py'` after installing dependencies, then exercise the POS with a copy of the real database and on your actual Windows computer, scanner and printer.
- The POS does **not** automatically validate a UPI bank transfer or fetch a UTR from NPCI. Do not mark a UPI tender as paid solely because the QR was displayed or scanned.
- This is a **staging patch**, not a full independent production/security certification. Confirm Windows login, HTTP auth, printer and bank settlement before deployment.
