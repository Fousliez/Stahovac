#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

requirements_hash="$(sha256sum requirements.txt | awk '{print $1}')"
requirements_stamp=".venv/.requirements.sha256"
installed_hash="$(cat "$requirements_stamp" 2>/dev/null || true)"

if [ "$installed_hash" != "$requirements_hash" ]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install --upgrade -r requirements.txt
  printf '%s\n' "$requirements_hash" > "$requirements_stamp"
fi

exec .venv/bin/python main.py
