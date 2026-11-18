"""Colours, names and seat order for the BOMA display.

Single source of truth for identity: the display page derives every label,
accent and left-to-right ordering from here, so rebranding is one file plus the
three SVGs in picture/.

The palette is warm rather than the reference project's cool blue-and-white --
cream ground, deep brown ink, and three accents drawn from West and East
African textile dyeing (gold, terracotta, malachite green).
"""

from __future__ import annotations

from boma_exhibit.script_data import KORA, PULAAR, ZURI

# ── Base palette ───────────────────────────────────────────────────────────
WHITE = "#ffffff"
CREAM = "#fbf6ee"       # main background, top of the gradient
SAND = "#f2e7d5"        # bottom of the gradient
INK = "#2a1d14"         # primary text, a deep warm brown-black
INK_SOFT = "#8a715a"    # secondary text
LINE = "#e0d0b8"        # hairlines and inactive rings

ACCENT = "#c8452b"      # terracotta -- headings and the frame
ACCENT_DEEP = "#8f2f1d"
ACCENT_GLOW = "#e8a33d"

RGB_ACCENT = (200, 69, 43)
RGB_ACCENT_DEEP = (143, 47, 29)
RGB_ACCENT_GLOW = (232, 163, 61)
RGB_INK = (42, 29, 20)
RGB_CREAM = (251, 246, 238)

# ── Per-character identity ─────────────────────────────────────────────────
ROLE_ACCENT = {
    ZURI: "#e2a32d",    # gold
    KORA: "#c8452b",    # terracotta
    PULAAR: "#1f7a6b",  # malachite green
}
ROLE_ACCENT_RGB = {
    ZURI: (226, 163, 45),
    KORA: (200, 69, 43),
    PULAAR: (31, 122, 107),
}
ROLE_NAMES = {ZURI: "ZURI", KORA: "KORA", PULAAR: "PULAAR"}
ROLE_LANGUAGE = {ZURI: "Swahili", KORA: "English", PULAAR: "Fulfulde"}

# Physical left-to-right ordering. Drives marker order on screen and the
# direction of any gaze cue.
ROLE_POSITION = {ZURI: 0, KORA: 1, PULAAR: 2}

APP_TITLE = "BOMA · THREE ROBOTS, ONE AFRICA"

FONT_FAMILY = (
    "'Segoe UI', 'Noto Sans', 'DejaVu Sans', system-ui, -apple-system, sans-serif"
)


def role_accent(role: str) -> str:
    return ROLE_ACCENT.get(role, ACCENT)


def role_accent_rgb(role: str) -> tuple[int, int, int]:
    return ROLE_ACCENT_RGB.get(role, RGB_ACCENT)


def gaze_direction(from_role: str, to_role: str) -> int:
    """-1 to look left, +1 to look right, 0 for straight ahead."""
    start = ROLE_POSITION.get(from_role, 1)
    end = ROLE_POSITION.get(to_role, 1)
    if end < start:
        return -1
    if end > start:
        return 1
    return 0
