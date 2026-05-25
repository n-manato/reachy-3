#!/usr/bin/env bash
# Thin wrapper that forwards to the cross-platform Python launcher.
# Usage: ./run.sh          (start podcast)
#        ./run.sh test     (run connectivity test)
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/run.py "$@"
