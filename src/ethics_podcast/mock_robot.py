"""MockRobot — a no-op stand-in for RobotController.

Lets the exhibit run on a machine with NO robots (and without the reachy_mini
SDK / GStreamer), e.g. a developer laptop that only has a git checkout. Every
motion/audio command is a no-op; audio is expected to play on the computer's
speakers (audio_mode="computer"), so push_audio is never called in that mode.

This module deliberately imports nothing from reachy_mini.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MockRobot:
    """Implements the RobotController surface used by the playback loop."""

    def __init__(self, role: str, name: str | None = None):
        self.role = role
        self.name = name or f"mock-{role}"
        # In demo mode audio plays via the computer/TV path, never the robot,
        # so this stays False and _play_robot_audio short-circuits.
        self.audio_ready = False

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def ensure_alive(self) -> None:
        pass

    def stop(self) -> None:
        pass

    # ── Motion / move queue (all no-ops) ──────────────────────────────────────
    def wiggle_antennas(self, *args, **kwargs) -> None:
        pass

    def clear_moves(self, *args, **kwargs) -> None:
        pass

    def set_speech_offsets(self, *args, **kwargs) -> None:
        pass

    def queue_speaking_ready_pose(self, *args, **kwargs) -> None:
        pass

    def queue_power_off_pose(self, *args, **kwargs) -> None:
        pass

    def queue_goto(self, *args, **kwargs) -> None:
        pass

    def queue_emotion(self, *args, **kwargs) -> None:
        pass

    def queue_dance(self, *args, **kwargs) -> None:
        pass

    def move_head(self, *args, **kwargs) -> None:
        pass

    # ── Audio ──────────────────────────────────────────────────────────────────
    def push_audio(self, *args, **kwargs) -> None:
        # No robot speaker in demo mode; audio is played on the computer.
        pass


def connect_mock_robots(
    role_names: tuple[str, str, str] = ("student", "professor", "moderator"),
) -> tuple[MockRobot, MockRobot, MockRobot]:
    """Create three MockRobots (Bentham / Korsgaard / Aquinas)."""
    a, b, c = (MockRobot(role) for role in role_names)
    logger.info("Using MOCK robots (no hardware, demo mode).")
    return a, b, c
