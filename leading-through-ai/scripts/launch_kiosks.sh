#!/usr/bin/env bash
# Open the three seat screens full-screen, one per monitor, and bind each
# touch panel to the monitor it is glued to.
#
#   scripts/launch_kiosks.sh                 # auto: monitors left->right
#   scripts/launch_kiosks.sh --list          # show monitors and touch devices
#   scripts/launch_kiosks.sh --touch-only    # only bind touch panels; open the
#                                            # pages yourself in a normal browser
#   TOUCH_LEFT="ELO Touch" TOUCH_CENTER=... TOUCH_RIGHT=... scripts/launch_kiosks.sh
#
# Why the touch mapping matters: on X11 every touchscreen is mapped to the
# WHOLE desktop by default, so with three panels a tap on the left monitor
# lands somewhere on the combined 11520px-wide desktop -- usually on the
# wrong screen. `xinput map-to-output` pins each panel to its own monitor.
#
# Identical touch panels share a name; then use the numeric ids from --list:
#   TOUCH_LEFT=12 TOUCH_CENTER=14 TOUCH_RIGHT=16 scripts/launch_kiosks.sh
# (Tip: to find which id is which, run `xinput test <id>` and touch a panel.)
set -euo pipefail
export DISPLAY="${DISPLAY:-:0}"
HOST="${LTA_HOST:-127.0.0.1}"
PORT="${DISPLAY_WEB_PORT:-8020}"
CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE_ROOT="$HERE/.kiosk"

mapfile -t MONITORS < <(xrandr --listmonitors | tail -n +2 | awk '{print $NF" "$3}' \
  | sed -E 's#([0-9]+)/[0-9]+x([0-9]+)/[0-9]+\+([0-9]+)\+([0-9]+)#\1 \2 \3 \4#' \
  | sort -k4 -n)   # name W H X Y, sorted by X (left to right)

if [[ "${1:-}" == "--list" ]]; then
  echo "Monitors (left to right):"; printf '  %s\n' "${MONITORS[@]}"
  echo; echo "Touch devices:"; xinput list | grep -iE "touch|elo|ilitek|egalax|wacom|eeti|goodix" || xinput list
  exit 0
fi

if (( ${#MONITORS[@]} < 3 )); then
  echo "Only ${#MONITORS[@]} monitor(s) found; opening what we can." >&2
fi

# Screens must never blank during the event.
xset s off -dpms s noblank 2>/dev/null || true

SEATS=(left center right)
TOUCH_VARS=(TOUCH_LEFT TOUCH_CENTER TOUCH_RIGHT)

# Auto-detect touch devices in xinput order when not given explicitly.
mapfile -t AUTO_TOUCH < <(xinput list --id-only 2>/dev/null | while read -r id; do
  xinput list-props "$id" 2>/dev/null | grep -q "Coordinate Transformation Matrix" || continue
  name=$(xinput list --name-only "$id")
  # A panel also exposes "... Pen"/"... Mouse" sub-devices, and laptops have a
  # "Touchpad": none of those is a touchscreen to map.
  [[ "$name" =~ [Pp]en|[Mm]ouse|[Tt]ouchpad|[Kk]eyboard ]] && continue
  [[ "$name" =~ [Tt]ouch|ELO|eGalax|ILITEK|Goodix|EETI ]] && echo "$id"; done || true)

if [[ -z "${TOUCH_LEFT:-}${TOUCH_CENTER:-}${TOUCH_RIGHT:-}" ]] && (( ${#AUTO_TOUCH[@]} != 3 )); then
  echo "WARNING: found ${#AUTO_TOUCH[@]} touchscreen(s), expected 3. Auto-mapping may be wrong;" >&2
  echo "         run with --list and set TOUCH_LEFT/TOUCH_CENTER/TOUCH_RIGHT." >&2
fi

for i in 0 1 2; do
  mon="${MONITORS[$i]:-}"; [[ -z "$mon" ]] && break
  read -r name w h x y <<<"$mon"
  seat="${SEATS[$i]}"
  var="${TOUCH_VARS[$i]}"; touch="${!var:-${AUTO_TOUCH[$i]:-}}"
  if [[ -n "$touch" ]]; then
    xinput map-to-output "$touch" "$name" && echo "touch '$touch' -> $name ($seat)" \
      || echo "WARNING: could not map touch '$touch' to $name" >&2
  else
    echo "WARNING: no touch device for $seat ($name); set $var" >&2
  fi
  [[ "${1:-}" == "--touch-only" ]] && continue
  if [[ -z "$CHROME" ]]; then echo "No Chrome/Chromium found; open the pages by hand." >&2; continue; fi
  mkdir -p "$PROFILE_ROOT/$seat"
  "$CHROME" --user-data-dir="$PROFILE_ROOT/$seat" --new-window \
    --window-position="$x,$y" --window-size="$w,$h" --kiosk \
    --autoplay-policy=no-user-gesture-required \
    --disable-pinch --overscroll-history-navigation=0 \
    --disable-features=TouchpadOverscrollHistoryNavigation,Translate \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --check-for-update-interval=31536000 --no-first-run \
    "http://$HOST:$PORT/seat/$seat" >/dev/null 2>&1 &
  echo "opened /seat/$seat on $name (${w}x${h}+${x}+${y})"
  sleep 1
done
echo "Staff page: http://$HOST:$PORT/control   (close kiosks: pkill -f '$PROFILE_ROOT')"
