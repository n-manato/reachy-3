"""Callback-based WAV audio-level follower.

Adapted from video_window.AudioLevelFollower, but decoupled from any
specific widget: it reads the currently-playing turn's WAV file and pushes a
0.0-1.0 level to an arbitrary callback. The mini displays use this to drive a
subtle audio-reactive accent line while a robot speaks.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.io import wavfile

from PySide6.QtCore import QObject, QTimer


class WavAudioLevelFollower(QObject):
    """Drive a level callback from the currently playing robot WAV file."""

    def __init__(self, on_level: Callable[[float], None]):
        super().__init__()

        self._on_level = on_level
        self.audio: np.ndarray | None = None
        self.sample_rate: int | None = None
        self.start_time: float | None = None

        self.timer = QTimer()
        self.timer.timeout.connect(self._update_level)

    def start(self, audio_path: str | Path) -> None:
        path = Path(audio_path)

        if not path.exists():
            print(f"  WARNING: Level audio file not found: {path}")
            self._on_level(0.4)
            return

        sample_rate, data = wavfile.read(path)

        if data.ndim > 1:
            data = data.mean(axis=1)

        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        else:
            data = data.astype(np.float32)

        self.audio = data
        self.sample_rate = int(sample_rate)
        self.start_time = time.monotonic()

        self.timer.start(16)

    def stop(self) -> None:
        self.timer.stop()
        self.audio = None
        self.sample_rate = None
        self.start_time = None
        self._on_level(0.0)

    def _update_level(self) -> None:
        if self.audio is None or self.sample_rate is None or self.start_time is None:
            return

        elapsed = time.monotonic() - self.start_time
        center = int(elapsed * self.sample_rate)

        window_size = int(self.sample_rate * 0.045)

        start = max(0, center - window_size // 2)
        end = min(len(self.audio), center + window_size // 2)

        if start >= end:
            self.stop()
            return

        chunk = self.audio[start:end]
        rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))

        level = min(1.0, rms * 10.0)

        if level > 0.01:
            level = max(level, 0.12)

        self._on_level(level)
