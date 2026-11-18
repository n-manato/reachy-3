"""List the ElevenLabs voices this account can use.

Casting the three robots is the one setup step that needs a human ear. Run
this, pick three voice ids, and put them in .env as ZURI_VOICE / KORA_VOICE /
PULAAR_VOICE.

    uv run python scripts/list_voices.py
    uv run python scripts/list_voices.py --accent nigerian
    uv run python scripts/list_voices.py --search deep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lta_exhibit.config import ROLE_ORDER, load_config
from lta_exhibit.elevenlabs_tts import list_voices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accent", help="only voices whose accent label matches")
    parser.add_argument("--search", help="substring match on name or description")
    args = parser.parse_args()

    config = load_config()
    try:
        voices = list_voices(config.elevenlabs_api_key)
    except Exception as exc:
        print(f"Could not list voices: {exc}")
        print("The API key needs the `voices_read` permission.")
        return 1

    cast = {config.robot(role).voice: role for role in ROLE_ORDER}

    rows = []
    for voice in voices:
        labels = voice.get("labels") or {}
        accent = labels.get("accent") or "-"
        if args.accent and args.accent.lower() not in accent.lower():
            continue
        haystack = f"{voice.get('name','')} {labels.get('descriptive','')}".lower()
        if args.search and args.search.lower() not in haystack:
            continue
        rows.append(
            (
                voice["voice_id"],
                voice.get("name", "")[:40],
                labels.get("gender") or "-",
                accent,
                labels.get("descriptive") or "-",
                cast.get(voice["voice_id"], ""),
            )
        )

    print(f"\n{len(rows)} voice(s)\n")
    print(f"  {'voice_id':<24} {'name':<40} {'gender':<8} {'accent':<12} {'style':<12} cast")
    print("  " + "-" * 104)
    for voice_id, name, gender, accent, style, role in rows:
        marker = f"<- {role.upper()}" if role else ""
        print(f"  {voice_id:<24} {name:<40} {gender:<8} {accent:<12} {style:<12} {marker}")

    print("\nCurrent cast:")
    for role in ROLE_ORDER:
        print(f"  {role.upper():<8} {config.robot(role).voice}")
    print("\nHear a candidate in all three languages with:")
    print("  uv run python scripts/probe_tts.py --models <model>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
