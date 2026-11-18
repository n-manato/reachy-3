"""Preview the browser-based exhibit UI on one machine — no robots needed.

Starts the WebDisplayManager (FastAPI server) and drives the full flow with
REAL pre-generated content (video + generated turn audio from output/<session>/)
played on the computer's speakers, exactly like scripts/preview_displays.py but
rendering in the browser instead of PySide6 windows.

Open any of the printed URLs in a browser (on this PC or any LAN device):
    http://<ip>:8000/all          (all four displays tiled)
    http://<ip>:8000/top          (just the big top display)
    http://<ip>:8000/student      (one mini display)  ... etc.

Run:
    .venv\\Scripts\\python.exe scripts\\preview_web.py
    .venv\\Scripts\\python.exe scripts\\preview_web.py video_military
    .venv\\Scripts\\python.exe scripts\\preview_web.py --connect-wait 15

Press Ctrl+C in the terminal to quit.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running as a plain script (python scripts/preview_web.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ethics_podcast.web_display import WebDisplayManager

try:
    import sounddevice as sd
    from scipy.io import wavfile
    import numpy as np
except Exception:  # pragma: no cover
    sd = None


def _prefer_existing(*paths: str) -> Path:
    for value in paths:
        path = Path(value)
        if path.exists():
            return path
    return Path(paths[0])


SESSIONS = {
    "video_environment": {
        "title": "AI and the Environment",
        "topic": (
            "AI uses electricity and water. Is it wasteful? "
            "Do humans have a duty to protect the planet?"
        ),
        "video": _prefer_existing(
            "videos/Aiden_Environment_Video.webm",
            "videos/Aiden_Environment_Video.mp4",
        ),
    },
    "video_military": {
        "title": "AI and the Military",
        "topic": (
            "Should AI replace humans in combat? "
            "Should AI make life and death decisions in battle?"
        ),
        "video": _prefer_existing(
            "videos/Aiden_Military_Video.webm",
            "videos/Aiden_Military_Video.mp4",
        ),
    },
    "video_relationship": {
        "title": "AI and Relationships",
        "topic": (
            "What kinds of relationships are appropriate with AI? "
            "Can AI be a friend, teacher, caregiver, or companion?"
        ),
        "video": _prefer_existing(
            "videos/Aiden_Relationship_Video.webm",
            "videos/Aiden_Relationship_Video.mp4",
        ),
    },
    "video_achievement": {
        "title": "AI and Human Achievement",
        "topic": (
            "If AI can do many tasks better and faster than humans, "
            "what should humans spend time doing?"
        ),
        "video": _prefer_existing(
            "videos/Aiden_Achievment_video.webm",
            "videos/Aiden_Achievment_video.mp4",
        ),
    },
}

DEFAULT_SESSION = "video_environment"

DUMMY_TURNS = [
    {"role": "student", "transcript":
        "If AI uses so much electricity and water, are we simply trading one "
        "harm for another in the name of progress?", "duration_secs": 6.0},
    {"role": "professor", "transcript":
        "We have a duty to weigh those costs honestly, not to hide them behind "
        "the convenience the technology offers us.", "duration_secs": 6.0},
    {"role": "moderator", "transcript":
        "A virtuous engineer asks not only 'can we build it' but 'what kind of "
        "world does building it make us into'.", "duration_secs": 6.0},
    {"role": "student", "transcript":
        "So restraint itself might be the more advanced choice.",
        "duration_secs": 4.5},
]


def _load_session(session: str):
    meta = SESSIONS.get(session)
    conv_path = Path("output") / session / "conversation.json"

    if meta is None or not conv_path.exists():
        print(f"  No generated session '{session}' — using dummy turns.")
        title = meta["title"] if meta else "AI Ethics — Preview"
        topic = meta["topic"] if meta else "A short preview of the exhibit UI."
        video = meta["video"] if meta else None
        for t in DUMMY_TURNS:
            t["audio_path"] = None
        return title, topic, video, list(DUMMY_TURNS)

    with open(conv_path, encoding="utf-8") as f:
        data = json.load(f)

    out_dir = conv_path.parent
    turns = []
    for t in data.get("turns", []):
        wav = out_dir / t.get("audio_file", "")
        turns.append(
            {
                "role": t.get("role"),
                "transcript": t.get("transcript", ""),
                "duration_secs": float(t.get("duration_secs", 5.0)),
                "audio_path": str(wav) if wav.exists() else None,
            }
        )

    return data.get("title", meta["title"]), meta["topic"], meta["video"], turns


def _play_wav_async(path: str | None) -> None:
    if sd is None or path is None:
        return
    try:
        rate, data = wavfile.read(path)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        sd.stop()
        sd.play(data, samplerate=int(rate), blocking=False)
    except Exception as e:  # pragma: no cover
        print(f"  WARNING: could not play {path}: {e}")


async def main() -> None:
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    session = positional[0] if positional else DEFAULT_SESSION

    connect_wait = 8.0
    if "--connect-wait" in args:
        try:
            connect_wait = float(args[args.index("--connect-wait") + 1])
        except (ValueError, IndexError):
            pass

    # Wait for the real video to end via the browser, or cap video preview time.
    wait_for_video_end = "--full-video" in args
    video_preview_secs = 20.0

    title, topic, video, turns = _load_session(session)
    video_arg = video if (video and Path(video).exists()) else None

    display = WebDisplayManager(video_path=video_arg)
    # The /top page only plays media while the transport is "playing". The
    # preview drives the display directly, so press PLAY on its behalf.
    display.request_play()

    print(f"  Session: {title}  ({len(turns)} turns)")
    print("  >>> Open one of the URLs above in a browser (e.g. /top or /all). <<<")
    if sd is None:
        print("  (sounddevice unavailable — subtitles will show without audio)")

    try:
        if video_arg is not None:
            # Hold the video until a top display is actually connected, so the
            # video stage is never missed.
            print(f"  Waiting up to {connect_wait:.0f}s for a /top display to connect...")
            connected = await display.wait_for_connection("top", timeout=connect_wait)
            if connected:
                print("  Top display connected.")
            else:
                print("  No top display yet — playing anyway.")
            print("  ▶ Playing video now — watch the /top (or /all) page.")
            display.play_video()
            if wait_for_video_end:
                await display.wait_until_near_end_and_pause()
            else:
                print(f"  (previewing first {video_preview_secs:.0f}s; "
                      "use --full-video to watch the whole clip)")
                await asyncio.sleep(video_preview_secs)
        else:
            print("  No video file — skipping video stage.")

        print("  Showing topic...")
        display.show_topic(title=title, topic=topic)
        await asyncio.sleep(3)

        roles = [t["role"] for t in turns]
        for i, turn in enumerate(turns):
            role = turn["role"]
            next_role = roles[i + 1] if i + 1 < len(roles) else None

            print(f"  [{role}] {turn['transcript'][:60]}...")
            display.show_subtitle(role, turn["transcript"], audio_path=turn["audio_path"])
            _play_wav_async(turn["audio_path"])

            await asyncio.sleep(turn["duration_secs"] + 0.3)

            if sd is not None:
                sd.stop()
            print(f"  [{role}] finished -> gaze to {next_role}")
            display.finish_subtitle(role, next_role)
            await asyncio.sleep(2.0)

        print("  Preview complete. Holding 10s (Ctrl+C to quit sooner)...")
        await asyncio.sleep(10)

    finally:
        if sd is not None:
            sd.stop()
        display.close()
        await asyncio.sleep(0.3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Preview cancelled.")
