"""White + blue, near-future-but-simple palette for the multi-display UI.

Centralizes colors, fonts, and per-role accents so every window stays
visually consistent. Colors are plain hex / QColor tuples so they can be
used in both Qt stylesheets and QPainter code.
"""

from __future__ import annotations

# ── Base palette ────────────────────────────────────────────────────────────
# Light, near-future feel: near-white backgrounds, deep/bright blue accents.

WHITE = "#ffffff"
SNOW = "#f4f8ff"          # very light blue-white, main background
MIST = "#e7f0fb"          # slightly deeper panel background
INK = "#0b2a4a"           # primary text on light backgrounds (deep navy-blue)
INK_SOFT = "#3f6a96"      # secondary text

ACCENT = "#1f8bff"        # primary blue accent
ACCENT_DEEP = "#0a5bd6"   # deeper blue
ACCENT_GLOW = "#6fc0ff"   # light glow blue
LINE = "#cfe0f5"          # hairline separators on light bg

# RGB tuples for QPainter (QColor(*rgb, alpha)).
RGB_ACCENT = (31, 139, 255)
RGB_ACCENT_DEEP = (10, 91, 214)
RGB_ACCENT_GLOW = (111, 192, 255)
RGB_INK = (11, 42, 74)
RGB_WHITE = (255, 255, 255)

# ── Per-role accents ──────────────────────────────────────────────────────
# Roles match the rest of the codebase: student / professor / moderator.
# Kept within the blue family (with subtle hue shifts) so the white+blue
# identity holds while each robot is still distinguishable.

ROLE_ACCENT = {
    "student": "#1f8bff",     # bright blue  (Bentham-Bot / Left)
    "professor": "#0a5bd6",   # deep blue    (Korsgaard-Bot / Center)
    "moderator": "#21b6e6",   # cyan-blue    (Aquinas-Bot / Right)
}

ROLE_ACCENT_RGB = {
    "student": (31, 139, 255),
    "professor": (10, 91, 214),
    "moderator": (33, 182, 230),
}

ROLE_NAMES = {
    "student": "Bentham-Bot",
    "professor": "Korsgaard-Bot",
    "moderator": "Aquinas-Bot",
}

ROLE_PERSPECTIVE = {
    "student": "Consequences / Utility",
    "professor": "Duty / Respect",
    "moderator": "Virtue / Purpose",
}

# Physical left-to-right ordering used for gaze direction.
#   0 = left, 1 = center, 2 = right
ROLE_POSITION = {
    "student": 0,
    "professor": 1,
    "moderator": 2,
}

# ── Fonts ──────────────────────────────────────────────────────────────────

FONT_FAMILY = "Segoe UI"


def role_accent(role: str) -> str:
    return ROLE_ACCENT.get(role, ACCENT)


def role_accent_rgb(role: str) -> tuple[int, int, int]:
    return ROLE_ACCENT_RGB.get(role, RGB_ACCENT)


def gaze_direction(from_role: str, to_role: str) -> int:
    """Return -1 (flow left), +1 (flow right), or 0 (same / unknown).

    Direction is derived from the robots' physical left-to-right positions,
    so a mini display under one robot streams its gaze-guidance toward the
    side where the next speaker physically sits.
    """
    a = ROLE_POSITION.get(from_role)
    b = ROLE_POSITION.get(to_role)

    if a is None or b is None or a == b:
        return 0

    return 1 if b > a else -1
