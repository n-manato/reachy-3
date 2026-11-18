"""Phase 2: Play a pre-generated conversation through three robots.

Robots:
  - Robot A / Hello  = student
  - Robot B / Mellow = professor
  - Robot C / Fellow = moderator

Motion architecture:
- Each robot worker runs a control loop with set_target()
- Primary moves go through the move queue
- Speech sway is layered on top while the robot speaks
- Non-speaking robots look toward the current speaker
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


import numpy as np
from scipy.io import wavfile

from ethics_podcast.config import Config
from ethics_podcast.robot_controller import RobotController, connect_robots
from ethics_podcast.bgm_manager import (
    BGMManager,
    get_bgm_path_for_session,
)

logger = logging.getLogger(__name__)

BASE_OUTPUT_DIR = Path("output")

AudioMode = Literal["robot", "computer", "surround", "hybrid_surround", "display"]

# Upper bound on the daemon HTTP play request at the start of a turn. If the
# robot is off Wi-Fi the turn goes on (silent) rather than freezing the show.
HTTP_AUDIO_START_TIMEOUT = 10.0

# Live voice-output switch (web /top page). A line moved mid-way resumes on the
# new output where it is now; the robot side skips this much further ahead to
# cover uploading the rest of the line to the daemon. A line with less than
# HANDOFF_MIN_REMAINING left just finishes where it is.
ROBOT_HANDOFF_LEAD = 0.5
HANDOFF_MIN_REMAINING = 0.4
OUTPUT_POLL_SECONDS = 0.1
HANDOFF_DIR = Path(tempfile.gettempdir()) / "ethics_podcast_handoff"

ROLE_PAN = {
    "student": -0.85,    # Bentham-Bot, left
    "professor": 0.0,    # Korsgaard-Bot, center
    "moderator": 0.85,   # Aquinas-Bot, right
}

ROLE_LABELS = {
    "student": "Bentham-Bot / Left",
    "professor": "Korsgaard-Bot / Center",
    "moderator": "Aquinas-Bot / Right",
}

SPEAKER_HEAD = (0, 0, 0.04, 0, -20, 0)
LISTENER_HEAD = (0, 0, -0.04, 0, -20, 0)
FINAL_HEAD = (0, 0, 0, 0, -15, 0)

# ── Speech sway parameters ────────────────────────────────────────────────

SWAY_MASTER = 1.5
SWAY_F_PITCH = 2.2
SWAY_A_PITCH = 0.09
SWAY_F_YAW = 0.6
SWAY_A_YAW = 0.15
SWAY_F_ROLL = 1.3
SWAY_A_ROLL = 0.015
SWAY_F_X = 0.35
SWAY_A_X = 0.0045
SWAY_F_Y = 0.45
SWAY_A_Y = 0.00375
SWAY_F_Z = 0.25
SWAY_A_Z = 0.00225

SWAY_DB_LOW = -46.0
SWAY_DB_HIGH = -18.0
LOUDNESS_GAMMA = 0.9

SWAY_HZ = 50
SWAY_SMOOTHING = 0.3
SWAY_MIN_WHILE_SPEAKING = 0.22

# Diagnostic switch: disable the speaker-ready / listener-lowered head posture
# choreography without changing speech sway or recorded emotions.
ENABLE_HEAD_POSTURE = True

# Diagnostic switch: disable continuous speaking sway while keeping recorded
# emotions and the rest of the playback flow intact.
ENABLE_SPEECH_SWAY = True

# Diagnostic switch: disable generated recorded emotions/dances. This leaves
# audio, UI, and timing intact while avoiding recorded move set_target streams.
ENABLE_RECORDED_ACTIONS = True


def start_space_key_listener(loop, callback, stop_event) -> None:
    """Call ``callback`` on the asyncio loop whenever SPACE is pressed.

    A daemon thread polls the console without blocking the loop. Windows uses
    msvcrt; elsewhere it is a no-op (the exhibit PC runs Windows).
    """
    import threading

    def reader() -> None:
        try:
            import msvcrt
        except Exception:
            return
        while not stop_event.is_set():
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch == b" ":
                        loop.call_soon_threadsafe(callback)
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    threading.Thread(target=reader, daemon=True).start()
    print("  [mute] Press SPACE (terminal or browser) to mute/unmute.")


def _diag(message: str) -> None:
    """Print a timeline marker that is easy to grep in field logs."""
    print(f"  [diag {time.strftime('%H:%M:%S')}] {message}", flush=True)


def _robot_diag(*robots) -> str:
    parts = []
    for robot in robots:
        status = getattr(robot, "diagnostic_status", None)
        parts.append(status() if callable(status) else type(robot).__name__)
    return " | ".join(parts)

def _rms_dbfs(pcm: np.ndarray) -> float:
    """Return loudness in dBFS."""
    rms = np.sqrt(np.mean(pcm * pcm) + 1e-12)
    return float(20.0 * math.log10(rms + 1e-12))


def _loudness_01(db: float) -> float:
    """Map dBFS to 0.0-1.0 loudness."""
    t = (db - SWAY_DB_LOW) / (SWAY_DB_HIGH - SWAY_DB_LOW)
    return max(0.0, min(1.0, t)) ** LOUDNESS_GAMMA


# ── Playback tasks ─────────────────────────────────────────────────────────

def _apply_stereo_pan(pcm: np.ndarray, pan: float) -> np.ndarray:
    """Convert mono audio to stereo and pan it left, center, or right.

    pan:
      -1.0 = hard left
       0.0 = center
       1.0 = hard right
    """
    pan = max(-1.0, min(1.0, pan))

    if pcm.ndim == 2:
        mono = pcm.mean(axis=1)
    else:
        mono = pcm

    if pan < 0:
        left_gain = 1.0
        right_gain = 1.0 + pan
    elif pan > 0:
        left_gain = 1.0 - pan
        right_gain = 1.0
    else:
        left_gain = 1.0
        right_gain = 1.0

    stereo = np.column_stack(
        [
            mono * left_gain,
            mono * right_gain,
        ]
    )

    return stereo.astype(np.float32)

async def _play_tv_audio(
    pcm: np.ndarray,
    sample_rate: int,
    audio_mode: AudioMode,
    pan: float = 0.0,
    tv_audio_delay: float = 0.0,
) -> None:
    """Play audio through computer / TV output."""
    import sounddevice as sd

    if tv_audio_delay > 0:
        await asyncio.sleep(tv_audio_delay)

    output_pcm = pcm

    if audio_mode in ("surround", "hybrid_surround"):
        output_pcm = _apply_stereo_pan(pcm, pan)

    # Use non-blocking playback so Ctrl+C can unwind immediately instead of
    # waiting on a worker thread that is stuck inside sd.play(..., blocking=True).
    sd.stop()
    try:
        sd.play(output_pcm, samplerate=sample_rate, blocking=False)
        await asyncio.sleep(len(output_pcm) / sample_rate)
    finally:
        sd.stop()


async def _play_robot_audio(
    robot: RobotController | None,
    pcm: np.ndarray,
    sample_rate: int,
    robot_audio_delay: float = 0.0,
    audio_path: Path | None = None,
) -> None:
    """Play audio through robot speaker.

    Streams PCM over the SDK media path when the worker reports it ready.
    Otherwise falls back to the daemon HTTP playback of the turn's WAV file
    (see ``RobotController.play_sound_file``), which needs no GStreamer WebRTC
    support on this PC.
    """
    if robot is None:
        return

    if robot_audio_delay > 0:
        await asyncio.sleep(robot_audio_delay)

    if not robot.audio_ready:
        if audio_path is not None:
            loop = asyncio.get_running_loop()
            try:
                started = await asyncio.wait_for(
                    loop.run_in_executor(None, robot.play_sound_file, audio_path),
                    timeout=HTTP_AUDIO_START_TIMEOUT,
                )
            except asyncio.TimeoutError:
                started = False
                print("  Robot speaker: play request did not complete in time.")
            if started:
                return
        print("  Robot speaker skipped because audio is not ready.")
        return

    chunk_size = sample_rate

    for i in range(0, len(pcm), chunk_size):
        robot.push_audio(pcm[i : i + chunk_size], source_rate=sample_rate)


async def _stream_audio(
    robot: RobotController | None,
    pcm: np.ndarray,
    sample_rate: int,
    audio_mode: AudioMode = "robot",
    pan: float = 0.0,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    audio_path: Path | None = None,
) -> None:
    """Send audio to robot speaker, computer speaker, surround, or both."""

    if audio_mode == "display":
        # The active browser display plays this turn's WAV through the same
        # output device that plays the video. Motion timing is still handled by
        # the duration sleep in _play_turn().
        return

    if audio_mode == "robot":
        await _play_robot_audio(
            robot, pcm, sample_rate, robot_audio_delay=0.0, audio_path=audio_path
        )
        return

    if audio_mode in ("computer", "surround"):
        await _play_tv_audio(
            pcm,
            sample_rate,
            audio_mode=audio_mode,
            pan=pan,
            tv_audio_delay=tv_audio_delay,
        )
        return

    if audio_mode == "hybrid_surround":
        await asyncio.gather(
            _play_robot_audio(
                robot,
                pcm,
                sample_rate,
                robot_audio_delay=robot_audio_delay,
                audio_path=audio_path,
            ),
            _play_tv_audio(
                pcm,
                sample_rate,
                audio_mode=audio_mode,
                pan=pan,
                tv_audio_delay=tv_audio_delay,
            ),
        )
        return

def _turn_audio_mode(audio_mode: AudioMode, display=None) -> AudioMode:
    """Where this turn's voice plays, given the configured mode and display.

    The web display carries a live voice-output switch on its /top page
    (``audio_output``: "robot" | "browser"); when present it decides, and the
    configured mode was only its start value. Otherwise a display that can
    play speech takes over the computer / surround modes, as before.
    """
    live_mode = _display_output_mode(display)
    if live_mode is not None:
        return live_mode
    if (
        display is not None
        and getattr(display, "plays_speech_audio", False)
        and audio_mode in ("computer", "surround")
    ):
        return "display"
    return audio_mode


def _display_output_mode(display) -> AudioMode | None:
    """The voice mode the display's live switch asks for, or None if it has none."""
    output = getattr(display, "audio_output", None)
    if output == "browser":
        return "display"
    if output == "robot":
        return "robot"
    return None


# ── Live output switch: move the voice and the BGM while they play ─────────


@dataclass
class _VoiceLine:
    """The line being spoken right now, so a switch can move it mid-way."""

    speaker: RobotController
    role: str
    audio_path: Path
    mode: AudioMode
    started: float  # time.monotonic() when the voice started
    duration: float

    def elapsed(self) -> float:
        return time.monotonic() - self.started


class _LiveVoice:
    """Holder shared by the turn loop (writer) and the switch follower (reader)."""

    def __init__(self) -> None:
        self.line: _VoiceLine | None = None


async def _play_robot_from(
    robot: RobotController, line: _VoiceLine, live: _LiveVoice
) -> None:
    """Play the rest of ``line`` on its robot, from where it is now."""
    offset = line.elapsed() + ROBOT_HANDOFF_LEAD
    if offset >= line.duration - HANDOFF_MIN_REMAINING:
        return

    sample_rate, pcm = wavfile.read(line.audio_path)
    start = int(offset * sample_rate)

    if robot.audio_ready:
        tail = pcm[start:].astype(np.float32) / 32768.0
        for i in range(0, len(tail), sample_rate):
            robot.push_audio(tail[i : i + sample_rate], source_rate=sample_rate)
        return

    # HTTP fallback: the daemon can only play a file from its start, so upload
    # the remainder as its own file. The name carries the offset so the
    # daemon-side upload cache never serves a different cut.
    session_dir = HANDOFF_DIR / line.audio_path.parent.name
    session_dir.mkdir(parents=True, exist_ok=True)
    tail_path = session_dir / f"{line.audio_path.stem}.from{int(offset * 1000)}ms.wav"
    wavfile.write(tail_path, sample_rate, pcm[start:])

    loop = asyncio.get_running_loop()
    if await loop.run_in_executor(None, robot.upload_sound_file, tail_path) is None:
        print("  Robot speaker: could not upload the rest of the line.")
        return
    if live.line is not line:
        return  # the line ended while uploading
    await loop.run_in_executor(None, robot.play_sound_file, tail_path)


async def _hand_off_voice(display, line: _VoiceLine, live: _LiveVoice) -> None:
    """Move the line being spoken to ``line.mode`` (already set by the caller)."""
    if line.mode == "display":
        # Silence the robot first (waiting out an in-flight play request so it
        # cannot land after the stop), then resume in the browser.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, functools.partial(line.speaker.stop_sound, 3.0)
        )
        offset = line.elapsed()
        if live.line is line and offset < line.duration - HANDOFF_MIN_REMAINING:
            display.play_speech(line.role, line.audio_path, offset)
        return

    # To the robot: the page already paused its voice when the switch flipped.
    await _play_robot_from(line.speaker, line, live)


class _DiscussionBgm:
    """The discussion's background music, in the browser or on this PC's mixer.

    The web display's voice-output switch moves it live, together with the
    voices: BROWSER puts all sound in the browser.
    """

    BROWSER_VOLUME = 0.18

    def __init__(self, path: Path, display) -> None:
        self.path = path
        self.display = display
        self.mixer: BGMManager | None = None
        self.in_browser = False

    def _browser_ok(self) -> bool:
        return self.display is not None and getattr(self.display, "plays_bgm_audio", False)

    def _new_mixer(self) -> BGMManager:
        return BGMManager(self.path, normal_volume=0.40, speaking_volume=0.18)

    async def start(self, in_browser: bool) -> None:
        if in_browser and self._browser_ok():
            self.display.set_bgm(self.path, volume=self.BROWSER_VOLUME)
            self.in_browser = True
            return
        self.mixer = self._new_mixer()
        await self.mixer.intro()
        # 会話パート全体ではBGMを低く固定する
        await self.mixer.fade_to(self.mixer.speaking_volume, duration=0.8)

    async def move(self, in_browser: bool) -> None:
        if in_browser == self.in_browser or not self._browser_ok():
            return
        if in_browser:
            self._drop_mixer()
            self.display.set_bgm(self.path, volume=self.BROWSER_VOLUME)
            self.in_browser = True
            return
        self.display.stop_bgm()
        self.in_browser = False
        mixer = self._new_mixer()
        self.mixer = mixer

        # pygame decodes the whole track up front (seconds for an MP3). Do it
        # off the event loop so speech sway and motion keep running mid-line.
        # Whoever drops self.mixer meanwhile (STOP, a switch back) wins: the
        # loader then silences what it just started.
        def load() -> None:
            mixer.start()
            if self.mixer is not mixer:
                mixer.hard_stop()

        await asyncio.get_running_loop().run_in_executor(None, load)
        if self.mixer is mixer:
            await mixer.fade_to(mixer.speaking_volume, duration=1.0)

    def _drop_mixer(self) -> None:
        # Clear the reference before stopping, so a loader thread still
        # decoding sees it was dropped (see move()).
        mixer, self.mixer = self.mixer, None
        if mixer is not None:
            mixer.hard_stop()

    async def stop(self) -> None:
        """Natural end of the discussion: fade out gently."""
        mixer, self.mixer = self.mixer, None
        if mixer is not None:
            await mixer.stop()
            mixer.hard_stop()  # no-op after the fade; catches a mixer still loading
        if self.in_browser:
            self._stop_browser()

    def hard_stop(self) -> None:
        """Safety net for Ctrl+C / STOP / errors: no awaits."""
        self._drop_mixer()
        if self.in_browser:
            self._stop_browser()

    def _stop_browser(self) -> None:
        try:
            self.display.stop_bgm()
        except Exception:
            pass
        self.in_browser = False


async def _follow_output_switch(display, live: _LiveVoice, bgm: _DiscussionBgm) -> None:
    """Apply the /top voice-output switch the moment it flips.

    The line being spoken moves to the new output from where it is, and the
    BGM moves with it. Later lines simply start on the new output.
    """
    while True:
        await asyncio.sleep(OUTPUT_POLL_SECONDS)
        mode = _display_output_mode(display)
        if mode is None:
            continue
        try:
            # Voice first: the BGM's mixer fade-in takes a second.
            line = live.line
            if line is not None and line.mode != mode:
                line.mode = mode
                print(f"  Voice output -> {'browser' if mode == 'display' else 'robot'} (mid-line)")
                await _hand_off_voice(display, line, live)
            await bgm.move(mode == "display")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Voice output switch failed: %s", e)


async def _preload_robot_audio(
    robots: dict[str, RobotController],
    turns: list[dict],
    output_dir: Path,
    audio_mode: AudioMode,
) -> None:
    """Upload each turn's WAV to its speaker before playback starts.

    Only for the robot-speaker audio modes, and only for robots on the HTTP
    fallback path (SDK audio not ready): uploading up front keeps the per-turn
    start latency to a single small POST so the voice stays in sync with the
    speech sway.
    """
    if audio_mode not in ("robot", "hybrid_surround"):
        return

    per_role: dict[str, list[Path]] = {}
    for turn in turns:
        role = turn.get("role")
        robot = robots.get(role)
        if robot is None or robot.audio_ready:
            continue
        path = output_dir / turn["audio_file"]
        if path.exists():
            per_role.setdefault(role, []).append(path)

    if not per_role:
        return

    print("  SDK robot audio not available; uploading turn audio to the robots (HTTP playback)...")

    loop = asyncio.get_running_loop()
    counts = await asyncio.gather(
        *[
            loop.run_in_executor(None, robots[role].preload_sound_files, paths)
            for role, paths in per_role.items()
        ]
    )

    for (role, paths), ready in zip(per_role.items(), counts):
        hint = "" if ready else "  (daemon unreachable, or too old for /api/media/sounds/upload?)"
        print(f"    {role}: {ready}/{len(paths)} turn files ready on robot{hint}")


async def _drive_speech_sway(
    robot: RobotController,
    pcm: np.ndarray,
    sample_rate: int,
    duration: float,
    phases: tuple[float, ...],
    stop: asyncio.Event,
) -> None:
    """Send speech sway offsets while speaking.

    The motion is still driven by audio loudness, but it keeps a small
    minimum movement so the robot does not freeze during quiet speech.
    """
    tick = 1.0 / SWAY_HZ
    window = int(sample_rate * tick * 2)
    total = len(pcm)
    smoothed = 0.0
    t_start = time.monotonic()

    try:
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

            # Important:
            # During the speaking duration, never allow motion strength
            # to fall completely to zero.
            if elapsed < duration:
                target = max(target, SWAY_MIN_WHILE_SPEAKING)

            smoothed += SWAY_SMOOTHING * (target - smoothed)

            loud = smoothed * SWAY_MASTER
            t = elapsed

            x = SWAY_A_X * loud * math.sin(
                2 * math.pi * SWAY_F_X * t + phases[0]
            )
            y = SWAY_A_Y * loud * math.sin(
                2 * math.pi * SWAY_F_Y * t + phases[1]
            )
            z = SWAY_A_Z * loud * math.sin(
                2 * math.pi * SWAY_F_Z * t + phases[2]
            )
            roll = SWAY_A_ROLL * loud * math.sin(
                2 * math.pi * SWAY_F_ROLL * t + phases[3]
            )
            pitch = SWAY_A_PITCH * loud * math.sin(
                2 * math.pi * SWAY_F_PITCH * t + phases[4]
            )
            yaw = SWAY_A_YAW * loud * math.sin(
                2 * math.pi * SWAY_F_YAW * t + phases[5]
            )

            robot.set_speech_offsets(x, y, z, roll, pitch, yaw)

            await asyncio.sleep(tick)

    finally:
        robot.set_speech_offsets(0, 0, 0, 0, 0, 0)


async def _replay_tool_calls(
    robot: RobotController,
    tool_calls: list[dict],
    stop: asyncio.Event,
) -> None:
    """Replay pre-generated tool calls at their recorded timestamps."""
    if not tool_calls or not ENABLE_RECORDED_ACTIONS:
        return

    t_start = time.monotonic()

    for tool_call in tool_calls:
        target_time = tool_call.get("at_secs", 0.0)
        name = tool_call.get("name")
        args = tool_call.get("arguments", {})

        # Avoid motion collision at the start of speech.
        # The speaker is still moving into speaking-ready / speaker pose.
        if name in ("play_emotion", "dance") and target_time < 0.0:
            target_time = 0.0

        while not stop.is_set():
            elapsed = time.monotonic() - t_start

            if elapsed >= target_time:
                break

            await asyncio.sleep(0.05)

        if stop.is_set():
            break

        if name == "play_emotion":
            robot.queue_emotion(args.get("emotion", "cheerful1"))

        elif name == "dance":
            robot.queue_dance(args.get("dance_name", "simple_nod"))

        elif name == "move_head":
            # Head movement is controlled by speaker/listener posture now.
            # This prevents generated tool calls from overriding the neck-up / neck-down rule.
            continue


async def _setup_listener(robot: RobotController, look_yaw: float) -> None:
    """Move a non-speaking robot to power-off-like idle pose."""
    if not ENABLE_HEAD_POSTURE:
        return
    robot.queue_power_off_pose(duration=1.2)

async def _setup_listeners(listeners: list[tuple[RobotController, float]]) -> None:
    """Set all non-speaking robots to power-off-like idle pose.

    Commands are staggered to avoid sending pose commands to multiple robots
    at the exact same moment.
    """
    for robot, look_yaw in listeners:
        robot.queue_power_off_pose(duration=0.8)
        await asyncio.sleep(0.08)


async def _play_turn(
    speaker: RobotController,
    listeners: list[tuple[RobotController, float]],
    audio_path: Path,
    duration: float,
    tool_calls: list[dict],
    speaker_phases: tuple[float, ...],
    audio_mode: AudioMode = "robot",
    pan: float = 0.0,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    display=None,
    video_window=None,
    role: str | None = None,
    text: str = "",
    turn_number: int | None = None,
    live: _LiveVoice | None = None,
) -> None:
    """Play one turn with audio, speech sway, actions, and listener movement.

    When ``display``/``video_window`` are provided, the subtitle and browser
    speech audio are fired from inside this function — AFTER the speaker's head
    is fully raised — so the voice starts in sync with the speech sway.
    """
    if not audio_path.exists():
        logger.warning("Audio file not found: %s", audio_path)
        await asyncio.sleep(duration)
        return

    sample_rate, pcm_data = wavfile.read(audio_path)
    pcm_float32 = pcm_data.astype(np.float32) / 32768.0

    # Reconnect any worker that died before this turn.
    speaker.ensure_alive()

    for listener, _ in listeners:
        listener.ensure_alive()

    speaker.clear_moves()
    await asyncio.sleep(0.08)

    for listener, _ in listeners:
        listener.clear_moves()
        await asyncio.sleep(0.08)

    if ENABLE_HEAD_POSTURE:
        # Speaker returns to normal speaking-ready pose first.
        ready_duration = 0.83
        ready_settle = 0.15
        ready_started = time.monotonic()
        speaker.queue_speaking_ready_pose(duration=ready_duration)
        await asyncio.sleep(0.12)

        # Non-speaking robots go to power-off-like pose.
        await _setup_listeners(listeners)

        # Start audio + sway only once the speaker's head is FULLY raised, so
        # the speech sway stays in sync with the voice from the very first word.
        # The listener setup above already consumed part of the ready move; wait
        # out whatever time is left (plus a short settle), and no more.
        remaining = ready_duration + ready_settle - (time.monotonic() - ready_started)
        if remaining > 0:
            await asyncio.sleep(remaining)

    # The head is fully raised now (or posture is disabled). Fire the subtitle
    # and the browser speech audio HERE — after the head is up — so the voice
    # begins in sync with the speech sway, not before the head finishes rising.
    if video_window is not None:
        video_window.show_subtitle(
            role=role, text=text, turn_number=turn_number, audio_path=audio_path
        )
    if display is not None:
        display.show_subtitle(
            role=role,
            text=text,
            turn_number=turn_number,
            audio_path=audio_path,
            speech_audio=audio_mode == "display",
        )

    stop = asyncio.Event()

    # Do not send an extra speaker head lift here.
    # Audio, speech sway, and emotion timing start from the ready pose.

    tasks = [
        _stream_audio(
            speaker,
            pcm_float32,
            sample_rate,
            audio_mode=audio_mode,
            pan=pan,
            tv_audio_delay=tv_audio_delay,
            robot_audio_delay=robot_audio_delay,
            audio_path=audio_path,
        ),
        _replay_tool_calls(speaker, tool_calls, stop),
        asyncio.sleep(duration + 0.01),
    ]
    if ENABLE_SPEECH_SWAY:
        tasks.append(
            _drive_speech_sway(
                speaker,
                pcm_float32,
                sample_rate,
                duration,
                speaker_phases,
                stop,
            )
        )

    # Publish the line so the live output switch can move it mid-way.
    if live is not None and role is not None:
        live.line = _VoiceLine(
            speaker=speaker,
            role=role,
            audio_path=audio_path,
            mode=audio_mode,
            started=time.monotonic(),
            duration=duration,
        )
    try:
        await asyncio.gather(*tasks)
    finally:
        if live is not None:
            live.line = None
    stop.set()

    if ENABLE_HEAD_POSTURE:
        # Lower the speaker immediately after speech ends.
        speaker.queue_power_off_pose(duration=1.2)
        await asyncio.sleep(0.08)

    # Reset speech sway after sending the lower command.
    speaker.set_speech_offsets(0, 0, 0, 0, 0, 0)

    await asyncio.sleep(0.05)

# ── Main playback ──────────────────────────────────────────────────────────


async def run_playback_with_connected_robots(
    config: Config,
    robot_a: RobotController,
    robot_b: RobotController,
    robot_c: RobotController,
    session_name: str,
    laptop_audio: bool = False,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    video_window=None,
    display=None,
) -> None:

    """Play a saved conversation using already-connected robots.

    This avoids reconnecting before every video discussion.

    Either `video_window` (legacy single window) or `display` (new
    DisplayManager multi-screen mode) may be supplied — they are mutually
    exclusive UI drivers. When `display` is used, the next speaker's role is
    looked ahead each turn so the gaze-guidance flow can point to it.
    """
    output_dir = BASE_OUTPUT_DIR / session_name
    meta_path = output_dir / "conversation.json"

    if audio_mode is None:
        audio_mode = "computer" if laptop_audio else "robot"

    if not meta_path.exists():
        print(f"No conversation found in {output_dir}/. Generate it first.")
        return

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    turns = meta["turns"]
    title = meta.get("title", session_name)

    print(f"\nSession: {title}")
    print(f"Loaded {len(turns)} turns. Using already-connected robots...")

    rng = np.random.default_rng(42)

    phase_a = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())
    phase_b = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())
    phase_c = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())

    robots = {
        "student": robot_a,
        "professor": robot_b,
        "moderator": robot_c,
    }

    phases = {
        "student": phase_a,
        "professor": phase_b,
        "moderator": phase_c,
    }

    labels = ROLE_LABELS

    start_audio_mode = _turn_audio_mode(audio_mode, display)
    await _preload_robot_audio(robots, turns, output_dir, start_audio_mode)

    print("\nStarting playback!\n")
    _diag(
        "playback_start "
        f"session={session_name} robots={_robot_diag(robot_a, robot_b, robot_c)} "
        f"head={ENABLE_HEAD_POSTURE} sway={ENABLE_SPEECH_SWAY} "
        f"recorded={ENABLE_RECORDED_ACTIONS}"
    )

    bgm_path = get_bgm_path_for_session(session_name)

    # When the browser display is the audio output (TV / computer), play the BGM
    # in the browser too — mono/centered — so ALL audio comes from one place and
    # the on-screen mute covers it. Otherwise fall back to the local pygame mixer.
    bgm = _DiscussionBgm(bgm_path, display)
    await bgm.start(in_browser=start_audio_mode == "display")

    # The web display's /top switch moves the voice (mid-line) and the BGM the
    # moment it flips; later lines just start on the new output.
    live = _LiveVoice()
    switch_task = (
        asyncio.create_task(_follow_output_switch(display, live, bgm))
        if _display_output_mode(display) is not None
        else None
    )

    async def _stop_following_switch() -> None:
        if switch_task is not None and not switch_task.done():
            switch_task.cancel()
            try:
                await switch_task
            except asyncio.CancelledError:
                pass

    window_stop = asyncio.Event()
    window_task = None

    ui = video_window if video_window is not None else display

    if ui is not None:
        window_task = asyncio.create_task(
            ui.pump_events_forever(window_stop)
        )

    # Roles of the upcoming turns (skipping unknown roles) so the gaze flow
    # can look ahead to the next speaker.
    playable_roles = [t["role"] for t in turns if t.get("role") in robots]
    role_cursor = 0

    if display is not None and playable_roles:
        display.point_to(playable_roles[0])

    try:
        for turn in turns:
            role = turn["role"]
            transcript = turn["transcript"]
            audio_file = output_dir / turn["audio_file"]
            duration = turn["duration_secs"]
            tool_calls = turn.get("tool_calls", [])

            if role not in robots:
                logger.warning("Unknown role '%s'. Skipping turn.", role)
                continue

            # Determine the next speaker for gaze guidance.
            role_cursor += 1
            next_role = (
                playable_roles[role_cursor]
                if role_cursor < len(playable_roles)
                else None
            )

            speaker = robots[role]
            speaker_phases = phases[role]

            if role == "student":
                listeners = [
                    (robot_b, 20.0),
                    (robot_c, 20.0),
                ]

            elif role == "professor":
                listeners = [
                    (robot_a, -20.0),
                    (robot_c, -20.0),
                ]

            else:
                listeners = [
                    (robot_a, 0.0),
                    (robot_b, 0.0),
                ]

            tools_str = f" + {len(tool_calls)} actions" if tool_calls else ""

            print(
                f"  [Turn {turn['turn']}] "
                f"{labels[role]} ({duration}s{tools_str}): "
                f"{transcript[:70]}..."
            )
            _diag(
                f"turn_start turn={turn['turn']} role={role} "
                f"duration={duration:.2f}s actions={len(tool_calls)} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            # Read per turn so the /top voice-output switch lands on the next line.
            turn_audio_mode = _turn_audio_mode(audio_mode, display)

            # The subtitle + speech audio are fired INSIDE _play_turn, after the
            # speaker's head is fully raised, so the voice and sway start
            # together (they used to fire here, before the head rose).
            await _play_turn(
                speaker=speaker,
                listeners=listeners,
                audio_path=audio_file,
                duration=duration,
                tool_calls=tool_calls,
                speaker_phases=speaker_phases,
                audio_mode=turn_audio_mode,
                pan=ROLE_PAN.get(role, 0.0),
                tv_audio_delay=tv_audio_delay,
                robot_audio_delay=robot_audio_delay,
                display=display,
                video_window=video_window,
                role=role,
                text=transcript,
                turn_number=turn.get("turn"),
                live=live,
            )
            _diag(
                f"turn_play_done turn={turn['turn']} role={role} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            if video_window is not None:
                video_window.finish_subtitle()

            if display is not None:
                display.finish_subtitle(role, next_role)

            await asyncio.sleep(config.inter_turn_pause)
            _diag(f"turn_end turn={turn['turn']} role={role}")

        print("\nPlayback complete!")
        _diag(f"playback_complete session={session_name}")

        # Natural end of the discussion: fade the BGM out gently. (The finally
        # below still hard-stops as a safety net for Ctrl+C / exceptions.)
        # Stop following the switch first so it cannot restart the BGM.
        await _stop_following_switch()
        await bgm.stop()

    finally:
        _diag(f"playback_cleanup session={session_name}")
        # Cancel without awaiting first: under STOP this task is itself being
        # cancelled, and the follower must not outlive the discussion.
        if switch_task is not None:
            switch_task.cancel()
        if ui is not None:
            window_stop.set()

            if window_task is not None:
                await window_task

        # Synchronous hard stop so Ctrl+C teardown cannot leave the BGM playing.
        bgm.hard_stop()

async def run_playback(
    config: Config,
    session_name: str | None = None,
    laptop_audio: bool = False,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
) -> None:
    """Connect to three robots and play the saved conversation."""
    if audio_mode is None:
        audio_mode = "computer" if laptop_audio else "robot"

    if session_name:
        output_dir = BASE_OUTPUT_DIR / session_name
    else:
        output_dir = BASE_OUTPUT_DIR

    meta_path = output_dir / "conversation.json"

    if not meta_path.exists():
        print(f"No conversation found in {output_dir}/. Run 'Generate' first.")
        return

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    turns = meta["turns"]
    title = meta.get("title", session_name or "Ethics Podcast")

    print(f"\nSession: {title}")
    print(f"Loaded {len(turns)} turns. Connecting to robots...")

    robot_a, robot_b, robot_c = await connect_robots(
        config.robot_a,
        config.robot_b,
        config.robot_c,
        disabled_roles=config.disabled_roles(),
    )

    rng = np.random.default_rng(42)

    phase_a = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())
    phase_b = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())
    phase_c = tuple(rng.uniform(0, 2 * math.pi, 6).tolist())

    robots = {
        "student": robot_a,
        "professor": robot_b,
        "moderator": robot_c,
    }

    phases = {
        "student": phase_a,
        "professor": phase_b,
        "moderator": phase_c,
    }

    labels = ROLE_LABELS

    bgm_path = get_bgm_path_for_session(session_name)

    bgm = BGMManager(
        bgm_path,
        normal_volume=0.40,
        speaking_volume=0.18,
    )

    try:
        robot_a.wiggle_antennas()
        robot_b.wiggle_antennas()
        robot_c.wiggle_antennas()

        await asyncio.sleep(2)

        await _preload_robot_audio(robots, turns, output_dir, audio_mode)

        print("\nStarting playback!\n")

        await bgm.intro()
        await bgm.fade_to(bgm.speaking_volume, duration=0.8)

        for turn in turns:
            role = turn["role"]
            transcript = turn["transcript"]
            audio_file = output_dir / turn["audio_file"]
            duration = turn["duration_secs"]
            tool_calls = turn.get("tool_calls", [])

            if role not in robots:
                logger.warning("Unknown role '%s'. Skipping turn.", role)
                continue

            speaker = robots[role]
            speaker_phases = phases[role]

            if role == "student":
                listeners = [
                    (robot_b, 20.0),
                    (robot_c, 20.0),
                ]

            elif role == "professor":
                listeners = [
                    (robot_a, -20.0),
                    (robot_c, -20.0),
                ]

            else:
                listeners = [
                    (robot_a, 0.0),
                    (robot_b, 0.0),
                ]

            tools_str = f" + {len(tool_calls)} actions" if tool_calls else ""

            print(
                f"  [Turn {turn['turn']}] "
                f"{labels[role]} ({duration}s{tools_str}): "
                f"{transcript[:70]}..."
            )        

            await _play_turn(
                speaker=speaker,
                listeners=listeners,
                audio_path=audio_file,
                duration=duration,
                tool_calls=tool_calls,
                speaker_phases=speaker_phases,
                audio_mode=audio_mode,
                pan=ROLE_PAN.get(role, 0.0),
                tv_audio_delay=tv_audio_delay,
                robot_audio_delay=robot_audio_delay,
            )

            await asyncio.sleep(config.inter_turn_pause)
            

        await bgm.stop()

        robot_a.clear_moves()
        robot_b.clear_moves()
        robot_c.clear_moves()

        robot_a.queue_goto(head=FINAL_HEAD, duration=1.5)
        robot_b.queue_goto(head=FINAL_HEAD, duration=1.5)
        robot_c.queue_goto(head=FINAL_HEAD, duration=1.5)

        await asyncio.sleep(2)

        print("\nPlayback complete!")

    finally:
        # Synchronous hard stop (no await): guarantees the music stops even
        # when this finally runs during a Ctrl+C teardown, where awaiting a
        # 1.5s fade would not complete and would leave the BGM playing.
        bgm.hard_stop()

        robot_a.stop()
        robot_b.stop()
        robot_c.stop()
