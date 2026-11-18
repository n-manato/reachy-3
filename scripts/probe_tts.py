"""Find a TTS model this ElevenLabs account can actually use, and listen to it.

Why this exists: the API key on this machine lacks the `models_read`
permission, so /v1/models is a 401 and there is no way to ask which models are
available. Worse, none of the account's library voices list `eleven_v3` among
their supported models, so the model we would most like for Swahili and
Fulfulde may simply not be reachable.

So we ask the only question the API will answer: we synthesise one very short
line per model and see which ones come back. For each model that works, we then
render the three languages the sketch actually uses, so a human can listen and
decide whether the pronunciation is good enough for the exhibit floor.

    uv run python scripts/probe_tts.py
    uv run python scripts/probe_tts.py --write-env   # also update .env
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boma_exhibit.audio_utils import duration_secs, normalize_rms, write_wav
from boma_exhibit.config import OUTPUT_DIR, PROJECT_ROOT, load_config
from boma_exhibit.elevenlabs_tts import (
    MODEL_PREFERENCE,
    ElevenLabsError,
    settings_for,
    synthesize_pcm,
)
from boma_exhibit.script_data import (
    KORA,
    LANGUAGE_NAMES,
    PULAAR,
    ZURI,
    line_by_number,
    tts_text_for,
)

PROBE_DIR = OUTPUT_DIR / "probe"

# One cheap word to answer "does this model exist for me at all?".
CHEAP_PROBE = "Hello."

# Real lines from the sketch -- the ones whose pronunciation actually matters.
#   line 2  Swahili, a full sentence
#   line 6  Fulfulde, contains the implosive that needs transliterating
#   line 31 English, the closing punchline
LANGUAGE_SAMPLES = (
    (ZURI, 2),
    (PULAAR, 6),
    (KORA, 31),
)


def probe_model(config, model_id: str) -> tuple[bool, str]:
    """Try one tiny synthesis. Returns (works, detail)."""
    try:
        pcm = synthesize_pcm(
            text=CHEAP_PROBE,
            voice_id=config.robot(KORA).voice,
            settings=settings_for(KORA),
            api_key=config.elevenlabs_api_key,
            model_id=model_id,
            timeout=60.0,
        )
    except ElevenLabsError as exc:
        return False, f"HTTP {exc.status}: {exc.body[:200]}"
    except Exception as exc:  # network, timeout, ...
        return False, f"{type(exc).__name__}: {exc}"

    if not pcm:
        return False, "empty audio returned"
    return True, f"{duration_secs(pcm):.2f}s of audio"


def render_samples(config, model_id: str) -> list[Path]:
    """Render the three languages so a human can judge the pronunciation."""
    out_dir = PROBE_DIR / model_id
    written: list[Path] = []

    for role, number in LANGUAGE_SAMPLES:
        line = line_by_number(number)
        spoken = tts_text_for(line)
        try:
            pcm = synthesize_pcm(
                text=spoken,
                voice_id=config.robot(role).voice,
                settings=settings_for(role),
                api_key=config.elevenlabs_api_key,
                model_id=model_id,
                timeout=60.0,
            )
        except Exception as exc:
            print(f"    [{LANGUAGE_NAMES[line['lang']]}] FAILED: {exc}")
            continue

        path = out_dir / f"{line['lang']}_{role}.wav"
        write_wav(path, normalize_rms(pcm))
        written.append(path)
        print(f"    [{LANGUAGE_NAMES[line['lang']]:<9}] {path.relative_to(PROJECT_ROOT)}")
        print(f"                 spoken as: {spoken}")
        if spoken != line["text"]:
            print(f"                 displayed: {line['text']}")
    return written


def write_env_model(model_id: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    text = env_path.read_text()
    updated, count = re.subn(
        r"^ELEVENLABS_MODEL=.*$", f"ELEVENLABS_MODEL={model_id}", text, flags=re.M
    )
    if count == 0:
        updated = text.rstrip("\n") + f"\nELEVENLABS_MODEL={model_id}\n"
    env_path.write_text(updated)
    print(f"\n.env updated: ELEVENLABS_MODEL={model_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="write the first working model into .env",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(MODEL_PREFERENCE),
        help="models to try, best first",
    )
    args = parser.parse_args()

    config = load_config()

    print("Probing ElevenLabs models (best first)\n")
    working: list[str] = []
    for model_id in args.models:
        ok, detail = probe_model(config, model_id)
        mark = "OK  " if ok else "FAIL"
        print(f"  [{mark}] {model_id:<24} {detail}")
        if ok:
            working.append(model_id)

    if not working:
        print("\nNo model worked. Check ELEVENLABS_API_KEY and the account plan.")
        return 1

    print(f"\nRendering three-language samples for: {', '.join(working)}")
    for model_id in working:
        print(f"\n  {model_id}")
        render_samples(config, model_id)

    chosen = working[0]
    print(f"\nBest available model: {chosen}")
    print(f"Listen to {PROBE_DIR.relative_to(PROJECT_ROOT)}/<model>/ before committing.")
    print("Swahili and Fulfulde are the ones to judge -- English will sound fine")
    print("on every model, so it tells you nothing.")

    if args.write_env:
        write_env_model(chosen)
    else:
        print(f"\nTo use it:  ELEVENLABS_MODEL={chosen}   (or re-run with --write-env)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
