import math
import random
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget


class AudioOrbWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.level = 0.0
        self.display_level = 0.0

        self.wave = deque([0.0] * 96, maxlen=96)
        self.status_text = "SIGNAL IDLE"
        self.speaker_name = ""

        self.phase = 0.0

        self.particles = [
            {
                "angle": random.uniform(0, math.tau),
                "distance": random.uniform(0.65, 1.45),
                "speed": random.uniform(0.002, 0.008),
                "size": random.uniform(1.0, 3.0),
                "alpha": random.randint(35, 95),
            }
            for _ in range(44)
        ]

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def set_audio_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def set_status(self, text: str) -> None:
        self.status_text = text

    def set_speaker(self, name: str) -> None:
        self.speaker_name = name

    def stop(self) -> None:
        self.level = 0.0
        self.status_text = "SIGNAL IDLE"
        self.speaker_name = ""

    def _tick(self) -> None:
        # Smooth level so the orb feels organic, not jittery.
        self.display_level += (self.level - self.display_level) * 0.22

        self.phase += 0.018 + self.display_level * 0.018

        # Fake waveform shape driven by real audio level.
        noise = random.uniform(-1.0, 1.0)
        soft_wave = math.sin(self.phase * 5.0) * 0.35
        self.wave.append((noise * 0.65 + soft_wave) * self.display_level)

        for particle in self.particles:
            particle["angle"] += particle["speed"] + self.display_level * 0.012

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        cx = w // 2
        cy = h // 2 - 38

        base_radius = min(w, h) * 0.225
        pulse = self.display_level * 30
        radius = base_radius + pulse

        # Important:
        # Text uses fixed radius so it does not shake with the orb.
        text_radius = base_radius

        # ── Background ──────────────────────────────────────────

        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor("#050612"))
        bg.setColorAt(0.45, QColor("#111334"))
        bg.setColorAt(1.0, QColor("#030308"))
        painter.fillRect(self.rect(), bg)

        # Large background glow.
        background_glow = QRadialGradient(cx, cy, min(w, h) * 0.72)
        background_glow.setColorAt(0.0, QColor(0, 180, 255, 42))
        background_glow.setColorAt(0.35, QColor(80, 60, 180, 24))
        background_glow.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(background_glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            int(cx - min(w, h) * 0.72),
            int(cy - min(w, h) * 0.72),
            int(min(w, h) * 1.44),
            int(min(w, h) * 1.44),
        )

        # Subtle scan lines.
        painter.setPen(QPen(QColor(255, 255, 255, 10), 1))
        line_gap = 18
        for y in range(0, h, line_gap):
            painter.drawLine(0, y, w, y)

        # ── Outer HUD Rings ─────────────────────────────────────

        painter.setBrush(Qt.BrushStyle.NoBrush)

        for idx, scale in enumerate([1.28, 1.48, 1.72, 2.05]):
            alpha = max(18, 78 - idx * 16)
            ring_radius = base_radius * scale + math.sin(self.phase * 2.0 + idx) * 3.0

            painter.setPen(QPen(QColor(24, 212, 245, alpha), 1))

            painter.drawEllipse(
                int(cx - ring_radius),
                int(cy - ring_radius),
                int(ring_radius * 2),
                int(ring_radius * 2),
            )

        # Rotating short HUD segments.
        arc_radius = base_radius * 1.58
        painter.setPen(QPen(QColor(120, 240, 255, 130), 2))

        for i in range(12):
            angle = self.phase * 1.55 + i * (math.tau / 12)
            length = 0.16 + self.display_level * 0.12

            x1 = cx + math.cos(angle) * arc_radius
            y1 = cy + math.sin(angle) * arc_radius
            x2 = cx + math.cos(angle + length) * arc_radius
            y2 = cy + math.sin(angle + length) * arc_radius

            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Smaller counter-rotating segments.
        inner_arc_radius = base_radius * 1.18
        painter.setPen(QPen(QColor(190, 90, 255, 95), 2))

        for i in range(8):
            angle = -self.phase * 1.2 + i * (math.tau / 8)
            length = 0.13 + self.display_level * 0.08

            x1 = cx + math.cos(angle) * inner_arc_radius
            y1 = cy + math.sin(angle) * inner_arc_radius
            x2 = cx + math.cos(angle + length) * inner_arc_radius
            y2 = cy + math.sin(angle + length) * inner_arc_radius

            painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        # ── Particles ───────────────────────────────────────────

        painter.setPen(Qt.PenStyle.NoPen)

        for particle in self.particles:
            angle = particle["angle"]
            dist = base_radius * particle["distance"]

            x = cx + math.cos(angle) * dist
            y = cy + math.sin(angle) * dist

            alpha = int(particle["alpha"] + self.display_level * 110)
            alpha = max(0, min(180, alpha))

            size = particle["size"] + self.display_level * 2.4

            painter.setBrush(QColor(90, 230, 255, alpha))
            painter.drawEllipse(
                int(x - size / 2),
                int(y - size / 2),
                int(size),
                int(size),
            )

        # ── Main Glow ───────────────────────────────────────────

        glow = QRadialGradient(cx, cy, radius * 2.05)
        glow.setColorAt(0.0, QColor(0, 220, 255, 95))
        glow.setColorAt(0.38, QColor(0, 160, 255, 42))
        glow.setColorAt(0.72, QColor(160, 70, 255, 20))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))

        painter.setBrush(glow)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            int(cx - radius * 2.05),
            int(cy - radius * 2.05),
            int(radius * 4.1),
            int(radius * 4.1),
        )

        # ── Main Orb ────────────────────────────────────────────

        orb = QRadialGradient(cx - radius * 0.25, cy - radius * 0.35, radius * 1.4)
        orb.setColorAt(0.0, QColor(35, 45, 95, 230))
        orb.setColorAt(0.42, QColor(14, 18, 54, 235))
        orb.setColorAt(0.72, QColor(8, 12, 36, 245))
        orb.setColorAt(1.0, QColor(0, 210, 255, 210))

        painter.setBrush(orb)
        painter.setPen(QPen(QColor(0, 230, 255, 190), 2))
        painter.drawEllipse(
            int(cx - radius),
            int(cy - radius),
            int(radius * 2),
            int(radius * 2),
        )

        # Bright rim.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(160, 245, 255, 180), 1))
        painter.drawEllipse(
            int(cx - radius * 0.94),
            int(cy - radius * 0.94),
            int(radius * 1.88),
            int(radius * 1.88),
        )

        # Inner shadow ring.
        painter.setPen(QPen(QColor(20, 40, 90, 160), 2))
        painter.drawEllipse(
            int(cx - radius * 0.72),
            int(cy - radius * 0.72),
            int(radius * 1.44),
            int(radius * 1.44),
        )

        # ── Waveform inside orb ─────────────────────────────────

        points = list(self.wave)
        wave_width = radius * 1.48
        start_x = cx - wave_width / 2
        step = wave_width / max(1, len(points) - 1)

        # Cyan wave.
        painter.setPen(QPen(QColor(35, 230, 255, 220), 2))

        last_x = None
        last_y = None

        for i, value in enumerate(points):
            x = start_x + i * step
            y = cy + value * radius * 0.42

            # Keep waveform inside the orb visually.
            distance_from_center = abs(x - cx) / (wave_width / 2)
            edge_fade = max(0.0, 1.0 - distance_from_center**2)
            y = cy + (y - cy) * edge_fade

            if last_x is not None:
                painter.drawLine(int(last_x), int(last_y), int(x), int(y))

            last_x = x
            last_y = y

        # Purple secondary wave.
        painter.setPen(QPen(QColor(205, 95, 255, 185), 2))

        last_x = None
        last_y = None

        for i, value in enumerate(points):
            x = start_x + i * step
            y = cy - value * radius * 0.31
            y += math.sin(i * 0.32 + self.phase * 4.0) * self.display_level * 13

            distance_from_center = abs(x - cx) / (wave_width / 2)
            edge_fade = max(0.0, 1.0 - distance_from_center**2)
            y = cy + (y - cy) * edge_fade

            if last_x is not None:
                painter.drawLine(int(last_x), int(last_y), int(x), int(y))

            last_x = x
            last_y = y

        # Center signal line.
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.drawLine(
            int(cx - wave_width * 0.48),
            int(cy),
            int(cx + wave_width * 0.48),
            int(cy),
        )

        # ── Bottom Text, fixed position ─────────────────────────

        speaker_y = int(cy + text_radius + 54)
        status_y = int(cy + text_radius + 84)

        if self.speaker_name:
            painter.setPen(QColor(24, 212, 245, 210))

            speaker_font = QFont("Arial")
            speaker_font.setPointSize(11)
            speaker_font.setBold(True)
            speaker_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 118)
            painter.setFont(speaker_font)

            painter.drawText(
                0,
                speaker_y,
                w,
                28,
                Qt.AlignmentFlag.AlignCenter,
                self.speaker_name.upper(),
            )

        painter.setPen(QColor(255, 255, 255, 225))

        status_font = QFont("Arial")
        status_font.setPointSize(18)
        status_font.setBold(True)
        status_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 112)
        painter.setFont(status_font)

        painter.drawText(
            0,
            status_y,
            w,
            38,
            Qt.AlignmentFlag.AlignCenter,
            self.status_text,
        )

        # Small bottom signal dots.
        dot_y = status_y + 50
        dot_count = 7
        dot_gap = 16
        start_dot_x = cx - ((dot_count - 1) * dot_gap) / 2

        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(dot_count):
            active = (i / max(1, dot_count - 1)) < self.display_level
            alpha = 210 if active else 55
            size = 5 if active else 3

            painter.setBrush(QColor(24, 212, 245, alpha))
            painter.drawEllipse(
                int(start_dot_x + i * dot_gap - size / 2),
                int(dot_y - size / 2),
                size,
                size,
            )