"""Run the exhibit display with no robots and no TV.

Everything except the hardware: the real rendered audio, the real captions, the
real timing. Open the printed URL in any browser on the LAN and watch a full
run of the sketch.

    uv run python scripts/preview_web.py
    uv run python scripts/preview_web.py --variant v03
    uv run python scripts/preview_web.py --loop
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boma_exhibit.config import load_config
from boma_exhibit.exhibit import ATTRACT_SUBTITLE, run_exhibit
from boma_exhibit.mock_robot import connect_mock_robots
from boma_exhibit.player import run_playback_with_connected_robots
from boma_exhibit.script_data import TITLE
from boma_exhibit.variants import list_variants
from boma_exhibit.web_display import WebDisplayManager


async def preview_one(config, variant: str | None, wait: float) -> None:
    variants = list_variants(config.session_dir)
    if not variants:
        print(f"Nothing rendered in {config.session_dir}. Run menu option 1 first.")
        return

    chosen = variants[0]
    if variant:
        matches = [path for path in variants if path.name == variant]
        if not matches:
            print(f"No variant named {variant}. Have: {[p.name for p in variants]}")
            return
        chosen = matches[0]

    display = WebDisplayManager(
        host=config.display_web_host,
        port=config.display_web_port,
        speech_bitrate=config.display_speech_bitrate,
        title=TITLE,
        subtitle=ATTRACT_SUBTITLE,
    )
    robots = {robot.role: robot for robot in connect_mock_robots()}

    try:
        await display.wait_for_connection(timeout=wait)
        display.show_attract(TITLE, ATTRACT_SUBTITLE)
        # Same gate as the real exhibit. The Play click is what unlocks audio in
        # the browser, so without it the preview would run in silence.
        print("\n  Press PLAY on the page to start the preview.\n")
        await display.wait_for_play()
        # "surround" is what the exhibit uses, and it is what routes the audio
        # into the browser instead of this machine's (absent) sound card.
        await run_playback_with_connected_robots(
            config,
            robots,
            chosen / "conversation.json",
            audio_mode="surround",
            display=display,
        )
        display.request_stop()
        display.show_attract(TITLE, ATTRACT_SUBTITLE)
        print("\nPreview finished. Ctrl+C to stop the server.")
        await asyncio.Event().wait()
    finally:
        display.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", help="which variant to play (default: the first)")
    parser.add_argument(
        "--loop", action="store_true", help="run the full exhibit loop instead"
    )
    parser.add_argument(
        "--connect-wait",
        type=float,
        default=60.0,
        help="seconds to wait for a browser before offering Play",
    )
    args = parser.parse_args()

    config = load_config()
    try:
        if args.loop:
            asyncio.run(run_exhibit(config, audio_mode="surround", demo_mode=True))
        else:
            asyncio.run(preview_one(config, args.variant, args.connect_wait))
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
