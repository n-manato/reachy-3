"""Small cross-platform input helpers shared across the display/video modes."""

from __future__ import annotations

try:  # Windows-only console input; a no-op everywhere else.
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None


def read_arrow_key_windows() -> str | None:
    """Read one arrow key on Windows without blocking.

    Returns "right" / "left" for the corresponding arrow key, or None when no
    arrow key is pending (or when not running on Windows).
    """
    if msvcrt is None or not msvcrt.kbhit():
        return None

    first = msvcrt.getch()

    # Arrow keys send a prefix byte first.
    if first not in (b"\x00", b"\xe0"):
        return None

    second = msvcrt.getch()

    if second == b"M":
        return "right"

    if second == b"K":
        return "left"

    return None
