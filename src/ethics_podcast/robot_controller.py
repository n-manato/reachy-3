"""Robot controller using fully independent subprocesses (subprocess.Popen).

Each robot runs in a completely separate Python process with its own GStreamer
state and a low-rate control loop. Communication is via stdin pipe
(length-prefixed pickled commands).

IMPORTANT:
Do not change the subprocess launch pattern. Running the worker as a direct
script via subprocess.Popen is the safest way to keep WebRTC working.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import struct
import subprocess
import sys
import time
import math
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OPENAI_SAMPLE_RATE = 24000


class RobotController:
    """Proxy that sends high-level commands to a robot subprocess."""

    def __init__(
        self,
        host: str,
        name: str | None,
        role: str,
        use_media: bool = False,
    ):
        self.host = host
        self.name = name
        self.role = role
        self.use_media = use_media

        self._last_pose_command: str | None = None
        self._last_pose_time: float = 0.0

    def start(self) -> None:
        """Launch the worker subprocess and wait for READY signal."""
        env = {**os.environ}

        worker_path = os.path.join(os.path.dirname(__file__), "robot_worker.py")
        cmd = [
            sys.executable,
            worker_path,
            self.host,
            self.role,
            self.name or "",
            "media" if self.use_media else "no_media",
        ]

        if self.name:
            cmd.append(self.name)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
        )

        logger.info(
            "Subprocess for %s started (pid=%d). Waiting for connection...",
            self.role,
            self._process.pid,
        )

        if self._process.stdout is None:
            raise RuntimeError(f"Robot {self.role} failed to start. No stdout pipe.")

        ready_line = self._process.stdout.readline().decode(errors="replace").strip()

        if ready_line.startswith("READY"):
            parts = ready_line.split()
            self._output_rate = int(parts[1]) if len(parts) > 1 else 16000
            self._audio_ready = parts[2] == "True" if len(parts) > 2 else False
            self._restart_attempts = 0

            logger.info(
                "Robot %s ready (output_rate=%d, audio=%s).",
                self.role,
                self._output_rate,
                self._audio_ready,
            )
            return

        raise RuntimeError(f"Robot {self.role} failed to start. Got: {ready_line}")

    def stop(self) -> None:
        """Shut down the subprocess."""
        if self._process is None:
            return

        if self._process.poll() is None:
            try:
                if self._process.stdin is not None:
                    self._process.stdin.write(struct.pack("<I", 0))
                    self._process.stdin.flush()
            except Exception:
                pass

            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()

                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()

            logger.info("Robot %s subprocess stopped.", self.role)

        self._process = None

    def is_alive(self) -> bool:
        """Return True if the worker subprocess is still running."""
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        """Restart the worker subprocess after a connection loss."""
        logger.warning("Restarting robot %s worker...", self.role)

        try:
            self.stop()
        except Exception as e:
            logger.warning("Error while stopping robot %s worker: %s", self.role, e)

        self._restart_attempts += 1

        # Backoff so the robot daemon has time to recover.
        delay = min(2.0 * self._restart_attempts, 8.0)
        time.sleep(delay)

        self.start()

        logger.info("Robot %s worker restarted.", self.role)

    def ensure_alive(self) -> None:
        """Restart the worker if it has exited."""
        if not self.is_alive():
            self.restart()

    def _send(self, action: str, kwargs: dict) -> None:
        """Send a command to the worker. Restart once if the worker died."""
        data = pickle.dumps((action, kwargs))

        for attempt in range(2):
            try:
                self.ensure_alive()

                if self._process is None or self._process.stdin is None:
                    raise RuntimeError("worker process is not available")

                self._process.stdin.write(struct.pack("<I", len(data)) + data)
                self._process.stdin.flush()

                return

            except (BrokenPipeError, OSError, RuntimeError) as e:
                logger.warning(
                    "Command %s failed for robot %s: %s",
                    action,
                    self.role,
                    e,
                )

                if attempt == 0:
                    try:
                        self.restart()
                    except Exception as restart_error:
                        logger.error(
                            "Could not restart robot %s worker: %s",
                            self.role,
                            restart_error,
                        )
                        return

                    continue

                logger.error(
                    "Could not send command %s to robot %s after reconnect.",
                    action,
                    self.role,
                )
                return

    @property
    def output_sample_rate(self) -> int:
        return self._output_rate

    @property
    def audio_ready(self) -> bool:
        return self._audio_ready

    # ── Audio ──────────────────────────────────────────────────────────────

    def push_audio(
        self,
        pcm_float32: np.ndarray,
        source_rate: int = OPENAI_SAMPLE_RATE,
    ) -> None:
        self._send(
            "push_audio",
            {
                "pcm_float32": pcm_float32,
                "source_rate": source_rate,
            },
        )

    # ── High-level motion commands ─────────────────────────────────────────

    def set_speech_offsets(
        self,
        x_m: float,
        y_m: float,
        z_m: float,
        roll_rad: float,
        pitch_rad: float,
        yaw_rad: float,
    ) -> None:
        """Set speech sway secondary offsets."""
        self._send(
            "set_speech_offsets",
            {
                "offsets": (
                    x_m,
                    y_m,
                    z_m,
                    roll_rad,
                    pitch_rad,
                    yaw_rad,
                )
            },
        )

    def queue_emotion(self, emotion_name: str) -> None:
        """Queue a recorded emotion to play through the move queue."""
        self._last_pose_command = None
        self._send("queue_emotion", {"emotion": emotion_name})

    def queue_dance(self, dance_name: str) -> None:
        """Queue a dance to play through the move queue."""
        self._send("queue_dance", {"dance_name": dance_name})

    def queue_goto(
        self,
        head: tuple[float, ...],
        antennas: tuple[float, float] = (0, 0),
        duration: float = 1.0,
    ) -> None:
        """Queue a smooth goto-pose move.

        head is in degrees:
        x, y, z, roll, pitch, yaw
        """
        self._send(
            "queue_goto",
            {
                "head": head,
                "antennas": antennas,
                "duration": duration,
            },
        )

    def queue_power_off_pose(
        self,
        duration: float = 0.8,
        force: bool = False,
    ) -> None:
        """Move to a power-off-like pose.

        This does not turn off motors.
        It only sends a motion command to lower the head/face direction
        and antennas.
        """
        now = time.monotonic()

        if (
            not force
            and self._last_pose_command == "power_off"
            and now - self._last_pose_time < 8.0
        ):
            logger.debug(
                "Skipping duplicate power-off pose for robot %s.",
                self.role,
            )
            return

        self._send(
            "queue_power_off_pose",
            {
                "duration": duration,
                "clear_first": True,
            },
        )

        self._last_pose_command = "power_off"
        self._last_pose_time = now

    def queue_speaking_ready_pose(
        self,
        duration: float = 0.7,
        force: bool = False,
    ) -> None:
        """Return to the normal pose before speaking.

        Use this before queue_emotion or speech_motion_loop.
        """
        now = time.monotonic()

        if (
            not force
            and self._last_pose_command == "speaking_ready"
            and now - self._last_pose_time < 8.0
        ):
            logger.debug(
                "Skipping duplicate speaking-ready pose for robot %s.",
                self.role,
            )
            return

        self._send(
            "queue_speaking_ready_pose",
            {
                "duration": duration,
                "clear_first": True,
            },
        )

        self._last_pose_command = "speaking_ready"
        self._last_pose_time = now

    def clear_moves(self) -> None:
        """Stop current move and clear the queue."""
        self._send("clear_moves", {})

    def wiggle_antennas(self) -> None:
        """Wiggle antennas."""
        self._send("wiggle", {})


async def speech_motion_loop(
    controller: RobotController,
    stop_event: asyncio.Event,
    interval: float = 0.22,
) -> None:
    """
    Keep sending small speech offsets while the robot is speaking.

    This prevents the robot from becoming motionless during long speech.
    The values are intentionally small to avoid WebSocket overload.
    """

    phase = {
        "professor": 0.0,
        "student": 1.4,
        "critic": 2.7,
        "robot_a": 0.0,
        "robot_b": 1.4,
        "robot_c": 2.7,
    }.get(controller.role.lower(), 0.5)

    try:
        while not stop_event.is_set():
            t = time.monotonic()

            # Small natural head movement.
            yaw = 0.045 * math.sin(2.0 * math.pi * 0.55 * t + phase)
            pitch = 0.035 * math.sin(2.0 * math.pi * 0.75 * t + phase)

            # Tiny roll so it does not look robotic.
            roll = 0.012 * math.sin(2.0 * math.pi * 0.35 * t + phase)

            controller.set_speech_offsets(
                0.0,    # x
                0.0,    # y
                0.0,    # z
                roll,   # roll
                pitch,  # pitch
                yaw,    # yaw
            )

            await asyncio.sleep(interval)

    finally:
        # Always reset after speaking.
        controller.set_speech_offsets(
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )


async def prepare_robots_for_turn(
    controllers: tuple[RobotController, ...],
    speaker: RobotController,
    ready_duration: float = 0.7,
    off_duration: float = 0.8,
) -> None:
    """Prepare robot poses before a speaking turn.

    Speaker:
    - return to normal face/head/antenna pose

    Non-speakers:
    - move to power-off-like pose
    """

    for controller in controllers:
        if controller is speaker:
            controller.queue_speaking_ready_pose(duration=ready_duration)
        else:
            controller.queue_power_off_pose(duration=off_duration)

    # Wait until the face/head/antennas are back before starting the next motion.
    await asyncio.sleep(max(ready_duration, off_duration) + 0.25)


async def connect_robots(
    *configs: Any,
    use_media: bool = False,
) -> tuple[RobotController, ...]:
    """Start robot subprocesses simultaneously.

    Works with 2 robots or 3 robots.
    """
    controllers = [
        RobotController(
            config.host,
            config.name,
            config.role,
            use_media=use_media,
        )
        for config in configs
    ]

    loop = asyncio.get_running_loop()

    await asyncio.gather(
        *[
            loop.run_in_executor(None, controller.start)
            for controller in controllers
        ]
    )

    return tuple(controllers)