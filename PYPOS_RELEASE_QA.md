# pyPOS Retail Hardening — QA summary

Tests used throwaway in-memory and temporary SQLite databases; no real shop
records were touched.

| Check | Result |
| --- | --- |
| Non-Flask Python unit/invariant tests | See release transcript; passing suite |
| Repeat checkout ID + 12 parallel retries | One recorded sale and one stock decrement |
| Historical database migration | Idempotent; IDs, items, totals, stock preserved |
| 20,000 bounded-discount cases | Exact allocated cents; no negative line value |
| Same-second cash sessions | New transactions attributed by explicit session ID |
| Mixed cash/card payment preview | Change reconciles; card overpay rejected |
| Physical stock count | Draft, stale-count guard, no uncounted SKU adjustments |
| Supplier receipt | Duplicate invoice rejected; cash supplier payment changes drawer |
| Product EAN-13 checks | Correct 95-module encoding; bad check digits rejected |
| JS module import/link/syntax | All page modules verified |
| Live Flask HTTP/auth/CSRF test | **Not executed — Flask unavailable** |
| Live Chromium shop flow | **Not executed — localhost navigation blocked** |
| Real scanner/58/80mm printer | **Not tested — hardware unavailable** |
| Legal GST/Tally certification | **Not performed** |

Review the implementation details and limitations in `PYPOS_RETAIL_HARDENING.md`.
