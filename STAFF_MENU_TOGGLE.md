# Staff menu visibility patch

Based on the pyPOS Retail Hardening release. Apply the files at the repository root and restart pyPOS. In Settings, use **Staff menu visibility** to toggle **Show Staff in main menu**; save the preference. You can still access Staff management from the button in Settings after hiding the sidebar link.

This toggle changes **navigation only**: PIN login, role-based API permissions, user records and activity logging continue as before. It does not disable staff security or alter any financial records. The default is visible for existing installations.

No schema migration is needed. Back up the data directory before installing.
