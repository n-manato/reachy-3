"""GazeFlowWidget — animated "flowing" UI that guides the viewer's gaze
toward the next robot that will speak.

The widget paints a stream of blue chevrons + light particles travelling in a
configurable direction. It has a transparent background so it can be layered
over a window's content. Direction is a unit-ish (dx, dy):
  - mini displays use horizontal flow: (-1, 0) left or (1, 0) right
  - the top display uses a downward diagonal toward the next robot below it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ethics_podcast.displays import theme


class GazeFlowWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Direction the gaze should flow toward, as (dx, dy).
        self._dir = (1.0, 0.0)
        self._accent = theme.RGB_ACCENT
        self._phase = 0.0
        self._active = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # ── Public API ─────────────────────────────────────────────────────────

    def set_direction(self, dx: float, dy: float) -> None:
        length = math.hypot(dx, dy)
        if length < 1e-6:
            self._dir = (1.0, 0.0)
        else:
            self._dir = (dx / length, dy / length)

    def set_accent(self, rgb: tuple[int, int, int]) -> None:
        self._accent = rgb

    def start(self) -> None:
        self._active = True
        self.show()
        if not self.timer.isActive():
            self.timer.start(16)

    def stop(self) -> None:
        self._active = False
        self.timer.stop()
        self.hide()

    # ── Animation ──────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._phase += 0.02
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        if w <= 0 or h <= 0:
            return

        dx, dy = self._dir
        r, g, b = self._accent

        cx = w / 2
        cy = h / 2

        # Travel axis length across the widget in the flow direction.
        axis = abs(dx) * w + abs(dy) * h
        axis = max(axis, 1.0)

        # Perpendicular vector for chevron arms.
        px, py = -dy, dx

        # ── Flowing chevrons ────────────────────────────────────────────────
        # Several chevrons march along the direction; their position cycles so
        # they appear to stream continuously toward the next speaker.
        count = 5
        arm = min(w, h) * 0.16

        for i in range(count):
            # progress in 0..1 along the axis, looping.
            t = (self._phase * 0.9 + i / count) % 1.0

            # Fade in/out at the ends so chevrons emerge and dissolve.
            fade = math.sin(t * math.pi)
            alpha = int(40 + 150 * fade)

            # Center of this chevron along the axis (origin = widget center,
            # offset back along -dir, advancing along +dir).
            offset = (t - 0.5) * axis
            ox = cx + dx * offset
            oy = cy + dy * offset

            pen = QPen(QColor(r, g, b, alpha), 6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)

            # Chevron tip points forward (+dir); arms fan back along -dir.
            tip_x = ox + dx * arm * 0.5
            tip_y = oy + dy * arm * 0.5
            back_x = ox - dx * arm * 0.5
            back_y = oy - dy * arm * 0.5

            a1x = back_x + px * arm * 0.6
            a1y = back_y + py * arm * 0.6
            a2x = back_x - px * arm * 0.6
            a2y = back_y - py * arm * 0.6

            painter.drawLine(int(a1x), int(a1y), int(tip_x), int(tip_y))
            painter.drawLine(int(a2x), int(a2y), int(tip_x), int(tip_y))

        # ── Light particles trailing along the axis ─────────────────────────
        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(18):
            t = (self._phase * 1.4 + i * 0.137) % 1.0
            fade = math.sin(t * math.pi)
            alpha = int(30 + 140 * fade)

            offset = (t - 0.5) * axis
            # small perpendicular jitter so the stream has body.
            jitter = math.sin(self._phase * 3.0 + i) * min(w, h) * 0.08
            ox = cx + dx * offset + px * jitter
            oy = cy + dy * offset + py * jitter

            size = 3 + 5 * fade
            gr, gg, gb = theme.RGB_ACCENT_GLOW
            painter.setBrush(QColor(gr, gg, gb, alpha))
            painter.drawEllipse(
                int(ox - size / 2),
                int(oy - size / 2),
                int(size),
                int(size),
            )
