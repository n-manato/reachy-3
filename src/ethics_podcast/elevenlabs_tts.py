"""ElevenLabs text-to-speech for the three philosopher robots.

Two synthesis paths:
  - synthesize_pcm()      blocking, returns the full PCM int16 bytes once.
  - stream_pcm()          async, yields PCM int16 chunks as they arrive.

All audio comes back as 24 kHz mono PCM int16 — same format the existing
robot push_audio + WAV-writing code already expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

import httpx

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

# 24 kHz keeps the format consistent with OPENAI_SAMPLE_RATE used everywhere
# else in this project (orchestrator, robot_worker, generator's WAV writer).
ELEVENLABS_SAMPLE_RATE = 24000
ELEVENLABS_OUTPUT_FORMAT = f"pcm_{ELEVENLABS_SAMPLE_RATE}"


@dataclass(frozen=True)
class VoiceSettings:
    """Per-philosopher tuning of ElevenLabs voice settings.

    stability        higher → flatter, more consistent. lower → emotional range.
    similarity_boost similarity to the reference voice timbre.
    style            stylistic exaggeration (energy, theatricality).
    use_speaker_boost cleaner enunciation at the cost of slight delay.
    """

    stability: float
    similarity_boost: float
    style: float
    use_speaker_boost: bool = True

    def to_dict(self) -> dict:
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "use_speaker_boost": self.use_speaker_boost,
        }


# Persona-specific voice settings. These were tuned for the philosopher
# personalities, not the default flat-TTS sound.
#   student   = Bentham   — witty, theatrical, lawyer-like
#   professor = Korsgaard — calm, deliberate, principled, anti-sing-songy
#   moderator = Aquinas   — warm, slow, patient, scholarly
ROLE_SETTINGS: dict[str, VoiceSettings] = {
    "student": VoiceSettings(
        stability=0.35,
        similarity_boost=0.80,
        style=0.45,
    ),
    "professor": VoiceSettings(
        stability=0.55,
        similarity_boost=0.85,
        style=0.10,
    ),
    "moderator": VoiceSettings(
        stability=0.65,
        similarity_boost=0.80,
        style=0.20,
    ),
}


def settings_for(role: str) -> VoiceSettings:
    """Return the voice settings for a given robot role.

    Falls back to professor (the most neutral preset) for unknown roles.
    """
    return ROLE_SETTINGS.get(role, ROLE_SETTINGS["professor"])


def _build_payload(text: str, settings: VoiceSettings, model_id: str) -> dict:
    return {
        "text": text,
        "model_id": model_id,
        "voice_settings": settings.to_dict(),
    }


def _headers(api_key: str) -> dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }


def synthesize_pcm(
    text: str,
    voice_id: str,
    settings: VoiceSettings,
    api_key: str,
    model_id: str = "eleven_flash_v2_5",
    timeout: float = 30.0,
) -> bytes:
    """Synthesize text and return raw 24 kHz mono PCM int16 bytes.

    Blocking call — use for offline WAV generation (generator.py).
    """
    url = (
        f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"
        f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    )

    response = httpx.post(
        url,
        headers=_headers(api_key),
        json=_build_payload(text, settings, model_id),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


async def stream_pcm(
    text: str,
    voice_id: str,
    settings: VoiceSettings,
    api_key: str,
    model_id: str = "eleven_flash_v2_5",
    chunk_size: int = 4096,
    timeout: float = 30.0,
) -> AsyncIterator[bytes]:
    """Stream synthesis as 24 kHz mono PCM int16 chunks.

    Use for live exhibits (orchestrator.py) where time-to-first-audio matters.
    Each yielded chunk is raw little-endian int16 bytes; combine sequential
    chunks to form valid PCM.
    """
    url = (
        f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}/stream"
        f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            url,
            headers=_headers(api_key),
            json=_build_payload(text, settings, model_id),
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                if chunk:
                    yield chunk
