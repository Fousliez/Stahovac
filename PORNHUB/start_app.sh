#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

show_info() {
  local message="$1"
  if command -v yad >/dev/null 2>&1; then
    yad --info --title="Pornhub Stahovač" --text="$message" --button="OK:0" --width=430 >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send "Pornhub Stahovač" "$message" || true
  fi
}

show_error() {
  local message="$1"
  if command -v yad >/dev/null 2>&1; then
    yad --error --title="Pornhub Stahovač" --text="$message" --button="OK:0" --width=520 >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send -u critical "Pornhub Stahovač" "$message" || true
  fi
}

first_setup=0
if [ ! -d .venv ]; then
  first_setup=1
  python3 -m venv .venv
fi

requirements_hash="$(sha256sum requirements.txt | awk '{print $1}')"
requirements_stamp=".venv/.requirements.sha256"
installed_hash="$(cat "$requirements_stamp" 2>/dev/null || true)"

if [ "$installed_hash" != "$requirements_hash" ]; then
  if [ "$first_setup" -eq 1 ]; then
    show_info "První spuštění připravuje samostatné prostředí a instaluje PySide6 + yt-dlp. Po potvrzení bude instalace pokračovat na pozadí."
  fi

  if ! .venv/bin/python -m pip install --upgrade pip; then
    show_error "Nepodařilo se aktualizovat pip. Podrobnosti jsou v logs/pornhub.log."
    exit 1
  fi

  if ! .venv/bin/python -m pip install --upgrade -r requirements.txt; then
    show_error "Nepodařilo se nainstalovat závislosti. Podrobnosti jsou v logs/pornhub.log."
    exit 1
  fi

  printf '%s\n' "$requirements_hash" > "$requirements_stamp"
fi

exec .venv/bin/python main.py
