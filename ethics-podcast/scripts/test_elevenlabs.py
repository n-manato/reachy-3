"""Smoke-test the ElevenLabs voices for the three philosopher robots.

Reads ELEVENLABS_API_KEY and the three voice IDs from .env, synthesizes one
short persona-matched line per robot, and writes WAVs under
output/voice-samples/ so you can preview the voices before running the
podcast.

Usage:
    uv run python scripts/test_elevenlabs.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

# Allow `python scripts/test_elevenlabs.py` to find the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ethics_podcast.config import Config
from ethics_podcast.elevenlabs_tts import (
    ELEVENLABS_SAMPLE_RATE,
    settings_for,
    synthesize_pcm,
)


# Short, persona-matched lines so you can judge fit fast.
SAMPLE_LINES = {
    "student": (
        "Have we truly considered everyone affected, my friends? "
        "If a single child suffers for the convenience of millions, "
        "is the calculation honestly worth it?"
    ),
    "professor": (
        "The reason behind an action matters more than its consequence. "
        "We must not treat any person merely as a means to someone else's ends."
    ),
    "moderator": (
        "Let us pause and consider, slowly, what purpose this serves. "
        "The good should be pursued, and the bad avoided, "
        "for the sake of human flourishing."
    ),
}

ROLE_TO_VOICE_FIELD = {
    "student": "robot_a",
    "professor": "robot_b",
    "moderator": "robot_c",
}


def _write_wav(path: Path, pcm_int16: bytes, sample_rate: int) -> None:
    """Write raw PCM int16 mono to a .wav file (no third-party deps)."""
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


def main() -> None:
    config = Config.from_env()

    out_dir = Path("output") / "voice-samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    for role, line in SAMPLE_LINES.items():
        robot_field = ROLE_TO_VOICE_FIELD[role]
        robot = getattr(config, robot_field)
        voice_id = robot.voice
        settings = settings_for(role)

        print(
            f"  Synthesizing {role:9s} "
            f"({robot_field}, voice={voice_id}) — "
            f"model={config.elevenlabs_model}..."
        )

        pcm = synthesize_pcm(
            text=line,
            voice_id=voice_id,
            settings=settings,
            api_key=config.elevenlabs_api_key,
            model_id=config.elevenlabs_model,
        )

        path = out_dir / f"{role}_{voice_id}.wav"
        _write_wav(path, pcm, ELEVENLABS_SAMPLE_RATE)

        duration = len(pcm) / 2 / ELEVENLABS_SAMPLE_RATE
        print(f"    → {path}  ({duration:.1f}s, {len(pcm)} bytes)")

    print(f"\nDone. Preview the WAVs under {out_dir}/")


if __name__ == "__main__":
    main()
