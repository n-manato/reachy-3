"""Video discussion loop for Ethics Podcast.

This mode:
  1. Plays each Aiden video.
  2. Detects when the video reaches the end.
  3. Keeps the video window open on the final frame.
  4. Runs the robot discussion.
  5. Closes the video.
  6. Moves to the next topic.

Requires:
  pip install python-vlc

Also requires VLC Media Player installed on Windows.
"""

from __future__ import annotations

import asyncio
import json
import time
import msvcrt
from pathlib import Path

from ethics_podcast.generator import run_generate_video_discussion
from ethics_podcast.player import AudioMode, run_playback_with_connected_robots
from ethics_podcast.robot_controller import connect_robots

ENABLE_PRE_VIDEO_HEAD_POSTURE = False
from ethics_podcast.video_window import ControlledVideoWindow


def _prefer_existing(*paths: str) -> Path:
    for value in paths:
        path = Path(value)
        if path.exists():
            return path
    return Path(paths[0])


VIDEO_ITEMS = [
    {
        "title": "AI and the Environment",
        "video_path": _prefer_existing(
            "videos/Aiden_Environment_Video.mp4",
            "videos/Aiden_Environment_Video.webm",
        ),
        "transcript_path": Path("transcripts/Aiden_Environment_Video.txt"),
        "playback_session": "video_environment",
        "topic": (
            "AI uses electricity and water. Is it wasteful? "
            "Do humans have a duty to protect the planet?"
        ),
    },
    {
        "title": "AI and the Military",
        "video_path": _prefer_existing(
            "videos/Aiden_Military_Video.mp4",
            "videos/Aiden_Military_Video.webm",
        ),
        "transcript_path": Path("transcripts/Aiden_Military_Video.txt"),
        "playback_session": "video_military",
        "topic": (
            "Should AI replace humans in combat? "
            "Should AI make life and death decisions in battle?"
        ),
    },
    {
        "title": "AI and Relationships",
        "video_path": _prefer_existing(
            "videos/Aiden_Relationship_Video.mp4",
            "videos/Aiden_Relationship_Video.webm",
        ),
        "transcript_path": Path("transcripts/Aiden_Relationship_Video.txt"),
        "playback_session": "video_relationship",
        "topic": (
            "What kinds of relationships are appropriate with AI? "
            "Can AI be a friend, teacher, caregiver, or companion?"
        ),
    },
    {
        "title": "AI and Human Achievement",
        "video_path": _prefer_existing(
            "videos/Aiden_Achievment_video.mp4",
            "videos/Aiden_Achievment_video.webm",
        ),
        "transcript_path": Path("transcripts/Aiden_Achievment_video.txt"),
        "playback_session": "video_achievement",
        "topic": (
            "If AI can do many tasks better and faster than humans, "
            "what should humans spend time doing?"
        ),
    },
]


def load_transcript(transcript_path: Path) -> str:
    """Load transcript text from disk."""
    if not transcript_path.exists():
        print(f"  WARNING: Transcript file not found: {transcript_path}")
        return ""

    return transcript_path.read_text(encoding="utf-8").strip()


def conversation_exists(session_name: str) -> bool:
    """Return True only if a valid generated conversation exists."""
    conversation_path = Path("output") / session_name / "conversation.json"

    if not conversation_path.exists():
        return False

    try:
        with open(conversation_path, encoding="utf-8") as f:
            data = json.load(f)

        return bool(data.get("turns"))

    except json.JSONDecodeError:
        print(f"  WARNING: Broken JSON found: {conversation_path}")
        print("  This discussion will be regenerated.")
        return False


def add_introductions_to_conversation(session_name: str) -> None:
    """Add one self-introduction to each robot's first turn in this session."""
    conversation_path = Path("output") / session_name / "conversation.json"

    if not conversation_path.exists():
        print(f"  WARNING: conversation.json not found: {conversation_path}")
        return

    with open(conversation_path, encoding="utf-8") as f:
        data = json.load(f)

    turns = data.get("turns", [])
    introduced = set()

    intros = {
        "student": (
            "Hi, I’m Bentham-Bot. "
            "I will look at this video from a consequence-based ethical perspective. "
        ),
        "professor": (
            "Hi, I’m Korsgaard-Bot. "
            "I will look at this video from a duty and respect-based ethical perspective. "
        ),
        "moderator": (
            "Hi, I’m Aquinas-Bot. "
            "I will look at this video from a virtue and purpose-based ethical perspective. "
        ),
        "bentham": (
            "Hi, I’m Bentham-Bot. "
            "I will look at this video from a consequence-based ethical perspective. "
        ),
        "korsgaard": (
            "Hi, I’m Korsgaard-Bot. "
            "I will look at this video from a duty and respect-based ethical perspective. "
        ),
        "aquinas": (
            "Hi, I’m Aquinas-Bot. "
            "I will look at this video from a virtue and purpose-based ethical perspective. "
        ),
        "robot_a": (
            "Hi, I’m Bentham-Bot. "
            "I will look at this video from a consequence-based ethical perspective. "
        ),
        "robot_b": (
            "Hi, I’m Korsgaard-Bot. "
            "I will look at this video from a duty and respect-based ethical perspective. "
        ),
        "robot_c": (
            "Hi, I’m Aquinas-Bot. "
            "I will look at this video from a virtue and purpose-based ethical perspective. "
        ),
    }

    changed = False

    for turn in turns:
        speaker = (
            turn.get("speaker")
            or turn.get("role")
            or turn.get("robot")
            or turn.get("robot_id")
            or turn.get("agent")
            or turn.get("name")
            or ""
        )

        text_key = None

        for candidate in ("text", "content", "line", "message"):
            if candidate in turn:
                text_key = candidate
                break

        if not speaker or not text_key:
            continue

        speaker_key = str(speaker).lower()

        matched_id = None
        intro = None

        for key, value in intros.items():
            if key in speaker_key:
                matched_id = key
                intro = value
                break

        if intro is None or matched_id is None:
            continue

        if matched_id in introduced:
            continue

        current_text = str(turn[text_key])

        # Avoid adding the intro twice.
        lower_text = current_text.lower()

        if "i’m bentham-bot" in lower_text:
            introduced.add(matched_id)
            continue

        if "i’m korsgaard-bot" in lower_text:
            introduced.add(matched_id)
            continue

        if "i’m aquinas-bot" in lower_text:
            introduced.add(matched_id)
            continue

        turn[text_key] = intro + current_text
        introduced.add(matched_id)
        changed = True

        print(f"  Added intro for {speaker}")

    if changed:
        with open(conversation_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"  Updated introductions in {conversation_path}")
    else:
        print("  No introductions were added. Check speaker/text keys in conversation.json.")


def _read_arrow_key_windows() -> str | None:
    """Read one arrow key on Windows without blocking.

    Returns:
      "right" for right arrow
      "left" for left arrow
      None if no arrow key was pressed
    """
    if not msvcrt.kbhit():
        return None

    first = msvcrt.getch()

    # Arrow keys send a prefix first.
    if first not in (b"\x00", b"\xe0"):
        return None

    second = msvcrt.getch()

    if second == b"M":
        return "right"

    if second == b"K":
        return "left"

    return None


def _print_audio_mode(audio_mode: AudioMode) -> None:
    """Print current audio mode."""
    if audio_mode == "robot":
        print("  Audio output: Robot speakers")

    elif audio_mode == "computer":
        print("  Audio output: Computer or TV normal audio")

    elif audio_mode == "surround":
        print("  Audio output: TV surround-style audio")
        print("  Pan layout: Bentham-Bot left, Korsgaard-Bot center, Aquinas-Bot right")

    elif audio_mode == "hybrid_surround":
        print("  Audio output: TV surround-style audio + robot speakers")
        print("  Pan layout: Bentham-Bot left, Korsgaard-Bot center, Aquinas-Bot right")
        print("  TV delay and robot delay are enabled for hybrid mode")

    else:
        print(f"  Audio output: Unknown mode {audio_mode}")


VIDEO_VOLUME = 75


async def prepare_all_discussions(config) -> None:
    """Generate all missing video discussions before any video starts."""
    print("\n--- PREPARING VIDEO DISCUSSIONS ---")

    for item in VIDEO_ITEMS:
        title = item["title"]
        topic = item["topic"]
        transcript_path = item["transcript_path"]
        playback_session = item["playback_session"]

        conversation_path = Path("output") / playback_session / "conversation.json"

        if conversation_exists(playback_session):
            print(f"  Found: {conversation_path}")
            continue

        print(f"\n  Missing discussion: {playback_session}")
        print("  Generating before video playback...")

        transcript = load_transcript(transcript_path)

        if not transcript:
            print(f"  WARNING: Transcript missing. Skipping {playback_session}.")
            continue

        await run_generate_video_discussion(
            config=config,
            session_name=playback_session,
            title=title,
            topic=topic,
            transcript=transcript,
            num_exchanges=3,
        )

    print("\n  All available discussions are ready.")


async def reset_then_power_off_all(robot_a, robot_b, robot_c) -> None:
    """Reset all robots to front-facing pose, then lower them safely.

    Commands are staggered to avoid overloading WebRTC / motion control.
    """

    robots = [robot_a, robot_b, robot_c]

    # 1. Stop old moves one by one.
    for robot in robots:
        robot.clear_moves()
        await asyncio.sleep(0.08)

    # 2. Stop speech sway one by one.
    for robot in robots:
        robot.set_speech_offsets(0, 0, 0, 0, 0, 0)
        await asyncio.sleep(0.08)

    if not ENABLE_PRE_VIDEO_HEAD_POSTURE:
        return

    # 3. Return to front-facing ready pose one by one.
    ready_duration = 1.35

    for robot in robots:
        robot.queue_speaking_ready_pose(duration=ready_duration)
        await asyncio.sleep(0.20)

    # Wait until all ready motions finish.
    await asyncio.sleep(ready_duration + 0.4)

    # 4. Clear again lightly before lowering.
    for robot in robots:
        robot.clear_moves()
        await asyncio.sleep(0.08)

    for robot in robots:
        robot.set_speech_offsets(0, 0, 0, 0, 0, 0)
        await asyncio.sleep(0.08)

    # 5. Lower into power-off-like pose one by one.
    off_duration = 1.6

    for robot in robots:
        robot.queue_power_off_pose(duration=off_duration)
        await asyncio.sleep(0.25)

    await asyncio.sleep(off_duration + 0.5)


async def run_video_discussion_loop(
    config,
    laptop_audio: bool = False,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
) -> None:
    """Run the full video discussion loop."""

    if audio_mode is None:
        audio_mode = "computer" if laptop_audio else "robot"

    print("\n--- VIDEO DISCUSSION LOOP ---")
    _print_audio_mode(audio_mode)

    print()
    print(f"  Bentham-Bot / Left:      {config.robot_a.name or '?'} ({config.robot_a.host})")
    print(f"  Korsgaard-Bot / Center:  {config.robot_b.name or '?'} ({config.robot_b.host})")
    print(f"  Aquinas-Bot / Right:     {config.robot_c.name or '?'} ({config.robot_c.host})")

    await prepare_all_discussions(config)

    print("\n--- CONNECTING TO ROBOTS ---")
    print("  Connecting before videos start so discussion can begin quickly.")

    use_media = audio_mode in ("robot", "hybrid_surround")

    print(f"  Robot media backend: {'ON' if use_media else 'OFF'}")

    robot_a, robot_b, robot_c = await connect_robots(
        config.robot_a,
        config.robot_b,
        config.robot_c,
        use_media=use_media,
    )

    print("  Robots are connected and ready.")

    video_player = None

    try:
        video_index = 0

        while True:
            item = VIDEO_ITEMS[video_index]
            i = video_index + 1

            title = item["title"]
            video_path = item["video_path"]
            transcript_path = item["transcript_path"]
            playback_session = item["playback_session"]

            print(f"\n{'=' * 60}")
            print(f"  Video {i}: {title}")
            print(f"  Video file: {video_path}")
            print(f"  Transcript file: {transcript_path}")
            print(f"  Playback session: {playback_session}")
            print(f"{'=' * 60}")

            transcript = load_transcript(transcript_path)

            if not transcript:
                print("  WARNING: Transcript is missing or empty.")
            else:
                print("  Transcript found.")

            if not video_path.exists():
                print(f"  WARNING: Video file not found: {video_path}")
                print("  Skipping this video.")
                continue

            if not conversation_exists(playback_session):
                print(f"  WARNING: No valid discussion found for {playback_session}.")
                print("  Skipping this video discussion.")
                continue

            if video_player is None:
                video_player = ControlledVideoWindow(video_path)
            else:
                video_player.load_video(video_path)

            # Before video starts, reset fully, then lower.
            await reset_then_power_off_all(robot_a, robot_b, robot_c)

            video_player.play()

            # Fade in the actual VLC video image.
            await video_player.fade_video_in(1.2)
            
            # Let the video play until the real end.
            # Then fade the video screen to black.
            await video_player.wait_until_end_with_fade_out(2.5)

            # Show the discussion screen while black,
            # then fade the discussion screen in.
            await video_player.show_discussion_with_fade_in(
                title=title,
                topic=item["topic"],
                fade_seconds=1.0,
            )
            add_introductions_to_conversation(playback_session)

            await run_playback_with_connected_robots(
                config=config,
                robot_a=robot_a,
                robot_b=robot_b,
                robot_c=robot_c,
                session_name=playback_session,
                audio_mode=audio_mode,
                tv_audio_delay=tv_audio_delay,
                robot_audio_delay=robot_audio_delay,
                video_window=video_player,
            )

            await video_player.fade_to_black(0.8)
            video_player.hide_discussion()

            video_index = (video_index + 1) % len(VIDEO_ITEMS)

            if video_index == 0:
                print("\n  Finished the fourth video. Returning to the first video...")
            else:
                print("\n  Moving to next video...")

            await asyncio.sleep(2)

    finally:
        if video_player is not None:
            try:
                video_player.close()
            except Exception as e:
                print(f"  WARNING: Could not close video player cleanly: {e}")

        print("\n--- STOPPING ROBOTS ---")

        try:
            robot_a.stop()
        except Exception as e:
            print(f"  WARNING: Could not stop Robot A cleanly: {e}")

        try:
            robot_b.stop()
        except Exception as e:
            print(f"  WARNING: Could not stop Robot B cleanly: {e}")

        try:
            robot_c.stop()
        except Exception as e:
            print(f"  WARNING: Could not stop Robot C cleanly: {e}")

        print("\n  Video discussion loop finished.")
