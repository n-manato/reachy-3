"""Phase 2: Play a pre-generated conversation through the robots.

Motion architecture (matches Pollen's conversation app):
- Each robot worker runs a 100Hz control loop with set_target()
- Primary moves (emotions, dances, goto) go through the move queue
- Speech sway is a secondary offset layered on top via compose_world_offset()
- Auto-breathing when idle (robot is always alive)
- This player only sends high-level commands — NO raw set_target/set_full calls

Network safety:
- Audio: push_audio sends PCM chunks (safe, buffered by WebRTC)
- Speech sway: set_speech_offsets at ~20Hz (lightweight 6-float tuple)
- Tool calls: queue_emotion/queue_dance (one-shot, infrequent)
- Listener: single queue_goto per turn (SDK interpolates smoothly)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from ethics_podcast.config import Config
from ethics_podcast.robot_controller import RobotController, connect_robots

logger = logging.getLogger(__name__)

BASE_OUTPUT_DIR = Path("output")

# ── Speech sway parameters (from conversation app's SwayRollRT) ────────────

SWAY_MASTER = 1.5
SWAY_F_PITCH = 2.2
SWAY_A_PITCH = 0.08     # radians (~4.5°)
SWAY_F_YAW = 0.6
SWAY_A_YAW = 0.13       # radians (~7.5°)
SWAY_F_ROLL = 1.3
SWAY_A_ROLL = 0.04      # radians (~2.25°)
SWAY_F_X = 0.35
SWAY_A_X = 0.0045       # meters (4.5mm)
SWAY_F_Y = 0.45
SWAY_A_Y = 0.00375      # meters (3.75mm)
SWAY_F_Z = 0.25
SWAY_A_Z = 0.00225      # meters (2.25mm)

SWAY_DB_LOW = -46.0
SWAY_DB_HIGH = -18.0
LOUDNESS_GAMMA = 0.9
SWAY_HZ = 20            # how often to update speech offsets

SWAY_SMOOTHING = 0.3    # loudness envelope smoothing


def _rms_dbfs(pcm: np.ndarray) -> float:
    rms = np.sqrt(np.mean(pcm * pcm) + 1e-12)
    return float(20.0 * math.log10(rms + 1e-12))


def _loudness_01(db: float) -> float:
    t = (db - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    return max(0.0, min(1.0, t)) ** LOUDNESS_GAMMA


# ── Playback tasks ─────────────────────────────────────────────────────────

async def _stream_audio(
    robot: RobotController | None, pcm: np.ndarray, sample_rate: int, laptop: bool = False,
) -> None:
    """Send audio to the robot or play on laptop speaker."""
    if laptop:
        import sounddevice as sd
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: sd.play(pcm, samplerate=sample_rate, blocking=True))
    else:
        chunk_size = sample_rate  # 1-second chunks
        for i in range(0, len(pcm), chunk_size):
            robot.push_audio(pcm[i : i + chunk_size], source_rate=sample_rate)


async def _drive_speech_sway(
    robot: RobotController,
    pcm: np.ndarray,
    sample_rate: int,
    duration: float,
    phases: tuple[float, ...],
    stop: asyncio.Event,
) -> None:
    """Send speech sway offsets at ~20Hz driven by audio loudness.

    Offsets are in meters (xyz) and radians (rpy) — the worker's 100Hz loop
    applies them as secondary offsets via compose_world_offset().
    """
    tick = 1.0 / SWAY_HZ
    window = int(sample_rate * tick * 2)
    total = len(pcm)
    smoothed = 0.0
    t_start = time.monotonic()

    while not stop.is_set():
        elapsed = time.monotonic() - t_start
        if elapsed >= duration + 0.3:
            break

        cursor = int(elapsed * sample_rate)
        if 0 <= cursor < total - window:
            db = _rms_dbfs(pcm[cursor : cursor + window])
            target = _loudness_01(db)
        else:
            target = 0.0

        smoothed += SWAY_SMOOTHING * (target - smoothed)
        loud = smoothed * SWAY_MASTER
        t = elapsed

        # Compute offsets in meters/radians (matching conversation app units)
        x = SWAY_A_X * loud * math.sin(2 * math.pi * SWAY_F_X * t + phases[0])
        y = SWAY_A_Y * loud * math.sin(2 * math.pi * SWAY_F_Y * t + phases[1])
        z = SWAY_A_Z * loud * math.sin(2 * math.pi * SWAY_F_Z * t + phases[2])
        roll = SWAY_A_ROLL * loud * math.sin(2 * math.pi * SWAY_F_ROLL * t + phases[3])
        pitch = SWAY_A_PITCH * loud * math.sin(2 * math.pi * SWAY_F_PITCH * t + phases[4])
        yaw = SWAY_A_YAW * loud * math.sin(2 * math.pi * SWAY_F_YAW * t + phases[5])

        robot.set_speech_offsets(x, y, z, roll, pitch, yaw)
        await asyncio.sleep(tick)

    # Clear offsets when done
    robot.set_speech_offsets(0, 0, 0, 0, 0, 0)


async def _replay_tool_calls(
    robot: RobotController,
    tool_calls: list[dict],
    stop: asyncio.Event,
) -> None:
    """Replay pre-generated tool calls at their recorded timestamps."""
    if not tool_calls:
        return

    t_start = time.monotonic()
    for tc in tool_calls:
        target_time = tc["at_secs"]
        name = tc["name"]
        args = tc["arguments"]

        # Wait until the right moment
        while not stop.is_set():
            elapsed = time.monotonic() - t_start
            if elapsed >= target_time:
                break
            await asyncio.sleep(0.05)

        if stop.is_set():
            break

        # Dispatch
        if name == "play_emotion":
            robot.queue_emotion(args.get("emotion", "cheerful1"))
        elif name == "dance":
            robot.queue_dance(args.get("dance_name", "simple_nod"))
        elif name == "move_head":
            direction = args.get("direction", "front")
            head_map = {
                "left": (0, 0, 0, 0, 0, 40),
                "right": (0, 0, 0, 0, 0, -40),
                "up": (0, 0, 0, 0, -30, 0),
                "down": (0, 0, 0, 0, 30, 0),
                "front": (0, 0, 0, 0, 0, 0),
                "up left": (0, 0, 0, 0, -25, 30),
                "up right": (0, 0, 0, 0, -25, -30),
                "down left": (0, 0, 0, 0, 25, 30),
                "down right": (0, 0, 0, 0, 25, -30),
            }
            robot.queue_goto(head_map.get(direction, (0, 0, 0, 0, 0, 0)), duration=0.8)


async def _setup_listener(robot: RobotController, look_yaw: float) -> None:
    """Set listener to look toward speaker (single command, SDK interpolates)."""
    robot.queue_goto(head=(0, 0, 0, 0, 0, look_yaw), duration=1.0)


async def _play_turn(
    speaker: RobotController,
    listener: RobotController,
    audio_path: Path,
    duration: float,
    tool_calls: list[dict],
    speaker_phases: tuple[float, ...],
    listener_look_yaw: float,
    laptop_audio: bool = False,
) -> None:
    """Play one turn: audio + speech sway + tool call replay, all in parallel."""
    if not audio_path.exists():
        logger.warning("Audio file not found: %s", audio_path)
        await asyncio.sleep(duration)
        return

    sample_rate, pcm_data = wavfile.read(audio_path)
    pcm_float32 = pcm_data.astype(np.float32) / 32768.0

    # Clear any leftover moves from previous turn
    speaker.clear_moves()

    # Point listener toward speaker
    await _setup_listener(listener, listener_look_yaw)

    stop = asyncio.Event()

    # Run everything in parallel
    await asyncio.gather(
        _stream_audio(speaker, pcm_float32, sample_rate, laptop=laptop_audio),
        _drive_speech_sway(speaker, pcm_float32, sample_rate, duration, speaker_phases, stop),
        _replay_tool_calls(speaker, tool_calls, stop),
        asyncio.sleep(duration + 0.5),  # master timer
    )

    stop.set()

    # Clear speech offsets and let breathing take over
    speaker.set_speech_offsets(0, 0, 0, 0, 0, 0)
    await asyncio.sleep(0.3)


# ── Main playback ──────────────────────────────────────────────────────────

async def run_playback(config: Config, session_name: str | None = None, laptop_audio: bool = False) -> None:
    """Connect to robots and play the saved conversation."""
    if session_name:
        output_dir = BASE_OUTPUT_DIR / session_name
    else:
        output_dir = BASE_OUTPUT_DIR

    meta_path = output_dir / "conversation.json"
    if not meta_path.exists():
        print(f"No conversation found in {output_dir}/. Run 'Generate' first.")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    turns = meta["turns"]
    title = meta.get("title", session_name or "Ethics Podcast")
    print(f"\nSession: {title}")
    print(f"Loaded {len(turns)} turns. Connecting to robots...")

    robot_a, robot_b = await connect_robots(config.robot_a, config.robot_b)

    rng = np.random.default_rng(42)
    phase_a = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())
    phase_b = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())

    try:
        robot_a.wiggle_antennas()
        robot_b.wiggle_antennas()
        await asyncio.sleep(2)

        print("\nStarting playback!\n")

        for turn in turns:
            role = turn["role"]
            transcript = turn["transcript"]
            audio_file = output_dir / turn["audio_file"]
            duration = turn["duration_secs"]
            tool_calls = turn.get("tool_calls", [])

            if role == "student":
                speaker, listener = robot_a, robot_b
                sp_phases = phase_a
                li_look_yaw = 20.0
            else:
                speaker, listener = robot_b, robot_a
                sp_phases = phase_b
                li_look_yaw = -20.0

            label = "Student" if role == "student" else "Professor"
            tools_str = f" + {len(tool_calls)} actions" if tool_calls else ""
            print(f"  [Turn {turn['turn']}] {label} ({duration}s{tools_str}): {transcript[:70]}...")

            await _play_turn(
                speaker, listener, audio_file, duration,
                tool_calls, sp_phases, li_look_yaw,
                laptop_audio=laptop_audio,
            )

            await asyncio.sleep(config.inter_turn_pause)

        # Final neutral
        robot_a.clear_moves()
        robot_b.clear_moves()
        robot_a.queue_goto(head=(0, 0, 0, 0, 0, 0), duration=1.5)
        robot_b.queue_goto(head=(0, 0, 0, 0, 0, 0), duration=1.5)
        await asyncio.sleep(2)

        print("\nPlayback complete!")

    finally:
        robot_a.stop()
        robot_b.stop()
