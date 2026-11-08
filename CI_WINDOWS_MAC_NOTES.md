# CI portability repairs (8 November 2026)

Incremental patch for the current code after the refund-numbering patch.

The failed 8 November GitHub Actions run supplied only partial Windows tracebacks.
This patch addresses the reproducible architecture/path mismatches, closes test
SQLite handles which Windows keeps locked, and removes the unnecessary WAL
checkpoint from the live backup operation. SQLite's online backup API already
captures committed WAL transactions; forcing a checkpoint before a backup
increases contention and can make the backup fail while billing is active.

The LAN security test continues to exercise backup creation, and prints its
response body if Windows still returns an error. This is NOT a waiver of failed
backups or a relaxation of authentication/CSRF checks.

No shop database, records, permissions or update artifacts are modified by
applying this source patch. Re-run the complete GitHub Actions Windows, macOS
Intel/ARM and Linux matrix; validation on actual Windows/macOS runners is still
required. Never treat Linux-only test passes as all-platform certification.
