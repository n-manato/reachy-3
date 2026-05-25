from __future__ import annotations

import asyncio
import msvcrt
import sys
import time
from pathlib import Path

import numpy as np
import vlc
from scipy.io import wavfile

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ethics_podcast.AudioOrbWidget import AudioOrbWidget


VIDEO_VOLUME = 75


def _get_app() -> QApplication:
    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    return app


def _read_arrow_key_windows() -> str | None:
    if not msvcrt.kbhit():
        return None

    first = msvcrt.getch()

    if first not in (b"\x00", b"\xe0"):
        return None

    second = msvcrt.getch()

    if second == b"M":
        return "right"

    if second == b"K":
        return "left"

    return None


class AudioLevelFollower(QObject):
    """Drive AudioOrbWidget from the currently playing robot WAV file."""

    def __init__(self, orb_widget: AudioOrbWidget):
        super().__init__()

        self.orb_widget = orb_widget
        self.audio: np.ndarray | None = None
        self.sample_rate: int | None = None
        self.start_time: float | None = None

        self.timer = QTimer()
        self.timer.timeout.connect(self._update_level)

    def start(self, audio_path: str | Path) -> None:
        path = Path(audio_path)

        if not path.exists():
            print(f"  WARNING: Orb audio file not found: {path}")
            self.orb_widget.set_audio_level(0.45)
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
        self.orb_widget.set_audio_level(0.0)

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

        # Visual gain. Increase this if the orb does not move enough.
        level = min(1.0, rms * 10.0)

        # Keep a small visible motion while speaking.
        if level > 0.01:
            level = max(level, 0.12)

        self.orb_widget.set_audio_level(level)


class InfoCard(QFrame):
    def __init__(self, title: str, value: str = "", accent: str = "#18d4f5"):
        super().__init__()

        self.accent = accent

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: rgba(255, 255, 255, 0.055);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 18px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"""
            font-size: 12px;
            font-weight: 900;
            color: {accent};
            letter-spacing: 1.5px;
            """
        )

        self.value_label = QLabel(value)
        self.value_label.setWordWrap(True)
        self.value_label.setStyleSheet(
            """
            font-size: 16px;
            font-weight: 800;
            color: white;
            """
        )

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class DiscussionPage(QWidget):
    ROLE_NAMES = {
        "student": "Bentham-Bot / LEFT",
        "professor": "Korsgaard-Bot / CENTER",
        "moderator": "Aquinas-Bot / RIGHT",
    }

    ROLE_SHORT_NAMES = {
        "student": "Bentham-Bot",
        "professor": "Korsgaard-Bot",
        "moderator": "Aquinas-Bot",
    }

    ROLE_POSITIONS = {
        "student": "Left",
        "professor": "Center",
        "moderator": "Right",
    }

    ROLE_COLORS = {
        "student": "#5dade2",
        "professor": "#bb8fce",
        "moderator": "#58d68d",
    }

    ROLE_DESCRIPTIONS = {
        "student": "Consequences / Utility",
        "professor": "Duty / Respect",
        "moderator": "Virtue / Purpose",
    }

    def __init__(self):
        super().__init__()

        self.setStyleSheet(
            """
            QWidget {
                background-color: #14152f;
                color: white;
                font-family: Arial;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(42, 26, 42, 30)
        root.setSpacing(18)

        # ── Top bar ─────────────────────────────────────────────

        top_bar = QHBoxLayout()
        top_bar.setSpacing(16)

        self.app_title_label = QLabel("AI Ethics Assistance")
        self.app_title_label.setStyleSheet(
            """
            font-size: 20px;
            font-weight: 900;
            color: #18d4f5;
            """
        )

        self.session_label = QLabel("Video Discussion")
        self.session_label.setStyleSheet(
            """
            font-size: 15px;
            font-weight: 700;
            color: rgba(255, 255, 255, 0.75);
            """
        )

        top_bar.addWidget(self.app_title_label)
        top_bar.addSpacing(20)
        top_bar.addWidget(self.session_label)
        top_bar.addStretch(1)

        self.status_label = QLabel("READY")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.status_label.setStyleSheet(
            """
            font-size: 13px;
            font-weight: 900;
            color: #f7dc6f;
            letter-spacing: 1.4px;
            """
        )

        top_bar.addWidget(self.status_label)
        root.addLayout(top_bar)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(255,255,255,0.08);")
        root.addWidget(divider)

        # ── Main 3-column layout ────────────────────────────────

        main = QHBoxLayout()
        main.setSpacing(28)
        root.addLayout(main, stretch=1)

        # Left column
        left_col = QVBoxLayout()
        left_col.setSpacing(16)

        left_title = QLabel("Robot Perspectives")
        left_title.setStyleSheet(
            """
            font-size: 18px;
            font-weight: 900;
            color: white;
            """
        )

        left_col.addWidget(left_title)

        self.student_card = InfoCard(
            "BENTHAM",
            "Consequences",
            self.ROLE_COLORS["student"],
        )
        self.professor_card = InfoCard(
            "KORSGAARD",
            "Duty and respect",
            self.ROLE_COLORS["professor"],
        )
        self.moderator_card = InfoCard(
            "AQUINAS",
            "Virtue and purpose",
            self.ROLE_COLORS["moderator"],
        )

        left_col.addWidget(self.student_card)
        left_col.addWidget(self.professor_card)
        left_col.addWidget(self.moderator_card)
        left_col.addStretch(1)

        left_panel = QFrame()
        left_panel.setLayout(left_col)
        left_panel.setFixedWidth(285)
        left_panel.setStyleSheet("background: transparent;")

        main.addWidget(left_panel)

        # Center column
        center_col = QVBoxLayout()
        center_col.setSpacing(12)

        self.title_label = QLabel("Discussion")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setStyleSheet(
            """
            font-size: 31px;
            font-weight: 900;
            color: white;
            """
        )

        self.question_label = QLabel("")
        self.question_label.setWordWrap(True)
        self.question_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.question_label.setStyleSheet(
            """
            font-size: 23px;
            font-weight: 800;
            line-height: 1.35;
            color: #f7dc6f;
            padding: 16px 22px;
            border: 1px solid rgba(247, 220, 111, 0.35);
            border-radius: 20px;
            background-color: rgba(0,0,0,0.20);
            """
        )

        self.audio_orb = AudioOrbWidget()
        self.audio_orb.setMinimumHeight(430)
        self.audio_orb.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.audio_follower = AudioLevelFollower(self.audio_orb)

        self.current_speaker_label = QLabel("")
        self.current_speaker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_speaker_label.setStyleSheet(
            """
            font-size: 27px;
            font-weight: 900;
            color: white;
            """
        )

        self.current_text_label = QLabel("")
        self.current_text_label.setWordWrap(True)
        self.current_text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_text_label.setMinimumHeight(145)
        self.current_text_label.setStyleSheet(
            """
            font-size: 31px;
            font-weight: 900;
            line-height: 1.35;
            color: white;
            padding: 22px 28px;
            border: 2px solid rgba(255,255,255,0.35);
            border-radius: 24px;
            background-color: rgba(0,0,0,0.45);
            """
        )

        center_col.addWidget(self.title_label)
        center_col.addWidget(self.question_label)
        center_col.addWidget(self.audio_orb, stretch=1)
        center_col.addWidget(self.current_speaker_label)
        center_col.addWidget(self.current_text_label)

        center_panel = QFrame()
        center_panel.setLayout(center_col)
        center_panel.setStyleSheet("background: transparent;")

        main.addWidget(center_panel, stretch=1)

        # Right column
        right_col = QVBoxLayout()
        right_col.setSpacing(16)

        right_title = QLabel("Current Turn")
        right_title.setStyleSheet(
            """
            font-size: 18px;
            font-weight: 900;
            color: white;
            """
        )
        right_col.addWidget(right_title)

        self.current_role_card = InfoCard("SPEAKER", "None", "#18d4f5")
        self.current_position_card = InfoCard("POSITION", "-", "#18d4f5")
        self.current_view_card = InfoCard("PERSPECTIVE", "-", "#18d4f5")

        right_col.addWidget(self.current_role_card)
        right_col.addWidget(self.current_position_card)
        right_col.addWidget(self.current_view_card)

        history_title = QLabel("Recent Lines")
        history_title.setStyleSheet(
            """
            font-size: 17px;
            font-weight: 900;
            color: white;
            margin-top: 10px;
            """
        )

        right_col.addWidget(history_title)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFixedHeight(280)
        self.scroll_area.setStyleSheet(
            """
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(255,255,255,0.05);
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(24, 212, 245, 0.55);
                border-radius: 4px;
            }
            """
        )

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch(1)

        self.scroll_area.setWidget(self.chat_container)
        right_col.addWidget(self.scroll_area)

        right_col.addStretch(1)

        right_panel = QFrame()
        right_panel.setLayout(right_col)
        right_panel.setFixedWidth(315)
        right_panel.setStyleSheet("background: transparent;")

        main.addWidget(right_panel)

    def show_discussion(self, title: str, topic: str) -> None:
        self.title_label.setText(title)
        self.question_label.setText(topic)

        self.current_speaker_label.setText("")
        self.current_text_label.setText("")

        self.current_role_card.set_value("None")
        self.current_position_card.set_value("-")
        self.current_view_card.set_value("-")

        self.status_label.setText("READY")
        self.audio_follower.stop()
        self.audio_orb.stop()

        self._clear_chat()

    def show_subtitle(
        self,
        role: str,
        text: str,
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
    ) -> None:
        speaker = self.ROLE_NAMES.get(role, role)
        short_name = self.ROLE_SHORT_NAMES.get(role, role)
        position = self.ROLE_POSITIONS.get(role, "-")
        perspective = self.ROLE_DESCRIPTIONS.get(role, "-")
        color = self.ROLE_COLORS.get(role, "#ffffff")

        if turn_number is not None:
            speaker_text = f"Turn {turn_number} · {speaker}"
        else:
            speaker_text = speaker

        self.status_label.setText("SPEAKING")
        self.current_role_card.set_value(short_name)
        self.current_position_card.set_value(position)
        self.current_view_card.set_value(perspective)

        self.audio_orb.set_speaker(speaker)
        self.audio_orb.set_status("Speaking...")

        if audio_path is not None:
            self.audio_follower.start(audio_path)
        else:
            # Fallback. This still makes the orb move during speech.
            self.audio_orb.set_audio_level(0.45)

        self.current_speaker_label.setText(speaker_text)
        self.current_speaker_label.setStyleSheet(
            f"""
            font-size: 27px;
            font-weight: 900;
            color: {color};
            """
        )

        self.current_text_label.setText(text)
        self.current_text_label.setStyleSheet(
            f"""
            font-size: 31px;
            font-weight: 900;
            line-height: 1.35;
            color: white;
            padding: 22px 28px;
            border: 2px solid {color};
            border-radius: 24px;
            background-color: rgba(0,0,0,0.45);
            """
        )

        self._add_chat_bubble(role, speaker, text)

    def finish_subtitle(self) -> None:
        self.current_speaker_label.setText("")
        self.current_text_label.setText("")

        self.current_role_card.set_value("None")
        self.current_position_card.set_value("-")
        self.current_view_card.set_value("-")

        self.status_label.setText("READY")

        self.audio_follower.stop()
        self.audio_orb.stop()

    def _clear_chat(self) -> None:
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        self.chat_layout.addStretch(1)

    def _add_chat_bubble(self, role: str, speaker: str, text: str) -> None:
        color = self.ROLE_COLORS.get(role, "#ffffff")

        bubble = QLabel()
        bubble.setWordWrap(True)
        bubble.setText(f"<b>{speaker}</b><br>{text}")
        bubble.setStyleSheet(
            f"""
            QLabel {{
                font-size: 15px;
                line-height: 1.35;
                color: white;
                padding: 11px 14px;
                border-radius: 14px;
                border-left: 7px solid {color};
                background-color: rgba(255,255,255,0.10);
            }}
            """
        )

        insert_index = max(0, self.chat_layout.count() - 1)
        self.chat_layout.insertWidget(insert_index, bubble)

        # Keep only last 4 bubbles.
        widgets = []

        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            widget = item.widget()

            if widget is not None:
                widgets.append(widget)

        while len(widgets) > 4:
            old = widgets.pop(0)
            self.chat_layout.removeWidget(old)
            old.deleteLater()

        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())


class ControlledVideoWindow(QWidget):
    """VLC embedded video player + same-window discussion UI."""

    def __init__(self, video_path: Path):
        self.app = _get_app()

        super().__init__()

        self.video_path = Path(video_path)

        self.setWindowTitle("Ethics Podcast Video Discussion")
        self.setStyleSheet("background-color: black;")

        self.stack = QStackedLayout(self)

        # Page 0: VLC video
        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.stack.addWidget(self.video_frame)

        # Page 1: Discussion UI
        self.discussion_page = DiscussionPage()
        self.stack.addWidget(self.discussion_page)

        self.stack.setCurrentWidget(self.video_frame)

        self.vlc_instance = vlc.Instance(
            "--no-video-title-show",
            "--quiet",
            "--avcodec-hw=d3d11va",
        )

        self.player = self.vlc_instance.media_player_new()

        self.load_video(self.video_path)

    def process_events(self) -> None:
        self.app.processEvents()

    async def pump_events_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.process_events()
            await asyncio.sleep(0.016)

    def load_video(self, video_path: Path) -> None:
        self.video_path = Path(video_path)

        print(f"  Loading video into embedded VLC player: {self.video_path}")

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
        print(f"  Playing embedded VLC fullscreen: {self.video_path}")

        self.stack.setCurrentWidget(self.video_frame)

        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.process_events()

        hwnd = int(self.video_frame.winId())
        self.player.set_hwnd(hwnd)

        self.player.audio_set_volume(VIDEO_VOLUME)
        self.player.play()

        self.process_events()

    def pause(self) -> None:
        self.player.set_pause(1)
        self.process_events()

    def resume(self) -> None:
        self.stack.setCurrentWidget(self.video_frame)
        self.player.set_pause(0)
        self.process_events()

    def close(self) -> None:
        print("  Closing embedded VLC window.")

        try:
            self.player.stop()
        except Exception:
            pass

        super().close()
        self.process_events()

    def show_discussion(self, title: str, topic: str) -> None:
        print("  Showing discussion page:")
        print("  Title:", title)
        print("  Topic:", topic)

        self.pause()

        self.discussion_page.show_discussion(title=title, topic=topic)
        self.stack.setCurrentWidget(self.discussion_page)

        self.process_events()

    def hide_discussion(self) -> None:
        self.stack.setCurrentWidget(self.video_frame)
        self.process_events()

    def show_subtitle(
        self,
        role: str,
        text: str,
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
    ) -> None:
        print("  Showing subtitle:", role, text[:60])

        self.discussion_page.show_subtitle(
            role=role,
            text=text,
            turn_number=turn_number,
            audio_path=audio_path,
        )

        self.process_events()

    def finish_subtitle(self) -> None:
        self.discussion_page.finish_subtitle()
        self.process_events()

    async def wait_until_near_end_and_pause(self) -> None:
        print("  Waiting until video reaches the end...")

        duration_ms = 0

        for _ in range(80):
            self.process_events()
            duration_ms = self.player.get_length()

            if duration_ms and duration_ms > 0:
                break

            await asyncio.sleep(0.25)

        if not duration_ms or duration_ms <= 0:
            print("  WARNING: Could not read video length.")
            print("  Using 60 second fallback wait.")
            await self._sleep_with_events(60)
            self.pause()
            return

        print(f"  Video length: {duration_ms / 1000:.1f} seconds")

        max_wait_seconds = (duration_ms / 1000) + 10
        start_time = time.monotonic()

        last_position_ms = -1
        stuck_count = 0

        while True:
            self.process_events()

            current_ms = self.player.get_time()
            elapsed = time.monotonic() - start_time

            key = _read_arrow_key_windows()

            if key == "right":
                new_time = min(current_ms + 10_000, duration_ms - 1000)
                self.player.set_time(int(new_time))
                print("  Skipped forward 10 seconds.")

            elif key == "left":
                new_time = max(0, current_ms - 10_000)
                self.player.set_time(int(new_time))
                print("  Rewound 10 seconds.")

            if elapsed > max_wait_seconds:
                print("  WARNING: Video wait timed out.")
                print("  Moving to 4 seconds before the end and pausing.")
                break

            if current_ms >= duration_ms - 500:
                print("  Video reached the end by time check.")
                break

            if current_ms == last_position_ms:
                stuck_count += 1
            else:
                stuck_count = 0
                last_position_ms = current_ms

            if stuck_count >= 75:
                print("  WARNING: Video playback time appears stuck.")
                print("  Moving to 4 seconds before the end and pausing.")
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
            f"  Video rewound to 4 seconds before the end and paused. "
            f"Target={rewind_position_ms / 1000:.1f}s, "
            f"Actual={actual_ms / 1000:.1f}s"
        )

    async def _sleep_with_events(self, seconds: float) -> None:
        end_time = time.monotonic() + seconds

        while time.monotonic() < end_time:
            self.process_events()
            await asyncio.sleep(0.016)