"""Re-render the audio of every existing podcast session using ElevenLabs.

For each `output/<session>/conversation.json`, walks every turn and replaces
the on-disk WAV file with a fresh ElevenLabs synthesis of the same transcript
(prefers `tts_text` if present, falls back to `transcript`). Old WAVs are
moved aside as `*.openai.wav` so you can compare or roll back.

Skips:
  - `voice-samples/` (preview output, no conversation.json)
  - `backup/`        (existing manual backup folder)

Usage:
    uv run python scripts/rerender_audio.py             # all sessions
    uv run python scripts/rerender_audio.py llm_writing # one session
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ethics_podcast.config import Config
from ethics_podcast.elevenlabs_tts import (
    ELEVENLABS_SAMPLE_RATE,
    settings_for,
    synthesize_pcm,
)


SKIP_DIRS = {"voice-samples", "backup"}


def _write_wav(path: Path, pcm_int16: bytes, sample_rate: int) -> None:
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_int16)

    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", num_channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(pcm_int16)


def _normalize_pcm_int16_rms(
    pcm_int16: bytes,
    target_rms: float = 0.18,
    max_peak: float = 0.95,
) -> bytes:
    """Match the loudness normalization the generator applies on save."""
    if not pcm_int16:
        return pcm_int16

    values: list[float] = []
    for i in range(0, len(pcm_int16), 2):
        if i + 1 >= len(pcm_int16):
            break
        sample = struct.unpack_from("<h", pcm_int16, i)[0]
        values.append(sample / 32768.0)

    if not values:
        return pcm_int16

    rms = math.sqrt(sum(v * v for v in values) / len(values))
    if rms == 0:
        return pcm_int16

    gain = target_rms / rms
    peak = max(abs(v) for v in values)
    if peak * gain > max_peak:
        gain = max_peak / peak

    normalized = bytearray(len(pcm_int16))
    for i in range(0, len(pcm_int16), 2):
        if i + 1 >= len(pcm_int16):
            break
        sample = struct.unpack_from("<h", pcm_int16, i)[0]
        new_sample = max(-32768, min(32767, int(sample * gain)))
        struct.pack_into("<h", normalized, i, new_sample)

    return bytes(normalized)


def _voice_id_for_role(config: Config, role: str) -> str:
    if role == "student":
        return config.robot_a.voice
    if role == "professor":
        return config.robot_b.voice
    if role == "moderator":
        return config.robot_c.voice
    return config.robot_b.voice


def _rerender_session(session_dir: Path, config: Config) -> None:
    meta_path = session_dir / "conversation.json"
    if not meta_path.exists():
        print(f"  skip {session_dir.name}: no conversation.json")
        return

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    turns = meta.get("turns", [])
    if not turns:
        print(f"  skip {session_dir.name}: no turns in conversation.json")
        return

    print(f"\n  Session: {session_dir.name}  ({len(turns)} turns)")

    for turn in turns:
        role = turn.get("role", "student")
        wav_name = turn.get("audio_file")
        if not wav_name:
            print(f"    turn {turn.get('turn')}: no audio_file in metadata, skip")
            continue

        text = turn.get("tts_text") or turn.get("transcript") or ""
        if not text.strip():
            print(f"    turn {turn.get('turn')}: empty text, skip")
            continue

        wav_path = session_dir / wav_name

        # Move the old WAV aside (only once — don't overwrite an existing backup)
        backup_path = wav_path.with_suffix(".openai.wav")
        if wav_path.exists() and not backup_path.exists():
            wav_path.rename(backup_path)

        voice_id = _voice_id_for_role(config, role)
        settings = settings_for(role)

        pcm = synthesize_pcm(
            text=text,
            voice_id=voice_id,
            settings=settings,
            api_key=config.elevenlabs_api_key,
            model_id=config.elevenlabs_model,
        )
        pcm = _normalize_pcm_int16_rms(pcm, target_rms=0.18, max_peak=0.95)
        _write_wav(wav_path, pcm, ELEVENLABS_SAMPLE_RATE)

        duration = len(pcm) / 2 / ELEVENLABS_SAMPLE_RATE
        turn["duration_secs"] = round(duration, 1)
        print(
            f"    turn {turn.get('turn'):>2} [{role:9s}] "
            f"{duration:4.1f}s → {wav_name}"
        )

    # Update metadata to reflect the new audio engine
    meta["tts_engine"] = "elevenlabs"
    meta["tts_model"] = config.elevenlabs_model
    meta["student_voice"] = config.robot_a.voice
    meta["professor_voice"] = config.robot_b.voice
    meta["moderator_voice"] = config.robot_c.voice
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    config = Config.from_env()

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    output_root = Path("output")
    if not output_root.exists():
        print(f"no {output_root}/ directory")
        return

    if arg:
        sessions = [output_root / arg]
    else:
        sessions = sorted(
            d for d in output_root.iterdir()
            if d.is_dir() and d.name not in SKIP_DIRS
        )

    if not sessions:
        print("no sessions to re-render")
        return

    print(
        f"  Re-rendering {len(sessions)} session(s) with ElevenLabs "
        f"(model={config.elevenlabs_model})"
    )

    for session_dir in sessions:
        _rerender_session(session_dir, config)

    print("\n  Done.")


if __name__ == "__main__":
    main()
