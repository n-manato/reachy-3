"""Environment configuration for the Leading Through AI exhibit.

Everything tunable lives in .env. The API keys are only needed to generate
(menus 1 and 2); the show itself runs entirely from files on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from lta_exhibit.personas import AEGIS, NOVA, ROLE_ORDER, SAGE

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
PICTURE_DIR = PROJECT_ROOT / "picture"
MUSIC_DIR = PROJECT_ROOT / "music"
VOTES_PATH = OUTPUT_DIR / "votes.jsonl"
SETTINGS_PATH = OUTPUT_DIR / "settings.json"   # volume, kept across restarts

__all__ = ["ROLE_ORDER", "Config", "RobotConfig", "load_config"]

# George / Matilda / Bill -- the Ethics Podcast cast, already proven on these
# robots' speakers. Overridable per role in .env.
DEFAULT_VOICES = {
    NOVA: "JBFqnCBsd6RMkjVDRZzb",
    SAGE: "XrExE9yKIg1WjnnlVkGX",
    AEGIS: "pqHfZKP75CvOlQylNhV4",
}

DEFAULT_HOSTS = {
    NOVA: "192.168.3.163",
    SAGE: "192.168.3.106",
    AEGIS: "192.168.3.132",
}

_ROLE_ALIASES = {
    "nova": NOVA, "left": NOVA, "a": NOVA,
    "sage": SAGE, "center": SAGE, "centre": SAGE, "b": SAGE,
    "aegis": AEGIS, "right": AEGIS, "c": AEGIS,
}

SEAT_OF_ROLE = {NOVA: "left", SAGE: "center", AEGIS: "right"}
ROLE_OF_SEAT = {seat: role for role, seat in SEAT_OF_ROLE.items()}


def normalize_role(value: str | None) -> str | None:
    if not value:
        return None
    return _ROLE_ALIASES.get(value.strip().lower())


@dataclass(frozen=True)
class RobotConfig:
    role: str
    name: str | None
    host: str
    voice: str


@dataclass(frozen=True)
class Config:
    openai_api_key: str
    script_model: str
    elevenlabs_api_key: str
    elevenlabs_model: str

    robots: dict[str, RobotConfig]
    robot_mode: int
    disabled_role: str | None
    inter_turn_pause: float

    audio_output: str            # "robot" | "display"
    robot_audio_latency: float
    robot_gain_db: float         # extra level for the robot speakers (limited)

    vote_seconds: float
    attract_seconds: float
    auto_start: bool

    display_web_host: str
    display_web_port: int
    display_speech_bitrate: int

    def robot(self, role: str) -> RobotConfig:
        return self.robots[role]

    def disabled_roles(self) -> frozenset[str]:
        if self.robot_mode == 2 and self.disabled_role:
            return frozenset({self.disabled_role})
        return frozenset()

    def is_role_active(self, role: str) -> bool:
        return role not in self.disabled_roles()


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
    robot_mode = 2 if os.getenv("ROBOT_MODE", "3").strip() == "2" else 3
    disabled = normalize_role(os.getenv("DISABLED_ROLE")) if robot_mode == 2 else None
    audio = os.getenv("AUDIO_OUTPUT", "robot").strip().lower()
    if audio not in ("robot", "display"):
        audio = "robot"

    return Config(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        script_model=os.getenv("SCRIPT_MODEL", "").strip() or "gpt-5",
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL", "").strip()
        or "eleven_multilingual_v2",
        robots={role: _robot_from_env(role) for role in ROLE_ORDER},
        robot_mode=robot_mode,
        disabled_role=disabled,
        inter_turn_pause=_env_float("INTER_TURN_PAUSE", 0.5),
        audio_output=audio,
        robot_audio_latency=_env_float("ROBOT_AUDIO_LATENCY", 0.15),
        # +8 dB through a peak limiter = about twice as loud (measured +6 dB RMS).
        robot_gain_db=max(0.0, min(12.0, _env_float("ROBOT_GAIN_DB", 8.0))),
        vote_seconds=max(5.0, _env_float("VOTE_SECONDS", 20.0)),
        attract_seconds=max(5.0, _env_float("ATTRACT_SECONDS", 45.0)),
        auto_start=os.getenv("AUTO_START", "1").strip() != "0",
        display_web_host=os.getenv("DISPLAY_WEB_HOST", "").strip() or "0.0.0.0",
        display_web_port=_env_int("DISPLAY_WEB_PORT", 8020),
        display_speech_bitrate=_env_int("DISPLAY_SPEECH_BITRATE", 48),
    )
