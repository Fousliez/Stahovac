#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

launch_choice() {
  local choice=""

  if command -v yad >/dev/null 2>&1; then
    choice="$(yad       --title="Stahovač"       --width=360       --height=180       --center       --list       --radiolist       --column=""       --column="Stahovač"       TRUE "Instagram"       FALSE "RedGIF"       --button="Spustit:0"       --button="Zrušit:1"       --print-column=2 2>/dev/null || true)"
  elif command -v zenity >/dev/null 2>&1; then
    choice="$(zenity       --list       --radiolist       --title="Stahovač"       --text="Vyber, co chceš spustit:"       --column=""       --column="Stahovač"       TRUE "Instagram"       FALSE "RedGIF" 2>/dev/null || true)"
  else
    printf '\nStahovač\n'
    printf '1) Instagram\n'
    printf '2) RedGIF\n'
    printf '0) Konec\n\n'
    read -r -p "Vyber: " answer
    case "$answer" in
      1) choice="Instagram" ;;
      2) choice="RedGIF" ;;
      *) exit 0 ;;
    esac
  fi

  case "$choice" in
    Instagram)
      exec bash "$PWD/INSTAGRAM/start_app.sh"
      ;;
    RedGIF)
      exec bash "$PWD/REDGIF/start_app.sh"
      ;;
    *)
      exit 0
      ;;
  esac
}

launch_choice
