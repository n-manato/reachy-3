"""Render every variant of the sketch to disk, before the show.

Layout produced under output/boma_welcome/:

    index.json              which variants exist, and how they were made
    common/                 the 28 lines that never change, rendered once
      parts/                per-voice pieces of the two chorus turns
    v01/ conversation.json  the verbatim script
         turn_01_kora.wav   only the six lines that vary
    v02/ ... vNN/

Rendering the fixed lines once is what keeps this affordable: 28 + 6N clips
instead of 34N. Each variant's conversation.json refers to the shared clips by
a relative path (`../common/...`), which the player resolves against the
variant directory.

Re-running is safe. A clip is only re-synthesised when the text, voice or model
behind it has changed -- so an interrupted run picks up where it stopped, and
changing one line does not re-bill the other thirty-three.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from boma_exhibit.audio_utils import (
    duration_secs,
    mix_mono,
    normalize_rms,
    read_wav,
    write_wav,
)
from boma_exhibit.config import Config, ROLE_ORDER
from boma_exhibit.elevenlabs_tts import ElevenLabsError, settings_for, synthesize_pcm
from boma_exhibit.script_data import (
    LANGUAGE_NAMES,
    LINES,
    SESSION_NAME,
    TITLE,
    audio_filename,
    audio_role,
    tts_text_for,
)

COMMON_DIR = "common"
PARTS_DIR = "parts"


def variant_name(index: int) -> str:
    """1 -> 'v01'."""
    return f"v{index:02d}"


def _fingerprint(text: str, voice_id: str, model_id: str) -> str:
    """What a rendered clip depends on. Stored beside it as a .txt sidecar."""
    return f"model={model_id}\nvoice={voice_id}\ntext={text}\n"


def _render_clip(
    config: Config,
    path: Path,
    text: str,
    role: str,
    *,
    force: bool = False,
) -> bytes:
    """Synthesise one line to `path`, or reuse what is already there."""
    voice_id = config.robot(role).voice
    model_id = config.elevenlabs_model
    sidecar = path.with_suffix(".txt")
    want = _fingerprint(text, voice_id, model_id)

    if not force and path.exists() and sidecar.exists():
        if sidecar.read_text(encoding="utf-8") == want:
            pcm, _ = read_wav(path)
            return pcm

    pcm = synthesize_pcm(
        text=text,
        voice_id=voice_id,
        settings=settings_for(role),
        api_key=config.elevenlabs_api_key,
        model_id=model_id,
        timeout=90.0,
    )
    pcm = normalize_rms(pcm)
    write_wav(path, pcm)
    sidecar.write_text(want, encoding="utf-8")
    return pcm


def _render_chorus(
    config: Config,
    session_dir: Path,
    line: dict[str, Any],
    *,
    force: bool = False,
) -> bytes:
    """Render each voice of a chorus turn, then sum them into one mono clip.

    Mono on purpose: the whole downstream path (WAV -> 48 kbps mono MP3 ->
    browser) handles mono unchanged, and a chorus belongs in the centre anyway.
    """
    parts_dir = session_dir / COMMON_DIR / PARTS_DIR
    spoken = tts_text_for(line)

    clips = []
    for role in line["speakers"]:
        part_path = parts_dir / f"turn_{line['n']:02d}_{role}.wav"
        clips.append(_render_clip(config, part_path, spoken, role, force=force))

    mixed = normalize_rms(mix_mono(clips))
    target = session_dir / COMMON_DIR / audio_filename(line)
    write_wav(target, mixed)
    # Fingerprint the mix on all three voices so a voice swap re-mixes it.
    voices = ",".join(config.robot(r).voice for r in line["speakers"])
    target.with_suffix(".txt").write_text(
        _fingerprint(spoken, voices, config.elevenlabs_model), encoding="utf-8"
    )
    return mixed


def _tool_calls_for(line: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    """Emotion/dance cues, placed on the speech clock.

    Positions in script_data are fractions of the turn, so they stay right
    whatever the rendered line turns out to be.
    """
    calls: list[dict[str, Any]] = []
    for name, position in line["emotions"]:
        calls.append(
            {
                "name": "play_emotion",
                "arguments": {"emotion": name},
                "at_secs": round(position * duration, 2),
            }
        )
    if line.get("dance"):
        calls.append(
            {
                "name": "dance",
                "arguments": {"dance_name": line["dance"]},
                "at_secs": 0.0,
            }
        )
    return calls


def _turn_entry(
    line: dict[str, Any],
    text: str,
    spoken: str,
    audio_ref: str,
    duration: float,
) -> dict[str, Any]:
    entry = {
        "turn": line["n"],
        "speakers": list(line["speakers"]),
        "lang": line["lang"],
        "language": LANGUAGE_NAMES[line["lang"]],
        "text": text,
        "audio_file": audio_ref,
        "duration_secs": round(duration, 2),
        "tool_calls": _tool_calls_for(line, duration),
    }
    if line.get("translation"):
        entry["translation"] = line["translation"]
    if spoken != text:
        entry["tts_text"] = spoken
    return entry


def render_all(
    config: Config,
    variants: dict[int, list[str]],
    *,
    variant_count: int | None = None,
    force: bool = False,
    progress=print,
) -> Path:
    """Render every clip and write one conversation.json per variant.

    ``variants`` maps a line number to its N wordings (from variator.py).
    Returns the session directory.
    """
    count = variant_count or config.variant_count
    session_dir = config.session_dir
    session_dir.mkdir(parents=True, exist_ok=True)

    varied_numbers = set(variants)
    if not varied_numbers and count > 1:
        # Nothing varies (VARY_LINES is empty, or the LLM filled nothing), so
        # every variant would be byte-identical. Write one.
        progress("No lines vary — writing a single variant.")
        count = 1

    # ── Pass 1: the lines every variant shares ─────────────────────────────
    shared: dict[int, tuple[str, float]] = {}
    fixed_lines = [line for line in LINES if line["n"] not in varied_numbers]
    progress(f"Rendering {len(fixed_lines)} shared lines...")

    for line in fixed_lines:
        spoken = tts_text_for(line)
        if len(line["speakers"]) > 1:
            pcm = _render_chorus(config, session_dir, line, force=force)
        else:
            path = session_dir / COMMON_DIR / audio_filename(line)
            pcm = _render_clip(
                config, path, spoken, line["speakers"][0], force=force
            )
        shared[line["n"]] = (
            f"../{COMMON_DIR}/{audio_filename(line)}",
            duration_secs(pcm),
        )
        progress(f"  turn {line['n']:>2} [{line['lang']}] {line['text'][:52]}")

    # ── Pass 2: the varying lines, once per variant ────────────────────────
    written: list[str] = []
    for index in range(1, count + 1):
        name = variant_name(index)
        variant_dir = session_dir / name
        variant_dir.mkdir(parents=True, exist_ok=True)
        progress(f"\nRendering {name} ({len(varied_numbers)} varying lines)...")

        turns: list[dict[str, Any]] = []
        for line in LINES:
            number = line["n"]
            if number in shared:
                audio_ref, duration = shared[number]
                spoken = tts_text_for(line)
                turns.append(
                    _turn_entry(line, line["text"], spoken, audio_ref, duration)
                )
                continue

            text = variants[number][index - 1]
            spoken = tts_text_for(line, text)
            path = variant_dir / audio_filename(line)
            pcm = _render_clip(
                config, path, spoken, line["speakers"][0], force=force
            )
            turns.append(
                _turn_entry(
                    line, text, spoken, audio_filename(line), duration_secs(pcm)
                )
            )
            progress(f"  turn {number:>2} {text[:60]}")

        meta = {
            "session": SESSION_NAME,
            "title": TITLE,
            "variant": name,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tts_engine": "elevenlabs",
            "tts_model": config.elevenlabs_model,
            "llm_model": config.llm_model,
            "varied_lines": sorted(varied_numbers),
            **{f"{role}_voice": config.robot(role).voice for role in ROLE_ORDER},
            "num_turns": len(turns),
            "total_secs": round(sum(t["duration_secs"] for t in turns), 1),
            "turns": turns,
        }
        (variant_dir / "conversation.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written.append(name)

    index_path = session_dir / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "session": SESSION_NAME,
                "title": TITLE,
                "variants": written,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tts_model": config.elevenlabs_model,
                "llm_model": config.llm_model,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    progress(f"\nWrote {len(written)} variants to {session_dir}")
    return session_dir


WORDINGS_FILE = "wordings.json"


def load_wordings(session_dir: Path, count: int) -> dict[int, list[str]] | None:
    """Previously-generated wordings, if they still cover `count` variants."""
    path = session_dir / WORDINGS_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    wordings = {int(k): v for k, v in raw.get("wordings", {}).items()}
    if not wordings or any(len(v) < count for v in wordings.values()):
        return None
    return {number: texts[:count] for number, texts in wordings.items()}


def save_wordings(session_dir: Path, wordings: dict[int, list[str]], model: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / WORDINGS_FILE).write_text(
        json.dumps(
            {
                "llm_model": model,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "wordings": {str(k): v for k, v in wordings.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def generate(
    config: Config,
    *,
    variant_count: int | None = None,
    force: bool = False,
    refresh_wordings: bool = False,
    progress=print,
) -> Path:
    """Full pre-render: settle on the wordings, then synthesise everything.

    The wordings are cached to disk. Without that, re-running after an
    interruption would ask the LLM again, get different sentences, and re-bill
    every varied clip -- so a resumed run would cost as much as a fresh one and
    quietly change lines the operator had already approved.
    """
    from boma_exhibit.variator import build_variants

    count = variant_count or config.variant_count
    progress(f"Generating {count} variant(s) of '{TITLE}'")
    progress(f"  TTS : {config.elevenlabs_model}")
    progress(f"  LLM : {config.llm_model} at {config.llm_base_url}\n")

    variants = None if refresh_wordings else load_wordings(config.session_dir, count)
    if variants is not None:
        progress(f"Reusing the wordings in {WORDINGS_FILE} (--refresh to re-ask).\n")
    else:
        variants = build_variants(config, variant_count=count)
        save_wordings(config.session_dir, variants, config.llm_model)

    try:
        return render_all(
            config, variants, variant_count=count, force=force, progress=progress
        )
    except ElevenLabsError as exc:
        progress(f"\nTTS failed: {exc}")
        progress("Nothing was lost -- re-run and it resumes from the last good clip.")
        raise
