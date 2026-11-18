"""Interactive menu for the BOMA exhibit.

    0. Setup, discover, assign & refresh robots
    1. Generate Variants        -- run once, before the doors open
    2. Play Exhibit             -- then pick 3 for TV surround audio
    3. Restart Robots
    4. Exit

Option 1 is the only one that talks to the LLM or to ElevenLabs. Option 2 plays
what option 1 already rendered.
"""

from __future__ import annotations

import asyncio
import sys

from boma_exhibit.config import PROJECT_ROOT, ROLE_ORDER, Config, load_config
from boma_exhibit.player import ROLE_LABELS, AudioMode
from boma_exhibit.script_data import TITLE
from boma_exhibit.variants import list_variants


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
    from boma_exhibit.robot_admin import (
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

    print("\n  Each robot will wiggle its antennas so you can tell which is which.")
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


# ── 1. Generate ────────────────────────────────────────────────────────────


def _generate(config: Config) -> None:
    from boma_exhibit.generator import generate

    existing = list_variants(config.session_dir)
    if existing:
        print(f"\n  Already rendered: {', '.join(p.name for p in existing)}")

    answer = _prompt(
        f"  How many variants? [{config.variant_count}]: ", str(config.variant_count)
    )
    try:
        count = max(1, int(answer))
    except ValueError:
        count = config.variant_count

    force = _prompt("  Re-render clips that already exist? (y/n) [n]: ", "n").lower() == "y"
    refresh = _prompt(
        "  Ask the LLM for new wordings? (y/n) [n — reuse the saved ones]: ", "n"
    ).lower() == "y"

    print()
    try:
        generate(config, variant_count=count, force=force, refresh_wordings=refresh)
    except Exception as exc:
        print(f"\n  Generation stopped: {exc}")


# ── 2. Play ────────────────────────────────────────────────────────────────


def _pick_audio_mode() -> AudioMode:
    print(
        "\n  Audio output:\n"
        "    1. Robot speakers   (needs GStreamer >= 1.20 -- see CLAUDE.md)\n"
        "    2. Computer / TV normal audio\n"
        "    3. TV surround-style audio      <- the exhibit setup\n"
        "    4. TV surround-style audio + robot speakers"
    )
    choice = _prompt("  Select audio output [3]: ", "3")
    return {
        "1": "robot",
        "2": "computer",
        "3": "surround",
        "4": "hybrid_surround",
    }.get(choice, "surround")


def _play(config: Config) -> None:
    from boma_exhibit.exhibit import run_exhibit

    if not list_variants(config.session_dir):
        print("\n  Nothing rendered yet. Run option 1 first.")
        return

    audio_mode = _pick_audio_mode()
    demo = _prompt("  Run without robots (display only)? (y/n) [n]: ", "n").lower() == "y"

    try:
        asyncio.run(run_exhibit(config, audio_mode=audio_mode, demo_mode=demo))
    except KeyboardInterrupt:
        print("\n  Exhibit stopped.")


# ── 3. Restart ─────────────────────────────────────────────────────────────


def _restart_robots(config: Config) -> None:
    from boma_exhibit.robot_admin import restart_all

    hosts = [
        config.robot(role).host
        for role in ROLE_ORDER
        if config.is_role_active(role)
    ]
    print(f"\n  Restarting {len(hosts)} robot daemon(s). This takes about a minute.")
    restart_all(hosts)


# ── Menu ───────────────────────────────────────────────────────────────────


def _banner(config: Config) -> None:
    variants = list_variants(config.session_dir)
    print("\n" + "=" * 62)
    print(f"  BOMA exhibit — {TITLE}")
    print("=" * 62)
    print(f"  TTS      : {config.elevenlabs_model}")
    print(f"  LLM      : {config.llm_model}")
    print(
        f"  Rendered : {len(variants)} variant(s)"
        + (f" ({', '.join(p.name for p in variants)})" if variants else " — run option 1")
    )
    if config.robot_mode == 2:
        print(f"  Robots   : 2-robot mode, {config.disabled_role} seat is empty")


def main() -> int:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 1

    actions = {
        "0": _setup_robots,
        "1": _generate,
        "2": _play,
        "3": _restart_robots,
    }

    while True:
        _banner(config)
        print("\n  0. Setup, discover, assign & refresh robots")
        print("  1. Generate Variants")
        print("  2. Play Exhibit")
        print("  3. Restart Robots")
        print("  4. Exit")

        choice = _prompt("\n  Select (0-4): ", "4")
        if choice == "4":
            print("  Bye.")
            return 0

        action = actions.get(choice)
        if action is None:
            print("  Please pick 0-4.")
            continue

        try:
            action(config)
        except KeyboardInterrupt:
            print("\n  Cancelled.")

        # Re-read .env so a change made in option 0 takes effect immediately.
        try:
            config = load_config()
        except ValueError:
            pass


if __name__ == "__main__":
    sys.exit(main())
