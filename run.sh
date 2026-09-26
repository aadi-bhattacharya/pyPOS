#!/bin/sh
# Run pyPOS from this folder with zero manual setup.
# Creates a local .venv on first run, installs Flask, starts the register.
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "First run - creating a virtual environment..."
    python3 -m venv .venv
    "$PY" -m pip install --quiet --upgrade pip
fi

if ! "$PY" -c "import flask" 2>/dev/null; then
    "$PY" -m pip install --quiet -r requirements.txt
fi

exec "$PY" main.py "$@"
