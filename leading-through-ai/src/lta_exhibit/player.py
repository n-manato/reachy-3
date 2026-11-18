"""Play pre-rendered lines through the three robots.

Motion architecture is inherited unchanged from the BOMA / Ethics Podcast
exhibits, where it was tuned against a congested shared Wi-Fi link:
  - each robot worker runs its own control loop calling set_target()
  - primary moves (poses, emotions) go through the worker's move queue
  - speech sway is layered on top as a secondary offset while a robot speaks
  - Stop eases everything down (fade_to_rest) instead of cutting it dead

What is new is where the voice comes from. With AUDIO_OUTPUT=robot each line
is played on the speaking robot's own speaker via the daemon's HTTP
play_sound; if that fails for any reason (robot down, upload failed, a mock
standing in) the line falls back to the center screen's browser, panned to
that seat, so the show never goes silent.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from lta_exhibit.config import ROLE_ORDER, Config
from lta_exhibit.mock_robot import MockRobot
from lta_exhibit.personas import PROFILES

logger = logging.getLogger(__name__)

# Poses during a run. Head: x, y, z (m) then roll, pitch, yaw (deg).
# Antennas: radians, larger magnitude = more drooped.
SPEAKING_HEAD_DEG = (0, 0, 0, 0, 0, 0)
SPEAKING_ANT = (-0.1745, 0.1745)
LISTENING_HEAD_DEG = (0, 0, 0, 0, 10, 0)
LISTENING_ANT = (-1.40, 1.40)

# ── Speech sway (unchanged from BOMA) ─────────────────────────────────────
SWAY_MASTER = 1.5
SWAY_F_PITCH, SWAY_A_PITCH = 2.2, 0.09
SWAY_F_YAW, SWAY_A_YAW = 0.6, 0.15
SWAY_F_ROLL, SWAY_A_ROLL = 1.3, 0.015
SWAY_F_X, SWAY_A_X = 0.35, 0.0045
SWAY_F_Y, SWAY_A_Y = 0.45, 0.00375
SWAY_F_Z, SWAY_A_Z = 0.25, 0.00225
SWAY_DB_LOW, SWAY_DB_HIGH = -46.0, -18.0
LOUDNESS_GAMMA = 0.9
SWAY_HZ = 50
SWAY_SMOOTHING = 0.3
SWAY_MIN_WHILE_SPEAKING = 0.22

FADE_OFFSET_SECS = 0.6
FADE_LOWER_SECS = 1.6
FADE_HZ = 25

SHORT_TURN_SECS = 2.5

# Where each robot sits, for the browser fallback's stereo pan.
ROLE_PAN = {"nova": -0.85, "sage": 0.0, "aegis": 0.85}

# Loudness envelope sent to the screens for the speaking "orb".
LEVEL_FPS = 20

# A play request that has not been accepted by the robot within this many
# seconds is abandoned and the line goes to the screen instead. After a
# failure that robot's speaker is skipped for SPEAKER_RETRY_SECS, so a robot
# that has dropped off the Wi-Fi costs one short hiccup, not one per line.
ROBOT_PLAY_TIMEOUT = 2.0
SPEAKER_RETRY_SECS = 60.0


def _diag(message: str) -> None:
    print(f"  [diag {time.strftime('%H:%M:%S')}] {message}", flush=True)


def _rms_dbfs(pcm: np.ndarray) -> float:
    rms = np.sqrt(np.mean(pcm * pcm) + 1e-12)
    return float(20.0 * math.log10(rms + 1e-12))


def _loudness_01(db: float) -> float:
    t = (db - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    return max(0.0, min(1.0, t)) ** LOUDNESS_GAMMA


def level_envelope(pcm: np.ndarray, sample_rate: int, fps: int = LEVEL_FPS) -> list[float]:
    hop = max(1, sample_rate // fps)
    out = []
    for start in range(0, len(pcm), hop):
        chunk = pcm[start : start + hop]
        out.append(round(_loudness_01(_rms_dbfs(chunk)), 2) if len(chunk) else 0.0)
    return out


def seeded_phases() -> dict[str, tuple[float, ...]]:
    rng = np.random.default_rng(42)
    return {role: tuple(rng.uniform(0, 2 * math.pi, 6).tolist()) for role in ROLE_ORDER}


async def _drive_speech_sway(robot, pcm, sample_rate, duration, phases, stop: asyncio.Event) -> None:
    tick = 1.0 / SWAY_HZ
    window = int(sample_rate * tick * 2)
    total = len(pcm)
    smoothed = 0.0
    t_start = time.monotonic()
    cancelled = False
    try:
        while not stop.is_set():
            elapsed = time.monotonic() - t_start
            if elapsed >= duration + 0.3:
                break
            cursor = int(elapsed * sample_rate)
            target = _loudness_01(_rms_dbfs(pcm[cursor : cursor + window])) if 0 <= cursor < total - window else 0.0
            if elapsed < duration:
                target = max(target, SWAY_MIN_WHILE_SPEAKING)
            smoothed += SWAY_SMOOTHING * (target - smoothed)
            loud = smoothed * SWAY_MASTER
            t = elapsed
            robot.set_speech_offsets(
                SWAY_A_X * loud * math.sin(2 * math.pi * SWAY_F_X * t + phases[0]),
                SWAY_A_Y * loud * math.sin(2 * math.pi * SWAY_F_Y * t + phases[1]),
                SWAY_A_Z * loud * math.sin(2 * math.pi * SWAY_F_Z * t + phases[2]),
                SWAY_A_ROLL * loud * math.sin(2 * math.pi * SWAY_F_ROLL * t + phases[3]),
                SWAY_A_PITCH * loud * math.sin(2 * math.pi * SWAY_F_PITCH * t + phases[4]),
                SWAY_A_YAW * loud * math.sin(2 * math.pi * SWAY_F_YAW * t + phases[5]),
            )
            await asyncio.sleep(tick)
    except asyncio.CancelledError:
        # Leave the offsets where they are; fade_to_rest eases them down.
        cancelled = True
        raise
    finally:
        if not cancelled:
            robot.set_speech_offsets(0, 0, 0, 0, 0, 0)


async def _replay_tool_calls(robot, tool_calls: list[dict], stop: asyncio.Event) -> None:
    t_start = time.monotonic()
    for call in tool_calls:
        target = max(0.0, float(call.get("at_secs", 0.0)))
        while not stop.is_set() and time.monotonic() - t_start < target:
            await asyncio.sleep(0.05)
        if stop.is_set():
            break
        args = call.get("arguments", {})
        if call.get("name") == "play_emotion":
            robot.queue_emotion(args.get("emotion", "cheerful1"))
        elif call.get("name") == "dance":
            robot.queue_dance(args.get("dance_name", "simple_nod"))


async def _fade_one_to_rest(robot, offset_fade: float, lower: float) -> None:
    start = tuple(getattr(robot, "last_speech_offsets", (0.0,) * 6))
    if offset_fade > 0 and any(start):
        steps = max(1, int(offset_fade * FADE_HZ))
        for step in range(1, steps + 1):
            scale = 0.5 * (1.0 + math.cos(math.pi * step / steps))
            try:
                robot.set_speech_offsets(*(v * scale for v in start))
            except Exception:
                break
            await asyncio.sleep(1.0 / FADE_HZ)
    try:
        robot.set_speech_offsets(0, 0, 0, 0, 0, 0)
        robot.queue_power_off_pose(duration=lower, force=True)
    except Exception:
        pass


async def fade_to_rest(robots, offset_fade: float = FADE_OFFSET_SECS, lower: float = FADE_LOWER_SECS) -> None:
    """Ease every robot down to rest. Deliberately does NOT call clear_moves()."""
    await asyncio.gather(*(_fade_one_to_rest(r, offset_fade, lower) for r in robots))


def stop_all_sound(robots) -> None:
    """Fire-and-forget stop of any line playing on a robot speaker."""
    loop = asyncio.get_event_loop()
    for robot in robots:
        stop = getattr(robot, "stop_sound", None)
        if callable(stop):
            loop.run_in_executor(None, stop, 1.0)


class LinePlayer:
    """Plays lines of a rendered topic, one robot at a time."""

    def __init__(self, config: Config, robots: dict, display) -> None:
        self.config = config
        self.robots = robots
        self.display = display
        self.phases = seeded_phases()
        # role -> monotonic time before which that robot's speaker is skipped.
        # Class-level so it survives from one topic to the next.
        self.speaker_down_until = LinePlayer.speaker_down

    speaker_down: dict[str, float] = {}

    async def play_lines(self, topic_dir: Path, lines: list[dict], next_after: str | None = None) -> None:
        for index, line in enumerate(lines):
            following = lines[index + 1]["role"] if index + 1 < len(lines) else next_after
            await self.play_line(topic_dir, line, following)
            await asyncio.sleep(self.config.inter_turn_pause)

    async def play_line(self, topic_dir: Path, line: dict, next_role: str | None = None) -> None:
        role = line["role"]
        speaker = self.robots[role]
        listeners = [r for k, r in self.robots.items() if k != role]
        audio_path = (topic_dir / line["audio_file"]).resolve()
        duration = float(line["duration_secs"])

        if not audio_path.exists():
            logger.warning("Missing audio %s", audio_path)
            return

        sample_rate, data = wavfile.read(audio_path)
        pcm = data.astype(np.float32) / 32768.0
        levels = level_envelope(pcm, sample_rate)

        for robot in self.robots.values():
            robot.ensure_alive()

        # Raise the speaker, lower the listeners (staggered for the Wi-Fi).
        snappy = duration < SHORT_TURN_SECS
        ready = 0.30 if snappy else 0.50
        started = time.monotonic()
        speaker.clear_moves()
        speaker.queue_goto(SPEAKING_HEAD_DEG, SPEAKING_ANT, duration=ready, clear_first=True)
        for listener in listeners:
            await asyncio.sleep(0.08)
            listener.queue_goto(LISTENING_HEAD_DEG, LISTENING_ANT, duration=ready, clear_first=True)
        remaining = ready + 0.08 - (time.monotonic() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)

        # Start the voice: robot speaker first, the screen if that fails.
        on_robot = False
        if self.config.audio_output == "robot" and time.monotonic() >= self.speaker_down_until.get(role, 0.0):
            loop = asyncio.get_running_loop()
            generation = getattr(speaker, "sound_generation", 0)
            try:
                on_robot = await asyncio.wait_for(
                    loop.run_in_executor(None, speaker.play_sound_file, audio_path, generation),
                    timeout=ROBOT_PLAY_TIMEOUT,
                )
            except asyncio.TimeoutError:
                # The thread may still deliver it late; make sure it cannot.
                stop_all_sound([speaker])
                on_robot = False
            except Exception as exc:  # noqa: BLE001
                logger.warning("Robot %s play failed: %s", role, exc)
                on_robot = False
            if on_robot:
                if self.config.robot_audio_latency > 0:
                    await asyncio.sleep(self.config.robot_audio_latency)
            elif not isinstance(speaker, MockRobot):
                self.speaker_down_until[role] = time.monotonic() + SPEAKER_RETRY_SECS
                _diag(f"robot_audio_fallback role={role} -> screen for {SPEAKER_RETRY_SECS:.0f}s")

        self.display.show_line(
            role=role,
            text=line["text"],
            duration=duration,
            levels=levels,
            audio_path=None if on_robot else audio_path,
            pan=ROLE_PAN.get(role, 0.0),
            next_role=next_role,
        )
        print(f"    {PROFILES[role]['name']:<5} ({duration:4.1f}s{' robot' if on_robot else ' screen'}): {line['text'][:70]}")

        stop = asyncio.Event()
        try:
            await asyncio.gather(
                asyncio.sleep(duration + 0.01),
                _replay_tool_calls(speaker, line.get("tool_calls", []), stop),
                _drive_speech_sway(speaker, pcm, sample_rate, duration, self.phases[role], stop),
            )
        except asyncio.CancelledError:
            if on_robot:
                stop_all_sound([speaker])
            raise
        finally:
            stop.set()

        settle = 0.45 if snappy else 0.7
        speaker.queue_goto(LISTENING_HEAD_DEG, LISTENING_ANT, duration=settle, clear_first=True)
        speaker.set_speech_offsets(0, 0, 0, 0, 0, 0)
        self.display.end_line(next_role)

    async def watch_screens(self, stop: asyncio.Event) -> None:
        """During the vote: each robot glances down at the screens and around
        the room, unhurried and out of step with its neighbours."""
        rng = np.random.default_rng()
        offsets = {role: i * 0.7 for i, role in enumerate(self.robots)}
        start = time.monotonic()
        next_move = {role: start + offsets[role] for role in self.robots}
        while not stop.is_set():
            now = time.monotonic()
            for role, robot in self.robots.items():
                if now < next_move[role]:
                    continue
                yaw = float(rng.uniform(-18, 18))
                pitch = float(rng.choice([14, 18, 4]))
                robot.queue_goto((0, 0, 0, 0, pitch, yaw), (-0.6, 0.6), duration=1.1, clear_first=True)
                if rng.random() < 0.25:
                    robot.queue_emotion(str(rng.choice(["inquiring1", "thoughtful1", "uncertain1"])))
                next_move[role] = now + float(rng.uniform(2.4, 4.0))
                await asyncio.sleep(0.08)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                pass
