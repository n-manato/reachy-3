"""MiniDisplayWindow — the small screen mounted under each robot.

States:
  - speaking : large subtitle + speaker name + audio-reactive accent line
  - idle     : content fades down (NOT fully black) — a calm dim screen
  - gaze     : a GazeFlowWidget streams toward the next speaking robot,
               layered over the dimmed idle background

A single QApplication pumps events for all windows, so animations here are
driven by lightweight QTimers / QPropertyAnimation.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt, QPropertyAnimation, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ethics_podcast.displays import theme
from ethics_podcast.displays.audio_level import WavAudioLevelFollower
from ethics_podcast.displays.gaze_flow import GazeFlowWidget


# Opacity the content fades to while idle. Never 0 — the screen stays "alive".
IDLE_OPACITY = 0.22
SPEAKING_OPACITY = 1.0


class AudioLine(QWidget):
    """A thin horizontal accent line whose glow scales with audio level."""

    def __init__(self, accent_rgb: tuple[int, int, int]):
        super().__init__()
        self._level = 0.0
        self._accent = accent_rgb
        self.setMinimumHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_accent(self, rgb: tuple[int, int, int]) -> None:
        self._accent = rgb
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cy = h / 2

        r, g, b = self._accent

        # Base track.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, 35))
        painter.drawRoundedRect(0, int(cy - 1), w, 2, 1, 1)

        # Active center bar scaled by level.
        bar_w = int(w * (0.12 + 0.88 * self._level))
        bar_x = int((w - bar_w) / 2)
        thickness = int(3 + 7 * self._level)

        painter.setBrush(QColor(r, g, b, 220))
        painter.drawRoundedRect(
            bar_x,
            int(cy - thickness / 2),
            bar_w,
            thickness,
            thickness / 2,
            thickness / 2,
        )


class MiniDisplayWindow(QWidget):
    def __init__(self, role: str):
        super().__init__()

        self.role = role
        self.accent = theme.role_accent(role)
        self.accent_rgb = theme.role_accent_rgb(role)

        self.setWindowTitle(f"Mini Display — {theme.ROLE_NAMES.get(role, role)}")

        # ── Content container (faded as a whole while idle) ──────────────────
        self.content = QWidget(self)
        self.content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(60, 48, 60, 48)
        layout.setSpacing(20)

        self.speaker_label = QLabel("")
        self.speaker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speaker_label.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 30px;
            font-weight: 800;
            color: {self.accent};
            letter-spacing: 2px;
            """
        )

        self.subtitle_label = QLabel("")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.subtitle_label.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 46px;
            font-weight: 700;
            color: {theme.INK};
            line-height: 1.3;
            """
        )

        self.audio_line = AudioLine(self.accent_rgb)

        layout.addWidget(self.speaker_label)
        layout.addWidget(self.subtitle_label, stretch=1)
        layout.addWidget(self.audio_line)

        # Opacity effect for idle fade.
        self._opacity = QGraphicsOpacityEffect(self.content)
        self._opacity.setOpacity(IDLE_OPACITY)
        self.content.setGraphicsEffect(self._opacity)

        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(700)

        # ── Gaze flow overlay ────────────────────────────────────────────────
        self.gaze = GazeFlowWidget(self)
        self.gaze.set_accent(self.accent_rgb)
        self.gaze.hide()

        # ── Audio follower drives the accent line ────────────────────────────
        self.follower = WavAudioLevelFollower(self.audio_line.set_level)

        # Ambient idle pulse so the dim screen still feels alive.
        self._phase = 0.0
        self._ambient = QTimer(self)
        self._ambient.timeout.connect(self._tick_ambient)
        self._ambient.start(40)

        self._state = "idle"
        self.set_idle(animate=False)

    # ── Geometry ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        self.content.setGeometry(self.rect())
        self.gaze.setGeometry(self.rect())
        super().resizeEvent(event)

    # ── Background paint (soft white→light-blue) ─────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(theme.WHITE))
        bg.setColorAt(1.0, QColor(theme.SNOW))
        painter.fillRect(self.rect(), bg)

        # Subtle ambient glow that breathes (alpha follows _phase).
        breathe = (math.sin(self._phase) + 1.0) / 2.0
        r, g, b = self.accent_rgb
        alpha = int(10 + 22 * breathe)

        glow = QLinearGradient(0, h, w, 0)
        glow.setColorAt(0.0, QColor(r, g, b, alpha))
        glow.setColorAt(1.0, QColor(r, g, b, 0))
        painter.fillRect(self.rect(), glow)

    def _tick_ambient(self) -> None:
        self._phase += 0.05
        if self._phase > math.tau:
            self._phase -= math.tau
        # Only repaint background when no heavier overlay is animating.
        if self._state != "gaze":
            self.update()

    # ── State transitions ────────────────────────────────────────────────────

    def _fade_to(self, target: float, animate: bool = True) -> None:
        self._fade.stop()
        if animate:
            self._fade.setStartValue(self._opacity.opacity())
            self._fade.setEndValue(target)
            self._fade.start()
        else:
            self._opacity.setOpacity(target)

    def show_subtitle(self, text: str, audio_path: str | Path | None = None) -> None:
        """Robot is speaking: bright subtitle + audio-reactive line."""
        self._state = "speaking"
        self.gaze.stop()

        self.speaker_label.setText(theme.ROLE_NAMES.get(self.role, self.role).upper())
        self.subtitle_label.setText(text)

        self._fade_to(SPEAKING_OPACITY)

        if audio_path is not None:
            self.follower.start(audio_path)
        else:
            self.audio_line.set_level(0.4)

        self.update()

    def set_idle(self, animate: bool = True) -> None:
        """Robot is not speaking: fade down, but never fully black."""
        self._state = "idle"
        self.follower.stop()
        self.gaze.stop()
        self.audio_line.set_level(0.0)
        # Keep the speaker name + last words faintly visible under the fade.
        self._fade_to(IDLE_OPACITY, animate=animate)
        self.update()

    def show_gaze(self, direction: int) -> None:
        """Turn finished: stream gaze guidance toward the next robot.

        direction: -1 flow left, +1 flow right, 0 no clear direction.
        """
        self._state = "gaze"
        self.follower.stop()
        self.audio_line.set_level(0.0)
        self._fade_to(IDLE_OPACITY)

        if direction == 0:
            self.gaze.stop()
            return

        self.gaze.set_direction(float(direction), 0.0)
        self.gaze.setGeometry(self.rect())
        self.gaze.start()
        self.gaze.raise_()
