"""TopDisplayWindow — the large portrait screen (1080×1980) wall-mounted above
the three robots.

Two pages (QStackedLayout):
  - page 0: embedded VLC video (same proven approach as ControlledVideoWindow)
  - page 1: presentation — huge topic text, a row of three robot markers with
            the current speaker highlighted, and a GazeFlowWidget streaming
            diagonally down toward the next robot that will speak.

VLC playback/seek/end-detection is ported from video_window.ControlledVideoWindow
so behaviour matches the existing single-window mode.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

try:
    import vlc
except Exception:  # pragma: no cover - allows UI-only runs without libVLC
    vlc = None

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtWidgets import QApplication

from ethics_podcast.displays import theme
from ethics_podcast.displays.gaze_flow import GazeFlowWidget
from ethics_podcast.input_utils import read_arrow_key_windows


VIDEO_VOLUME = 75


class RobotMarker(QFrame):
    """A small column marker representing one robot on the presentation page."""

    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.accent = theme.role_accent(role)
        self._active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.dot = QLabel("●")
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.name = QLabel(theme.ROLE_NAMES.get(role, role))
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setWordWrap(True)

        layout.addWidget(self.dot)
        layout.addWidget(self.name)

        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = active

        dot_color = self.accent if active else theme.LINE
        name_color = theme.INK if active else theme.INK_SOFT
        weight = 900 if active else 600
        size = 34 if active else 22

        self.dot.setStyleSheet(
            f"font-size: {size}px; color: {dot_color};"
        )
        self.name.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 20px;
            font-weight: {weight};
            color: {name_color};
            """
        )


class PresentationPage(QWidget):
    """Topic + robot markers + gaze flow toward next speaker."""

    def __init__(self):
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(80, 90, 80, 90)
        root.setSpacing(30)

        self.app_title = QLabel("AI ETHICS · LIVE")
        self.app_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.app_title.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 34px;
            font-weight: 900;
            color: {theme.ACCENT};
            letter-spacing: 6px;
            """
        )

        self.title_label = QLabel("")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 64px;
            font-weight: 900;
            color: {theme.INK};
            """
        )

        self.topic_label = QLabel("")
        self.topic_label.setWordWrap(True)
        self.topic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.topic_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.topic_label.setStyleSheet(
            f"""
            font-family: {theme.FONT_FAMILY};
            font-size: 52px;
            font-weight: 700;
            color: {theme.ACCENT_DEEP};
            line-height: 1.3;
            """
        )

        # Robot markers row (left → center → right).
        markers_row = QHBoxLayout()
        markers_row.setSpacing(40)
        self.markers: dict[str, RobotMarker] = {}

        for role in ("student", "professor", "moderator"):
            marker = RobotMarker(role)
            self.markers[role] = marker
            markers_row.addWidget(marker, stretch=1)

        markers_container = QWidget()
        markers_container.setLayout(markers_row)

        root.addWidget(self.app_title)
        root.addWidget(self.title_label)
        root.addWidget(self.topic_label, stretch=1)
        root.addWidget(markers_container)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(theme.WHITE))
        bg.setColorAt(1.0, QColor(theme.SNOW))
        painter.fillRect(self.rect(), bg)

        # Hairline frame for a clean, near-future look.
        r, g, b = theme.RGB_ACCENT
        painter.setPen(QPen(QColor(r, g, b, 40), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(28, 28, -28, -28), 28, 28)


class TopDisplayWindow(QWidget):
    def __init__(self, video_path: Path | None = None):
        self.app = QApplication.instance()

        super().__init__()

        self.setWindowTitle("Ethics Podcast — Top Display")
        self.setStyleSheet("background-color: black;")

        self.stack = QStackedLayout(self)

        # Page 0: VLC video frame.
        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_frame.setAttribute(
            Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True
        )
        self.stack.addWidget(self.video_frame)

        # Page 1: Presentation.
        self.presentation = PresentationPage()
        self.stack.addWidget(self.presentation)

        # Gaze overlay lives on the presentation page.
        self.gaze = GazeFlowWidget(self.presentation)
        self.gaze.hide()

        self.stack.setCurrentWidget(self.presentation)

        # VLC is optional: if libVLC is unavailable (e.g. a UI-only preview
        # machine without VLC installed) the window still runs, just without
        # video — it goes straight to the presentation page.
        self.vlc_instance = None
        self.player = None

        if vlc is not None:
            try:
                self.vlc_instance = vlc.Instance(
                    "--no-video-title-show",
                    "--quiet",
                    "--avcodec-hw=d3d11va",
                )
                self.player = self.vlc_instance.media_player_new()
            except Exception as e:  # pragma: no cover
                print(f"  [top] WARNING: VLC unavailable, video disabled: {e}")
                self.vlc_instance = None
                self.player = None
        else:
            print("  [top] WARNING: python-vlc not importable, video disabled.")

        self.video_path: Path | None = None
        if video_path is not None and self.player is not None:
            self.load_video(Path(video_path))

    # ── Event pumping helper ─────────────────────────────────────────────────

    def process_events(self) -> None:
        if self.app is not None:
            self.app.processEvents()

    # ── Video (ported from ControlledVideoWindow) ────────────────────────────

    def load_video(self, video_path: Path) -> None:
        self.video_path = Path(video_path)

        if self.player is None:
            print(f"  [top] (no VLC) skipping load: {self.video_path}")
            return

        print(f"  [top] Loading video: {self.video_path}")

        self.stack.setCurrentWidget(self.video_frame)
        self.process_events()

        try:
            self.player.stop()
        except Exception:
            pass

        media = self.vlc_instance.media_new(str(self.video_path.resolve()))
        self.player.set_media(media)

        hwnd = int(self.video_frame.winId())
        self.player.set_hwnd(hwnd)
        self.player.audio_set_volume(VIDEO_VOLUME)

        self.process_events()

    def play(self) -> None:
        if self.player is None:
            print("  [top] (no VLC) skipping video playback.")
            return

        print(f"  [top] Playing video: {self.video_path}")
        self.stack.setCurrentWidget(self.video_frame)
        self.raise_()
        self.activateWindow()
        self.process_events()

        hwnd = int(self.video_frame.winId())
        self.player.set_hwnd(hwnd)
        self.player.audio_set_volume(VIDEO_VOLUME)
        self.player.play()
        self.process_events()

    def pause(self) -> None:
        if self.player is None:
            return
        self.player.set_pause(1)
        self.process_events()

    def resume(self) -> None:
        if self.player is None:
            return
        self.stack.setCurrentWidget(self.video_frame)
        self.player.set_pause(0)
        self.process_events()

    def close(self) -> None:
        print("  [top] Closing top display window.")
        try:
            if self.player is not None:
                self.player.stop()
        except Exception:
            pass
        super().close()
        self.process_events()

    # ── Presentation ─────────────────────────────────────────────────────────

    def show_topic(self, title: str, topic: str) -> None:
        """After the video ends: show the topic huge on the top display."""
        print("  [top] Showing topic:", title)
        self.pause()

        self.presentation.title_label.setText(title)
        self.presentation.topic_label.setText(topic)

        for role, marker in self.presentation.markers.items():
            marker.set_active(False)

        self.gaze.stop()
        self.stack.setCurrentWidget(self.presentation)
        self.process_events()

    def set_current_speaker(self, role: str) -> None:
        """Highlight the robot currently speaking; clear gaze flow."""
        self.gaze.stop()
        for r, marker in self.presentation.markers.items():
            marker.set_active(r == role)
        self.process_events()

    def set_next_speaker(self, role: str | None) -> None:
        """Stream the gaze flow diagonally down toward the next robot."""
        for r, marker in self.presentation.markers.items():
            marker.set_active(False)

        if role is None:
            self.gaze.stop()
            self.process_events()
            return

        pos = theme.ROLE_POSITION.get(role, 1)
        # -1 (left), 0 (center), +1 (right) → horizontal lean, always downward.
        dx = (pos - 1) * 0.7
        self.gaze.set_accent(theme.role_accent_rgb(role))
        self.gaze.set_direction(dx, 1.0)
        self.gaze.setGeometry(self.presentation.rect())
        self.gaze.start()
        self.gaze.raise_()
        self.process_events()

    def resizeEvent(self, event):
        if self.gaze.isVisible():
            self.gaze.setGeometry(self.presentation.rect())
        super().resizeEvent(event)

    # ── Video end detection (ported) ─────────────────────────────────────────

    async def wait_until_near_end_and_pause(self) -> None:
        if self.player is None:
            print("  [top] (no VLC) no video to wait on.")
            return

        print("  [top] Waiting until video reaches the end...")

        duration_ms = 0

        for _ in range(80):
            self.process_events()
            duration_ms = self.player.get_length()

            if duration_ms and duration_ms > 0:
                break

            await asyncio.sleep(0.25)

        if not duration_ms or duration_ms <= 0:
            print("  [top] WARNING: Could not read video length. 60s fallback.")
            await self._sleep_with_events(60)
            self.pause()
            return

        print(f"  [top] Video length: {duration_ms / 1000:.1f}s")

        max_wait_seconds = (duration_ms / 1000) + 10
        start_time = time.monotonic()

        last_position_ms = -1
        stuck_count = 0

        while True:
            self.process_events()

            current_ms = self.player.get_time()
            elapsed = time.monotonic() - start_time

            key = read_arrow_key_windows()

            if key == "right":
                new_time = min(current_ms + 10_000, duration_ms - 1000)
                self.player.set_time(int(new_time))
                print("  [top] Skipped forward 10s.")
            elif key == "left":
                new_time = max(0, current_ms - 10_000)
                self.player.set_time(int(new_time))
                print("  [top] Rewound 10s.")

            if elapsed > max_wait_seconds:
                print("  [top] WARNING: Video wait timed out.")
                break

            if current_ms >= duration_ms - 500:
                print("  [top] Video reached the end.")
                break

            if current_ms == last_position_ms:
                stuck_count += 1
            else:
                stuck_count = 0
                last_position_ms = current_ms

            if stuck_count >= 75:
                print("  [top] WARNING: Video playback appears stuck.")
                break

            await asyncio.sleep(0.2)

        rewind_ms = 4000
        rewind_position_ms = max(0, duration_ms - rewind_ms)

        self.pause()
        await self._sleep_with_events(0.2)
        self.player.set_time(int(rewind_position_ms))
        await self._sleep_with_events(0.5)
        self.pause()
        await self._sleep_with_events(0.2)

        actual_ms = self.player.get_time()
        print(
            f"  [top] Rewound to 4s before end and paused. "
            f"Target={rewind_position_ms / 1000:.1f}s, "
            f"Actual={actual_ms / 1000:.1f}s"
        )

    async def _sleep_with_events(self, seconds: float) -> None:
        end_time = time.monotonic() + seconds
        while time.monotonic() < end_time:
            self.process_events()
            await asyncio.sleep(0.016)
