"""Encode a source video into a streaming-friendly 720p WebM.

The exhibit serves each topic video over Wi-Fi from the WebDisplayManager to a
separate browser machine. Large 1080p files (the ~65-100 MB mp4s) can't be
delivered fast enough and the browser video stalls / re-buffers. The webms that
DO stream smoothly in the field are VP8, 1280x720, ~1.8 Mbps — this script
reproduces exactly that profile so every clip behaves like the proven ones.

Usage:
    uv run python scripts/encode_web_video.py videos/Aiden_Achievment_video.mp4
    uv run python scripts/encode_web_video.py            # all VIDEO_ITEMS sources

It writes <name>.webm next to the source. An existing webm is backed up to
<name>.webm.bak before being overwritten (use --force to skip the backup).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Proven streaming profile, matched to the field-tested 720p webms.
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
VIDEO_BITRATE = "1800k"
AUDIO_BITRATE = "96k"
# libvpx speed/quality knob: 0 = slow/best, 5 = fast/worst. 2 is a good balance.
CPU_USED = "2"


def encode(src: Path, *, force: bool = False) -> Path:
    """Two-pass VP8 encode of ``src`` to a 720p webm sibling. Returns the path."""
    if not src.exists():
        raise FileNotFoundError(src)

    out = src.with_suffix(".webm")
    if out.exists() and out != src:
        if force:
            print(f"  Overwriting existing {out.name} (--force)")
        else:
            backup = out.with_suffix(".webm.bak")
            print(f"  Backing up existing {out.name} -> {backup.name}")
            shutil.move(str(out), str(backup))

    # Downscale only if larger than the target; never upscale.
    vf = (
        f"scale='min({TARGET_WIDTH},iw)':'min({TARGET_HEIGHT},ih)'"
        ":force_original_aspect_ratio=decrease"
    )

    # Two-pass keeps the size predictable and the quality better than 1-pass at
    # the same bitrate. Pass 1 analyses; pass 2 writes the file.
    log_prefix = str(src.with_suffix(""))  # ffmpeg appends "-0.log"
    common = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libvpx", "-b:v", VIDEO_BITRATE,
        "-deadline", "good", "-cpu-used", CPU_USED,
        "-passlogfile", log_prefix,
    ]

    print(f"  Pass 1/2: analysing {src.name} ...")
    subprocess.run(
        common + ["-pass", "1", "-an", "-f", "null", os.devnull],
        check=True,
    )

    print(f"  Pass 2/2: encoding -> {out.name} ...")
    subprocess.run(
        common + ["-pass", "2", "-c:a", "libvorbis", "-b:a", AUDIO_BITRATE, str(out)],
        check=True,
    )

    # Clean up the two-pass log ffmpeg leaves behind.
    for leftover in src.parent.glob(f"{src.stem}-*.log"):
        try:
            leftover.unlink()
        except OSError:
            pass

    mb = out.stat().st_size / 1e6
    print(f"  Done: {out} ({mb:.1f} MB)\n")
    return out


def _default_sources() -> list[Path]:
    """The mp4 sources behind VIDEO_ITEMS, so a no-arg run encodes them all."""
    from ethics_podcast.video_discussion import VIDEO_ITEMS

    sources: list[Path] = []
    for item in VIDEO_ITEMS:
        # Resolve back to the mp4 master even if the entry now prefers webm.
        mp4 = item["video_path"].with_suffix(".mp4")
        if mp4.exists():
            sources.append(mp4)
    return sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", type=Path, help="Source video(s)")
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing webm without making a .bak backup",
    )
    args = parser.parse_args()

    sources = args.sources or _default_sources()
    if not sources:
        print("No source videos found.")
        sys.exit(1)

    print(f"Encoding {len(sources)} video(s) to {TARGET_HEIGHT}p VP8 webm:\n")
    for src in sources:
        encode(src, force=args.force)


if __name__ == "__main__":
    main()
