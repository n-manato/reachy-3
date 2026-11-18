"""Audience voting: one open ballot at a time, plus the night's running tally.

Every accepted tap is appended to output/votes.jsonl the moment it lands, so a
crash or a restart loses nothing, and the evening's results can be exported
for the Executive MBA team afterwards (menu 6).
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from lta_exhibit.topics import OPTION_KEYS, get_topic

# A second tap on the same screen within this window is treated as a bounce
# (or an over-eager visitor) rather than a new voter.
SCREEN_DEBOUNCE_SECS = 1.2


@dataclass
class VoteSession:
    topic_id: str
    run_id: str
    counts: Counter = field(default_factory=Counter)
    open: bool = True
    _last_tap: dict = field(default_factory=dict)

    def cast(self, seat: str, option: str) -> bool:
        """Count a tap. Returns False when it was ignored."""
        if not self.open or option not in OPTION_KEYS:
            return False
        now = time.monotonic()
        if now - self._last_tap.get(seat, -1e9) < SCREEN_DEBOUNCE_SECS:
            return False
        self._last_tap[seat] = now
        self.counts[option] += 1
        return True

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def close(self) -> str | None:
        """Close the ballot; the winning option, or None if nobody voted.

        A tie is broken at random among the leaders -- nobody in the room can
        see which way a tie "should" go, and a fixed preference would show.
        """
        self.open = False
        if not self.total:
            return None
        top = max(self.counts.values())
        leaders = [k for k in OPTION_KEYS if self.counts[k] == top]
        return random.choice(leaders)

    def as_dict(self) -> dict:
        return {k: self.counts.get(k, 0) for k in OPTION_KEYS}


class VoteLog:
    """Append-only record of every vote, and the night's totals from it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.totals: dict[str, Counter] = defaultdict(Counter)
        self.runs: Counter = Counter()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        for raw in lines:
            # Each line on its own: a half-written line from a power cut must
            # cost that one row, not every vote recorded after it.
            try:
                row = json.loads(raw) if raw.strip() else None
                if not isinstance(row, dict):
                    continue
                if row.get("kind") == "vote":
                    self.totals[row["topic"]][row["option"]] += 1
                elif row.get("kind") == "result":
                    self.runs[row["topic"]] += 1
                elif row.get("kind") == "reset":
                    self.totals.clear()
                    self.runs.clear()
            except (ValueError, KeyError, TypeError):
                continue

    def _append(self, row: dict) -> None:
        row = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("ab+") as handle:
                # Never glue a new row onto a truncated last line.
                if handle.tell() > 0:
                    handle.seek(-1, 2)
                    if handle.read(1) != b"\n":
                        handle.write(b"\n")
                handle.write((json.dumps(row) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            # A full or read-only disk must not stop the vote being counted.
            print(f"  [votes] could not write log: {exc}")

    def record_vote(self, session: VoteSession, seat: str, option: str) -> None:
        self.totals[session.topic_id][option] += 1
        self._append({"kind": "vote", "run": session.run_id, "topic": session.topic_id,
                      "seat": seat, "option": option})

    def record_result(self, session: VoteSession, winner: str | None) -> None:
        self.runs[session.topic_id] += 1
        self._append({"kind": "result", "run": session.run_id, "topic": session.topic_id,
                      "counts": session.as_dict(), "winner": winner})

    def reset(self) -> None:
        """Start a fresh tally (e.g. after rehearsal). History stays in the file."""
        self.totals.clear()
        self.runs.clear()
        self._append({"kind": "reset"})

    def tonight(self, topic_id: str) -> dict:
        counts = self.totals.get(topic_id, Counter())
        total = sum(counts.values())
        return {
            "counts": {k: counts.get(k, 0) for k in OPTION_KEYS},
            "total": total,
            "runs": self.runs.get(topic_id, 0),
        }

    def tonight_all(self) -> dict:
        return {t: self.tonight(t) for t in set(self.totals) | set(self.runs)}

    def export_csv(self, out_path: Path) -> Path:
        """One row per topic and option, counting only votes since the last reset."""
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["topic", "option", "label", "votes", "share"])
            for topic_id, counts in self.totals.items():
                try:
                    topic = get_topic(topic_id)
                except KeyError:
                    continue  # a topic that has since been removed
                total = sum(counts.values()) or 1
                for key in OPTION_KEYS:
                    writer.writerow([
                        topic["title"], key, topic["options"][key]["label"],
                        counts.get(key, 0), f"{counts.get(key, 0) / total:.0%}",
                    ])
        return out_path
