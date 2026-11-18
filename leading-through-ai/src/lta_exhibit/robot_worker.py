"""Standalone robot worker — runs as a completely independent process.

Architecture:
- Low-rate control loop thread runs set_target()
- Primary moves are sequential via move queue
- Speech sway is a secondary offset layered on top
- Main thread reads commands from stdin pipe
- Audio can be pushed directly to the robot's media system

Commands accepted via pipe:
- push_audio: send PCM audio to speaker
- queue_emotion: queue an emotion move
- queue_dance: queue a dance move
- queue_goto: queue a goto-pose move
- queue_power_off_pose: move to power-off-like idle pose
- queue_speaking_ready_pose: return head/face/antennas to ready pose before speaking
- set_speech_offsets: update speech sway
- clear_moves: stop current move and clear queue
- wiggle: antenna wiggle
- shutdown: stop everything
"""

import logging
import math
import os
import pickle
import struct
import sys
import time
import threading
from collections import deque
from typing import Any

import numpy as np
from scipy.signal import resample


OPENAI_SAMPLE_RATE = 24000

# Control-loop rate. Sending set_target this often is what loads the shared
# Wi-Fi: with the program running, ICMP ping to the robots jumps from ~15 ms to
# 500-1400 ms with timeouts, while idle ping stays clean — i.e. our own traffic
# is congesting the link/adapter. Both values are env-tunable so the rate can
# be swept against a live ping without editing code:
#   ROBOT_CONTROL_HZ  — rate during emotions / goto poses (default 25, was 50)
#   ROBOT_SPEECH_HZ   — rate during speech-only sway, a slow ~0.75 Hz sinusoid
#                       that is fine at a low rate (default 10)
CONTROL_HZ = int(os.environ.get("ROBOT_CONTROL_HZ", "50"))
CONTROL_PERIOD = 1.0 / CONTROL_HZ

SPEECH_ONLY_HZ = int(os.environ.get("ROBOT_SPEECH_HZ", "50"))
SPEECH_ONLY_PERIOD = 1.0 / SPEECH_ONLY_HZ
# Stagger the three control loops by a third of a period each so the robots do
# not all hit the shared Wi-Fi on the same tick.
CONTROL_PHASE_OFFSETS = {
    "nova": 0.000,
    "sage": CONTROL_PERIOD / 3.0,
    "aegis": CONTROL_PERIOD * 2.0 / 3.0,
    "robot_a": 0.000,
    "robot_b": CONTROL_PERIOD / 3.0,
    "robot_c": CONTROL_PERIOD * 2.0 / 3.0,
}

# Idle breathing — head bob + antenna sway while the robot is doing nothing.
# Disabled by default; set ROBOT_IDLE_BREATHING=1 to re-enable.
ENABLE_IDLE_BREATHING = os.environ.get("ROBOT_IDLE_BREATHING", "0") == "1"

BREATHING_Z_AMP = 0.005       # 5mm head bob
BREATHING_FREQ = 0.1
BREATHING_ANT_AMP_RAD = 0.26  # ~15 degrees antenna sway
BREATHING_ANT_FREQ = 0.5

# Seconds of idle before breathing starts.
IDLE_DELAY = 0.3

# Neutral antenna position.
# This must exist because last_primary_ant uses it.
NEUTRAL_ANT = (-0.1745, 0.1745)

# Power-off-like idle pose.
# This does NOT turn off motors.
# It only makes the robot look like it is powered down.
#
# create_head_pose takes 6 values:
# x, y, z, roll, pitch, yaw
#
# If the head goes the wrong direction, change 35 to -35.
OFF_HEAD_DEG =  (0, 0, -0.04, 0, 23, 0)

# Antennas down.
# If the antennas move the wrong direction, swap the signs.
OFF_ANT = (-math.pi, math.pi)
# Speaking ready pose.
# This returns the head/face direction and antennas to the normal state.
READY_HEAD_DEG = (0, 0, 0, 0, 0, 0)
READY_ANT = NEUTRAL_ANT

# When an emotion (a recorded ABSOLUTE pose sequence) starts, blend from the
# current pose into its first frame over this many seconds so the head does not
# snap. The blend is a _GotoMove, so it is min-jerk eased.
EMOTION_BLEND_DURATION = 0.3

# Treat the control connection as dead only after set_target has failed
# CONTINUOUSLY for this many seconds. The Wi-Fi link to the robots stalls for
# up to ~1.5 s (confirmed by ICMP ping spikes of 500-1400 ms and occasional
# timeouts — and ICMP bypasses the robot daemon entirely, so this is a
# network/RF problem, not daemon load). The underlying websocket usually
# survives such stalls and set_target starts succeeding again on its own.
# The old count-based threshold (24 failures ≈ 0.5 s at 50 Hz) tripped on these
# transient stalls and killed the worker needlessly, turning a ~1 s hiccup into
# an 8-20 s restart freeze. A multi-second grace window lets most stalls
# self-heal with no restart; a genuinely dead daemon still triggers one.
FAILURE_GRACE_SECONDS = 4.0

# A single set_target success does NOT prove the link is healthy. A half-broken
# Wi-Fi link returns sparse successes between long bursts of failures; the old
# code reset the failure window on every success, so the grace timer never
# accumulated and the worker never reconnected — it just logged errors forever.
# Require a short streak of CONSECUTIVE successes before declaring the link
# recovered. At 50 Hz this is ~0.3 s of clean frames: trivial for a genuinely
# healed link, unreachable for a link that can't go 0.3 s without dropping.
RECOVERY_SUCCESS_STREAK = 15

# ── Startup "full reconnect" ──────────────────────────────────────────────
# Before connecting, rebuild the robot's daemon backend from scratch so every
# run begins from a known-good state. This recovers from the two failure modes
# we keep hitting at the exhibit:
#   - the robot sitting in SLEEP mode (motors disabled, head down), and
#   - a PREVIOUS run that crashed and left the backend wedged, a move stuck
#     running, or a media/WebRTC session held open.
# A daemon backend restart tears all of that down and rebuilds the motor bus.
# Set ROBOT_FULL_RESET_ON_START=0 to skip it (e.g. for fast local iteration).
FULL_RESET_ON_START = os.environ.get("ROBOT_FULL_RESET_ON_START", "1") != "0"
# The daemon is on the robot LAN. `requests` on Windows reads the WinINET proxy
# out of the registry, so a campus proxy can capture these calls and none of
# them then reaches 192.168.3.0/24. One session with `trust_env = False` fixes
# every call site, and it changes nothing on Linux.
#
# Built on first use, not at import: `requests` is deliberately imported inside
# the three functions that need it, so there is nothing to build a session from
# up here. Doing it at module level cost a worker that died with
# `NameError: name 'requests' is not defined` before it could say anything else.
_http_session = None


def _http():
    global _http_session
    if _http_session is None:
        import requests

        _http_session = requests.Session()
        _http_session.trust_env = False
    return _http_session


DAEMON_PORT = 8000
DAEMON_REACHABLE_TIMEOUT = 30.0   # wait this long for the HTTP daemon to answer
BACKEND_READY_TIMEOUT = 60.0      # wait this long for the backend to rebuild
WAKE_SETTLE_SECONDS = 3.0         # let the wake-up emote finish before connecting


def _acquire_media(host: str, log, timeout: float = 20.0) -> bool:
    """Make the daemon take its media hardware back before we ask for audio.

    Connecting with media_backend="no_media" makes the SDK call release_media()
    on the daemon, and the daemon stays released after that client goes away --
    with its WebRTC signalling server (port 8443) shut down along with it. The
    next client that does want audio then fails with a bare "Connection
    refused", which points at nothing useful. So: ask for it back, and wait for
    the signalling port to actually come up before handing over to the SDK.
    """
    import socket

    import requests

    base = f"http://{host}:8000/api/media"
    try:
        status = _http().get(f"{base}/status", timeout=5).json()
    except Exception as exc:
        log.warning("Could not read media status: %s", exc)
        status = {}

    if status.get("no_media"):
        log.warning("Daemon reports no media hardware; audio will be unavailable.")
        return False

    if status.get("released", True):
        try:
            _http().post(f"{base}/acquire", timeout=15).raise_for_status()
            log.info("Media re-acquired from the daemon.")
        except Exception as exc:
            log.error("Could not acquire media: %s", exc)
            return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex((host, 8443)) == 0:
                log.info("WebRTC signalling is up.")
                return True
        time.sleep(0.5)

    log.warning("WebRTC signalling did not come up within %.0fs.", timeout)
    return False


def _full_reset_and_wake(host: str, log: "logging.Logger") -> None:
    """Force a clean robot backend before the worker connects.

    All steps go through the daemon HTTP API (the same API the SDK uses
    internally) and are best-effort: any failure is logged and we fall
    through to connecting anyway, so a flaky daemon never blocks startup.

    Sequence:
      1. wait for the daemon HTTP server to answer,
      2. restart the backend (or start it if it was stopped) — this drops
         any residual move / media / session state from a crashed run,
      3. wait until the backend is serving again (motors/status -> 200;
         it returns 503 "Backend not running" during the rebuild),
      4. enable motors,
      5. play the wake-up emote so a sleeping robot rises to its init pose.
    """
    import requests

    base = f"http://{host}:{DAEMON_PORT}/api"

    # 1. Daemon HTTP reachable?
    deadline = time.monotonic() + DAEMON_REACHABLE_TIMEOUT
    state = None
    while True:
        try:
            r = _http().get(f"{base}/daemon/status", timeout=5)
            r.raise_for_status()
            state = (r.json() or {}).get("state")
            break
        except Exception as e:  # noqa: BLE001 - best effort
            if time.monotonic() >= deadline:
                log.warning(
                    "Daemon not reachable after %.0fs (%s); connecting without reset.",
                    DAEMON_REACHABLE_TIMEOUT,
                    e,
                )
                return
            time.sleep(1.0)

    # 2. Rebuild the backend. restart only works from running/error; if the
    #    backend was stopped/not_initialized, start it instead.
    try:
        if state in ("stopped", "not_initialized"):
            log.info("Daemon backend is %s; starting it.", state)
            _http().post(
                f"{base}/daemon/start", params={"wake_up": "true"}, timeout=10
            ).raise_for_status()
        else:
            log.info("Restarting daemon backend (state=%s).", state)
            resp = _http().post(f"{base}/daemon/restart", timeout=10)
            if resp.status_code == 409:
                log.warning("Daemon busy; skipping restart.")
            else:
                resp.raise_for_status()
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("Backend restart request failed: %s; continuing.", e)

    # 3. Wait for the backend to come back. Give teardown a moment to begin so
    #    we don't mistake the old (about-to-die) backend for the new one.
    time.sleep(2.0)
    ready_deadline = time.monotonic() + BACKEND_READY_TIMEOUT
    while True:
        try:
            r = _http().get(f"{base}/motors/status", timeout=5)
            if r.status_code == 200:
                log.info(
                    "Backend ready after reset (motors=%s).",
                    (r.json() or {}).get("mode"),
                )
                break
        except Exception:  # noqa: BLE001 - 503/connection error while rebuilding
            pass
        if time.monotonic() >= ready_deadline:
            log.warning(
                "Backend not ready %.0fs after reset; connecting anyway.",
                BACKEND_READY_TIMEOUT,
            )
            break
        time.sleep(1.0)

    # 4. Enable motors.
    try:
        _http().post(
            f"{base}/motors/set_mode/enabled", timeout=5
        ).raise_for_status()
        log.info("Motors enabled.")
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("Could not enable motors: %s", e)

    # 5. Wake from sleep (init pose + wake emote), then let it settle so the
    #    move finishes before the control loop starts sending targets.
    try:
        _http().post(f"{base}/move/play/wake_up", timeout=5).raise_for_status()
        log.info("Wake-up emote triggered.")
        time.sleep(WAKE_SETTLE_SECONDS)
    except Exception as e:  # noqa: BLE001 - best effort
        log.warning("Wake-up failed: %s", e)


def main() -> None:
    # Ctrl+C in the terminal, or systemd's SIGINT to the whole service cgroup,
    # reaches this process too. Ignore it: the parent shuts each worker down
    # in order (fade, then a zero-length "shutdown" frame), and a worker that
    # died first would only be revived by the parent's own fade commands.
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)

    host = sys.argv[1]
    role = sys.argv[2]
    robot_name = sys.argv[3] if len(sys.argv) > 3 else None
    use_media = sys.argv[4].lower() == "media" if len(sys.argv) > 4 else False

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{role}] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger(f"robot.{role}")

    from reachy_mini import ReachyMini
    from reachy_mini.utils import create_head_pose
    from reachy_mini.utils.interpolation import (
        compose_world_offset,
        linear_pose_interpolation,
        time_trajectory,
        InterpolationTechnique,
    )
    from reachy_mini.motion.recorded_move import RecordedMoves

    kwargs: dict[str, Any] = {
        "host": host,
        "connection_mode": "network",
        "media_backend": "default" if use_media else "no_media",
    }

    if robot_name:
        kwargs["robot_name"] = robot_name

    # Full reconnect: rebuild the daemon backend and wake the robot before we
    # open the SDK session, so we never inherit sleep mode or wedged state from
    # a previous crashed run.
    if FULL_RESET_ON_START:
        log.info("Performing full robot reset before connecting...")
        _full_reset_and_wake(host, log)

    if use_media:
        _acquire_media(host, log)

    log.info("Connecting to %s at %s...", robot_name or "auto", host)

    robot = ReachyMini(**kwargs)

    audio_ready = False
    output_rate = 16000

    if use_media:
        try:
            if hasattr(robot, "media") and robot.media is not None:
                robot.media.start_playing()
                output_rate = int(robot.media.get_output_audio_samplerate())
                audio_ready = True

                log.info(
                    "Audio playback initialized, output_rate=%d",
                    output_rate,
                )
            else:
                log.warning("Robot media object is not available.")

        except Exception as e:
            audio_ready = False
            log.error("Audio playback initialization failed: %s", e)
    else:
        log.info("Media disabled by audio mode.")

    import requests

    try:
        ms = _http().get(f"http://{host}:8000/api/motors/status", timeout=5).json()

        if ms.get("mode") != "enabled":
            log.warning("Motors disabled. Enabling...")
            _http().post(f"http://{host}:8000/api/motors/set_mode/enabled", timeout=5)
            time.sleep(1)
            ms = _http().get(f"http://{host}:8000/api/motors/status", timeout=5).json()

        log.info("Motors: %s", ms.get("mode"))

    except Exception as e:
        log.warning("Motor check failed: %s", e)

    time.sleep(2)

    log.info(
        "Connected. media enabled, output_rate=%d, audio_ready=%s",
        output_rate,
        audio_ready,
    )

    sys.stdout.write(f"READY {output_rate} {audio_ready}\n")
    sys.stdout.flush()

    # ── Shared state for control loop ──────────────────────────────────────

    neutral_head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)

    move_queue: deque = deque()
    current_move: list = [None]
    move_start: list = [None]

    last_primary_head: list = [neutral_head.copy()]
    last_primary_ant: list = [NEUTRAL_ANT]
    last_primary_body: list = [0.0]
    posture_state: list = ["unknown"]

    last_activity: list = [time.monotonic()]
    breathing_active: list = [False]

    speech_lock = threading.Lock()
    speech_offsets: list = [(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]
    speech_dirty: list = [False]

    stop_event = threading.Event()

    err_suppressed = [0]
    last_err_time = [0.0]
    set_target_failures = [0]
    # monotonic timestamp of the first failure in the current failing streak,
    # or None once the link is confirmed healthy again.
    first_failure_time: list = [None]
    # count of consecutive set_target successes since the last failure. The link
    # is only declared recovered (failure window cleared) once this reaches
    # RECOVERY_SUCCESS_STREAK, so sparse successes in a half-broken state cannot
    # keep resetting the grace timer.
    consecutive_successes = [0]
    recorded_moves_cache: list = [None]

    def _get_recorded_moves():
        if recorded_moves_cache[0] is None:
            recorded_moves_cache[0] = RecordedMoves(
                "pollen-robotics/reachy-mini-emotions-library"
            )

        return recorded_moves_cache[0]

    def _move_debug_label(move) -> str:
        if move is None:
            return "none"
        name = type(move).__name__
        duration = getattr(move, "duration", None)
        if duration is None:
            return name
        try:
            return f"{name}(duration={float(duration):.2f}s)"
        except Exception:
            return name

    class _GotoMove:
        """Local inline goto move. Interpolates from start to target."""

        def __init__(self, sh, sa, th, ta, d, label: str = "goto"):
            self._sh = sh
            self._sa = np.array(sa)
            self._th = th
            self._ta = np.array(ta)
            self.duration = d
            self.label = label

        def evaluate(self, t):
            p = max(0.0, min(1.0, t / self.duration))
            # SDK-standard min-jerk easing: smooth accel/decel, no abrupt
            # start/stop (matches goto_target and the daemon's own goto).
            alpha = time_trajectory(p, InterpolationTechnique.MIN_JERK)
            h = linear_pose_interpolation(self._sh, self._th, alpha)
            a = (1 - alpha) * self._sa + alpha * self._ta
            return h, a, 0.0

    def _get_current_start_pose():
        """Get current head and antenna positions safely."""
        try:
            start_head = robot.get_current_head_pose()
            _, start_ant_arr = robot.get_current_joint_positions()
            start_ant = (float(start_ant_arr[0]), float(start_ant_arr[1]))

        except Exception:
            start_head = last_primary_head[0]
            start_ant = last_primary_ant[0]

        return start_head, start_ant

    def _queue_pose_move(
        target_head_deg,
        target_ant,
        duration: float,
        label: str = "goto",
    ):
        """Queue a simple head + antenna pose move."""
        target_head = create_head_pose(*target_head_deg, degrees=True)
        start_head, start_ant = _get_current_start_pose()

        move_queue.append(
            _GotoMove(
                start_head,
                start_ant,
                target_head,
                target_ant,
                duration,
                label,
            )
        )

        last_activity[0] = time.monotonic()
        breathing_active[0] = False

    def _clear_motion_state(reset_posture: bool = False):
        """Stop current queued movement and stop speech sway."""
        move_queue.clear()
        current_move[0] = None
        move_start[0] = None
        breathing_active[0] = False
        if reset_posture:
            posture_state[0] = "unknown"

        with speech_lock:
            speech_offsets[0] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            speech_dirty[0] = True

    # ── Control loop ───────────────────────────────────────────────────────

    def control_loop() -> None:
        now = time.monotonic
        phase_offset = CONTROL_PHASE_OFFSETS.get(role.lower(), 0.0)
        if phase_offset > 0:
            time.sleep(phase_offset)

        while not stop_event.is_set():
            t = now()

            # 1. Manage current move.
            cm = current_move[0]

            if cm is not None and move_start[0] is not None:
                elapsed = t - move_start[0]

                if elapsed >= cm.duration:
                    try:
                        h, a, b = cm.evaluate(cm.duration)

                        if h is not None:
                            last_primary_head[0] = h.copy()

                        if a is not None:
                            last_primary_ant[0] = (float(a[0]), float(a[1]))

                        if b is not None:
                            last_primary_body[0] = float(b)

                    except Exception:
                        pass

                    label = getattr(cm, "label", None)
                    if label is not None:
                        posture_state[0] = label

                    current_move[0] = None
                    move_start[0] = None

            # 2. Start next queued move.
            if current_move[0] is None and move_queue:
                current_move[0] = move_queue.popleft()
                move_start[0] = t
                breathing_active[0] = False

            # 3. Start idle breathing only if enabled.
            if (
                ENABLE_IDLE_BREATHING
                and current_move[0] is None
                and not move_queue
                and not breathing_active[0]
                and t - last_activity[0] >= IDLE_DELAY
            ):
                breathing_active[0] = True

            # 4. Evaluate primary pose.
            if current_move[0] is not None and move_start[0] is not None:
                elapsed = t - move_start[0]

                try:
                    h, a, b = current_move[0].evaluate(elapsed)

                    head = h if h is not None else neutral_head
                    ant = (float(a[0]), float(a[1])) if a is not None else NEUTRAL_ANT
                    body = float(b) if b is not None else 0.0

                    last_primary_head[0] = head.copy()
                    last_primary_ant[0] = ant
                    last_primary_body[0] = body

                except Exception:
                    head = last_primary_head[0]
                    ant = last_primary_ant[0]
                    body = last_primary_body[0]

            elif breathing_active[0]:
                bt = t - last_activity[0] - IDLE_DELAY

                # Breathe ON TOP of the pose the robot is currently holding
                # (e.g. the lowered power-off pose), instead of resetting to
                # neutral. A lowered/listening robot stays down while it gently
                # bobs its head and sways its already-lowered antennas —
                # "resting but alive".
                z = BREATHING_Z_AMP * math.sin(2 * math.pi * BREATHING_FREQ * bt)

                breath_offset = create_head_pose(
                    0,
                    0,
                    z,
                    0,
                    0,
                    0,
                    degrees=False,
                    mm=False,
                )
                head = compose_world_offset(
                    last_primary_head[0],
                    breath_offset,
                    reorthonormalize=True,
                )

                sway = BREATHING_ANT_AMP_RAD * math.sin(
                    2 * math.pi * BREATHING_ANT_FREQ * bt
                )

                base_ant = last_primary_ant[0]
                ant = (base_ant[0] + sway, base_ant[1] - sway)
                body = last_primary_body[0]

            else:
                head = last_primary_head[0]
                ant = last_primary_ant[0]
                body = last_primary_body[0]

            # 5. Apply speech sway as secondary offset.
            with speech_lock:
                if speech_dirty[0]:
                    speech_dirty[0] = False

                so = speech_offsets[0]

            if any(v != 0.0 for v in so):
                sec_head = create_head_pose(
                    so[0],
                    so[1],
                    so[2],
                    so[3],
                    so[4],
                    so[5],
                    degrees=False,
                    mm=False,
                )

                head = compose_world_offset(
                    head,
                    sec_head,
                    reorthonormalize=True,
                )

            # 6. Do not continuously send idle targets.
            # This prevents WebSocket overload while videos are playing.
            has_active_move = current_move[0] is not None or bool(move_queue)
            has_speech_sway = any(v != 0.0 for v in so)
            has_breathing = breathing_active[0]

            if not has_active_move and not has_speech_sway and not has_breathing:
                time.sleep(CONTROL_PERIOD)
                continue

            # 7. Single full-target call.
            speech_only = has_speech_sway and not has_active_move and not has_breathing
            send_mode = "full"

            try:
                robot.set_target(head=head, antennas=ant, body_yaw=body)
                consecutive_successes[0] += 1

                # Only treat the link as recovered after a streak of clean
                # frames. A lone success between failure bursts (half-broken
                # link) must NOT reset the grace window, or reconnect never
                # fires.
                if consecutive_successes[0] >= RECOVERY_SUCCESS_STREAK:
                    set_target_failures[0] = 0
                    first_failure_time[0] = None

            except Exception as e:
                consecutive_successes[0] = 0
                set_target_failures[0] += 1

                now_t = time.monotonic()
                if first_failure_time[0] is None:
                    first_failure_time[0] = now_t

                if now_t - last_err_time[0] >= 2.0:
                    active = current_move[0]
                    elapsed = (
                        time.monotonic() - move_start[0]
                        if active is not None and move_start[0] is not None
                        else 0.0
                    )
                    speech_active = any(v != 0.0 for v in so)
                    msg = (
                        f"set_target failed: {e} | "
                        f"move={_move_debug_label(active)} elapsed={elapsed:.2f}s "
                        f"queue={len(move_queue)} speech={speech_active} "
                        f"breathing={breathing_active[0]} "
                        f"mode={send_mode} speech_only={speech_only}"
                    )

                    if err_suppressed[0]:
                        msg += f" ({err_suppressed[0]} repeats suppressed)"
                        err_suppressed[0] = 0

                    log.error(msg)
                    last_err_time[0] = now_t

                else:
                    err_suppressed[0] += 1

                failing_for = now_t - first_failure_time[0]
                if failing_for >= FAILURE_GRACE_SECONDS:
                    log.error(
                        "Connection appears lost: set_target failing for %.1fs "
                        "(%d failed calls, best recent success streak < %d). "
                        "Exiting worker so controller can reconnect.",
                        failing_for,
                        set_target_failures[0],
                        RECOVERY_SUCCESS_STREAK,
                    )
                    stop_event.set()
                    # The main thread is blocked reading stdin and would only
                    # notice after the next command. Exit now so the parent
                    # sees a dead worker immediately and can revive it.
                    for handler in logging.getLogger().handlers:
                        try:
                            handler.flush()
                        except Exception:
                            pass
                    os._exit(3)

            # 8. Sleep to maintain control rate.
            # Throttle hard during speech-only sway to spare the flaky robots.
            loop_period = SPEECH_ONLY_PERIOD if speech_only else CONTROL_PERIOD
            dt = time.monotonic() - t
            sleep = max(0, loop_period - dt)

            if sleep > 0:
                time.sleep(sleep)

    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    log.info("%dHz control loop started.", CONTROL_HZ)

    # ── Main thread: read commands from stdin pipe ─────────────────────────

    stdin = sys.stdin.buffer

    while not stop_event.is_set():
        try:
            length_bytes = stdin.read(4)

            if not length_bytes or len(length_bytes) < 4:
                break

            msg_len = struct.unpack("<I", length_bytes)[0]

            if msg_len == 0:
                break

            data = stdin.read(msg_len)

            if not data:
                break

            action, kw = pickle.loads(data)

        except Exception:
            break

        try:
            if action == "push_audio":
                pcm = kw["pcm_float32"]
                source_rate = kw.get("source_rate", OPENAI_SAMPLE_RATE)

                if not audio_ready:
                    log.warning("Ignoring push_audio because audio is not ready.")
                    continue

                if output_rate != source_rate:
                    num = int(len(pcm) * output_rate / source_rate)
                    pcm = resample(pcm, num).astype(np.float32)

                pcm = pcm.astype(np.float32)

                if pcm.ndim == 1:
                    pcm = pcm.reshape(-1, 1)

                try:
                    robot.media.push_audio_sample(pcm)
                except Exception as e:
                    log.warning("push_audio_sample failed: %s", e)

            elif action == "set_speech_offsets":
                with speech_lock:
                    speech_offsets[0] = tuple(kw["offsets"])
                    speech_dirty[0] = True

                last_activity[0] = time.monotonic()

            elif action == "queue_emotion":
                rm = _get_recorded_moves()
                emotion_name = kw["emotion"]
                emotion_move = rm.get(emotion_name)

                # Blend smoothly from the current pose into the emotion's first
                # frame so the head does not snap when the emotion starts.
                try:
                    fh, fa, _fb = emotion_move.evaluate(0.0)
                    if fh is not None and fa is not None:
                        start_head, start_ant = _get_current_start_pose()
                        move_queue.append(
                            _GotoMove(
                                start_head,
                                start_ant,
                                fh,
                                (float(fa[0]), float(fa[1])),
                                EMOTION_BLEND_DURATION,
                                "emotion_blend",
                            )
                        )
                except Exception as e:
                    log.warning("Emotion blend skipped: %s", e)

                move_queue.append(emotion_move)

                last_activity[0] = time.monotonic()
                breathing_active[0] = False

                log.info("Queued emotion: %s", emotion_name)

            elif action == "queue_dance":
                from reachy_mini_dances_library.collection.dance import AVAILABLE_MOVES
                from reachy_mini_dances_library.dance_move import DanceMove

                dance_name = kw["dance_name"]

                if dance_name in AVAILABLE_MOVES:
                    dance_move = DanceMove(dance_name)

                    # Blend smoothly into the dance's first frame so the head
                    # does not snap when the dance starts (same as emotions).
                    try:
                        fh, fa, _fb = dance_move.evaluate(0.0)
                        if fh is not None and fa is not None:
                            start_head, start_ant = _get_current_start_pose()
                            move_queue.append(
                                _GotoMove(
                                    start_head,
                                    start_ant,
                                    fh,
                                    (float(fa[0]), float(fa[1])),
                                    EMOTION_BLEND_DURATION,
                                    "dance_blend",
                                )
                            )
                    except Exception as e:
                        log.warning("Dance blend skipped: %s", e)

                    move_queue.append(dance_move)

                    last_activity[0] = time.monotonic()
                    breathing_active[0] = False

                    log.info("Queued dance: %s", dance_name)

            elif action == "queue_power_off_pose":
                # Make this robot look powered off.
                # This does not disable motors.
                clear_first = kw.get("clear_first", True)
                dur = kw.get("duration", 0.8)
                active_label = getattr(current_move[0], "label", None)

                # Only "already lowered" if nothing else is moving the head: a
                # goto (e.g. rising to speak) may be running while posture_state
                # still says "off", and skipping then would leave it raised.
                if (
                    clear_first
                    and not move_queue
                    and (
                        active_label == "off"
                        or (current_move[0] is None and posture_state[0] == "off")
                    )
                ):
                    log.info("Skipped power-off-like pose; already lowered")
                    continue

                if clear_first:
                    _clear_motion_state()

                _queue_pose_move(
                    target_head_deg=OFF_HEAD_DEG,
                    target_ant=OFF_ANT,
                    duration=dur,
                    label="off",
                )

                log.info(
                    "Queued power-off-like pose: head=%s ant=%s duration=%.2fs",
                    OFF_HEAD_DEG,
                    OFF_ANT,
                    dur,
                )

            elif action == "queue_speaking_ready_pose":
                # Return to a clean initial pose before speaking.
                # Use this before queue_emotion or speech sway.
                clear_first = kw.get("clear_first", True)
                dur = kw.get("duration", 0.7)

                if clear_first:
                    _clear_motion_state(reset_posture=True)

                _queue_pose_move(
                    target_head_deg=READY_HEAD_DEG,
                    target_ant=READY_ANT,
                    duration=dur,
                    label="ready",
                )

                log.info("Queued speaking-ready pose")

            elif action == "queue_goto":
                # Same option the pose commands have: replace whatever is
                # playing rather than queueing behind it.
                if kw.get("clear_first"):
                    _clear_motion_state()

                target_head = create_head_pose(*kw["head"], degrees=True)
                target_ant = tuple(kw.get("antennas", NEUTRAL_ANT))
                dur = kw.get("duration", 1.0)

                start_head, start_ant = _get_current_start_pose()

                move_queue.append(
                    _GotoMove(
                        start_head,
                        start_ant,
                        target_head,
                        target_ant,
                        dur,
                        "goto",
                    )
                )

                last_activity[0] = time.monotonic()
                breathing_active[0] = False

            elif action == "clear_moves":
                _clear_motion_state()

            elif action == "set_volume":
                # Over the SDK link, NOT the daemon's REST /api/volume/set:
                # that one plays a test sound on every change.
                from reachy_mini.io.protocol import SetVolumeCmd

                robot.client.send_command(SetVolumeCmd(volume=int(kw["volume"])))

            elif action == "wiggle":
                robot.goto_target(antennas=[0.5, -0.5], duration=0.5)
                robot.goto_target(antennas=[0, 0], duration=0.5)

            elif action == "shutdown":
                stop_event.set()
                break

        except Exception as e:
            err_key = f"{action}:{type(e).__name__}"

            if not hasattr(main, "_seen_errors"):
                main._seen_errors = set()

            if err_key not in main._seen_errors:
                log.error(
                    "Command %s failed: %s. Further repeats suppressed.",
                    action,
                    e,
                )
                main._seen_errors.add(err_key)

    # ── Shutdown ───────────────────────────────────────────────────────────

    stop_event.set()
    control_thread.join(timeout=2)

    log.info("Shutting down.")

    try:
        if hasattr(robot, "media") and robot.media is not None:
            try:
                robot.media.stop_playing()
            except Exception:
                pass

            try:
                robot.media.stop_recording()
            except Exception:
                pass

            try:
                robot.media.close()
            except Exception:
                pass

    except Exception:
        pass

    try:
        robot.client.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    main()
