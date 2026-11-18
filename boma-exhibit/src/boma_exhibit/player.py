"""Play a pre-rendered variant of the BOMA sketch through three robots.

    ZURI   left    Swahili
    KORA   center  English
    PULAAR right   Fulfulde

Motion architecture (inherited from the Ethics Podcast exhibit, and worth
keeping intact -- it was tuned against a congested shared Wi-Fi link):
  - each robot worker runs its own control loop calling set_target()
  - primary moves go through that worker's move queue
  - speech sway is layered on top as a secondary offset while a robot speaks
  - robots that are not speaking drop to a lowered "asleep" pose

What is new here is that a turn can have MORE THAN ONE speaker. Two turns in
the sketch are spoken in chorus -- "KORAAAA!" and the closing "Welcome to
BOMA!" -- so `_play_turn` takes a list of speakers, animates all of them, and
plays a single pre-mixed mono clip.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Literal

import numpy as np
from scipy.io import wavfile

from boma_exhibit.bgm import get_bgm_path
from boma_exhibit.config import Config, ROLE_ORDER
from boma_exhibit.robot_audio import RobotAudioMixer
from boma_exhibit.robot_controller import RobotController, connect_robots
from boma_exhibit.script_data import KORA, PULAAR, ZURI
from boma_exhibit.theme import ROLE_LANGUAGE, ROLE_NAMES

logger = logging.getLogger(__name__)

AudioMode = Literal["robot", "computer", "surround", "hybrid_surround", "display"]

# Stereo placement, matching where each robot physically stands. The browser
# applies this with a Web Audio StereoPanner; the sounddevice path applies it
# with _apply_stereo_pan. Chorus turns are pre-mixed and play centred.
ROLE_PAN = {
    ZURI: -0.85,   # left
    KORA: 0.0,     # center
    PULAAR: 0.85,  # right
}

ROLE_LABELS = {
    role: f"{ROLE_NAMES[role]} / {ROLE_LANGUAGE[role]}" for role in ROLE_ORDER
}

# Poses used while a run is in progress. Head values are x, y, z in metres then
# roll, pitch, yaw in degrees; antennas are radians, and a larger magnitude
# means further drooped.
#
# Listening used to be the full power-off pose -- head 4cm down, pitched 23
# degrees, antennas folded flat. That reads as asleep rather than attentive,
# and it is a long way to travel: a robot with a two-word line cannot spend
# most of it hauling its head back up, so the move never finished and the
# motion looked broken. Listening now sits close to neutral. The contrast in
# the antennas, plus the fact that only the speaker sways, is enough to say who
# is talking -- and the screen says it too.
SPEAKING_HEAD_DEG = (0, 0, 0, 0, 0, 0)      # level, attentive
SPEAKING_ANT = (-0.1745, 0.1745)            # perked (the worker's neutral)
LISTENING_HEAD_DEG = (0, 0, 0, 0, 10, 0)    # a small nod down, no drop
LISTENING_ANT = (-1.40, 1.40)               # ~80 degrees of droop

# The deep power-off pose is now used only between shows, never mid-run.

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

# How a run eases out when the operator presses Stop.
FADE_OFFSET_SECS = 0.6   # walk the speech-sway offsets down to zero
FADE_LOWER_SECS = 1.6    # then descend to the resting pose, slower than usual
FADE_HZ = 25

# A turn shorter than this is treated as a quick riposte and gets tighter head
# choreography. Twelve of the sketch's 34 turns are under two seconds.
SHORT_TURN_SECS = 2.5

# Diagnostic switches: each disables one layer without touching the others.
ENABLE_HEAD_POSTURE = True
ENABLE_SPEECH_SWAY = True
ENABLE_RECORDED_ACTIONS = True


def start_space_key_listener(loop, callback, stop_event) -> None:
    """Call ``callback`` on the asyncio loop whenever SPACE is pressed.

    A daemon thread polls the console without blocking the loop. The browser
    showing /top listens for SPACE too, so muting works from either the
    operator's terminal or the TV's keyboard.
    """
    import threading

    def reader_windows() -> None:
        import msvcrt

        while not stop_event.is_set():
            try:
                if msvcrt.kbhit() and msvcrt.getch() == b" ":
                    loop.call_soon_threadsafe(callback)
                else:
                    time.sleep(0.05)
            except Exception:
                time.sleep(0.1)

    def reader_posix() -> None:
        import select
        import sys
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            # cbreak, not raw: Ctrl-C still reaches the process as usual.
            tty.setcbreak(fd)
            while not stop_event.is_set():
                if select.select([sys.stdin], [], [], 0.2)[0]:
                    if sys.stdin.read(1) == " ":
                        loop.call_soon_threadsafe(callback)
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except Exception:
                pass

    try:
        import msvcrt  # noqa: F401

        reader = reader_windows
    except ImportError:
        import sys

        # No TTY (nohup, systemd, a pipe) means no key presses to read.
        if not sys.stdin.isatty():
            print("  [mute] stdin is not a terminal; mute with SPACE in the browser.")
            return
        reader = reader_posix

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
) -> None:
    """Play audio through robot speaker."""
    if robot is None:
        return

    if not robot.audio_ready:
        print("  Robot speaker skipped because audio is not ready.")
        return

    if robot_audio_delay > 0:
        await asyncio.sleep(robot_audio_delay)

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
    mixer: RobotAudioMixer | None = None,
) -> None:
    """Send audio to robot speaker, computer speaker, surround, or both."""

    if audio_mode == "display":
        # The active browser display plays this turn's WAV through the same
        # output device that plays the video. Motion timing is still handled by
        # the duration sleep in _play_turn().
        return

    if audio_mode == "robot":
        if mixer is not None and mixer.active:
            # The mixer owns this robot's speaker -- it is already streaming the
            # music bed, so the line must be summed into that stream rather than
            # pushed alongside it, or the two would queue instead of mixing.
            mixer.speak(robot.role, pcm, sample_rate)
        else:
            await _play_robot_audio(robot, pcm, sample_rate, robot_audio_delay=0.0)
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
        if mixer is not None and mixer.active:
            mixer.speak(robot.role, pcm, sample_rate)
            await _play_tv_audio(pcm, sample_rate, audio_mode=audio_mode,
                                 pan=pan, tv_audio_delay=tv_audio_delay)
            return
        await asyncio.gather(
            _play_robot_audio(
                robot,
                pcm,
                sample_rate,
                robot_audio_delay=robot_audio_delay,
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
    cancelled = False

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

    except asyncio.CancelledError:
        # The turn was cut short -- the operator pressed Stop. Leave the offsets
        # exactly where they are so fade_to_rest() can ease them down from here.
        # Zeroing them now would be precisely the snap the fade exists to avoid.
        cancelled = True
        raise
    finally:
        if not cancelled:
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


async def _setup_listeners(
    listeners: list[RobotController], duration: float = 0.5
) -> None:
    """Put every non-speaking robot into the listening pose.

    Staggered, because sending pose commands to three robots on the same tick
    is exactly the traffic pattern that spikes latency on the shared Wi-Fi.
    """
    if not ENABLE_HEAD_POSTURE:
        return
    for listener in listeners:
        listener.queue_goto(
            LISTENING_HEAD_DEG, LISTENING_ANT, duration=duration, clear_first=True
        )
        await asyncio.sleep(0.08)


async def _fade_one_to_rest(robot, offset_fade: float, lower: float) -> None:
    start = tuple(getattr(robot, "last_speech_offsets", (0.0,) * 6))

    if offset_fade > 0 and any(start):
        steps = max(1, int(offset_fade * FADE_HZ))
        for step in range(1, steps + 1):
            # Cosine ease: flat at both ends, so the head neither jerks as the
            # fade begins nor stops abruptly as it reaches zero.
            scale = 0.5 * (1.0 + math.cos(math.pi * step / steps))
            try:
                robot.set_speech_offsets(*(value * scale for value in start))
            except Exception:
                break
            await asyncio.sleep(1.0 / FADE_HZ)

    try:
        robot.set_speech_offsets(0, 0, 0, 0, 0, 0)
        # force=True so the 8s duplicate-pose guard cannot swallow a Stop.
        robot.queue_power_off_pose(duration=lower, force=True)
    except Exception:
        pass


async def fade_to_rest(
    robots,
    offset_fade: float = FADE_OFFSET_SECS,
    lower: float = FADE_LOWER_SECS,
) -> None:
    """Ease every robot out of a turn, rather than cutting it dead.

    Stopping mid-sentence used to make the heads flinch. `queue_power_off_pose`
    sends `clear_first`, and the worker's `_clear_motion_state` zeroes the
    speech-sway offsets within a single tick -- a jump of up to about thirteen
    degrees before the descent even starts.

    So the offsets are walked down to zero first, and only then is the descent
    queued. The descent itself was always smooth: the worker interpolates from
    whatever pose the head is actually in.

    Deliberately does NOT call clear_moves() -- that zeroes the offsets in one
    tick too, which is the very snap being avoided here.
    """
    await asyncio.gather(
        *(_fade_one_to_rest(robot, offset_fade, lower) for robot in robots)
    )


async def _play_turn(
    speakers: list[RobotController],
    listeners: list[RobotController],
    audio_path: Path,
    duration: float,
    tool_calls: list[dict],
    phases: dict[str, tuple[float, ...]],
    audio_mode: AudioMode = "robot",
    pan: float = 0.0,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    display=None,
    roles: list[str] | None = None,
    text: str = "",
    translation: str = "",
    language: str = "",
    turn_number: int | None = None,
    mixer: RobotAudioMixer | None = None,
) -> None:
    """Play one turn: audio, speech sway, emotions, and listener posture.

    ``speakers`` usually holds one robot. For the two chorus turns it holds two
    or three, and they all rise, sway and emote together against a single
    pre-mixed clip.

    The subtitle and the browser's speech audio are fired from inside this
    function -- AFTER the speakers' heads are fully raised -- so the voice
    starts in sync with the sway rather than while the head is still rising.
    """
    if not audio_path.exists():
        logger.warning("Audio file not found: %s", audio_path)
        await asyncio.sleep(duration)
        return

    sample_rate, pcm_data = wavfile.read(audio_path)
    pcm_float32 = pcm_data.astype(np.float32) / 32768.0

    # Reconnect any worker that died since the previous turn.
    for robot in (*speakers, *listeners):
        robot.ensure_alive()

    for speaker in speakers:
        speaker.clear_moves()
        await asyncio.sleep(0.08)

    for listener in listeners:
        listener.clear_moves()
        await asyncio.sleep(0.08)

    if ENABLE_HEAD_POSTURE:
        # The reference exhibit was a slow philosophical discussion, where a
        # leisurely 0.83s rise read as thoughtful. This is a comedy sketch built
        # on two-word ripostes ("A little." / "Yes."), and a full slow rise in
        # front of each one adds more silence than the joke does speech. Short
        # lines therefore get a brisk rise and a brisk drop; long lines keep the
        # original, more deliberate movement.
        snappy = duration < SHORT_TURN_SECS
        # Shorter than they used to be: the trip between listening and speaking
        # is now a small one, so there is no reason to spend most of a quick
        # riposte getting into position.
        ready_duration = 0.30 if snappy else 0.50
        ready_settle = 0.06 if snappy else 0.10
        ready_started = time.monotonic()

        for speaker in speakers:
            speaker.queue_goto(
                SPEAKING_HEAD_DEG,
                SPEAKING_ANT,
                duration=ready_duration,
                clear_first=True,
            )
            await asyncio.sleep(0.08)

        await _setup_listeners(listeners, duration=ready_duration)

        # Wait out only what is LEFT of the ready move -- the listener setup
        # above already consumed part of it.
        remaining = ready_duration + ready_settle - (time.monotonic() - ready_started)
        if remaining > 0:
            await asyncio.sleep(remaining)

    if display is not None:
        # With the robots doing the talking, the page must show the caption but
        # NOT sound the line -- handing it an audio path would have the browser
        # play the same words a beat behind the robot.
        display.show_subtitle(
            roles=roles or [],
            text=text,
            translation=translation,
            language=language,
            turn_number=turn_number,
            audio_path=None if audio_mode == "robot" else audio_path,
        )

    stop = asyncio.Event()

    # The audio is pushed to one robot only. In "robot"/"hybrid" mode that is
    # the lead speaker's own speaker cone; in the TV modes nothing is pushed to
    # the robots at all and this argument is ignored.
    tasks = [
        _stream_audio(
            speakers[0],
            pcm_float32,
            sample_rate,
            audio_mode=audio_mode,
            pan=pan,
            tv_audio_delay=tv_audio_delay,
            robot_audio_delay=robot_audio_delay,
            mixer=mixer,
        ),
        asyncio.sleep(duration + 0.01),
    ]

    for speaker in speakers:
        tasks.append(_replay_tool_calls(speaker, tool_calls, stop))
        if ENABLE_SPEECH_SWAY:
            tasks.append(
                _drive_speech_sway(
                    speaker,
                    pcm_float32,
                    sample_rate,
                    duration,
                    phases[speaker.role],
                    stop,
                )
            )

    await asyncio.gather(*tasks)
    stop.set()

    if ENABLE_HEAD_POSTURE:
        # Whoever just spoke becomes a listener. Settling to the deep rest pose
        # here is what made the next robot's entrance so expensive.
        settle = 0.45 if duration < SHORT_TURN_SECS else 0.7
        for speaker in speakers:
            speaker.queue_goto(
                LISTENING_HEAD_DEG, LISTENING_ANT, duration=settle, clear_first=True
            )
            await asyncio.sleep(0.08)

    for speaker in speakers:
        speaker.set_speech_offsets(0, 0, 0, 0, 0, 0)

    await asyncio.sleep(0.05)


# ── Main playback ──────────────────────────────────────────────────────────


def _turn_roles(turn: dict) -> list[str]:
    """Speakers of a turn. Accepts the single-speaker shorthand too."""
    roles = turn.get("speakers")
    if roles:
        return list(roles)
    role = turn.get("role")
    return [role] if role else []


def _seeded_phases() -> dict[str, tuple[float, ...]]:
    """Per-robot sway phase offsets, fixed so runs look identical.

    Six independent sinusoids per robot, offset differently per seat, so the
    three heads never sway in lockstep.
    """
    rng = np.random.default_rng(42)
    return {role: tuple(rng.uniform(0, 2 * math.pi, 6).tolist()) for role in ROLE_ORDER}


async def run_playback_with_connected_robots(
    config: Config,
    robots: dict[str, RobotController],
    conversation_path: Path,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    display=None,
) -> None:
    """Play one pre-rendered variant using already-connected robots.

    ``conversation_path`` points at a specific variant's conversation.json
    rather than naming a session, because the exhibit loop picks a different
    variant every time round.
    """
    if audio_mode is None:
        audio_mode = "robot"

    if not conversation_path.exists():
        print(f"No conversation at {conversation_path}. Generate it first (menu 1).")
        return

    variant_dir = conversation_path.parent
    with open(conversation_path, encoding="utf-8") as handle:
        meta = json.load(handle)

    turns = meta["turns"]
    title = meta.get("title", "BOMA")
    variant = meta.get("variant", variant_dir.name)

    print(f"\n{title} — variant {variant}")
    print(f"Loaded {len(turns)} turns. Using already-connected robots...")

    phases = _seeded_phases()

    print("\nStarting playback!\n")
    _diag(
        f"playback_start variant={variant} "
        f"robots={_robot_diag(*robots.values())} "
        f"head={ENABLE_HEAD_POSTURE} sway={ENABLE_SPEECH_SWAY} "
        f"recorded={ENABLE_RECORDED_ACTIONS}"
    )

    # When the browser is the audio output, let it play the background music
    # too, so every sound in the room comes from one device and one mute.
    bgm_path = get_bgm_path()
    use_browser_bgm = (
        bgm_path is not None
        and display is not None
        and getattr(display, "plays_bgm_audio", False)
        and audio_mode in ("computer", "surround")
    )
    if use_browser_bgm:
        # Between turns. The page ducks this to ~30% whenever a robot is
        # actually speaking, so the resting level can afford to be higher than
        # it could with a flat bed.
        display.set_bgm(bgm_path, volume=0.26)

    # When the robots are the sound system, one mixer owns all three speakers:
    # the music bed goes to every robot, each line is summed into the stream of
    # whoever is saying it, and the bed ducks on that robot while it talks.
    mixer: RobotAudioMixer | None = None
    if audio_mode in ("robot", "hybrid_surround"):
        mixer = RobotAudioMixer(
            robots,
            bgm_path,
            volume=(lambda: display.volume) if display is not None else None,
        )
        if mixer.active:
            mixer.start()
            print(f"  [audio] streaming to {len(robots)} robot speaker(s)"
                  + (f", music: {bgm_path.name}" if bgm_path else ", no music"))
        else:
            print("  [audio] no robot has audio available; speakers stay silent")

    window_stop = asyncio.Event()
    window_task = None
    if display is not None:
        window_task = asyncio.create_task(display.pump_events_forever(window_stop))

    # Look ahead so the display can point at whoever speaks next.
    upcoming = [_turn_roles(t) for t in turns]
    if display is not None and upcoming:
        display.point_to(upcoming[0])

    try:
        for index, turn in enumerate(turns):
            roles = _turn_roles(turn)
            known = [role for role in roles if role in robots]
            if not known:
                logger.warning("Turn %s has no known speaker %s", turn.get("turn"), roles)
                continue

            text = turn.get("text", turn.get("transcript", ""))
            translation = turn.get("translation") or ""
            language = turn.get("language") or ""
            audio_file = (variant_dir / turn["audio_file"]).resolve()
            duration = turn["duration_secs"]
            tool_calls = turn.get("tool_calls", [])

            next_roles = upcoming[index + 1] if index + 1 < len(upcoming) else None

            speakers = [robots[role] for role in known]
            listeners = [
                robot for role, robot in robots.items() if role not in known
            ]

            label = " + ".join(ROLE_LABELS.get(r, r) for r in known)
            tools_str = f" + {len(tool_calls)} actions" if tool_calls else ""
            print(
                f"  [Turn {turn['turn']:>2}] {label} "
                f"({duration}s{tools_str}): {text[:60]}"
            )
            _diag(
                f"turn_start turn={turn['turn']} roles={','.join(known)} "
                f"duration={duration:.2f}s actions={len(tool_calls)} "
                f"robots={_robot_diag(*robots.values())}"
            )

            # With the browser display driving a TV, hand the audio to it: the
            # server has no sound card, and the browser can pan per speaker.
            turn_audio_mode: AudioMode = audio_mode
            if (
                display is not None
                and getattr(display, "plays_speech_audio", False)
                and audio_mode in ("computer", "surround")
            ):
                turn_audio_mode = "display"

            # Chorus clips are pre-mixed mono and belong in the centre.
            pan = ROLE_PAN.get(known[0], 0.0) if len(known) == 1 else 0.0

            await _play_turn(
                speakers=speakers,
                listeners=listeners,
                audio_path=audio_file,
                duration=duration,
                tool_calls=tool_calls,
                phases=phases,
                audio_mode=turn_audio_mode,
                pan=pan,
                tv_audio_delay=tv_audio_delay,
                robot_audio_delay=robot_audio_delay,
                display=display,
                roles=known,
                text=text,
                translation=translation,
                language=language,
                turn_number=turn.get("turn"),
                mixer=mixer,
            )
            _diag(f"turn_play_done turn={turn['turn']} roles={','.join(known)}")

            if display is not None:
                display.finish_subtitle(known, next_roles)

            await asyncio.sleep(config.inter_turn_pause)
            _diag(f"turn_end turn={turn['turn']}")

        print("\nPlayback complete!")
        _diag(f"playback_complete variant={variant}")

        if mixer is not None:
            await mixer.stop()

        if use_browser_bgm and display is not None:
            try:
                display.stop_bgm()
            except Exception:
                pass

    finally:
        _diag(f"playback_cleanup variant={variant}")
        if mixer is not None:
            # Runs on the Stop path too, where the surrounding task is already
            # cancelled -- so fade without awaiting, then let the buffered tail
            # drain on its own.
            mixer.begin_fade()
        if display is not None:
            window_stop.set()
            if window_task is not None:
                # Cancel rather than await. This finally also runs on the Stop
                # path, where the surrounding task is already cancelled, and an
                # await there can re-raise before the cleanup below gets to run.
                # The task is only sitting on an Event, so it has nothing to
                # unwind.
                window_task.cancel()
        if use_browser_bgm and display is not None:
            try:
                display.stop_bgm()
            except Exception:
                pass


async def run_playback(
    config: Config,
    conversation_path: Path,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    display=None,
) -> None:
    """Connect the three robots, play one variant, then disconnect."""
    if audio_mode is None:
        audio_mode = "robot"

    # Only "robot" and "hybrid" modes need the robot's own audio pipeline.
    # Skipping it avoids the GStreamer/WebRTC media stack entirely.
    use_media = audio_mode in ("robot", "hybrid_surround")

    controllers = await connect_robots(
        *(config.robot(role) for role in ROLE_ORDER),
        use_media=use_media,
        disabled_roles=config.disabled_roles(),
    )
    robots = {controller.role: controller for controller in controllers}

    try:
        await run_playback_with_connected_robots(
            config,
            robots,
            conversation_path,
            audio_mode=audio_mode,
            tv_audio_delay=tv_audio_delay,
            robot_audio_delay=robot_audio_delay,
            display=display,
        )
    finally:
        for controller in controllers:
            try:
                controller.stop()
            except Exception:
                pass
