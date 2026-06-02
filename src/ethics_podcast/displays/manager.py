"""DisplayManager — owns the four exhibit windows and exposes a single
high-level API the playback loop drives.

Windows:
  - 1 TopDisplayWindow (large portrait, above the robots)
  - 3 MiniDisplayWindows (one per role, under each robot)

Monitor placement is config-driven. When fewer monitors are connected than
needed, or DISPLAY_TEST_MODE is on, all four windows are tiled on the primary
screen so the full flow (video → topic → subtitles → gaze) can be verified on
one machine without the exhibit hardware.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ethics_podcast.displays import theme
from ethics_podcast.displays.mini_window import MiniDisplayWindow
from ethics_podcast.displays.top_window import TopDisplayWindow


ROLES = ("student", "professor", "moderator")


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class DisplayManager:
    """Coordinates the top display and three mini displays as one unit."""

    def __init__(
        self,
        video_path: Path | None = None,
        top_monitor: int = 0,
        mini_monitors: tuple[int, int, int] = (1, 2, 3),
        test_mode: bool = False,
    ):
        self.app = _get_app()

        self.top_monitor = top_monitor
        self.mini_monitors = mini_monitors
        self.test_mode = test_mode

        self.top = TopDisplayWindow(video_path)
        self.minis: dict[str, MiniDisplayWindow] = {
            role: MiniDisplayWindow(role) for role in ROLES
        }

        self._place_windows()

    # ── Window placement ─────────────────────────────────────────────────────

    def _place_windows(self) -> None:
        screens = self.app.screens()
        needed = 4

        if self.test_mode or len(screens) < needed:
            if not self.test_mode:
                print(
                    f"  [display] Only {len(screens)} screen(s) detected, "
                    f"need {needed}. Falling back to tiled test layout."
                )
            self._tile_on_primary(screens[0] if screens else None)
            return

        self._place_fullscreen(self.top, screens[self.top_monitor])

        for role, mon in zip(ROLES, self.mini_monitors):
            idx = mon if 0 <= mon < len(screens) else 0
            self._place_fullscreen(self.minis[role], screens[idx])

    def _place_fullscreen(self, win, screen) -> None:
        geo = screen.geometry()
        win.setGeometry(geo)
        win.show()
        handle = win.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        win.showFullScreen()
        self.app.processEvents()

    def _tile_on_primary(self, screen) -> None:
        """Tile all four windows on one screen for development/testing."""
        if screen is None:
            # No screen info — just show windows at default positions.
            self.top.resize(360, 640)
            self.top.show()
            for i, role in enumerate(ROLES):
                self.minis[role].resize(300, 220)
                self.minis[role].move(400, i * 240)
                self.minis[role].show()
            self.app.processEvents()
            return

        area = screen.availableGeometry()
        margin = 16

        # Top display: tall portrait strip on the left (~32% width).
        top_w = int(area.width() * 0.30)
        top_h = area.height() - 2 * margin
        self.top.setGeometry(
            area.x() + margin,
            area.y() + margin,
            top_w,
            top_h,
        )
        self.top.show()

        # Mini displays: stacked on the right, one row each.
        mini_x = area.x() + top_w + 2 * margin
        mini_w = area.width() - top_w - 3 * margin
        usable_h = area.height() - 4 * margin
        mini_h = usable_h // 3

        for i, role in enumerate(ROLES):
            win = self.minis[role]
            win.setGeometry(
                mini_x,
                area.y() + margin + i * (mini_h + margin),
                mini_w,
                mini_h,
            )
            win.show()

        self.app.processEvents()

    # ── Video (delegated to top display) ─────────────────────────────────────

    def load_video(self, video_path: Path) -> None:
        self.top.load_video(video_path)

    def play_video(self) -> None:
        self.top.play()
        # Minis sit idle (dimmed, not black) while the video plays.
        for role in ROLES:
            self.minis[role].set_idle(animate=False)

    async def wait_until_near_end_and_pause(self) -> None:
        await self.top.wait_until_near_end_and_pause()

    # ── Presentation flow ─────────────────────────────────────────────────────

    def show_topic(self, title: str, topic: str) -> None:
        """Video finished: blast the topic on the top display."""
        self.top.show_topic(title, topic)
        for role in ROLES:
            self.minis[role].set_idle()

    def point_to(self, next_role: str | None) -> None:
        """Guide every display's gaze toward the next robot to speak."""
        self.top.set_next_speaker(next_role)

        for role in ROLES:
            mini = self.minis[role]
            if next_role is None or role == next_role:
                mini.set_idle()
            else:
                mini.show_gaze(theme.gaze_direction(role, next_role))

    def show_subtitle(
        self,
        role: str,
        text: str,
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
    ) -> None:
        """A robot starts speaking: subtitle on its mini, others dim."""
        self.top.set_current_speaker(role)

        for r in ROLES:
            if r == role:
                self.minis[r].show_subtitle(text, audio_path=audio_path)
            else:
                self.minis[r].set_idle()

    def finish_subtitle(self, role: str, next_role: str | None = None) -> None:
        """A robot finished: fade its mini, then guide gaze to the next."""
        self.minis[role].set_idle()
        self.point_to(next_role)

    # ── Event loop ─────────────────────────────────────────────────────────────

    def process_events(self) -> None:
        self.app.processEvents()

    async def pump_events_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.process_events()
            await asyncio.sleep(0.016)

    def close(self) -> None:
        try:
            self.top.close()
        except Exception:
            pass
        for role in ROLES:
            try:
                self.minis[role].close()
            except Exception:
                pass
        self.process_events()
