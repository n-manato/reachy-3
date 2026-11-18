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

WORKER_SCRIPT_NAME = "robot_worker.py"

# Minimum gap between attempts to revive a dead worker. A restart costs up to
# eight seconds in stop() alone, and commands are sent tens of times a second
# during speech sway -- without this, one robot that will not come back turns
# every subsequent command into a multi-second stall and freezes the show.
RESTART_COOLDOWN_SECS = 20.0


def kill_stale_workers(grace: float = 3.0) -> int:
    """Kill leftover ``robot_worker.py`` processes from a previous crashed run.

    A clean shutdown stops these subprocesses, but if the previous
    ethics-podcast run crashed or was force-killed, orphaned workers can
    linger — still holding the robots' WebSocket/media sessions and the
    local audio device, which makes the next run's connection flaky. Sweep
    them before spawning fresh workers.

    Processes that are descendants of THIS process are never touched, so
    workers we already own are safe even if this is called more than once.

    Returns the number of stale workers terminated.
    """
    try:
        import psutil
    except Exception as e:  # noqa: BLE001 - psutil should be installed; degrade gracefully
        logger.warning("psutil unavailable (%s); skipping stale-worker sweep.", e)
        return 0

    me = psutil.Process()
    own_pids = {me.pid}
    try:
        own_pids |= {child.pid for child in me.children(recursive=True)}
    except psutil.Error:
        pass

    def _is_worker(proc) -> bool:
        # The worker is launched as: <python> <.../robot_worker.py> <host> ...
        # Match ONLY that shape — a Python interpreter running robot_worker.py
        # as its script argument (argv[1]). A loose substring search over the
        # whole command line is dangerous: it also matches a `py_compile
        # robot_worker.py`, a grep of the file, or the very shell that launched
        # this tool, and would kill them.
        name = (proc.info.get("name") or "").lower()
        if not name.startswith("python"):
            return False
        cmdline = proc.info.get("cmdline") or []
        return (
            len(cmdline) >= 2
            and os.path.basename(str(cmdline[1])) == WORKER_SCRIPT_NAME
        )

    victims = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid in own_pids:
                continue
            if _is_worker(proc):
                victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not victims:
        return 0

    for proc in victims:
        try:
            proc.terminate()
        except psutil.Error:
            pass

    gone, alive = psutil.wait_procs(victims, timeout=grace)
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass

    logger.warning(
        "Swept %d stale %s process(es) from a previous run.",
        len(victims),
        WORKER_SCRIPT_NAME,
    )
    return len(victims)


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
        self._process: subprocess.Popen | None = None
        self._output_rate = 16000
        self._audio_ready = False
        self._restart_attempts = 0
        self._last_reported_exit_pid: int | None = None
        self._last_restart_attempt = 0.0
        # The most recent speech-sway offsets, so a caller can ease them back
        # to zero instead of letting them snap (see player.fade_to_rest).
        self.last_speech_offsets: tuple[float, ...] = (0.0,) * 6

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
            self._last_reported_exit_pid = None

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

    def diagnostic_status(self) -> str:
        """Return a compact status string for timeline diagnostics."""
        if self._process is None:
            return f"{self.role}:no-process"

        code = self._process.poll()
        if code is None:
            return f"{self.role}:alive pid={self._process.pid}"

        return f"{self.role}:exited pid={self._process.pid} code={code}"

    def restart(self) -> None:
        """Restart the worker subprocess after a connection loss."""
        logger.warning("Restarting robot %s worker...", self.role)

        try:
            self.stop()
        except Exception as e:
            logger.warning("Error while stopping robot %s worker: %s", self.role, e)

        self._restart_attempts += 1

        # Small fixed backoff so a flaky robot recovers fast without a long
        # on-screen freeze. The reconnect itself already takes ~4s, so an
        # escalating backoff (previously up to 8s) only made repeated drops
        # look worse without helping the daemon recover.
        time.sleep(1.0)

        self.start()

        logger.info("Robot %s worker restarted.", self.role)

    def ensure_alive(self) -> None:
        """Restart the worker if it has exited.

        Never raises. A robot that refuses to come back must not take the whole
        exhibit down with it -- this used to propagate out of start() and kill a
        running show. Commands to a dead worker are dropped by _send(), so the
        performance carries on with that robot simply not moving, and a retry
        happens on the next command after the cooldown.
        """
        if not self.is_alive():
            if self._process is not None:
                pid = self._process.pid
                code = self._process.poll()
                if self._last_reported_exit_pid != pid:
                    logger.error(
                        "Robot %s worker is not alive before command "
                        "(pid=%s, exit_code=%s).",
                        self.role,
                        pid,
                        code,
                    )
                    self._last_reported_exit_pid = pid

            now = time.monotonic()
            if now - self._last_restart_attempt < RESTART_COOLDOWN_SECS:
                return
            self._last_restart_attempt = now

            try:
                self.restart()
            except Exception as exc:
                logger.error(
                    "Robot %s could not be restarted (%s). Continuing without it; "
                    "retrying in %.0fs.",
                    self.role,
                    exc,
                    RESTART_COOLDOWN_SECS,
                )

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
        self.last_speech_offsets = (x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad)
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
        clear_first: bool = False,
    ) -> None:
        """Queue a smooth goto-pose move.

        head is in degrees:
        x, y, z, roll, pitch, yaw

        clear_first replaces whatever is currently playing, instead of waiting
        in line behind it.
        """
        self._send(
            "queue_goto",
            {
                "head": head,
                "antennas": antennas,
                "duration": duration,
                "clear_first": clear_first,
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
        "zuri": 0.0,
        "kora": 1.4,
        "pulaar": 2.7,
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
        await asyncio.sleep(0.12)

    # Wait until the face/head/antennas are back before starting the next motion.
    await asyncio.sleep(max(ready_duration, off_duration) + 0.25)


async def connect_robots(
    *configs: Any,
    use_media: bool = False,
    allow_partial: bool = True,
    disabled_roles: "frozenset[str] | set[str] | None" = None,
) -> tuple[RobotController, ...]:
    """Start robot subprocesses simultaneously.

    Works with 2 robots or 3 robots. When ``allow_partial`` is true, robots
    that fail to connect are replaced with MockRobot so the exhibit can still
    run with the robots that are available.

    ``disabled_roles`` names roles that have no physical robot (2-robot mode).
    Those are never contacted: a MockRobot stands in from the start, so there is
    no connection attempt, timeout, or mid-show reconnect churn for the absent
    unit. The returned tuple stays in the same order as ``configs`` (so callers
    that unpack robot_a, robot_b, robot_c keep working), with a MockRobot in the
    disabled position.
    """
    disabled_roles = frozenset(disabled_roles or ())

    from boma_exhibit.mock_robot import MockRobot

    # Clear out any orphaned workers from a previous crashed run before we
    # spawn fresh ones, so they cannot fight over the robots' sessions.
    kill_stale_workers()

    # Only build/start real controllers for active roles; disabled roles get a
    # MockRobot placeholder without ever spawning a worker.
    controllers: list[Any] = []
    to_start: list[RobotController] = []
    for config in configs:
        if getattr(config, "role", None) in disabled_roles:
            controllers.append(
                MockRobot(getattr(config, "role", "unknown"), getattr(config, "name", None))
            )
        else:
            controller = RobotController(
                config.host,
                config.name,
                config.role,
                use_media=use_media,
            )
            controllers.append(controller)
            to_start.append(controller)

    loop = asyncio.get_running_loop()

    results = await asyncio.gather(
        *[
            loop.run_in_executor(None, controller.start)
            for controller in to_start
        ],
        return_exceptions=allow_partial,
    )

    # Map each started controller back to its start() result.
    result_by_controller = dict(zip(to_start, results))

    if not allow_partial:
        return tuple(controllers)

    active = []
    for config, controller in zip(configs, controllers):
        # Disabled-role placeholders (already MockRobot) pass through untouched.
        if not isinstance(controller, RobotController):
            active.append(controller)
            continue

        result = result_by_controller.get(controller)
        if isinstance(result, Exception):
            logger.warning(
                "Robot %s (%s) could not connect: %s. Using mock fallback.",
                config.role,
                getattr(config, "host", "?"),
                result,
            )
            try:
                controller.stop()
            except Exception:
                pass

            from boma_exhibit.mock_robot import MockRobot

            active.append(MockRobot(config.role, getattr(config, "name", None)))
        else:
            active.append(controller)

    connected = sum(isinstance(robot, RobotController) for robot in active)
    if connected < len(active):
        logger.warning(
            "Running with %d real robot(s) and %d mock fallback(s).",
            connected,
            len(active) - connected,
        )

    return tuple(active)
