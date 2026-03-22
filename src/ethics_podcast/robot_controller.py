"""Robot controller using fully independent subprocesses (subprocess.Popen).

Each robot runs in a completely separate Python process with its own GStreamer
state and a 100Hz control loop. Communication is via stdin pipe (length-prefixed
pickled commands).

IMPORTANT: Do not change the subprocess launch pattern. Running the worker as a
direct script via subprocess.Popen is the ONLY proven way to get WebRTC audio
working on both robots simultaneously on macOS. Do NOT use multiprocessing.Process
or python -m module invocation — both break WebRTC.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import struct
import subprocess
import sys
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OPENAI_SAMPLE_RATE = 24000


class RobotController:
    """Proxy that sends high-level commands to a robot subprocess.

    The subprocess runs a 100Hz control loop that handles:
    - Primary moves (emotions, dances, goto) via move queue
    - Secondary offsets (speech sway) layered on top
    - Auto-breathing when idle
    - Single set_target() call per tick with proper pose composition

    This proxy only sends lightweight commands over the pipe — no raw set_target
    calls, which avoids WebSocket flooding.
    """

    def __init__(self, host: str, name: str | None, role: str):
        self.host = host
        self.name = name
        self.role = role
        self._process: subprocess.Popen | None = None
        self._output_rate: int = 0
        self._audio_ready: bool = False

    def start(self) -> None:
        """Launch the worker subprocess and wait for READY signal."""
        env = {**os.environ}

        worker_path = os.path.join(os.path.dirname(__file__), "robot_worker.py")
        cmd = [sys.executable, worker_path, self.host, self.role]
        if self.name:
            cmd.append(self.name)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
        )

        logger.info("Subprocess for %s started (pid=%d). Waiting for connection...", self.role, self._process.pid)

        ready_line = self._process.stdout.readline().decode().strip()
        if ready_line.startswith("READY"):
            parts = ready_line.split()
            self._output_rate = int(parts[1]) if len(parts) > 1 else 16000
            self._audio_ready = parts[2] == "True" if len(parts) > 2 else False
            logger.info("Robot %s ready (output_rate=%d, audio=%s).", self.role, self._output_rate, self._audio_ready)
        else:
            raise RuntimeError(f"Robot {self.role} failed to start. Got: {ready_line}")

    def stop(self) -> None:
        """Shut down the subprocess."""
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write(struct.pack("<I", 0))
                self._process.stdin.flush()
            except Exception:
                pass
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.terminate()
            logger.info("Robot %s subprocess stopped.", self.role)

    def _send(self, action: str, kwargs: dict) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        data = pickle.dumps((action, kwargs))
        try:
            self._process.stdin.write(struct.pack("<I", len(data)) + data)
            self._process.stdin.flush()
        except BrokenPipeError:
            logger.warning("Worker %s pipe broken.", self.role)

    @property
    def output_sample_rate(self) -> int:
        return self._output_rate

    @property
    def audio_ready(self) -> bool:
        return self._audio_ready

    # ── Audio ──────────────────────────────────────────────────────────────

    def push_audio(self, pcm_float32: np.ndarray, source_rate: int = OPENAI_SAMPLE_RATE) -> None:
        self._send("push_audio", {"pcm_float32": pcm_float32, "source_rate": source_rate})

    # ── High-level motion commands (processed by worker's 100Hz loop) ─────

    def set_speech_offsets(self, x_m: float, y_m: float, z_m: float, roll_rad: float, pitch_rad: float, yaw_rad: float) -> None:
        """Set speech sway secondary offsets (meters for xyz, radians for rpy)."""
        self._send("set_speech_offsets", {"offsets": (x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad)})

    def queue_emotion(self, emotion_name: str) -> None:
        """Queue a recorded emotion to play through the move queue."""
        self._send("queue_emotion", {"emotion": emotion_name})

    def queue_dance(self, dance_name: str) -> None:
        """Queue a dance to play through the move queue."""
        self._send("queue_dance", {"dance_name": dance_name})

    def queue_goto(self, head: tuple[float, ...], antennas: tuple[float, float] = (0, 0), duration: float = 1.0) -> None:
        """Queue a smooth goto-pose move (head in degrees: x,y,z,roll,pitch,yaw)."""
        self._send("queue_goto", {"head": head, "antennas": antennas, "duration": duration})

    def clear_moves(self) -> None:
        """Stop current move and clear the queue."""
        self._send("clear_moves", {})

    def wiggle_antennas(self) -> None:
        self._send("wiggle", {})


async def connect_robots(
    config_a: Any,
    config_b: Any,
) -> tuple[RobotController, RobotController]:
    """Start both robot subprocesses simultaneously."""
    ctrl_a = RobotController(config_a.host, config_a.name, config_a.role)
    ctrl_b = RobotController(config_b.host, config_b.name, config_b.role)

    loop = asyncio.get_running_loop()
    await asyncio.gather(
        loop.run_in_executor(None, ctrl_a.start),
        loop.run_in_executor(None, ctrl_b.start),
    )
    return ctrl_a, ctrl_b
