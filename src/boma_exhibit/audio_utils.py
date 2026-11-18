"""PCM helpers shared by the generator and the player.

Everything here works on 24 kHz mono int16 -- the one format ElevenLabs
returns, the WAV files store, and the robots/browser consume.

The reference project loops over samples in pure Python for normalisation;
numpy is already a dependency here, so these do the same arithmetic vectorised.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000
INT16_FULL_SCALE = 32768.0


def pcm_bytes_to_float(pcm_int16: bytes) -> np.ndarray:
    """Raw little-endian int16 bytes -> float32 in [-1, 1]."""
    # An odd trailing byte would break the dtype view; drop it.
    if len(pcm_int16) % 2:
        pcm_int16 = pcm_int16[:-1]
    return np.frombuffer(pcm_int16, dtype="<i2").astype(np.float32) / INT16_FULL_SCALE


def float_to_pcm_bytes(samples: np.ndarray) -> bytes:
    """float32 in [-1, 1] -> raw little-endian int16 bytes, clipped."""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * (INT16_FULL_SCALE - 1)).astype("<i2").tobytes()


def normalize_rms(
    pcm_int16: bytes,
    target_rms: float = 0.18,
    max_peak: float = 0.95,
) -> bytes:
    """Even out average loudness across turns without clipping the peaks.

    Different voices and different models come back at wildly different levels;
    without this the quiet lines vanish under the room noise at the exhibit.
    """
    samples = pcm_bytes_to_float(pcm_int16)
    if samples.size == 0:
        return pcm_int16

    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms == 0.0:
        return pcm_int16

    gain = target_rms / rms
    peak = float(np.max(np.abs(samples)))
    if peak * gain > max_peak:
        gain = max_peak / peak

    return float_to_pcm_bytes(samples * gain)


def mix_mono(clips: list[bytes], headroom: float = 0.95) -> bytes:
    """Sum several mono clips into one, for the two chorus turns.

    Staying mono is deliberate: the whole downstream path (WAV -> 48 kbps mono
    MP3 -> browser) handles mono unchanged, and the browser pans the result to
    centre anyway. Shorter clips are zero-padded so every voice starts together
    and the ragged tail is whoever holds the note longest.
    """
    if not clips:
        return b""
    if len(clips) == 1:
        return clips[0]

    tracks = [pcm_bytes_to_float(clip) for clip in clips]
    length = max(track.size for track in tracks)
    total = np.zeros(length, dtype=np.float32)
    for track in tracks:
        total[: track.size] += track

    peak = float(np.max(np.abs(total)))
    if peak > headroom:
        total *= headroom / peak

    return float_to_pcm_bytes(total)


def write_wav(path: Path, pcm_int16: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    """Write mono 16-bit PCM to a WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm_int16)


def read_wav(path: Path) -> tuple[bytes, int]:
    """Read a mono 16-bit WAV back as (raw pcm bytes, sample rate)."""
    with wave.open(str(path), "rb") as handle:
        return handle.readframes(handle.getnframes()), handle.getframerate()


def duration_secs(pcm_int16: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    """Length of raw mono int16 PCM in seconds."""
    return len(pcm_int16) / 2 / sample_rate
