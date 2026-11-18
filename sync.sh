#!/usr/bin/env bash
# Pull the latest commits of the three exhibits into this monorepo and push.
# Each exhibit keeps its own working directory (they run as live services
# from there); this repository only aggregates their committed history.
set -euo pipefail
cd "$(dirname "$0")"
DEV="${DEV:-..}"

pull() {  # <prefix> <working directory> <branch>
    git subtree pull --prefix="$1" "$DEV/$2" "$3" -m "Sync $1 from $2 ($3)"
}

pull ethics-podcast     Reachy_3        audio_out
pull leading-through-ai Reachy_LTA      main
pull boma-exhibit       Africa/Reachy_03 main

git push origin main
