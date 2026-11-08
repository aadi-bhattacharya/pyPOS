# Unified staff permissions: incremental update

This patch is for the pyPOS source **after** the regression-test and managed-auto-update patches. It changes only the files in the diff; do not extract a replacement application. No checkout, invoice, loyalty or stock ledgers are reset.

## Everyday store workflow

The first owner account is created **once**. Its register PIN is only that owner's personal login; staff do not need to know it. Add an operator in **Staff & permissions** and assign individual capabilities using the grouped on/off switches.

By default, an ordinary operator can bill customers, open and close the till, add products, receive positive stock, import new or additive stock, work with customers and view reports. No administrator needs to attend daily opening or closing. A supervisor preset offers broader operational permissions, and the owner can edit every switch individually. A permission to reduce stock, void/refund bills, pay out cash, or change customer credit must be deliberately enabled.

There are now two account *types*: **Owner / administrator** (protected security functions) and **Staff** (individually configured operations). The predefined "Everyday staff" and "Supervisor" presets are shortcuts, **not new fixed account roles** or shared permission templates. Owner accounts always have full rights and cannot have capability switches edited.

## Existing database migration

An additive `staff_permissions` table stores the operator's boolean grants. The existing `staff_users` table and its foreign keys are not rebuilt. For compatibility, historical `cashier` and `manager` values remain in the database, but both display as `Staff` in the UI. A historical operator without a custom permission record inherits the nearest legacy preset. Once saved, that employee's permissions are explicit. New Staff rows are stored using the old `cashier` DB enum under the hood, solely because the existing CHECK constraint is retained for safety. Existing product/audit records remain intact.

Existing settings `cashier_allow_stock_reduction` and `cashier_allow_stock_import` may remain in the database for older installations, but **neither grants staff access** in this release: individual switches control these operations. The global `cashier_max_discount_percent` setting continues to limit staff without the `discount_override` permission.

## Security details

- A personal staff session is mandatory for business API calls, checked again on every request. Disabling an operator or revoking their capability takes effect on the next API call, including already signed-in browsers.
- Capability validation accepts only the complete advertised set of strict boolean values; unknown, malformed or partial updates are rejected.
- New non-owner mutating API endpoints deny access unless deliberately assigned a capability in the server routing guard.
- Owner-only staff administration, Settings mutation, administrator password, backups and Google Drive administration are not grantable through switches.
- Owner staff management requires both the owner's operator session and the separate administrator Settings password.
- The API, not merely hidden buttons, enforces all permissions. The existing origin/CSRF protections stay enabled.
- The existing staff action audit is an operational log, not an independently tamper-proof financial audit.

## Apply and verify

Back up the POS database first. From the repository root, after both prior patches:

```sh
git apply --check pyPOS-Staff-Unified-Permissions-Incremental.patch
git apply pyPOS-Staff-Unified-Permissions-Incremental.patch
python -m unittest discover -s tests -p 'test_*.py'
node tests/check_frontend_modules.mjs
```

Optionally run the standalone layout test (`python tests/test_ui_layout_smoke.py`) on systems with Playwright and `/usr/bin/chromium`.

For **production**, build/publish a new tagged release using `.github/workflows/build-binaries.yml`, then verify an installed native binary on the actual checkout hardware. Editing the source repository does not modify an already-built PyInstaller executable.

## Scope/limitations

An administrator can configure each employee separately; reusable named custom roles/permission templates are not part of this change. All previous automatic updater behavior is left unchanged. A pass of the test suite is not a substitute for native-Windows deployment/receipt printer/scanner testing. Historical SQLite ResourceWarnings from other tests are not addressed by this patch.
