"""Background music lookup.

The exhibit PC has no sound card, so there is no server-side mixer here (the
reference project's pygame BGMManager is deliberately not ported). The browser
showing /top loops the track itself, which keeps every sound in the room on one
device and under one mute key.

The level is not fixed. The page fades the bed in over a few seconds, ducks it
to about a third whenever a robot is actually speaking, and lets it drift back
up between turns -- all driven by the speech element's own events, so it is in
step with what the room hears and costs nothing on the link the robots need for
motion.

`boma.mp3` is what the show plays. It is not an album track: see
scripts/make_loop.py for how one is turned into a seamless, speech-friendly
loop.

Drop an .mp3 into music/ to enable it; with the directory empty the show simply
runs without background music.
"""

from __future__ import annotations

from pathlib import Path

from boma_exhibit.config import MUSIC_DIR

# Preferred track, if present. Otherwise the first mp3 in the directory.
PREFERRED = "boma.mp3"


def get_bgm_path() -> Path | None:
    """The track to loop under the sketch, or None when music/ is empty."""
    preferred = MUSIC_DIR / PREFERRED
    if preferred.exists():
        return preferred

    for candidate in sorted(MUSIC_DIR.glob("*.mp3")):
        return candidate
    return None
