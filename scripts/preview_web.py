"""Run the whole show with mock robots: screens, voting, branches, tally.

    uv run python scripts/preview_web.py              # then open /all
    uv run python scripts/preview_web.py --auto-vote  # bots tap the screens
    uv run python scripts/preview_web.py --auto-vote --start agents_keys

Robot voices fall back to the center screen (mocks have no speaker), so open
/all or /seat/center to hear them. Votes from a preview are logged to
output/votes.jsonl like real ones -- reset the tally from /control afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random

from lta_exhibit.config import load_config
from lta_exhibit.show import Exhibit


async def _auto_vote(ex: Exhibit) -> None:
    seats = ("left", "center", "right")
    while True:
        await asyncio.sleep(random.uniform(0.6, 2.5))
        if ex.session is not None and ex.session.open:
            ex.on_vote(random.choice(seats), random.choices("ABC", weights=(2, 5, 3))[0])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-vote", action="store_true")
    ap.add_argument("--start", help="topic id to start immediately")
    ap.add_argument("--attract", type=float, default=12.0)
    ap.add_argument("--vote", type=float, default=12.0)
    args = ap.parse_args()

    os.environ["ATTRACT_SECONDS"] = str(args.attract)
    os.environ["VOTE_SECONDS"] = str(args.vote)
    config = load_config()
    ex = Exhibit(config, demo=True)
    tasks = []
    if args.auto_vote:
        tasks.append(asyncio.create_task(_auto_vote(ex)))
    if args.start:
        async def kick() -> None:
            while not ex.ready:
                await asyncio.sleep(0.2)
            ex.on_topic_tap("preview", args.start)
        tasks.append(asyncio.create_task(kick()))
    await ex.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
