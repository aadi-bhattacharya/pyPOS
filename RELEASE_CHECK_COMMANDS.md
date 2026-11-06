# Release verification commands

Run from the pyPOS repository root, in a Python environment with `pip install -r requirements.txt` completed.

```bash
python -m unittest discover -s tests -p 'test_*.py' -q
node tests/check_frontend_modules.mjs
```

If Playwright and Chromium are installed:

```bash
python tests/smoke_receipts.py
python tests/smoke_receipt_layout.py
python tests/smoke_refunds.py
python tests/smoke_cash_drawer.py
python tests/smoke_multi_mrp.py
python tests/smoke_image_optimizer.py
```

For a **copy of the live database only**, check integrity:

```bash
python - <<'PY'
from pos.db import connect
conn = connect('PATH_TO_COPIED_DB')
print('Integrity:', conn.execute('PRAGMA integrity_check').fetchone()[0])
print('Foreign key errors:', conn.execute('PRAGMA foreign_key_check').fetchall())
conn.close()
PY
```

Review `PRODUCTION_AUDIT_2026-11-06.md` before deploying.
