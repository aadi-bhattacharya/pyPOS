"""Separate native GUI launcher; normal use never needs cmd.exe or a shell."""
from pos.managed_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
