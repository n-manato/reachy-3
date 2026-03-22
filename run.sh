#!/usr/bin/env bash
# Launch the Ethics Podcast with required macOS GStreamer env vars.
# Usage: ./run.sh          (start podcast)
#        ./run.sh test     (run connectivity test)

set -euo pipefail
cd "$(dirname "$0")"

export GI_TYPELIB_PATH=/opt/homebrew/lib/girepository-1.0
export DYLD_LIBRARY_PATH=/opt/homebrew/lib
export GST_PLUGIN_PATH=/opt/homebrew/lib/gstreamer-1.0

if [ "${1:-}" = "test" ]; then
    exec uv run python scripts/test_connectivity.py
else
    exec uv run ethics-podcast
fi
