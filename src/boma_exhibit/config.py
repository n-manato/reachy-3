"""Environment configuration for the BOMA exhibit.

Everything tunable lives in .env. The one hard requirement is
ELEVENLABS_API_KEY; the LLM is optional because the show only needs it when
pre-rendering variants, never while running.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from boma_exhibit.script_data import KORA, PULAAR, ZURI, vary_line_numbers
from boma_exhibit.variants import normalize_mode

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
PICTURE_DIR = PROJECT_ROOT / "picture"
MUSIC_DIR = PROJECT_ROOT / "music"

# Left to right, as the robots sit on stage. Every dict keyed by role in this
# project uses this order.
ROLE_ORDER = (ZURI, KORA, PULAAR)

# African-accented voices already present in this account's library. Overridable
# per role in .env; list what else is available with scripts/list_voices.py.
DEFAULT_VOICES = {
    ZURI: "PSIwmc50KeuW20kehlBE",    # Chineye - warm, expressive, Nigerian
    KORA: "G2LDKmwqqBo2DD9ucIji",    # Ade - talkative, fast, excited
    PULAAR: "8dvhVJc85Oy9HBPo11aI",  # Daniel - young, deep, narrative
}

DEFAULT_HOSTS = {
    ZURI: "192.168.3.163",
    KORA: "192.168.3.106",
    PULAAR: "192.168.3.132",
}

# Accepted spellings for DISABLED_ROLE and the setup menu.
_ROLE_ALIASES = {
    "zuri": ZURI, "left": ZURI, "a": ZURI, "swahili": ZURI,
    "kora": KORA, "center": KORA, "centre": KORA, "b": KORA, "english": KORA,
    "pulaar": PULAAR, "right": PULAAR, "c": PULAAR, "fulfulde": PULAAR,
}


def normalize_role(value: str | None) -> str | None:
    if not value:
        return None
    return _ROLE_ALIASES.get(value.strip().lower())


@dataclass(frozen=True)
class RobotConfig:
    role: str          # zuri | kora | pulaar
    name: str | None   # robot_name label; blank means "don't disambiguate"
    host: str          # IP or hostname of the robot's daemon
    voice: str         # ElevenLabs voice id


@dataclass(frozen=True)
class Config:
    # ── TTS ────────────────────────────────────────────────────────────────
    elevenlabs_api_key: str
    elevenlabs_model: str

    # ── Robots ─────────────────────────────────────────────────────────────
    robots: dict[str, RobotConfig]
    robot_mode: int
    disabled_role: str | None
    inter_turn_pause: float

    # ── LLM (pre-rendering only) ───────────────────────────────────────────
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    vary_lines: tuple[int, ...]
    variant_count: int

    # ── Show ───────────────────────────────────────────────────────────────
    loop_mode: str

    # ── Display ────────────────────────────────────────────────────────────
    display_web_host: str
    display_web_port: int
    display_speech_bitrate: int
    attract_seconds: float

    @property
    def session_dir(self) -> Path:
        from boma_exhibit.script_data import SESSION_NAME

        return OUTPUT_DIR / SESSION_NAME

    def robot(self, role: str) -> RobotConfig:
        return self.robots[role]

    def disabled_roles(self) -> frozenset[str]:
        if self.robot_mode == 2 and self.disabled_role:
            return frozenset({self.disabled_role})
        return frozenset()

    def is_role_active(self, role: str) -> bool:
        return role not in self.disabled_roles()

    def active_roles(self) -> tuple[str, ...]:
        return tuple(r for r in ROLE_ORDER if self.is_role_active(r))


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _parse_vary_lines() -> tuple[int, ...]:
    """VARY_LINES=1,3,7 -- an empty value means a fully fixed script."""
    raw = os.getenv("VARY_LINES")
    if raw is None:
        return vary_line_numbers()
    if not raw.strip():
        return ()
    numbers = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk:
            numbers.append(int(chunk))
    return tuple(numbers)


def _robot_from_env(role: str) -> RobotConfig:
    prefix = role.upper()
    name = os.getenv(f"{prefix}_NAME", "").strip().strip('"') or None
    return RobotConfig(
        role=role,
        name=name,
        host=os.getenv(f"{prefix}_HOST", "").strip() or DEFAULT_HOSTS[role],
        voice=os.getenv(f"{prefix}_VOICE", "").strip() or DEFAULT_VOICES[role],
    )


def load_config() -> Config:
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is not set. Copy .env.example to .env.")

    robot_mode = 2 if os.getenv("ROBOT_MODE", "3").strip() == "2" else 3
    disabled = normalize_role(os.getenv("DISABLED_ROLE")) if robot_mode == 2 else None

    return Config(
        elevenlabs_api_key=api_key,
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL", "").strip()
        or "eleven_multilingual_v2",
        robots={role: _robot_from_env(role) for role in ROLE_ORDER},
        robot_mode=robot_mode,
        disabled_role=disabled,
        inter_turn_pause=_env_float("INTER_TURN_PAUSE", 0.6),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip()
        or "http://127.0.0.1:11434/v1",
        llm_model=os.getenv("LLM_MODEL", "").strip() or "qwen3.6:35b-a3b-q6_K_L",
        llm_api_key=os.getenv("LLM_API_KEY", "").strip() or "ollama",
        vary_lines=_parse_vary_lines(),
        variant_count=max(1, _env_int("VARIANT_COUNT", 5)),
        loop_mode=normalize_mode(os.getenv("LOOP_MODE")),
        display_web_host=os.getenv("DISPLAY_WEB_HOST", "").strip() or "0.0.0.0",
        display_web_port=_env_int("DISPLAY_WEB_PORT", 8010),
        display_speech_bitrate=_env_int("DISPLAY_SPEECH_BITRATE", 48),
        attract_seconds=_env_float("ATTRACT_SECONDS", 20.0),
    )
