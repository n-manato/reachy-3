"""Interactive menu for the Leading Through AI exhibit.

    0. Setup: discover & assign robots
    1. Draft scripts (OpenAI)         -- then review output/<topic>/script.md
    2. Render audio (ElevenLabs)      -- only changed lines are re-rendered
    3. Play exhibit
    4. Play exhibit without robots (screens only)
    5. Restart robot daemons
    6. Export tonight's votes (CSV)
    7. Exit

`lta-exhibit --exhibit` skips the menu and runs the show (systemd);
`--demo` does the same with mock robots.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from lta_exhibit.config import OUTPUT_DIR, PROJECT_ROOT, ROLE_ORDER, VOTES_PATH, Config, load_config
from lta_exhibit.personas import PROFILES
from lta_exhibit.topics import TOPIC_IDS

ROLE_LABELS = {r: f"{PROFILES[r]['name']} / {PROFILES[r]['seat']}" for r in ROLE_ORDER}


def _prompt(message: str, default: str = "") -> str:
    try:
        answer = input(message).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


# ── 0. Setup ───────────────────────────────────────────────────────────────


def _setup_robots(config: Config) -> None:
    """Find the robots on the network and assign each one a seat."""
    from lta_exhibit.robot_admin import (
        arp_table,
        daemon_status,
        local_subnet,
        scan_subnet,
        update_env_hosts,
    )

    print("\n  Configured seats:")
    macs = arp_table()
    for role in ROLE_ORDER:
        host = config.robot(role).host
        status = daemon_status(host)
        state = str((status or {}).get("state", "no reply"))
        mac = macs.get(host, "?")
        print(f"    {ROLE_LABELS[role]:<22} {host:<16} {state:<16} {mac}")

    subnet = local_subnet()
    if subnet is None:
        print("\n  Could not work out the robot subnet; check the Wi-Fi adapter.")
        return

    if _prompt(f"\n  Scan {subnet} for robots? (y/n) [y]: ", "y").lower() != "y":
        return

    print(f"  Scanning {subnet}... (a few seconds)")
    found = scan_subnet(subnet)
    if not found:
        print("  No robot daemons answered.")
        return

    macs = arp_table()
    print(f"\n  Found {len(found)} robot daemon(s):")
    for index, host in enumerate(found, start=1):
        status = daemon_status(host) or {}
        print(
            f"    {index}. {host:<16} state={status.get('state', '?'):<16} "
            f"{macs.get(host, '?')}"
        )

    if _prompt("\n  Assign these to seats? (y/n) [n]: ", "n").lower() != "y":
        return

    assignment: dict[str, str] = {}
    for role in ROLE_ORDER:
        current = config.robot(role).host
        answer = _prompt(
            f"    Which number is {ROLE_LABELS[role]}? "
            f"(1-{len(found)}, blank keeps {current}): "
        )
        if not answer:
            continue
        try:
            host = found[int(answer) - 1]
        except (ValueError, IndexError):
            print("      Not a listed number; keeping the current host.")
            continue
        assignment[role] = host

    if not assignment:
        print("  Nothing changed.")
        return

    for role, host in assignment.items():
        print(f"    {ROLE_LABELS[role]:<22} -> {host}")

    if _prompt("\n  Save to .env? (y/n) [y]: ", "y").lower() == "y":
        update_env_hosts(PROJECT_ROOT / ".env", assignment)
        print("  Saved. Restart the program to pick up the new hosts.")


# ── 1. Scripts ─────────────────────────────────────────────────────────────


def _draft(config: Config) -> None:
    from lta_exhibit.script_gen import generate_scripts, script_path

    existing = [t for t in TOPIC_IDS if script_path(t).exists()]
    overwrite = False
    if existing:
        print(f"\n  Existing scripts: {', '.join(existing)} (may contain hand edits)")
        overwrite = _prompt("  Re-draft and OVERWRITE them? (y/n) [n]: ", "n").lower() == "y"
    generate_scripts(config, overwrite=overwrite)
    print("\n  Review output/<topic>/script.md, edit script.json if needed, then run 2.")


# ── 2. Audio ───────────────────────────────────────────────────────────────


def _render(config: Config) -> None:
    from lta_exhibit.generator import render_all

    force = _prompt("  Re-render every clip, even unchanged ones? (y/n) [n]: ", "n").lower() == "y"
    try:
        render_all(config, force=force)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  Rendering stopped: {exc}")


# ── 3/4. Play ──────────────────────────────────────────────────────────────


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _run(config: Config, demo: bool) -> None:
    from lta_exhibit.show import run_exhibit

    if _port_in_use(config.display_web_port):
        # Most likely the systemd service. Starting anyway would sweep its
        # robot workers (kill_stale_workers) and fight it for the robots.
        print(
            f"\n  Port {config.display_web_port} is already in use -- the exhibit is probably\n"
            "  already running (systemd: `systemctl --user stop lta-exhibit`)."
        )
        return

    try:
        asyncio.run(run_exhibit(config, demo=demo))
    except KeyboardInterrupt:
        print("\n  Exhibit stopped.")


def _play(config: Config) -> None:
    _run(config, demo=False)


def _play_demo(config: Config) -> None:
    _run(config, demo=True)


# ── 5. Restart ─────────────────────────────────────────────────────────────


def _restart_robots(config: Config) -> None:
    from lta_exhibit.robot_admin import restart_all

    hosts = [config.robot(r).host for r in ROLE_ORDER if config.is_role_active(r)]
    print(f"\n  Restarting {len(hosts)} robot daemon(s). This takes about a minute.")
    restart_all(hosts)


# ── 6. Export ──────────────────────────────────────────────────────────────


def _export(config: Config) -> None:
    from lta_exhibit.votes import VoteLog

    log = VoteLog(VOTES_PATH)
    out = OUTPUT_DIR / f"votes_export_{datetime.now():%Y%m%d_%H%M}.csv"
    log.export_csv(out)
    print(f"\n  Wrote {out}")
    for topic_id, data in sorted(log.tonight_all().items()):
        print(f"    {topic_id:<16} {data['counts']}  total={data['total']}  runs={data['runs']}")


# ── Menu ───────────────────────────────────────────────────────────────────


def _banner(config: Config) -> None:
    from lta_exhibit.generator import rendered_topics
    from lta_exhibit.script_gen import script_path

    scripts = [t for t in TOPIC_IDS if script_path(t).exists()]
    rendered = rendered_topics()
    print("\n" + "=" * 62)
    print("  Leading Through AI — The Robot C-Suite")
    print("=" * 62)
    print(f"  Scripts  : {len(scripts)}/{len(TOPIC_IDS)}   ({config.script_model})")
    print(f"  Audio    : {len(rendered)}/{len(TOPIC_IDS)}   ({config.elevenlabs_model})")
    print(f"  Voices   : from {config.audio_output}   ·   screens on :{config.display_web_port}")
    for role in ROLE_ORDER:
        flag = "" if config.is_role_active(role) else "  (seat empty)"
        print(f"    {ROLE_LABELS[role]:<16} {config.robot(role).host}{flag}")


def main() -> int:
    config = load_config()
    if "--exhibit" in sys.argv or "--demo" in sys.argv:
        _run(config, demo="--demo" in sys.argv)
        return 0

    actions = {
        "0": _setup_robots, "1": _draft, "2": _render, "3": _play,
        "4": _play_demo, "5": _restart_robots, "6": _export,
    }
    while True:
        _banner(config)
        print("\n  0. Setup: discover & assign robots")
        print("  1. Draft scripts (OpenAI)")
        print("  2. Render audio (ElevenLabs)")
        print("  3. Play exhibit")
        print("  4. Play exhibit without robots (screens only)")
        print("  5. Restart robot daemons")
        print("  6. Export votes (CSV)")
        print("  7. Exit")
        choice = _prompt("\n  Select (0-7): ", "7")
        if choice == "7":
            print("  Bye.")
            return 0
        action = actions.get(choice)
        if action is None:
            print("  Please pick 0-7.")
            continue
        try:
            action(config)
        except KeyboardInterrupt:
            print("\n  Cancelled.")
        config = load_config()


if __name__ == "__main__":
    sys.exit(main())
