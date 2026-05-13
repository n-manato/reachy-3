#!/usr/bin/env python3
"""Cross-platform launcher for the Ethics Podcast.

Why this exists:
  On macOS, GStreamer is installed via Homebrew at /opt/homebrew (Apple
  Silicon) or /usr/local (Intel). PyGObject + GStreamer need three env
  vars to find their libs, and DYLD_LIBRARY_PATH must be set BEFORE
  Python launches (it cannot be set from inside Python and take effect).
  On Linux, system GStreamer is on the default loader path. On Windows,
  the `gstreamer-msvc-runtime` wheel is self-contained.

Usage:
    python scripts/run.py            # launch the podcast
    python scripts/run.py test       # run connectivity test
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys


def _set_macos_gstreamer_env() -> None:
    """Auto-detect Homebrew prefix and export GStreamer env vars."""
    brew = shutil.which("brew")
    if brew is None:
        print(
            "warning: Homebrew not found on PATH; GStreamer may fail to load.\n"
            "  Install Homebrew from https://brew.sh and run:\n"
            "    brew install gstreamer gst-plugins-base gst-plugins-good "
            "pygobject3 py3cairo",
            file=sys.stderr,
        )
        return

    try:
        prefix = subprocess.check_output([brew, "--prefix"], text=True).strip()
    except subprocess.CalledProcessError:
        return

    # setdefault: respect any value the user already exported.
    os.environ.setdefault("GI_TYPELIB_PATH", f"{prefix}/lib/girepository-1.0")
    os.environ.setdefault("DYLD_LIBRARY_PATH", f"{prefix}/lib")
    os.environ.setdefault("GST_PLUGIN_PATH", f"{prefix}/lib/gstreamer-1.0")


def main() -> int:
    if platform.system() == "Darwin":
        _set_macos_gstreamer_env()

    uv = shutil.which("uv")
    if uv is None:
        print(
            "error: 'uv' not found on PATH.\n"
            "  Install: https://docs.astral.sh/uv/getting-started/installation/",
            file=sys.stderr,
        )
        return 1

    args = sys.argv[1:]
    if args and args[0] == "test":
        cmd = [uv, "run", "python", "scripts/test_connectivity.py", *args[1:]]
    else:
        cmd = [uv, "run", "ethics-podcast", *args]

    # On Unix, exec replaces this process so signals (Ctrl-C) propagate
    # cleanly. On Windows, execvp has flaky signal behavior — use
    # subprocess.call and forward the exit code instead.
    if platform.system() == "Windows":
        return subprocess.call(cmd)
    os.execvp(uv, cmd)
    return 0  # unreachable on Unix


if __name__ == "__main__":
    sys.exit(main())
