#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

launch_detached() {
  local script="$1"
  local log_file="$2"

  nohup setsid bash "$script" >"$log_file" 2>&1 < /dev/null &
  disown || true
  exit 0
}

launch_instagram() {
  launch_detached "$ROOT_DIR/INSTAGRAM/start_app.sh" "$LOG_DIR/instagram.log"
}

launch_redgif() {
  launch_detached "$ROOT_DIR/REDGIF/start_app.sh" "$LOG_DIR/redgif.log"
}

launch_pornhub() {
  launch_detached "$ROOT_DIR/PORNHUB/start_app.sh" "$LOG_DIR/pornhub.log"
}

if command -v yad >/dev/null 2>&1; then
  set +e
  yad     --title="Stahovač"     --text="<b>Co chceš spustit?</b>"     --width=420     --height=160     --center     --button="Instagram:10"     --button="RedGIF:20"     --button="Pornhub:30"     --button="Zrušit:1"
  code=$?
  set -e

  case "$code" in
    10) launch_instagram ;;
    20) launch_redgif ;;
    30) launch_pornhub ;;
    *) exit 0 ;;
  esac

elif command -v zenity >/dev/null 2>&1; then
  choice="$(zenity     --list     --radiolist     --title="Stahovač"     --text="Vyber, co chceš spustit:"     --column=""     --column="Stahovač"     TRUE "Instagram"     FALSE "RedGIF"     FALSE "Pornhub" 2>/dev/null || true)"

  choice="${choice%%|*}"
  case "$choice" in
    Instagram) launch_instagram ;;
    RedGIF) launch_redgif ;;
    Pornhub) launch_pornhub ;;
    *) exit 0 ;;
  esac

else
  printf '\nStahovač\n'
  printf '1) Instagram\n'
  printf '2) RedGIF\n'
  printf '3) Pornhub\n'
  printf '0) Konec\n\n'
  read -r -p "Vyber: " answer

  case "$answer" in
    1) launch_instagram ;;
    2) launch_redgif ;;
    3) launch_pornhub ;;
    *) exit 0 ;;
  esac
fi
