"""ElevenLabs text-to-speech for the three BOMA robots.

Everything is rendered offline, so this module only needs the blocking path.
Audio comes back as 24 kHz mono PCM int16 -- the format the WAV writer and the
robot audio push already expect.

Model handling is the fiddly part. ``eleven_v3`` covers far more languages than
the v2 models (which matters for Swahili and Fulfulde) but it interprets
``voice_settings`` differently: stability is a three-way choice rather than a
continuous knob, and style / similarity_boost are not accepted. So the payload
is built per model family, and scripts/probe_tts.py decides which family the
account can actually reach.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

# 24 kHz is the single source of truth for sample rate across this project.
ELEVENLABS_SAMPLE_RATE = 24000
ELEVENLABS_OUTPUT_FORMAT = f"pcm_{ELEVENLABS_SAMPLE_RATE}"

# Tried in this order by scripts/probe_tts.py.
MODEL_PREFERENCE = ("eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5")

# v3 exposes stability as three named modes rather than a continuous value.
V3_CREATIVE, V3_NATURAL, V3_ROBUST = 0.0, 0.5, 1.0


@dataclass(frozen=True)
class VoiceSettings:
    """Per-character tuning.

    stability         higher -> flatter and more consistent; lower -> more range
    similarity_boost  how tightly to hug the reference timbre
    style             stylistic exaggeration (energy, theatricality)
    use_speaker_boost cleaner enunciation, slightly slower
    """

    stability: float
    similarity_boost: float
    style: float
    use_speaker_boost: bool = True

    def payload_for(self, model_id: str) -> dict:
        if model_id.startswith("eleven_v3"):
            # v3 rejects style/similarity_boost and snaps stability to a mode.
            return {
                "stability": self._v3_stability(),
                "use_speaker_boost": self.use_speaker_boost,
            }
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "use_speaker_boost": self.use_speaker_boost,
        }

    def _v3_stability(self) -> float:
        if self.stability < 0.34:
            return V3_CREATIVE
        if self.stability < 0.67:
            return V3_NATURAL
        return V3_ROBUST


# Tuned for the three characters, not for flat narration.
#   zuri   -- bright and quick, laughs mid-line, needs range
#   kora   -- the fast-talking host; the comedy lives in the swings
#   pulaar -- deadpan. Flatness IS the joke, so stability goes high.
ROLE_SETTINGS: dict[str, VoiceSettings] = {
    "zuri": VoiceSettings(stability=0.35, similarity_boost=0.80, style=0.50),
    "kora": VoiceSettings(stability=0.30, similarity_boost=0.80, style=0.60),
    "pulaar": VoiceSettings(stability=0.75, similarity_boost=0.85, style=0.05),
}

_NEUTRAL = VoiceSettings(stability=0.50, similarity_boost=0.80, style=0.20)


def settings_for(role: str) -> VoiceSettings:
    """Voice settings for a role, falling back to a neutral preset."""
    return ROLE_SETTINGS.get(role, _NEUTRAL)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }


def build_payload(text: str, settings: VoiceSettings, model_id: str) -> dict:
    return {
        "text": text,
        "model_id": model_id,
        "voice_settings": settings.payload_for(model_id),
    }


class ElevenLabsError(RuntimeError):
    """A non-2xx response, carrying enough detail to tell why it failed."""

    def __init__(self, status: int, body: str, model_id: str) -> None:
        super().__init__(f"ElevenLabs {status} for model {model_id}: {body[:400]}")
        self.status = status
        self.body = body
        self.model_id = model_id


def synthesize_pcm(
    text: str,
    voice_id: str,
    settings: VoiceSettings,
    api_key: str,
    model_id: str = "eleven_multilingual_v2",
    timeout: float = 60.0,
) -> bytes:
    """Synthesize text and return raw 24 kHz mono PCM int16 bytes."""
    url = (
        f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"
        f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    )
    response = httpx.post(
        url,
        headers=_headers(api_key),
        json=build_payload(text, settings, model_id),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise ElevenLabsError(response.status_code, response.text, model_id)
    return response.content


def list_voices(api_key: str, timeout: float = 30.0) -> list[dict]:
    """Every voice the account can use. Needs the `voices_read` permission."""
    response = httpx.get(
        f"{ELEVENLABS_API_BASE}/voices",
        headers={"xi-api-key": api_key},
        params={"page_size": 100},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("voices", [])
