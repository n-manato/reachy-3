"""Run the multi-display exhibit UI on a robot-less PC.

This drives the four-window DisplayManager with REAL pre-generated content
(video + generated turn audio from output/<session>/), playing the audio on
the computer's speakers. No robots, no reachy_mini, no GStreamer/pygobject,
no OpenAI/ElevenLabs keys are required — it only needs PySide6, python-vlc
(optional, for video), numpy/scipy and sounddevice.

Use it to see the full flow on this machine:
    video -> topic -> per-robot subtitles -> gaze guidance to the next robot.

Run:
    .venv\\Scripts\\python.exe scripts\\preview_displays.py
    .venv\\Scripts\\python.exe scripts\\preview_displays.py video_military
    .venv\\Scripts\\python.exe scripts\\preview_displays.py --test   (tiled, dummy)

Press Ctrl+C in the terminal to quit.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running as a plain script (python scripts/preview_displays.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ethics_podcast.displays.manager import DisplayManager

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


# Inline session metadata (mirrors video_discussion.VIDEO_ITEMS) so this
# preview does not import the robot stack.
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
    """Return (title, topic, video_path, turns) for a real generated session.

    Each turn dict has: role, transcript, duration_secs, audio_path (or None).
    Falls back to a dummy discussion if the session is unavailable.
    """
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
    """Start playing a WAV on the computer speakers (non-blocking)."""
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
    args = [a for a in sys.argv[1:]]
    test_only = "--test" in args
    positional = [a for a in args if not a.startswith("--")]
    session = positional[0] if positional else DEFAULT_SESSION

    if test_only:
        title, topic, video, turns = (
            "AI Ethics — Preview",
            "Tiled UI test with dummy turns.",
            None,
            [dict(t, audio_path=None) for t in DUMMY_TURNS],
        )
    else:
        title, topic, video, turns = _load_session(session)

    video_arg = video if (video and Path(video).exists()) else None

    display = DisplayManager(video_path=video_arg, test_mode=True)

    stop = asyncio.Event()
    pump = asyncio.create_task(display.pump_events_forever(stop))

    print(f"  Session: {title}  ({len(turns)} turns)")
    if sd is None:
        print("  (sounddevice unavailable — subtitles will show without audio)")

    try:
        if video_arg is not None and display.top.player is not None:
            print("  Previewing video for ~6s...")
            display.play_video()
            await asyncio.sleep(6)
            display.top.pause()
        else:
            print("  No video stage (no file or no libVLC).")

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

        print("  Preview complete. Holding 5s...")
        await asyncio.sleep(5)

    finally:
        if sd is not None:
            sd.stop()
        stop.set()
        await pump
        display.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Preview cancelled.")
