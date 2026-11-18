"""Names, colours and seat order for the touch screens.

A dark "boardroom" palette: near-black ground, warm off-white type, and one
accent per robot so a visitor can tell at a glance whose screen they are at.
"""

from __future__ import annotations

from lta_exhibit.personas import AEGIS, NOVA, PROFILES, SAGE

APP_TITLE = "Leading Through AI"
APP_SUBTITLE = "The Robot C-Suite"
SHOWCASE_LABEL = "AI-CORE Pop-Up Showcase"

ROLE_ACCENT = {
    NOVA: "#f2a33a",   # amber
    SAGE: "#2ec4b6",   # teal
    AEGIS: "#5b8def",  # blue
}

ROLE_POSITION = {NOVA: 0, SAGE: 1, AEGIS: 2}

ROLE_NAMES = {role: p["name"] for role, p in PROFILES.items()}


def page_identity() -> dict:
    """Everything the page needs to label and colour the cast."""
    return {
        role: {
            "name": p["name"],
            "title": p["title"],
            "archetype": p["archetype"],
            "tagline": p["tagline"],
            "seat": p["seat"],
            "accent": ROLE_ACCENT[role],
            "position": ROLE_POSITION[role],
        }
        for role, p in PROFILES.items()
    }
