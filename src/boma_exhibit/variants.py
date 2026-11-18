"""Choosing which pre-rendered wording of the sketch to play next.

Shuffle mode is a bag rather than plain random choice: every variant is shown
once before any repeats. On an exhibit floor, plain randomness reads as broken
-- visitors who catch the show twice in a row and hear the identical wording
conclude it is a recording, which is exactly the impression the variants exist
to avoid.

A plain bag is not enough on its own, though. Two things had to be added after
watching it run:

  - **A floor on the distance between repeats.** A bag only stops a variant
    being adjacent to itself. The last pick of one bag and the second pick of
    the next can still be the same one -- two runs apart, and it reads as the
    show getting stuck. Each slot is now drawn from the variants that have not
    appeared in the last few picks, carrying that history across the seam.
  - **Memory that outlives the process.** A fresh picker had no idea what was
    on screen a moment ago, so restarting the program and pressing PLAY could
    hand you back the variant you had just been watching.
"""

from __future__ import annotations

import json
import random
from collections import deque
from pathlib import Path

# Never show the same variant twice inside this many runs. A plain shuffle bag
# only stops the two halves of a seam being *adjacent*: the last pick of one bag
# and the second pick of the next can still be the same variant, which is two
# runs apart and reads as "it keeps playing the same one". Observed in the wild
# as v05, v04, v05.
MIN_GAP = 3

# Which variant played last, remembered across restarts. Without this a fresh
# process has no idea what was on screen a moment ago, so restarting mid-show
# and pressing PLAY could hand you the very variant you just stopped.
STATE_FILE = ".playback_state.json"


def list_variants(session_dir: Path) -> list[Path]:
    """Every renderable variant directory, in order.

    Reads index.json when it is there, but falls back to scanning for v*/
    directories -- so deleting a variant you did not like is enough to retire
    it, without having to edit the index by hand.
    """
    found = sorted(
        path
        for path in session_dir.glob("v*")
        if path.is_dir() and (path / "conversation.json").exists()
    )
    index_path = session_dir / "index.json"
    if not index_path.exists():
        return found

    try:
        listed = json.loads(index_path.read_text(encoding="utf-8")).get("variants", [])
    except (OSError, ValueError):
        return found

    by_name = {path.name: path for path in found}
    ordered = [by_name[name] for name in listed if name in by_name]
    # Anything on disk but missing from the index still counts.
    ordered += [path for path in found if path.name not in set(listed)]
    return ordered


# The three ways the exhibit can pick what to play next. The operator cycles
# through them with one button on the display, the way a music player does.
SHUFFLE = "shuffle"        # every variant once, in a random order, then reshuffle
SEQUENTIAL = "sequential"  # v01, v02, ... vNN, back to v01
SINGLE = "one"             # always the first variant -- the verbatim script

MODES = (SHUFFLE, SEQUENTIAL, SINGLE)

MODE_LABELS = {
    SHUFFLE: "SHUFFLE",
    SEQUENTIAL: "ALL",
    SINGLE: "ONE",
}


def normalize_mode(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in MODES else SHUFFLE


class VariantPicker:
    """Chooses the next variant according to the current loop mode."""

    def __init__(
        self,
        session_dir: Path,
        mode: str = SHUFFLE,
        rng: random.Random | None = None,
    ) -> None:
        self.session_dir = session_dir
        self._mode = normalize_mode(mode)
        self._rng = rng or random.Random()
        self._bag: list[Path] = []
        self._recent: deque[str] = deque(maxlen=max(1, MIN_GAP - 1))
        self._load_state()

    # ── State that outlives the process ───────────────────────────────────

    def _state_path(self) -> Path:
        return self.session_dir / STATE_FILE

    def _load_state(self) -> None:
        try:
            raw = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name in raw.get("recent", [])[-self._recent.maxlen :]:
            if isinstance(name, str):
                self._recent.append(name)

    def _save_state(self) -> None:
        try:
            self._state_path().write_text(
                json.dumps({"recent": list(self._recent)}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ── Mode ──────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch modes. Takes effect on the very next pick."""
        mode = normalize_mode(mode)
        if mode == self._mode:
            return
        self._mode = mode
        # Drop the half-dealt bag; keeping it would make the first pick after a
        # switch follow the old mode's plan.
        self._bag = []

    @property
    def last_played(self) -> str | None:
        return self._recent[-1] if self._recent else None

    # ── Picking ───────────────────────────────────────────────────────────

    def available(self) -> list[Path]:
        return list_variants(self.session_dir)

    def next(self) -> Path | None:
        """The next variant directory, or None when nothing is rendered."""
        available = self.available()
        if not available:
            return None

        if self._mode == SINGLE:
            chosen = available[0]
        elif self._mode == SEQUENTIAL:
            chosen = self._next_in_order(available)
        else:
            chosen = self._next_shuffled(available)

        self._recent.append(chosen.name)
        self._save_state()
        return chosen

    def _next_in_order(self, available: list[Path]) -> Path:
        """Step to the variant after the last one, wrapping at the end.

        Tracked by name rather than index so deleting a variant mid-show only
        shortens the rotation instead of jumping to an unrelated place in it.
        """
        names = [path.name for path in available]
        last = self.last_played
        if last is not None and last in names:
            return available[(names.index(last) + 1) % len(names)]
        return available[0]

    def _next_shuffled(self, available: list[Path]) -> Path:
        if not self._bag:
            self._bag = self._deal(available)
        return self._bag.pop(0)

    def _deal(self, available: list[Path]) -> list[Path]:
        """Lay out a fresh bag that never echoes the recent past.

        The bag still guarantees every variant plays before any repeats. What
        it adds is a floor on the distance between two showings of the same
        one: each slot is drawn at random from the variants that have not
        appeared in the last few picks, carrying the history forward from the
        previous bag so the seam between bags is covered too.

        The window shrinks for small libraries. With N variants only N-1 can be
        held back at once; asking for more would leave nothing to choose from
        and the fallback would repeat something immediately.
        """
        pool = list(available)
        window = min(self._recent.maxlen or 1, max(1, len(pool) - 1))
        history: deque[str] = deque(list(self._recent)[-window:], maxlen=window)

        bag: list[Path] = []
        while pool:
            allowed = [path for path in pool if path.name not in history]
            chosen = self._rng.choice(allowed or pool)
            pool.remove(chosen)
            bag.append(chosen)
            history.append(chosen.name)
        return bag

    def next_conversation(self) -> Path | None:
        variant = self.next()
        return variant / "conversation.json" if variant else None
