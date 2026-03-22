"""Standalone robot worker — runs as a completely independent process.

Architecture (modeled after Pollen's conversation app):
- 100Hz control loop thread runs set_target() every 10ms
- Primary moves (emotions, dances, goto, breathing) are sequential via move queue
- Speech sway is a secondary offset layered on top via compose_world_offset()
- Main thread reads commands from stdin pipe (length-prefixed pickle)
- Audio is pushed directly to the robot's media system

Commands accepted via pipe:
- push_audio: send PCM audio to speaker
- queue_emotion: queue an emotion move
- queue_dance: queue a dance move
- queue_goto: queue a goto-pose move
- set_speech_offsets: update speech sway (x,y,z,roll,pitch,yaw) in meters/radians
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
from typing import Any, Tuple

import numpy as np
from scipy.signal import resample

OPENAI_SAMPLE_RATE = 24000
CONTROL_HZ = 100
CONTROL_PERIOD = 1.0 / CONTROL_HZ
BREATHING_Z_AMP = 0.005       # 5mm
BREATHING_FREQ = 0.1           # Hz
BREATHING_ANT_AMP_RAD = 0.26  # ~15 degrees
BREATHING_ANT_FREQ = 0.5      # Hz
IDLE_DELAY = 0.3               # seconds before breathing starts
NEUTRAL_ANT = (-0.1745, 0.1745)  # ~10° offset to reduce shaking


def main() -> None:
    host = sys.argv[1]
    role = sys.argv[2]
    robot_name = sys.argv[3] if len(sys.argv) > 3 else None

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{role}] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger(f"robot.{role}")

    from reachy_mini import ReachyMini
    from reachy_mini.utils import create_head_pose
    from reachy_mini.utils.interpolation import compose_world_offset, linear_pose_interpolation
    from reachy_mini.motion.recorded_move import RecordedMoves

    kwargs = {"host": host, "connection_mode": "network"}
    if robot_name:
        kwargs["robot_name"] = robot_name

    log.info("Connecting to %s at %s...", robot_name or "auto", host)
    robot = ReachyMini(**kwargs)

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

    time.sleep(6)
    audio_ready = robot.media.audio._audio_send_ready
    if not audio_ready:
        log.warning("Audio not ready. Polling up to 15s...")
        for i in range(15):
            time.sleep(1)
            audio_ready = robot.media.audio._audio_send_ready
            if audio_ready:
                log.info("Audio ready after %ds.", i + 1)
                break

    output_rate = robot.media.get_output_audio_samplerate()
    log.info("Connected. output_rate=%d, audio_ready=%s", output_rate, audio_ready)

    sys.stdout.write(f"READY {output_rate} {audio_ready}\n")
    sys.stdout.flush()

    # ── Shared state for 100Hz control loop ────────────────────────────────
    neutral_head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)

    move_queue: deque = deque()
    current_move: list = [None]  # mutable container for thread access
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

    recorded_moves_cache: list = [None]

    def _get_recorded_moves():
        if recorded_moves_cache[0] is None:
            recorded_moves_cache[0] = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
        return recorded_moves_cache[0]

    # ── 100Hz control loop (runs in dedicated thread) ──────────────────────
    def control_loop():
        now = time.monotonic
        prev = now()

        while not stop_event.is_set():
            t = now()

            # 1) Manage move queue
            cm = current_move[0]
            if cm is not None and move_start[0] is not None:
                elapsed = t - move_start[0]
                if elapsed >= cm.duration:
                    # Move finished — cache final pose
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
                    current_move[0] = None
                    move_start[0] = None

            if current_move[0] is None and move_queue:
                current_move[0] = move_queue.popleft()
                move_start[0] = t
                breathing_active[0] = False

            # 2) Auto-breathing when idle
            if (current_move[0] is None and not move_queue
                    and not breathing_active[0]
                    and t - last_activity[0] >= IDLE_DELAY):
                breathing_active[0] = True

            # 3) Evaluate primary pose
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
                # Breathing animation
                bt = t - last_activity[0] - IDLE_DELAY
                z = BREATHING_Z_AMP * math.sin(2 * math.pi * BREATHING_FREQ * bt)
                head = create_head_pose(0, 0, z, 0, 0, 0, degrees=False, mm=False)
                sway = BREATHING_ANT_AMP_RAD * math.sin(2 * math.pi * BREATHING_ANT_FREQ * bt)
                ant = (sway, -sway)
                body = 0.0
            else:
                head = last_primary_head[0]
                ant = last_primary_ant[0]
                body = last_primary_body[0]

            # 4) Apply speech sway as secondary offset
            with speech_lock:
                if speech_dirty[0]:
                    speech_dirty[0] = False
                so = speech_offsets[0]

            if any(v != 0.0 for v in so):
                sec_head = create_head_pose(
                    so[0], so[1], so[2], so[3], so[4], so[5],
                    degrees=False, mm=False,
                )
                head = compose_world_offset(head, sec_head, reorthonormalize=True)

            # 5) Single set_target call
            try:
                robot.set_target(head=head, antennas=ant, body_yaw=body)
            except Exception as e:
                now_t = time.monotonic()
                if now_t - last_err_time[0] >= 2.0:
                    msg = f"set_target failed: {e}"
                    if err_suppressed[0]:
                        msg += f" ({err_suppressed[0]} repeats suppressed)"
                        err_suppressed[0] = 0
                    log.error(msg)
                    last_err_time[0] = now_t
                else:
                    err_suppressed[0] += 1

            # 6) Sleep to maintain ~100Hz
            dt = time.monotonic() - t
            sleep = max(0, CONTROL_PERIOD - dt)
            if sleep > 0:
                time.sleep(sleep)

    # Start the control loop thread
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()
    log.info("100Hz control loop started.")

    # ── Main thread: read commands from stdin pipe ─────────────────────────
    stdin = sys.stdin.buffer

    while True:
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
                if output_rate != source_rate:
                    num = int(len(pcm) * output_rate / source_rate)
                    pcm = resample(pcm, num).astype(np.float32)
                robot.media.push_audio_sample(pcm)

            elif action == "set_speech_offsets":
                # (x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad)
                with speech_lock:
                    speech_offsets[0] = tuple(kw["offsets"])
                    speech_dirty[0] = True
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

            elif action == "queue_goto":
                # Smooth interpolation to a target pose using a local Move
                target_head = create_head_pose(*kw["head"], degrees=True)
                target_ant = tuple(kw.get("antennas", (0, 0)))
                dur = kw.get("duration", 1.0)
                try:
                    start_head = robot.get_current_head_pose()
                    _, start_ant_arr = robot.get_current_joint_positions()
                    start_ant = (float(start_ant_arr[0]), float(start_ant_arr[1]))
                except Exception:
                    start_head = last_primary_head[0]
                    start_ant = last_primary_ant[0]

                class _GotoMove:
                    """Local inline goto move — interpolates from start to target."""
                    def __init__(self, sh, sa, th, ta, d):
                        self._sh, self._sa = sh, np.array(sa)
                        self._th, self._ta = th, np.array(ta)
                        self.duration = d
                    def evaluate(self, t):
                        alpha = max(0.0, min(1.0, t / self.duration))
                        h = linear_pose_interpolation(self._sh, self._th, alpha)
                        a = (1 - alpha) * self._sa + alpha * self._ta
                        return h, a, 0.0

                move_queue.append(_GotoMove(start_head, start_ant, target_head, target_ant, dur))
                last_activity[0] = time.monotonic()
                breathing_active[0] = False

            elif action == "clear_moves":
                move_queue.clear()
                current_move[0] = None
                move_start[0] = None
                breathing_active[0] = False

            elif action == "wiggle":
                robot.goto_target(antennas=[0.5, -0.5], duration=0.5)
                robot.goto_target(antennas=[0, 0], duration=0.5)

        except Exception as e:
            err_key = f"{action}:{type(e).__name__}"
            if not hasattr(main, '_seen_errors'):
                main._seen_errors = set()
            if err_key not in main._seen_errors:
                log.error("Command %s failed: %s (further repeats suppressed)", action, e)
                main._seen_errors.add(err_key)

    # Shutdown
    stop_event.set()
    control_thread.join(timeout=2)
    log.info("Shutting down.")
    try:
        robot.media.close()
    except Exception:
        pass
    robot.client.disconnect()


if __name__ == "__main__":
    main()
