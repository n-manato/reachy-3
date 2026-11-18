"""Check that each robot can speak through its own speaker (HTTP play_sound).

    uv run python scripts/test_robot_audio.py            # all three
    uv run python scripts/test_robot_audio.py nova       # one

For each robot: acquire media, upload one of its rendered clips, play it, and
report the upload time and the play-request latency. Run this first on the
day: if it fails, set AUDIO_OUTPUT=display in .env and the center screen
speaks for everyone.
"""

from __future__ import annotations

import sys
import time

from lta_exhibit.audio_utils import robot_clip
from lta_exhibit.config import OUTPUT_DIR, ROLE_ORDER, load_config
from lta_exhibit.generator import load_conversation, rendered_topics
from lta_exhibit.robot_controller import RobotController


def clip_for(role: str):
    for topic in rendered_topics():
        conv = load_conversation(topic)
        for line in conv["segments"]["intro"] + conv["segments"]["discussion"]:
            if line["role"] == role:
                return OUTPUT_DIR / topic / line["audio_file"], line["duration_secs"]
    return None, 0


def main() -> int:
    config = load_config()
    roles = [r for r in ROLE_ORDER if len(sys.argv) < 2 or r in sys.argv[1:]]
    ok_all = True
    for role in roles:
        rc = config.robot(role)
        ctl = RobotController(rc.host, rc.name, role)  # never start(): HTTP only
        path, dur = clip_for(role)
        print(f"\n{role.upper()} @ {rc.host}")
        if path is None:
            print("  no rendered clip -- run menu 2 first"); return 1
        # The show sends the +ROBOT_GAIN_DB copy; test with the same loudness.
        path = robot_clip(path, config.robot_gain_db)
        t0 = time.monotonic(); media = ctl.ensure_media()
        print(f"  media acquired: {media}  ({time.monotonic() - t0:.2f}s)")
        t0 = time.monotonic(); name = ctl.upload_sound_file(path)
        print(f"  upload: {'ok' if name else 'FAILED'}  ({time.monotonic() - t0:.2f}s)")
        t0 = time.monotonic(); played = ctl.play_sound_file(path)
        print(f"  play request: {'ok' if played else 'FAILED'}  ({time.monotonic() - t0:.2f}s)")
        ok_all &= bool(played)
        if played:
            print(f"  -> you should hear {role.upper()} for {dur:.1f}s")
            time.sleep(dur + 0.5)
    print("\nALL OK" if ok_all else "\nSome robots failed. Use AUDIO_OUTPUT=display as a fallback.")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
