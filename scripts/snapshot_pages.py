"""Drive the screens through fixed states and screenshot each one.

    uv run python scripts/snapshot_pages.py [--size 3840x2160] [--out DIR]

No robots, no audio: a WebDisplayManager on port 8031 is pushed into each
phase in turn while headless Chrome captures the seat that matters. Use it to
check layout at the real panel resolution before the event.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from lta_exhibit.web_display import WebDisplayManager

PORT = 8031


def shot(out: Path, name: str, seat: str, size: str) -> None:
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or "chromium-browser"
    w, h = size.split("x")
    profile = out / f".chrome_{name}"
    result = subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
         # Time for the socket's first state to arrive and render.
         "--virtual-time-budget=4000",
         f"--window-size={w},{h}", "--enable-logging=stderr", "--v=0",
         f"--user-data-dir={profile}", f"--screenshot={out / (name + '.png')}",
         f"http://127.0.0.1:{PORT}/seat/{seat}?nostart&snapshot"],
        capture_output=True, text=True, timeout=60,
    )
    shutil.rmtree(profile, ignore_errors=True)
    errors = [line for line in result.stderr.splitlines() if "Uncaught" in line or "CONSOLE" in line]
    print(f"  {name:<8} /seat/{seat:<7} {'OK' if not errors else errors[:2]}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--out", default="output/snapshots")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    d = WebDisplayManager(port=PORT)
    loop = asyncio.get_running_loop()

    async def snap(name: str, seat: str) -> None:
        await loop.run_in_executor(None, shot, out, name, seat, args.size)

    tonight = {"counts": {"A": 7, "B": 15, "C": 9}, "total": 31, "runs": 4}
    await asyncio.sleep(0.5)
    d.set_ready(True)  # else the attract view says "warming up", no topic cards
    d.show_attract(45)
    await snap("attract", "right")

    d.begin_topic("agents_keys", tonight)
    d.show_line("nova", "Speed wins. We launched a pilot that auto-resolved eighty percent of "
                "tier-one tickets in six weeks and saved two point four million.", 9.0,
                [0.5] * 200, None, -0.85, "aegis")
    await snap("speak", "left")
    await snap("listen", "right")

    d.open_vote()
    d.start_countdown(20)
    d.update_counts({"A": 3, "B": 6, "C": 2}, 11)
    await snap("vote", "center")

    d.show_result("B", {"A": 3, "B": 6, "C": 2}, 11, tonight)
    d.show_line("nova", "I can live with that. We still auto-approve clean cases and bank most "
                "of the savings.", 8.0, [0.4] * 200, None, -0.85, "aegis")
    await snap("result", "left")

    d.show_close(tonight)
    await snap("close", "center")
    d.close()
    print(f"\nScreenshots in {out}")


if __name__ == "__main__":
    asyncio.run(main())
