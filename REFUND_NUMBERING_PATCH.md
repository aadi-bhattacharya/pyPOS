# Refund receipt references (incremental patch)

- New refund receipt identifiers use the **refund date in IST** and a dedicated
  financial-year serial, followed by the original sale's internal transaction ID.
  Example: `26-27/R000001-000123` means the first refund recorded in FY 2026–27
  against original `sales.id = 123`.
- The generated `refund_number` is **not** a GST credit note, a tax invoice or
  an IRN. Statutory GST credit notes need their own compliance workflow.
- Every partial refund gets its own reference; idempotent API retries return
  the original one. The serial is assigned in the same SQLite write transaction
  as the refund, so a failed refund does not consume a number.
- A unique index protects against duplicates. Receipt, Sales list/detail,
  Returns history/search, and refund, sales and item-level GST CSV exports
  expose the reference. Each new CSV `refund_number` column is **appended at
  the end**, preserving old column order.
- Migration adds the `sales.refund_number` column and `refund_sequences` table.
  Historical refunds are **not** assigned fabricated, backdated document codes;
  they keep their existing refund ID and remain readable. No old invoice,
  stock movement, tender, refund, or customer balance is modified.
- Apply only after `pyPOS-Staff-Unified-Permissions-Incremental.patch`:

  ```bash
  git apply --check pyPOS-Refund-Numbering-Incremental.patch
  git apply pyPOS-Refund-Numbering-Incremental.patch
  python -m unittest discover -s tests -p 'test_*.py'
  node tests/check_frontend_modules.mjs
  ```

**Operational note:** Verify how local refunds are to be accounted for with your
accountant. A printed refund reference is not a replacement for a GST-compliant
credit-note series.
