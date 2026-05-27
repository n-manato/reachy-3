from __future__ import annotations

import asyncio
import msvcrt
import sys
import time
from pathlib import Path

import numpy as np
import vlc
from scipy.io import wavfile

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)


VIDEO_VOLUME = 75

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PICTURE_DIR = PROJECT_ROOT / "picture"


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


def _add_shadow(
    widget: QWidget,
    blur: int = 28,
    y_offset: int = 8,
    alpha: int = 80,
    color: str = "#00d9ff",
) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y_offset)

    qcolor = QColor(color)
    qcolor.setAlpha(alpha)

    shadow.setColor(qcolor)
    widget.setGraphicsEffect(shadow)


def _get_audio_duration_secs(audio_path: str | Path | None) -> float | None:
    if audio_path is None:
        return None

    path = Path(audio_path)

    if not path.exists():
        return None

    try:
        sample_rate, data = wavfile.read(path)
    except Exception:
        return None

    if sample_rate <= 0:
        return None

    return len(data) / float(sample_rate)


class AudioLevelFollower(QObject):
    """Read the current robot WAV file and drive the active speaker avatar."""

    def __init__(self):
        super().__init__()

        self.audio: np.ndarray | None = None
        self.sample_rate: int | None = None
        self.start_time: float | None = None
        self.target_panel = None

        self.timer = QTimer()
        self.timer.timeout.connect(self._update_level)

    def start(self, audio_path: str | Path, target_panel) -> None:
        self.stop()

        path = Path(audio_path)

        if not path.exists():
            print(f"  WARNING: UI audio file not found: {path}")
            target_panel.set_audio_level(0.45)
            return

        try:
            sample_rate, data = wavfile.read(path)
        except Exception as e:
            print(f"  WARNING: Could not read UI audio file: {path} ({e})")
            target_panel.set_audio_level(0.45)
            return

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
        self.target_panel = target_panel

        self.timer.start(16)

    def stop(self) -> None:
        self.timer.stop()

        if self.target_panel is not None:
            self.target_panel.set_audio_level(0.0)

        self.audio = None
        self.sample_rate = None
        self.start_time = None
        self.target_panel = None

    def _update_level(self) -> None:
        if (
            self.audio is None
            or self.sample_rate is None
            or self.start_time is None
            or self.target_panel is None
        ):
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

        level = min(1.0, rms * 11.0)

        if level > 0.01:
            level = max(level, 0.14)

        self.target_panel.set_audio_level(level)


class FuturisticBackground(QWidget):
    """Full-screen dark sci-fi background."""

    def __init__(self):
        super().__init__()

        self.phase = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _tick(self) -> None:
        self.phase += 0.015
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor("#030612"))
        bg.setColorAt(0.45, QColor("#071021"))
        bg.setColorAt(1.0, QColor("#02030a"))
        painter.fillRect(self.rect(), bg)

        glow = QRadialGradient(w / 2, h * 0.45, min(w, h) * 0.74)
        glow.setColorAt(0.0, QColor(0, 220, 255, 35))
        glow.setColorAt(0.35, QColor(130, 70, 255, 20))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(
            int(w / 2 - min(w, h) * 0.74),
            int(h * 0.45 - min(w, h) * 0.74),
            int(min(w, h) * 1.48),
            int(min(w, h) * 1.48),
        )

        painter.setPen(QPen(QColor(0, 220, 255, 18), 1))

        grid_gap = 42
        offset = int((self.phase * 80) % grid_gap)

        for x in range(-grid_gap + offset, w + grid_gap, grid_gap):
            painter.drawLine(x, 0, x, h)

        for y in range(-grid_gap + offset, h + grid_gap, grid_gap):
            painter.drawLine(0, y, w, y)

        painter.setPen(QPen(QColor(150, 90, 255, 16), 1))

        for i in range(-h, w, 160):
            painter.drawLine(i, h, i + h, 0)

        top_glow = QLinearGradient(0, 0, 0, h)
        top_glow.setColorAt(0.0, QColor(0, 220, 255, 34))
        top_glow.setColorAt(0.16, QColor(0, 0, 0, 0))
        top_glow.setColorAt(0.84, QColor(0, 0, 0, 0))
        top_glow.setColorAt(1.0, QColor(130, 70, 255, 28))

        painter.fillRect(self.rect(), top_glow)


class AvatarWidget(QWidget):
    def __init__(
        self,
        role: str,
        color: str,
        label: str,
        image_path: str | Path | None = None,
    ):
        super().__init__()

        self.role = role
        self.color = QColor(color)
        self.label = label
        self.phase = 0.0
        self.active = False

        self.level = 0.0
        self.display_level = 0.0

        self.image_path = Path(image_path) if image_path is not None else None
        self.pixmap = (
            QPixmap(str(self.image_path))
            if self.image_path is not None
            else QPixmap()
        )

        if self.image_path is not None and self.pixmap.isNull():
            print(f"  WARNING: Avatar image could not be loaded: {self.image_path}")

        # 写真の大きさはほぼそのまま。
        # 表示枠だけ広くして、光源とリングが欠けないようにする。
        self.setFixedSize(230, 230)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))
        self.update()

    def _tick(self) -> None:
        self.phase += 0.055
        self.display_level += (self.level - self.display_level) * 0.24

        if not self.active and self.level > 0:
            self.level *= 0.88

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2

        qcolor = self.color

        # 写真本体は小さめ固定。
        # 音声で少しだけ膨らむ。
        base_radius = 50
        pulse = self.display_level * 5
        photo_radius = base_radius + pulse

        ring_alpha = 145 if self.active else 45
        ring_alpha += int(self.display_level * 80)
        ring_alpha = min(ring_alpha, 230)

        glow_alpha = 76 if self.active else 24
        glow_alpha += int(self.display_level * 115)
        glow_alpha = min(glow_alpha, 220)

        # 重要:
        # 以前の 2.65 は広すぎて表示枠から欠ける。
        # 1.78 なら光源は広がるが、230x230 の中に収まる。
        glow_scale = 1.78

        glow = QRadialGradient(cx, cy, photo_radius * glow_scale)
        glow.setColorAt(
            0.0,
            QColor(qcolor.red(), qcolor.green(), qcolor.blue(), glow_alpha),
        )
        glow.setColorAt(
            0.58,
            QColor(
                qcolor.red(),
                qcolor.green(),
                qcolor.blue(),
                40 if self.active else 14,
            ),
        )
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(
            QRectF(
                cx - photo_radius * glow_scale,
                cy - photo_radius * glow_scale,
                photo_radius * glow_scale * 2,
                photo_radius * glow_scale * 2,
            )
        )

        # Animated rings
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for i, scale in enumerate([1.12, 1.32, 1.55]):
            rr = (
                photo_radius * scale
                + np.sin(self.phase * 2 + i) * 2
                + self.display_level * 6
            )
            alpha = max(18, ring_alpha - i * 44)

            painter.setPen(
                QPen(
                    QColor(
                        qcolor.red(),
                        qcolor.green(),
                        qcolor.blue(),
                        alpha,
                    ),
                    2 if self.active and i == 0 else 1,
                )
            )
            painter.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))

        # Circular photo
        circle_path = QPainterPath()
        circle_path.addEllipse(
            QRectF(
                cx - photo_radius,
                cy - photo_radius,
                photo_radius * 2,
                photo_radius * 2,
            )
        )

        painter.save()
        painter.setClipPath(circle_path)

        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(
                int(photo_radius * 2),
                int(photo_radius * 2),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )

            x = int(cx - scaled.width() / 2)
            y = int(cy - scaled.height() / 2)

            painter.drawPixmap(x, y, scaled)
        else:
            fallback = QRadialGradient(cx, cy, photo_radius)
            fallback.setColorAt(0.0, QColor(255, 255, 255, 210))
            fallback.setColorAt(
                1.0,
                QColor(qcolor.red(), qcolor.green(), qcolor.blue(), 60),
            )

            painter.setBrush(fallback)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                QRectF(
                    cx - photo_radius,
                    cy - photo_radius,
                    photo_radius * 2,
                    photo_radius * 2,
                )
            )

            font = QFont("Arial")
            font.setPointSize(36)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(qcolor)
            painter.drawText(
                QRectF(
                    cx - photo_radius,
                    cy - photo_radius,
                    photo_radius * 2,
                    photo_radius * 2,
                ),
                Qt.AlignmentFlag.AlignCenter,
                self.label,
            )

        painter.restore()

        # Photo border
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(qcolor.red(), qcolor.green(), qcolor.blue(), 220),
                2,
            )
        )
        painter.drawEllipse(
            QRectF(
                cx - photo_radius,
                cy - photo_radius,
                photo_radius * 2,
                photo_radius * 2,
            )
        )

        # Small badge
        badge_radius = 17
        badge_x = w - 64
        badge_y = h - 64

        painter.setBrush(QColor(3, 6, 18, 235))
        painter.setPen(
            QPen(
                QColor(qcolor.red(), qcolor.green(), qcolor.blue(), 190),
                2,
            )
        )
        painter.drawEllipse(
            QRectF(
                badge_x,
                badge_y,
                badge_radius * 2,
                badge_radius * 2,
            )
        )

        font = QFont("Arial")
        font.setPointSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(qcolor)
        painter.drawText(
            QRectF(
                badge_x,
                badge_y,
                badge_radius * 2,
                badge_radius * 2,
            ),
            Qt.AlignmentFlag.AlignCenter,
            self.label,
        )


class QuestionCard(QFrame):
    def __init__(self):
        super().__init__()

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(120)
        self.setMaximumHeight(135)

        _add_shadow(self, blur=36, y_offset=8, alpha=75, color="#00d9ff")

        self.setStyleSheet(
            """
            QFrame {
                background-color: rgba(5, 14, 31, 225);
                border: 2px solid rgba(0, 220, 255, 150);
                border-radius: 26px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 12, 40, 12)
        root.setSpacing(0)

        self.topic_label = QLabel("")
        self.topic_label.setWordWrap(True)
        self.topic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.topic_label.setStyleSheet(
            """
            QLabel {
                color: #eaf6ff;
                background: transparent;
                border: none;
                font-size: 24px;
                font-weight: 900;
                line-height: 1.3;
            }
            """
        )

        root.addWidget(self.topic_label)

    def set_question(self, topic: str) -> None:
        self.topic_label.setText(topic)


class CurrentSpeechPanel(QFrame):
    def __init__(self):
        super().__init__()

        self.color = QColor("#00d9ff")
        self.full_text = ""
        self.visible_count = 0

        self.type_timer = QTimer(self)
        self.type_timer.timeout.connect(self._type_next)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(360)
        self.setMaximumHeight(385)

        _add_shadow(self, blur=42, y_offset=10, alpha=80, color="#00d9ff")

        self.setStyleSheet(
            """
            QFrame {
                background-color: rgba(5, 14, 31, 235);
                border: 2px solid rgba(0, 220, 255, 145);
                border-radius: 28px;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 20, 30, 22)
        root.setSpacing(10)

        self.speaker_label = QLabel("")
        self.speaker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.speaker_label.setStyleSheet(
            """
            QLabel {
                color: #55e7ff;
                background: transparent;
                border: none;
                font-size: 20px;
                font-weight: 900;
                letter-spacing: 3px;
            }
            """
        )

        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.text_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.text_label.setStyleSheet(
            """
            QLabel {
                color: #eaf6ff;
                background: transparent;
                border: none;
                font-size: 22px;
                font-weight: 650;
                line-height: 1.45;
                padding: 4px 8px;
            }
            """
        )

        root.addWidget(self.speaker_label)
        root.addWidget(self.text_label, 1)

    def set_speaker_style(self, speaker_name: str, color: str) -> None:
        self.color = QColor(color)

        self.speaker_label.setText(speaker_name.upper())
        self.speaker_label.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background: transparent;
                border: none;
                font-size: 20px;
                font-weight: 900;
                letter-spacing: 3px;
            }}
            """
        )

        qcolor = QColor(color)

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: rgba(5, 14, 31, 238);
                border: 2px solid rgba(
                    {qcolor.red()},
                    {qcolor.green()},
                    {qcolor.blue()},
                    170
                );
                border-radius: 28px;
            }}
            """
        )

    def set_text(
        self,
        speaker_name: str,
        text: str,
        color: str,
        duration_secs: float | None = None,
    ) -> None:
        self.type_timer.stop()

        self.set_speaker_style(speaker_name, color)

        self.full_text = text.strip()
        self.visible_count = 0

        if not self.full_text:
            self.text_label.setText("")
            return

        self.words = self.full_text.split()
        self.visible_word_count = 0

        if duration_secs is not None and duration_secs > 0:
            total_ms = max(700, int(duration_secs * 1000 * 0.88))
            interval_ms = max(120, total_ms // max(1, len(self.words)))
        else:
            interval_ms = 140

        self.text_label.setText("“ ”")

        self.type_timer.start(interval_ms)

    def _type_next(self) -> None:
        if not hasattr(self, "words"):
            self.type_timer.stop()
            self.text_label.setText(f"“ {self.full_text} ”")
            return

        if self.visible_word_count >= len(self.words):
            self.type_timer.stop()
            self.text_label.setText(f"“ {self.full_text} ”")
            return

        words_per_tick = 2
        self.visible_word_count = min(
            len(self.words),
            self.visible_word_count + words_per_tick,
        )

        visible_text = " ".join(self.words[: self.visible_word_count])
        self.text_label.setText(f"“ {visible_text}▌ ”")

    def finish_typing(self) -> None:
        self.type_timer.stop()

        if self.full_text:
            self.text_label.setText(f"“ {self.full_text} ”")

    def clear(self) -> None:
        self.type_timer.stop()
        self.full_text = ""
        self.visible_count = 0
        self.speaker_label.setText("")
        self.text_label.setText("")
        

class SpeakerAvatarPanel(QFrame):
    def __init__(
        self,
        role: str,
        name: str,
        color: str,
        avatar_label: str,
        image_path: str | Path | None = None,
    ):
        super().__init__()

        self.role = role
        self.name = name
        self.color = color
        self.active = False
        self.audio_level = 0.0

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(270)
        self.setMaximumHeight(290)
        self.setMinimumWidth(0)

        _add_shadow(self, blur=28, y_offset=8, alpha=55, color=color)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 10, 20, 10)
        root.setSpacing(2)

        self.name_label = QLabel(name.upper())
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet(
            f"""
            QLabel {{
                color: {color};
                background: transparent;
                border: none;
                font-size: 14px;
                font-weight: 900;
                letter-spacing: 2px;
            }}
            """
        )

        self.avatar = AvatarWidget(
            role=role,
            color=color,
            label=avatar_label,
            image_path=image_path,
        )

        root.addWidget(self.name_label)
        root.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignCenter)

        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.avatar.set_active(active)

        qcolor = QColor(self.color)

        if active:
            self.setStyleSheet(
                f"""
                QFrame {{
                    background-color: rgba(6, 18, 38, 230);
                    border: 2px solid rgba(
                        {qcolor.red()},
                        {qcolor.green()},
                        {qcolor.blue()},
                        210
                    );
                    border-radius: 24px;
                }}
                """
            )
            self.name_label.setStyleSheet(
                f"""
                QLabel {{
                    color: {self.color};
                    background: transparent;
                    border: none;
                    font-size: 15px;
                    font-weight: 900;
                    letter-spacing: 2px;
                }}
                """
            )
        else:
            self.setStyleSheet(
                """
                QFrame {
                    background-color: rgba(5, 11, 26, 160);
                    border: 1px solid rgba(120, 160, 210, 38);
                    border-radius: 24px;
                }
                """
            )
            self.name_label.setStyleSheet(
                f"""
                QLabel {{
                    color: {self.color};
                    background: transparent;
                    border: none;
                    font-size: 14px;
                    font-weight: 900;
                    letter-spacing: 2px;
                    opacity: 0.7;
                }}
                """
            )

        if not active:
            self.set_audio_level(0.0)

    def set_audio_level(self, level: float) -> None:
        self.audio_level = max(0.0, min(1.0, level))
        self.avatar.set_level(self.audio_level)


class DiscussionPage(FuturisticBackground):
    ROLE_NAMES = {
        "student": "Bentham-Bot",
        "professor": "Korsgaard-Bot",
        "moderator": "Aquinas-Bot",
    }

    ROLE_COLORS = {
        "student": "#00d9ff",
        "professor": "#50ff9a",
        "moderator": "#a879ff",
    }

    ROLE_BADGES = {
        "student": "B",
        "professor": "K",
        "moderator": "A",
    }

    ROLE_IMAGES = {
        "student": PICTURE_DIR / "Bentham.png",
        "professor": PICTURE_DIR / "korsgaard.jpg",
        "moderator": PICTURE_DIR / "Aquinas.jpg",
    }

    def __init__(self):
        super().__init__()

        self.panels: dict[str, SpeakerAvatarPanel] = {}
        self.audio_follower = AudioLevelFollower()

        self.setObjectName("discussionPage")
        self.setAutoFillBackground(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(52, 18, 52, 20)
        root.setSpacing(14)

        self.top_bar = QHBoxLayout()
        self.top_bar.setSpacing(12)

        self.app_label = QLabel("AI ETHICS DISCUSSION")
        self.app_label.setStyleSheet(
            """
            QLabel {
                color: #55e7ff;
                font-size: 15px;
                font-weight: 900;
                letter-spacing: 2px;
                background: transparent;
            }
            """
        )

        self.system_label = QLabel("LIVE MULTI-AGENT ETHICS PANEL")
        self.system_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.system_label.setStyleSheet(
            """
            QLabel {
                color: rgba(219, 234, 254, 0.42);
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 2px;
                background: transparent;
            }
            """
        )

        self.session_label = QLabel("")
        self.session_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.session_label.setStyleSheet(
            """
            QLabel {
                color: rgba(219, 234, 254, 0.58);
                font-size: 11px;
                font-weight: 700;
                background: transparent;
            }
            """
        )

        self.top_bar.addWidget(self.app_label)
        self.top_bar.addStretch(1)
        self.top_bar.addWidget(self.system_label)
        self.top_bar.addStretch(1)
        self.top_bar.addWidget(self.session_label)

        root.addLayout(self.top_bar)

        self.question_card = QuestionCard()
        root.addWidget(self.question_card)

        self.current_speech = CurrentSpeechPanel()
        root.addWidget(self.current_speech)

        self.avatar_row = QHBoxLayout()
        self.avatar_row.setSpacing(34)
        self.avatar_row.setContentsMargins(0, 0, 0, 0)

        for role in ["student", "professor", "moderator"]:
            panel = SpeakerAvatarPanel(
                role=role,
                name=self.ROLE_NAMES[role],
                color=self.ROLE_COLORS[role],
                avatar_label=self.ROLE_BADGES[role],
                image_path=self.ROLE_IMAGES[role],
            )
            self.panels[role] = panel
            self.avatar_row.addWidget(panel, 1)

        root.addLayout(self.avatar_row)
        root.addStretch(0)

    def show_discussion(self, title: str, topic: str) -> None:
        self.session_label.setText(title.upper())
        self.question_card.set_question(topic)

        self.current_speech.clear()
        self.audio_follower.stop()

        for panel in self.panels.values():
            panel.set_active(False)
            panel.set_audio_level(0.0)

    def show_subtitle(
        self,
        role: str,
        text: str,
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
    ) -> None:
        self.audio_follower.stop()

        active_panel = self.panels.get(role)

        if active_panel is None:
            return

        duration_secs = _get_audio_duration_secs(audio_path)
        speaker_name = self.ROLE_NAMES.get(role, role)
        color = self.ROLE_COLORS.get(role, "#00d9ff")

        self.current_speech.set_text(
            speaker_name=speaker_name,
            text=text,
            color=color,
            duration_secs=duration_secs,
        )

        for panel_role, panel in self.panels.items():
            panel.set_active(panel_role == role)

        if audio_path is not None:
            self.audio_follower.start(audio_path, active_panel)
        else:
            active_panel.set_audio_level(0.45)

    def finish_subtitle(self) -> None:
        self.audio_follower.stop()
        self.current_speech.finish_typing()

        for panel in self.panels.values():
            panel.set_active(False)
            panel.set_audio_level(0.0)




class ControlledVideoWindow(QWidget):
    """VLC embedded video player + same-window discussion UI."""

    def __init__(self, video_path: Path):
        self.app = _get_app()

        super().__init__()

        self.video_path = Path(video_path)

        self.setWindowTitle("Ethics Podcast Video Discussion")
        self.setObjectName("controlledVideoWindow")
        self.setStyleSheet(
            """
            QWidget#controlledVideoWindow {
                background-color: #02030a;
            }
            """
        )

        self.stack = QStackedLayout(self)

        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_frame.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.stack.addWidget(self.video_frame)

        self.discussion_page = DiscussionPage()
        self.stack.addWidget(self.discussion_page)

        self.stack.setCurrentWidget(self.video_frame)
        self.stack.setCurrentWidget(self.video_frame)

        # Black overlay used for fade-out / fade-in transitions.
        # This avoids showing the desktop between videos or pages.
        self.fade_overlay = QWidget(self)
        self.fade_overlay.setStyleSheet("background-color: black;")
        self.fade_overlay.hide()

        self.fade_effect = QGraphicsOpacityEffect(self.fade_overlay)
        self.fade_effect.setOpacity(0.0)
        self.fade_overlay.setGraphicsEffect(self.fade_effect)

        self.vlc_instance = vlc.Instance(
            "--no-video-title-show",
            "--quiet",
            "--avcodec-hw=none",
        )

        self.player = self.vlc_instance.media_player_new()

        self.load_video(self.video_path)

    def process_events(self) -> None:
        self.app.processEvents()
    
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

        if hasattr(self, "fade_overlay"):
            self.fade_overlay.setGeometry(self.rect())
            self.fade_overlay.raise_()

    async def _fade_overlay(
        self,
        start_opacity: float,
        end_opacity: float,
        seconds: float,
    ) -> None:
        """Fade the black overlay between two opacity values."""
        seconds = max(0.01, float(seconds))

        self.fade_overlay.setGeometry(self.rect())
        self.fade_overlay.show()
        self.fade_overlay.raise_()

        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            t = min(1.0, elapsed / seconds)

            # Smooth ease-in/ease-out.
            eased = t * t * (3.0 - 2.0 * t)

            opacity = start_opacity + (end_opacity - start_opacity) * eased
            self.fade_effect.setOpacity(opacity)

            self.process_events()

            if t >= 1.0:
                break

            await asyncio.sleep(0.016)

        self.fade_effect.setOpacity(end_opacity)

        if end_opacity <= 0.0:
            self.fade_overlay.hide()

        self.process_events()

    def hold_black_screen(self) -> None:
        """Show a full black screen immediately.

        Use this before loading or starting a video so the first frame
        does not flash before fade-in.
        """
        self.fade_overlay.setGeometry(self.rect())
        self.fade_overlay.show()
        self.fade_overlay.raise_()
        self.fade_effect.setOpacity(1.0)
        self.process_events()

    async def fade_to_black(self, seconds: float = 1.0) -> None:
        """Fade the visible page to black."""
        current = float(self.fade_effect.opacity())
        await self._fade_overlay(current, 1.0, seconds)

    def set_video_visual_level(self, level: float) -> None:
        """Set VLC video brightness/contrast/saturation together.

        level=0.0 means black.
        level=1.0 means normal.
        """
        level = max(0.0, min(1.0, float(level)))

        try:
            self.player.video_set_adjust_int(
                vlc.VideoAdjustOption.Enable,
                1,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Brightness,
                level,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Contrast,
                level,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Saturation,
                level,
            )
        except Exception as e:
            print(f"  WARNING: Could not set VLC video visual level: {e}")

    async def fade_video_in(
        self,
        seconds: float = 1.0,
    ) -> None:
        """Fade VLC video image from black to normal.

        This changes the actual VLC image.
        It does not depend on the Qt overlay.
        """
        seconds = max(0.01, float(seconds))

        # Qt overlay can block the view if it happens to be above VLC.
        # Hide it before using VLC image fade.
        self.fade_effect.setOpacity(0.0)
        self.fade_overlay.hide()

        # Start from black.
        self.set_video_visual_level(0.0)

        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception as e:
            print(f"  WARNING: Could not restore VLC video audio on load: {e}")

        self.process_events()

        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            t = min(1.0, elapsed / seconds)

            eased = t * t * (3.0 - 2.0 * t)

            self.set_video_visual_level(eased)

            try:
                self.player.audio_set_mute(False)
                self.player.audio_set_volume(VIDEO_VOLUME)
            except Exception:
                pass

            self.process_events()

            if t >= 1.0:
                break

            await asyncio.sleep(0.016)

        self.set_video_visual_level(1.0)

        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception:
            pass

        self.process_events()

    async def fade_video_out(
        self,
        seconds: float = 1.0,
    ) -> None:
        """Fade out VLC video image only.

        Do not touch VLC audio here.
        Video audio should stay controlled by play() / load_video().
        """
        seconds = max(0.01, float(seconds))

        # Keep video audio ON.
        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception:
            pass

        # Enable VLC video image adjustment.
        try:
            self.player.video_set_adjust_int(
                vlc.VideoAdjustOption.Enable,
                1,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Brightness,
                1.0,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Contrast,
                1.0,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Saturation,
                1.0,
            )
        except Exception as e:
            print(f"  WARNING: Could not enable VLC video adjustment: {e}")

        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            t = min(1.0, elapsed / seconds)

            eased = t * t * (3.0 - 2.0 * t)
            visual = max(0.0, 1.0 - eased)

            try:
                self.player.video_set_adjust_float(
                    vlc.VideoAdjustOption.Brightness,
                    visual,
                )
                self.player.video_set_adjust_float(
                    vlc.VideoAdjustOption.Contrast,
                    visual,
                )
                self.player.video_set_adjust_float(
                    vlc.VideoAdjustOption.Saturation,
                    visual,
                )

                # Important:
                # Keep audio alive during fade.
                self.player.audio_set_mute(False)
                self.player.audio_set_volume(VIDEO_VOLUME)

            except Exception:
                pass

            self.process_events()

            if t >= 1.0:
                break

            await asyncio.sleep(0.016)

        # End on black visually, but do NOT set audio volume to 0.
        try:
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Brightness,
                0.0,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Contrast,
                0.0,
            )
            self.player.video_set_adjust_float(
                vlc.VideoAdjustOption.Saturation,
                0.0,
            )

            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)

        except Exception:
            pass

        self.fade_overlay.setGeometry(self.rect())
        self.fade_overlay.show()
        self.fade_overlay.raise_()
        self.fade_effect.setOpacity(1.0)

        self.process_events()

    async def fade_from_black(self, seconds: float = 1.0) -> None:
        """Fade from black to the visible page."""
        current = float(self.fade_effect.opacity())

        if current <= 0.0:
            self.fade_overlay.setGeometry(self.rect())
            self.fade_overlay.show()
            self.fade_overlay.raise_()
            self.fade_effect.setOpacity(1.0)
            current = 1.0

        await self._fade_overlay(current, 0.0, seconds)

    async def pump_events_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.process_events()
            await asyncio.sleep(0.016)

    def load_video(self, video_path: Path) -> None:
        self.video_path = Path(video_path)

        print(f"  Loading video into embedded VLC player: {self.video_path}")

        # Keep the screen black while changing videos.
        self.hold_black_screen()

        self.stack.setCurrentWidget(self.video_frame)
        self.hold_black_screen()

        try:
            self.player.stop()
        except Exception:
            pass

        media = self.vlc_instance.media_new(str(self.video_path.resolve()))
        self.player.set_media(media)

        hwnd = int(self.video_frame.winId())
        self.player.set_hwnd(hwnd)
        
        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception as e:
            print(f"  WARNING: Could not restore VLC video audio on load: {e}")

        # Prepare the VLC image as black.
        # fade_video_in() will brighten it after play().
        self.set_video_visual_level(0.0)

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

        # Start the VLC image from black.
        # fade_video_in() will brighten it later.
        self.set_video_visual_level(0.0)

        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception as e:
            print(f"  WARNING: Could not restore VLC video audio before play: {e}")

        self.player.play()

        # VLC may ignore audio settings before play starts.
        # Set it again immediately after play.
        try:
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(VIDEO_VOLUME)
        except Exception as e:
            print(f"  WARNING: Could not restore VLC video audio after play: {e}")

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

    async def show_discussion_with_fade_in(
        self,
        title: str,
        topic: str,
        fade_seconds: float = 1.0,
    ) -> None:
        """Switch to discussion page while screen is already black, then fade in."""
        print("  Showing discussion page with fade-in:")
        print("  Title:", title)
        print("  Topic:", topic)

        # Important:
        # Make sure the black overlay is fully visible BEFORE changing pages.
        self.fade_overlay.setGeometry(self.rect())
        self.fade_overlay.show()
        self.fade_overlay.raise_()
        self.fade_effect.setOpacity(1.0)
        self.process_events()

        try:
            self.player.stop()
        except Exception:
            pass

        self.discussion_page.show_discussion(title=title, topic=topic)
        self.stack.setCurrentWidget(self.discussion_page)

        # Important:
        # After switching pages, immediately raise the overlay again
        # before processing events. This prevents the discussion screen
        # from flashing for one frame.
        self.fade_overlay.setGeometry(self.rect())
        self.fade_overlay.show()
        self.fade_overlay.raise_()
        self.fade_effect.setOpacity(1.0)
        self.process_events()

        await self.fade_from_black(fade_seconds)

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

    async def wait_until_end_with_fade_out(self, fade_seconds: float = 1.0) -> None:
        """Fade out during the final part of the video, then wait for the real end.

        This does not rewind.
        This does not pause near the end.
        """
        print("  Waiting until video reaches fade-out point...")

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
            await self.fade_video_out(fade_seconds)
            return

        print(f"  Video length: {duration_ms / 1000:.1f} seconds")

        fade_ms = int(fade_seconds * 1000)
        fade_start_ms = max(0, duration_ms - fade_ms- 500)

        max_wait_seconds = (duration_ms / 1000) + 10
        start_time = time.monotonic()

        last_position_ms = -1
        stuck_count = 0
        fade_started = False

        while True:
            self.process_events()

            current_ms = self.player.get_time()
            elapsed = time.monotonic() - start_time
            state = self.player.get_state()

            key = _read_arrow_key_windows()

            if key == "right":
                new_time = min(current_ms + 10_000, duration_ms - 1000)
                self.player.set_time(int(new_time))
                print("  Skipped forward 10 seconds.")

            elif key == "left":
                new_time = max(0, current_ms - 10_000)
                self.player.set_time(int(new_time))
                print("  Rewound 10 seconds.")

            # Start fade-out BEFORE the video ends.
            # This prevents the VLC final black frame from flashing first.
            if not fade_started and current_ms >= fade_start_ms:
                fade_started = True
                print("  Starting video fade-out near the end.")

                await self.fade_video_out(fade_seconds)

                # Keep waiting under black until VLC actually reaches the end.
                continue

            if state == vlc.State.Ended:
                print("  Video ended by VLC state.")
                break

            if current_ms >= duration_ms - 150:
                print("  Video reached the end by time check.")
                break

            if elapsed > max_wait_seconds:
                print("  WARNING: Video wait timed out.")
                print("  Fading out and moving on.")
                break

            if current_ms == last_position_ms:
                stuck_count += 1
            else:
                stuck_count = 0
                last_position_ms = current_ms

            if stuck_count >= 100:
                print("  WARNING: Video playback time appears stuck.")
                print("  Fading out and moving on.")
                break

            await asyncio.sleep(0.05)

        # If the video ended before fade started, still make screen black.
        if not fade_started:
            await self.fade_video_out(fade_seconds)
        else:
            self.fade_overlay.setGeometry(self.rect())
            self.fade_overlay.show()
            self.fade_overlay.raise_()
            self.fade_effect.setOpacity(1.0)
            self.process_events()


    async def _sleep_with_events(self, seconds: float) -> None:
        end_time = time.monotonic() + seconds

        while time.monotonic() < end_time:
            self.process_events()
            await asyncio.sleep(0.016)