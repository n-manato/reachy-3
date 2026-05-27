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

# Lower control rate to avoid WebSocket timeouts with three robots.
CONTROL_HZ = 100
CONTROL_PERIOD = 1.0 / CONTROL_HZ

# Idle breathing is disabled for stability.
ENABLE_IDLE_BREATHING = False

BREATHING_Z_AMP = 0.0
BREATHING_FREQ = 0.1
BREATHING_ANT_AMP_RAD = 0.0
BREATHING_ANT_FREQ = 0.5

# Keep this very high because breathing is disabled.
IDLE_DELAY = 9999.0

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
OFF_HEAD_DEG =  (0, 0, -0.19, 0, 69, 0)

# Antennas down.
# If the antennas move the wrong direction, swap the signs.
OFF_ANT = (-3.3, 3.3)
# Speaking ready pose.
# This returns the head/face direction and antennas to the normal state.
READY_HEAD_DEG = (0, 0, 0, 0, 0, 0)
READY_ANT = NEUTRAL_ANT

# If set_target fails repeatedly, assume the WebSocket connection is dead.
# The worker exits so robot_controller.py can restart it.
MAX_SET_TARGET_FAILURES = 200



def main() -> None:

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
    )
    from reachy_mini.motion.recorded_move import RecordedMoves

    kwargs: dict[str, Any] = {
        "host": host,
        "connection_mode": "network",
        "media_backend": "default" if use_media else "no_media",
    }

    if robot_name:
        kwargs["robot_name"] = robot_name

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
        ms = requests.get(f"http://{host}:8000/api/motors/status", timeout=5).json()

        if ms.get("mode") != "enabled":
            log.warning("Motors disabled. Enabling...")
            requests.post(f"http://{host}:8000/api/motors/set_mode/enabled", timeout=5)
            time.sleep(1)
            ms = requests.get(f"http://{host}:8000/api/motors/status", timeout=5).json()

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

    last_activity: list = [time.monotonic()]
    breathing_active: list = [False]

    speech_lock = threading.Lock()
    speech_offsets: list = [(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)]
    speech_dirty: list = [False]

    stop_event = threading.Event()

    err_suppressed = [0]
    last_err_time = [0.0]
    set_target_failures = [0]

    # ── Debug state for set_target connection loss ─────────────────────────
    last_command = ["none"]
    last_command_time = [time.monotonic()]
    last_visible_command = ["none"]
    last_visible_command_time = [time.monotonic()]

    last_set_target_ok_time = [time.monotonic()]
    last_set_target_ok_state = ["none"]

    set_target_total = [0]
    set_target_ok_total = [0]
    set_target_fail_total = [0]

    last_loop_state = ["none"]

    recorded_moves_cache: list = [None]

    def _get_recorded_moves():
        if recorded_moves_cache[0] is None:
            recorded_moves_cache[0] = RecordedMoves(
                "pollen-robotics/reachy-mini-emotions-library"
            )

        return recorded_moves_cache[0]

    class _GotoMove:
        """Local inline goto move. Interpolates from start to target."""

        def __init__(self, sh, sa, th, ta, d):
            self._sh = sh
            self._sa = np.array(sa)
            self._th = th
            self._ta = np.array(ta)
            self.duration = d

        def evaluate(self, t):
            alpha = max(0.0, min(1.0, t / self.duration))
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

    def _queue_pose_move(target_head_deg, target_ant, duration: float):
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
            )
        )

        last_activity[0] = time.monotonic()
        breathing_active[0] = False

    def _clear_motion_state():
        """Stop current queued movement and stop speech sway."""
        move_queue.clear()
        current_move[0] = None
        move_start[0] = None
        breathing_active[0] = False

        with speech_lock:
            speech_offsets[0] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            speech_dirty[0] = True
            
    def _safe_move_eval_time(move, t: float) -> float:
        """Return a safe timestamp for RecordedMove.evaluate().

        Some recorded moves raise an error when evaluated exactly at
        duration or a tiny bit beyond it. Keep evaluation slightly inside
        the valid range.
        """
        duration = float(getattr(move, "duration", 0.0) or 0.0)

        if duration <= 0.0:
            return 0.0

        # Stay 1 ms before the end.
        # This avoids:
        # "Tried to evaluate recorded move beyond its duration."
        end_t = max(0.0, duration - 0.001)

        return max(0.0, min(float(t), end_t))

    # ── Control loop ───────────────────────────────────────────────────────

    def control_loop() -> None:
        now = time.monotonic

        while not stop_event.is_set():
            t = now()

            # 1. Manage current move.
            cm = current_move[0]

            if cm is not None and move_start[0] is not None:
                elapsed = t - move_start[0]

                if elapsed >= cm.duration:
                    finished_move = cm
                    finished_elapsed = elapsed

                    try:
                        final_t = _safe_move_eval_time(cm, cm.duration)
                        h, a, b = cm.evaluate(final_t)

                        if h is not None:
                            last_primary_head[0] = h.copy()

                        if a is not None:
                            last_primary_ant[0] = (float(a[0]), float(a[1]))

                        if b is not None:
                            last_primary_body[0] = float(b)

                    except Exception as e:
                        log.warning(
                            "Move final evaluate failed: %s move=%s",
                            e,
                            finished_move.__class__.__name__,
                        )

                    current_move[0] = None
                    move_start[0] = None

                    log.info(           
                        "Move END: %s elapsed=%.3fs duration=%.3fs queue_remaining=%d last_visible_command=%s last_command=%s",
                        finished_move.__class__.__name__,
                        finished_elapsed,
                        getattr(finished_move, "duration", -1.0),
                        len(move_queue),
                        last_visible_command[0],    
                        last_command[0],
                    )

            # 2. Start next queued move.
            if current_move[0] is None and move_queue:
                current_move[0] = move_queue.popleft()
                move_start[0] = t
                breathing_active[0] = False
                log.info(
                    "Move START: %s queue_remaining=%d last_visible_command=%s last_command=%s",
                    current_move[0].__class__.__name__,
                    len(move_queue),
                    last_visible_command[0],
                    last_command[0],
                )

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
                    safe_elapsed = _safe_move_eval_time(current_move[0], elapsed)
                    h, a, b = current_move[0].evaluate(safe_elapsed)

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

                z = BREATHING_Z_AMP * math.sin(2 * math.pi * BREATHING_FREQ * bt)

                head = create_head_pose(
                    0,
                    0,
                    z,
                    0,
                    0,
                    0,
                    degrees=False,
                    mm=False,
                )

                sway = BREATHING_ANT_AMP_RAD * math.sin(
                    2 * math.pi * BREATHING_ANT_FREQ * bt
                )

                ant = (sway, -sway)
                body = 0.0

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

            state_parts = []

            if has_active_move:
                state_parts.append("move")

            if has_speech_sway:
                state_parts.append("sway")

            if has_breathing:
                state_parts.append("breathing")

            if not state_parts:
                state_parts.append("idle")

            loop_state = "+".join(state_parts)
            last_loop_state[0] = loop_state

            if not has_active_move and not has_speech_sway and not has_breathing:
                time.sleep(CONTROL_PERIOD)
                continue

            # 7. Single set_target call.
            try:
                robot.set_target(head=head, antennas=ant, body_yaw=body)

                set_target_total[0] += 1
                set_target_ok_total[0] += 1

                if set_target_failures[0] > 0:
                    log.warning(
                        "set_target recovered after %d failures. "
                        "state=%s last_visible_command=%s last_command=%s last_ok_gap=%.3fs "
                        "queue=%d current_move=%s sway=%s breathing=%s",
                        set_target_failures[0],
                        loop_state,
                        last_visible_command[0],
                        last_command[0],
                        time.monotonic() - last_set_target_ok_time[0],
                        len(move_queue),
                        current_move[0].__class__.__name__
                        if current_move[0] is not None
                        else "None",
                        has_speech_sway,
                        has_breathing,
                    )

                set_target_failures[0] = 0
                last_set_target_ok_time[0] = time.monotonic()
                last_set_target_ok_state[0] = loop_state

            except Exception as e:
                set_target_total[0] += 1
                set_target_fail_total[0] += 1
                set_target_failures[0] += 1

                now_t = time.monotonic()
                gap = now_t - last_set_target_ok_time[0]

                if now_t - last_err_time[0] >= 2.0:
                    msg = (
                        f"set_target failed: {e} "
                        f"fail={set_target_failures[0]}/{MAX_SET_TARGET_FAILURES} "
                        f"state={loop_state} "
                        f"last_visible_command={last_visible_command[0]} "
                        f"last_command={last_command[0]} "
                        f"last_ok_gap={gap:.3f}s "
                        f"queue={len(move_queue)} "
                        f"current_move="
                        f"{current_move[0].__class__.__name__ if current_move[0] is not None else 'None'} "
                        f"sway={has_speech_sway} "
                        f"breathing={has_breathing} "
                        f"total={set_target_total[0]} "
                        f"ok={set_target_ok_total[0]} "
                        f"fail_total={set_target_fail_total[0]}"
                    )

                    if err_suppressed[0]:
                        msg += f" ({err_suppressed[0]} repeats suppressed)"
                        err_suppressed[0] = 0

                    log.error(msg)
                    last_err_time[0] = now_t

                else:
                    err_suppressed[0] += 1

                if set_target_failures[0] >= MAX_SET_TARGET_FAILURES:
                    log.error(
                        "Connection appears lost after %d set_target failures. "
                        "state=%s last_visible_command=%s last_command=%s last_ok_gap=%.3fs "
                        "last_ok_state=%s queue=%d current_move=%s "
                        "sway=%s breathing=%s. "
                        "Exiting worker so controller can reconnect.",
                        set_target_failures[0],
                        loop_state,
                        last_visible_command[0],
                        last_command[0],
                        gap,
                        last_set_target_ok_state[0],
                        len(move_queue),
                        current_move[0].__class__.__name__
                        if current_move[0] is not None
                        else "None",
                        has_speech_sway,
                        has_breathing,
                    )
                    stop_event.set()
                    break

            # 8. Sleep to maintain control rate.
            dt = time.monotonic() - t
            sleep = max(0, CONTROL_PERIOD - dt)

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

            last_command[0] = action
            last_command_time[0] = time.monotonic()

            if action != "set_speech_offsets":
                last_visible_command[0] = action
                last_visible_command_time[0] = time.monotonic()

                log.info(
                    "Command received: %s queue=%d current_move=%s last_state=%s",
                    action,
                    len(move_queue),
                    current_move[0].__class__.__name__
                    if current_move[0] is not None
                    else "None",
                    last_loop_state[0],
                )

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
                new_offsets = tuple(kw["offsets"])

                with speech_lock:
                    old_has_sway = any(v != 0.0 for v in speech_offsets[0])
                    new_has_sway = any(v != 0.0 for v in new_offsets)

                    speech_offsets[0] = new_offsets
                    speech_dirty[0] = True

                if old_has_sway != new_has_sway:
                    log.info(
                        "Speech sway %s. queue=%d current_move=%s last_state=%s",
                        "START" if new_has_sway else "STOP",
                        len(move_queue),
                        current_move[0].__class__.__name__
                        if current_move[0] is not None
                        else "None",
                        last_loop_state[0],
                    )

                last_activity[0] = time.monotonic()

            elif action == "queue_emotion":
                rm = _get_recorded_moves()
                emotion_name = kw["emotion"]
                emotion_move = rm.get(emotion_name)

                move_queue.append(emotion_move)

                last_activity[0] = time.monotonic()
                breathing_active[0] = False

                log.info("Queued emotion: %s", emotion_name)

            elif action == "queue_dance":
                from reachy_mini_dances_library.collection.dance import AVAILABLE_MOVES
                from reachy_mini_dances_library.dance_move import DanceMove

                dance_name = kw["dance_name"]

                if dance_name in AVAILABLE_MOVES:
                    move_queue.append(DanceMove(dance_name))

                    last_activity[0] = time.monotonic()
                    breathing_active[0] = False

                    log.info("Queued dance: %s", dance_name)

            elif action == "queue_power_off_pose":
                # Make this robot look powered off.
                # This does not disable motors.
                clear_first = kw.get("clear_first", True)
                dur = kw.get("duration", 0.8)

                if clear_first:
                    _clear_motion_state()
                    

                _queue_pose_move(
                    target_head_deg=OFF_HEAD_DEG,
                    target_ant=OFF_ANT,
                    duration=dur,
                )

                log.info("Queued power-off-like pose")

            elif action == "queue_speaking_ready_pose":
                # Return to a clean initial pose before speaking.
                # Use this before queue_emotion or speech sway.
                clear_first = kw.get("clear_first", True)
                dur = kw.get("duration", 0.7)

                if clear_first:
                    _clear_motion_state()

                _queue_pose_move(
                    target_head_deg=READY_HEAD_DEG,
                    target_ant=READY_ANT,
                    duration=dur,
                )

                log.info("Queued speaking-ready pose")

            elif action == "queue_goto":
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
                    )
                )

                last_activity[0] = time.monotonic()
                breathing_active[0] = False

            elif action == "clear_moves":
                _clear_motion_state()
                log.info("Cleared moves and speech sway")

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