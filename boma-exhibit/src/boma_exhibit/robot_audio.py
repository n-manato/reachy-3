"""Continuous audio streaming to the robots' own speakers.

Each Reachy Mini has a single audio output, so "music from every robot, speech
from whoever is talking" cannot be two separate pushes -- they have to be summed
into one stream before they leave this process. That is what this module does:
one pump per robot, mixing a looping music bed with that robot's speech, paced
in real time.

Three things it has to get right:

  pacing   push_audio is fire-and-forget into a WebRTC buffer. Pushing as fast
           as the pipe accepts would grow latency without bound; pushing exactly
           in real time would underrun on the first scheduling hiccup. So each
           pump keeps a fixed lead of buffered audio and paces against a virtual
           clock rather than accumulating sleeps.

  sync     three robots playing the same loop will drift apart if each counts
           its own chunks. They all read the music from a position derived from
           one shared start time, so a late pump skips forward rather than
           falling behind.

  rate     the robots run at 16 kHz while everything else here is 24 kHz. The
           music is resampled once at load; speech once per turn. Handing the
           worker audio already at its own rate keeps scipy out of the hot path.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy.signal import resample

logger = logging.getLogger(__name__)

CHUNK_SECS = 0.25       # how much audio each push carries
LEAD_SECS = 0.75        # how far ahead of the speakers we stay
DUCK = 0.30             # music level while this robot is speaking
DUCK_ATTACK = 0.25      # seconds to dip
DUCK_RELEASE = 1.40     # seconds to come back
FADE_SECS = 0.70        # start-of-show fade in, stop fade out
MUSIC_FADE_IN = 3.0


def load_music(path: Path, rate: int) -> np.ndarray:
    """Decode a music file to mono float32 at `rate`."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
        capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float32)


def _ramp(current: float, target: float, secs: float, dt: float) -> float:
    """Move `current` toward `target`, covering the full span in `secs`."""
    if secs <= 0:
        return target
    step = dt / secs
    if target > current:
        return min(target, current + step)
    return max(target, current - step)


class _Pump:
    """One robot's audio stream."""

    def __init__(self, robot, music: np.ndarray | None, rate: int) -> None:
        self.robot = robot
        self.rate = rate
        self.music = music
        self.chunk = max(1, int(CHUNK_SECS * rate))

        self.speech: np.ndarray | None = None
        self.speech_pos = 0
        self.music_gain = 0.0          # ramps up when the show starts
        self.master = 1.0              # ramped down on stop
        self._target_music = 0.0

    def speak(self, pcm: np.ndarray) -> None:
        self.speech = pcm
        self.speech_pos = 0

    def silence_speech(self) -> None:
        self.speech = None
        self.speech_pos = 0

    @property
    def speaking(self) -> bool:
        return self.speech is not None and self.speech_pos < len(self.speech)

    def render(self, music_pos: int, music_level: float) -> np.ndarray:
        """Build the next chunk: bed plus this robot's speech, if any."""
        n = self.chunk
        out = np.zeros(n, dtype=np.float32)

        if self.music is not None and len(self.music) and music_level > 0:
            idx = (np.arange(music_pos, music_pos + n)) % len(self.music)
            out += self.music[idx] * music_level

        if self.speaking:
            take = self.speech[self.speech_pos : self.speech_pos + n]
            out[: len(take)] += take
            self.speech_pos += n
            if self.speech_pos >= len(self.speech):
                self.speech = None

        return np.clip(out * self.master, -1.0, 1.0)

    # NOTE: the operator's level is applied by the caller, after this returns,
    # so that a slider move takes effect on the very next chunk.


class RobotAudioMixer:
    """Drives every robot's speaker: one looping bed, speech where it belongs."""

    def __init__(
        self,
        robots: dict,
        music_path: Path | None,
        volume: "callable | None" = None,
    ) -> None:
        # Read afresh every chunk rather than captured once, so the slider on
        # the display moves the robots' speakers while they are talking.
        self._volume = volume or (lambda: 1.0)
        self._pumps: dict[str, _Pump] = {}
        self._music_cache: dict[int, np.ndarray] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._started = 0.0
        self._fading = False

        for role, robot in robots.items():
            if not getattr(robot, "audio_ready", False):
                logger.info("Robot %s has no audio; it will stay silent.", role)
                continue
            rate = int(getattr(robot, "output_sample_rate", 16000))
            music = None
            if music_path is not None:
                if rate not in self._music_cache:
                    self._music_cache[rate] = load_music(music_path, rate)
                music = self._music_cache[rate]
            self._pumps[role] = _Pump(robot, music, rate)

    @property
    def active(self) -> bool:
        return bool(self._pumps)

    def speak(self, role: str, pcm: np.ndarray, sample_rate: int) -> None:
        """Hand one turn's audio to the robot that is saying it."""
        pump = self._pumps.get(role)
        if pump is None:
            return
        if sample_rate != pump.rate:
            pcm = resample(pcm, int(len(pcm) * pump.rate / sample_rate)).astype(np.float32)
        pump.speak(np.asarray(pcm, dtype=np.float32))

    def start(self) -> None:
        if self._task is not None or not self._pumps:
            return
        self._stop.clear()
        self._fading = False
        self._started = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def begin_fade(self) -> None:
        """Start fading out without awaiting anything.

        Safe to call from a cancelled task's finally block, where an await can
        re-raise before the rest of the cleanup runs. The pump notices the fade,
        rides the level down and retires itself.
        """
        self._fading = True

    async def stop(self, fade: float = FADE_SECS) -> None:
        """Fade every speaker out, then let the buffered tail run dry."""
        if self._task is None:
            return
        self.begin_fade()
        await asyncio.sleep(fade + LEAD_SECS)
        self._stop.set()
        task, self._task = self._task, None
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    async def _run(self) -> None:
        """Push chunks, staying a fixed lead ahead of real time."""
        pushed = {role: 0 for role in self._pumps}
        # One clock for every robot, so a pump that runs late skips forward
        # instead of drifting behind the others.
        started = time.monotonic()

        while not self._stop.is_set():
            now = time.monotonic() - started
            for role, pump in self._pumps.items():
                due = pushed[role] / pump.rate
                if due > now + LEAD_SECS:
                    continue

                elapsed = pushed[role] / pump.rate
                dt = CHUNK_SECS

                # Music comes up once at the start and ducks under speech.
                target = DUCK if pump.speaking else 1.0
                pump._target_music = target
                pump.music_gain = _ramp(
                    pump.music_gain, target,
                    DUCK_ATTACK if target < pump.music_gain else
                    (MUSIC_FADE_IN if elapsed < MUSIC_FADE_IN else DUCK_RELEASE),
                    dt,
                )
                if self._fading:
                    pump.master = _ramp(pump.master, 0.0, FADE_SECS, dt)

                try:
                    level = max(0.0, min(1.0, float(self._volume())))
                except Exception:
                    level = 1.0
                chunk = pump.render(pushed[role], pump.music_gain) * level
                try:
                    pump.robot.push_audio(chunk, source_rate=pump.rate)
                except Exception as exc:
                    logger.debug("push to %s failed: %s", role, exc)
                pushed[role] += len(chunk)

            if self._fading and all(p.master <= 0.0 for p in self._pumps.values()):
                # Faded to nothing. Keep pushing silence just long enough to
                # cover the lead already sitting in the robots' buffers, then
                # retire -- otherwise the tail of the last chunk would repeat.
                await asyncio.sleep(LEAD_SECS)
                break

            await asyncio.sleep(CHUNK_SECS / 3)
