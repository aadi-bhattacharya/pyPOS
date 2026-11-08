# Windows login / Origin:null cross-site shield fix

This incremental patch is for the **combined UPI & Till Operations release**.
It fixes writes rejected with:

    Forbidden — mutating requests from another site are blocked (cross-site request shield).
    request Origin was: 'null', Referer was: ''

## Why not just allow Origin:null?

`Origin: null` is also sent by sandboxed, local-file, and other untrusted
browser contexts. Allowing it unconditionally would allow cross-site requests
to reach a local POS server. The protection is retained.

## What changed

- The server creates a cryptographically unpredictable token in each browser
  session, and embeds it in its locally served register page.
- Staff login, first-run staff setup, and Settings login receive hidden tokens.
- The built-in JavaScript API, stock-import upload, backup restore upload,
  and Google Drive connect/disconnect include the token on writes.
- Requests with `Origin: null` are permitted *only* with a valid matching
  session token; cross-origin requests with an explicit foreign origin remain
  blocked even if they present a token.
- The HTML pages carrying session tokens are marked `Cache-Control: no-store`.
- No database files, schema migrations, prices, or transaction records change.

## Apply

From the root of pyPOS, after applying the combined UPI & Till Operations
patch, use:

    git apply --check pyPOS-Windows-Null-Origin-Login-Fix.patch
    git apply pyPOS-Windows-Null-Origin-Login-Fix.patch

Then restart the POS server and **close/reopen the browser tab** at
`http://127.0.0.1:8420/` (or the exact URL shown by the server). Do not
open `pos/templates/index.html` directly via `file://` or embed it in an
untrusted sandbox. If using the Windows launcher, start `run.bat` and use
its opened browser tab.

## Verification

Run with Flask installed:

    python -m unittest tests.test_security tests.test_till_http
    node tests/check_frontend_modules.mjs
    node tests/check_opaque_origin_client.mjs

In the patch author's environment, Flask was not available, so the Flask HTTP
integration suite was **added but not executed**. The request shield was
exercised directly against an isolated guard harness, the login forms were
checked for token injection, the frontend request-header checks passed, and
227 non-Flask Python tests passed. Verify real login on Windows before live use.
