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
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OPENAI_SAMPLE_RATE = 24000

WORKER_SCRIPT_NAME = "robot_worker.py"

# Linux guarantees writes up to PIPE_BUF (4096) bytes to a pipe are atomic.
PIPE_ATOMIC_BYTES = 4096

# The Reachy Mini daemon's HTTP API port (same as robot_worker.DAEMON_PORT).
DAEMON_PORT = 8000

_http_session = None


def _http():
    """A requests session that ignores proxy settings.

    A campus/WinINET proxy would otherwise capture calls to the robot LAN.
    """
    global _http_session
    if _http_session is None:
        import requests

        _http_session = requests.Session()
        _http_session.trust_env = False
    return _http_session

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

        # HTTP sound playback on the robot's own speaker (see play_sound_file).
        self._uploaded_sounds: set[str] = set()
        self._http_lock = threading.Lock()
        self._abort_http_audio = False
        self._sound_started = False
        self._media_acquired = False
        self._restarting = False
        self._closing = False
        self._dropped = 0
        self._revive_thread: threading.Thread | None = None
        # Bumped by stop_sound(); a play request issued before a stop is dropped.
        self.sound_generation = 0

    def start(self, full_reset: bool = True) -> None:
        """Launch the worker subprocess and wait for READY signal.

        ``full_reset=False`` skips the daemon backend rebuild -- used when
        reviving a worker mid-show, where speed matters more than a pristine
        backend.
        """
        env = {**os.environ}
        if not full_reset:
            env["ROBOT_FULL_RESET_ON_START"] = "0"

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
        try:
            # See _write_raw: commands must never block the asyncio thread.
            os.set_blocking(self._process.stdin.fileno(), False)
        except (OSError, AttributeError) as exc:  # pragma: no cover - non-Linux
            logger.warning("Robot %s: could not make the command pipe non-blocking: %s", self.role, exc)

        logger.info(
            "Subprocess for %s started (pid=%d). Waiting for connection...",
            self.role,
            self._process.pid,
        )

        if self._process.stdout is None:
            raise RuntimeError(f"Robot {self.role} failed to start. No stdout pipe.")

        ready_line = self._process.stdout.readline().decode(errors="replace").strip()

        if ready_line.startswith("READY"):
            # A no_media SDK connect releases the daemon's media again, so
            # the next robot-speaker line must re-acquire it.
            self._media_acquired = False
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
        """Shut down for good: no revival may start a worker after this."""
        self._closing = True
        reviver = self._revive_thread
        if reviver is not None and reviver.is_alive():
            # A revival in progress owns self._process; give it a moment, then
            # stop whatever it has started (its start() fails on our EOF).
            reviver.join(timeout=8)
        self._stop_process()

    def _stop_process(self) -> None:
        """Shut down the current worker subprocess (it may be revived later)."""
        if self._process is None:
            return

        if self._process.poll() is None:
            try:
                self._write_raw(struct.pack("<I", 0))
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
            self._stop_process()
        except Exception as e:
            logger.warning("Error while stopping robot %s worker: %s", self.role, e)

        self._restart_attempts += 1

        # Small fixed backoff so a flaky robot recovers fast without a long
        # on-screen freeze. The reconnect itself already takes ~4s, so an
        # escalating backoff (previously up to 8s) only made repeated drops
        # look worse without helping the daemon recover.
        time.sleep(1.0)

        self.start(full_reset=False)

        logger.info("Robot %s worker restarted.", self.role)

    def ensure_alive(self) -> None:
        """Schedule a revival if the worker has exited. Never blocks, never raises.

        Reviving a worker means waiting for its READY line, which can take many
        seconds. That used to happen inline -- on the asyncio thread, from the
        50 Hz speech-sway loop -- freezing the screens, the votes and every
        other robot until it finished. Now it runs in a background thread, at
        most once per RESTART_COOLDOWN_SECS, and commands sent meanwhile are
        simply dropped: the show carries on with that robot standing still.
        """
        if self._closing or self._restarting or self.is_alive():
            return
        if self._process is not None:
            pid = self._process.pid
            if self._last_reported_exit_pid != pid:
                logger.error(
                    "Robot %s worker is not alive (pid=%s, exit_code=%s).",
                    self.role, pid, self._process.poll(),
                )
                self._last_reported_exit_pid = pid
        now = time.monotonic()
        if now - self._last_restart_attempt < RESTART_COOLDOWN_SECS:
            return
        self._last_restart_attempt = now
        self._restarting = True

        def revive() -> None:
            try:
                if self._closing:
                    return
                self.restart()
                if self._closing:
                    # stop() was called while we were starting: undo.
                    self._stop_process()
                    return
                # The fresh no_media connect released the daemon's media; take
                # it back now rather than inside the next line's 2 s budget.
                self.ensure_media()
            except Exception as exc:
                logger.error(
                    "Robot %s could not be restarted (%s). Continuing without it; "
                    "retrying in %.0fs.", self.role, exc, RESTART_COOLDOWN_SECS,
                )
            finally:
                self._restarting = False
            # Commands are dropped while _restarting, so a volume change made
            # during the revival never reached the worker: send the latest.
            volume = getattr(self, "volume", None)
            if volume is not None and not self._closing and self.is_alive():
                self._send("set_volume", {"volume": volume})

        self._revive_thread = threading.Thread(target=revive, name=f"revive-{self.role}", daemon=True)
        self._revive_thread.start()

    def _send(self, action: str, kwargs: dict) -> None:
        """Send a command to the worker; drop it if the worker is down."""
        if self._restarting:
            return
        if not self.is_alive():
            self.ensure_alive()
            return
        data = pickle.dumps((action, kwargs))
        try:
            self._write_raw(struct.pack("<I", len(data)) + data)
        except BlockingIOError:
            # The worker is alive but not reading (hung). Drop the command
            # rather than freeze the event loop -- and with it the screens,
            # the votes and the other two robots.
            self._dropped += 1
            if self._dropped in (1, 100) or self._dropped % 1000 == 0:
                logger.warning("Robot %s worker not reading; dropped %d command(s).",
                               self.role, self._dropped)
        except (BrokenPipeError, OSError, AttributeError, ValueError) as e:
            logger.warning("Command %s failed for robot %s: %s", action, self.role, e)
            self.ensure_alive()

    def _write_raw(self, payload: bytes) -> None:
        """One write to the worker's stdin that never blocks.

        The pipe is non-blocking (set in start()). Writes of at most PIPE_BUF
        bytes are atomic on a pipe -- all or nothing -- so a full pipe raises
        BlockingIOError and never leaves half a frame behind. Larger payloads
        (audio, unused in this exhibit) are dropped rather than risk it.
        """
        process = self._process
        if process is None or process.stdin is None:
            raise BrokenPipeError("no worker")
        if len(payload) > PIPE_ATOMIC_BYTES:
            raise BlockingIOError("payload larger than an atomic pipe write")
        os.write(process.stdin.fileno(), payload)

    @property
    def output_sample_rate(self) -> int:
        return self._output_rate

    @property
    def audio_ready(self) -> bool:
        return self._audio_ready

    # ── HTTP file playback on the robot speaker ────────────────────────────
    #
    # This PC cannot stream audio to the robots over WebRTC (GStreamer 1.16 has
    # no webrtcsrc). The daemon can still play a WAV uploaded to it over plain
    # HTTP, through its own player: POST /api/media/sounds/upload, then
    # /api/media/play_sound. Ported from the Ethics Podcast exhibit.
    #
    # The worker connects with media_backend="no_media", which makes the SDK
    # release the daemon's media; ensure_media() takes it back first.

    def _daemon_url(self, path: str) -> str:
        return f"http://{self.host}:{DAEMON_PORT}{path}"

    def ensure_media(self) -> bool:
        """Make sure the daemon holds its audio hardware. Best effort."""
        try:
            status = _http().get(self._daemon_url("/api/media/status"), timeout=2).json()
        except Exception as exc:
            logger.warning("Robot %s: media status unavailable: %s", self.role, exc)
            status = {}
        if status.get("no_media"):
            logger.warning("Robot %s: daemon reports no media hardware.", self.role)
            return False
        if status.get("released", True):
            try:
                _http().post(self._daemon_url("/api/media/acquire"), timeout=5).raise_for_status()
                logger.info("Robot %s: media re-acquired.", self.role)
            except Exception as exc:
                logger.warning("Robot %s: media acquire failed: %s", self.role, exc)
                return False
        self._media_acquired = True
        return True

    def _resync_uploaded_sounds(self) -> None:
        """Drop cache entries the daemon no longer has (e.g. after a reboot)."""
        if not self._uploaded_sounds:
            return
        try:
            response = _http().get(self._daemon_url("/api/media/sounds"), timeout=3)
            response.raise_for_status()
            present = set(response.json().get("files", []))
        except Exception:
            present = set()
        self._uploaded_sounds &= present

    @staticmethod
    def _remote_sound_name(path: Path) -> str:
        # The daemon's upload directory is flat; namespace by topic folder.
        parts = Path(path).parts[-3:]
        return re.sub(r"[^A-Za-z0-9._-]", "_", "__".join(parts))

    def upload_sound_file(self, path: Path) -> str | None:
        path = Path(path)
        name = self._remote_sound_name(path)
        if name in self._uploaded_sounds:
            return name
        try:
            with open(path, "rb") as fh:
                response = _http().post(
                    self._daemon_url("/api/media/sounds/upload"),
                    files={"file": (name, fh, "audio/wav")},
                    timeout=(3, 20),
                )
            response.raise_for_status()
        except Exception as e:
            logger.warning("Robot %s: sound upload failed for %s: %s", self.role, path.name, e)
            return None
        self._uploaded_sounds.add(name)
        return name

    def preload_sound_files(self, paths: list[Path]) -> int:
        """Upload several WAVs ahead of playback. Returns how many are ready."""
        if not self._media_acquired:
            self.ensure_media()
        self._resync_uploaded_sounds()
        with self._http_lock:
            self._abort_http_audio = False
            ready = 0
            for path in paths:
                if self._abort_http_audio:
                    break
                if self.upload_sound_file(path) is not None:
                    ready += 1
            return ready

    def play_sound_file(self, path: Path, generation: int | None = None) -> bool:
        """Play a WAV on the robot speaker through the daemon HTTP API.

        ``generation`` is ``sound_generation`` as read when the line was
        requested. If stop_sound() ran since then (staff pressed Pause while
        this call was still queued in its thread), nothing is played.
        """
        path = Path(path)
        if generation is None:
            generation = self.sound_generation
        if not self._media_acquired:
            self.ensure_media()
        with self._http_lock:
            self._abort_http_audio = False
            for _attempt in range(2):
                name = self.upload_sound_file(path)
                if name is None:
                    return False
                if generation != self.sound_generation:
                    return False
                # Marked BEFORE the request: if the reply is lost or late, a
                # stop_sound() must still be sent, or the robot would speak the
                # line on top of the screen's fallback copy.
                self._sound_started = True
                try:
                    response = _http().post(
                        self._daemon_url("/api/media/play_sound"),
                        json={"file": name},
                        timeout=(1.0, 1.5),
                    )
                    if generation != self.sound_generation:
                        # Stopped (or timed out by the player) while in flight:
                        # the daemon may have started it anyway, so stop it.
                        self._post_stop()
                        return False
                    if response.ok:
                        return True
                    logger.warning("Robot %s: play_sound %s -> HTTP %s",
                                   self.role, name, response.status_code)
                except Exception as e:
                    logger.warning("Robot %s: play_sound %s failed: %s", self.role, name, e)
                # Retry once: re-acquire media and re-upload (the daemon or the
                # worker may have restarted since the last line).
                self._uploaded_sounds.discard(name)
                self.ensure_media()
            return False

    def _post_stop(self) -> None:
        self._sound_started = False
        try:
            _http().post(self._daemon_url("/api/media/stop_sound"), timeout=1.5)
        except Exception:
            pass

    def stop_sound(self, wait: float = 0.0) -> None:
        """Best-effort stop of HTTP-triggered playback (Stop mid-line)."""
        self._abort_http_audio = True
        self.sound_generation += 1
        acquired = (self._http_lock.acquire(timeout=wait) if wait > 0
                    else self._http_lock.acquire(blocking=False))
        try:
            if not self._sound_started:
                return
            self._post_stop()
        finally:
            if acquired:
                self._http_lock.release()

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

    def set_volume(self, volume: int) -> None:
        """Speaker volume 0-100 (silent: no test sound). Persists on the robot."""
        self.volume = max(0, min(100, int(volume)))
        self._send("set_volume", {"volume": self.volume})

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

    from lta_exhibit.mock_robot import MockRobot

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

            from lta_exhibit.mock_robot import MockRobot

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
