#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

launch_instagram() {
  exec bash "$ROOT_DIR/INSTAGRAM/start_app.sh"
}

launch_redgif() {
  exec bash "$ROOT_DIR/REDGIF/start_app.sh"
}

if command -v yad >/dev/null 2>&1; then
  set +e
  yad     --title="Stahovač"     --text="<b>Co chceš spustit?</b>"     --width=360     --height=150     --center     --button="Instagram:10"     --button="RedGIF:20"     --button="Zrušit:1"
  code=$?
  set -e

  case "$code" in
    10) launch_instagram ;;
    20) launch_redgif ;;
    *) exit 0 ;;
  esac

elif command -v zenity >/dev/null 2>&1; then
  choice="$(zenity     --list     --radiolist     --title="Stahovač"     --text="Vyber, co chceš spustit:"     --column=""     --column="Stahovač"     TRUE "Instagram"     FALSE "RedGIF" 2>/dev/null || true)"

  choice="${choice%%|*}"
  case "$choice" in
    Instagram) launch_instagram ;;
    RedGIF) launch_redgif ;;
    *) exit 0 ;;
  esac

else
  printf '\nStahovač\n'
  printf '1) Instagram\n'
  printf '2) RedGIF\n'
  printf '0) Konec\n\n'
  read -r -p "Vyber: " answer

  case "$answer" in
    1) launch_instagram ;;
    2) launch_redgif ;;
    *) exit 0 ;;
  esac
fi
