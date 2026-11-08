#!/usr/bin/env python3
"""Run pyPOS from a source checkout. The implementation lives in pos/cli.py —
this shim exists so `python main.py` keeps working exactly as before."""
import sys
from pos.cli import main

if __name__ == "__main__":
    sys.exit(main())
