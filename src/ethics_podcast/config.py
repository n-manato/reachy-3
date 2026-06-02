"""Configuration for the Ethics Podcast application."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# Default ElevenLabs voice IDs chosen to fit each philosopher persona.
# Robot A = Bentham-Bot   = George   (warm British storyteller, theatrical lawyer)
# Robot B = Korsgaard-Bot = Matilda  (calm, principled female academic alto)
# Robot C = Aquinas-Bot   = Bill     (warm, mature, slow, deliberate narrator)
DEFAULT_VOICE_A = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_VOICE_B = "XrExE9yKIg1WjnnlVkGX"
DEFAULT_VOICE_C = "pqHfZKP75CvOlQylNhV4"


@dataclass(frozen=True)
class RobotConfig:
    """Connection config for a single Reachy Mini."""

    name: str | None  # robot_name label
    host: str  # hostname or IP address of the robot's daemon
    voice: str  # ElevenLabs voice_id
    role: str  # "student", "professor", or "moderator"


@dataclass(frozen=True)
class Config:
    """Top-level application config."""

    openai_api_key: str
    openai_model: str

    elevenlabs_api_key: str
    elevenlabs_model: str

    robot_a: RobotConfig  # Hello, Bentham-Bot
    robot_b: RobotConfig  # Mellow, Korsgaard-Bot
    robot_c: RobotConfig  # Fellow, Aquinas-Bot

    # Turn management
    inter_turn_pause: float = 1.0
    max_response_tokens: int = 200

    # Multi-display exhibit layout
    display_top_monitor: int = 0
    # Monitor index per role, in (student, professor, moderator) order.
    display_mini_monitors: tuple[int, int, int] = (1, 2, 3)
    # When True (or when too few monitors are connected) all four windows
    # are tiled on the primary screen so the flow can be tested on one machine.
    display_test_mode: bool = False

    # Display backend for the Multi-Display Exhibit:
    #   "qt"  — four native PySide6 windows on locally-attached monitors
    #   "web" — browser pages served over the LAN (one per display)
    display_backend: str = "qt"
    # Host/port for the "web" backend. 0.0.0.0 = reachable from any LAN device.
    display_web_host: str = "0.0.0.0"
    display_web_port: int = 8000

    @classmethod
    def from_env(cls) -> "Config":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        eleven_key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required")

        return cls(
            openai_api_key=api_key,
            openai_model=os.environ.get("MODEL_NAME", "gpt-4o-realtime-preview"),

            elevenlabs_api_key=eleven_key,
            elevenlabs_model=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"),

            robot_a=RobotConfig(
                name=os.environ.get("ROBOT_A_NAME"),
                host=os.environ.get("ROBOT_A_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_A_VOICE", DEFAULT_VOICE_A),
                role="student",
            ),

            robot_b=RobotConfig(
                name=os.environ.get("ROBOT_B_NAME"),
                host=os.environ.get("ROBOT_B_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_B_VOICE", DEFAULT_VOICE_B),
                role="professor",
            ),

            robot_c=RobotConfig(
                name=os.environ.get("ROBOT_C_NAME"),
                host=os.environ.get("ROBOT_C_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_C_VOICE", DEFAULT_VOICE_C),
                role="moderator",
            ),

            inter_turn_pause=float(os.environ.get("INTER_TURN_PAUSE", "1.0")),

            display_top_monitor=int(os.environ.get("DISPLAY_TOP_MONITOR", "0")),
            display_mini_monitors=_parse_mini_monitors(
                os.environ.get("DISPLAY_MINI_MONITORS", "1,2,3")
            ),
            display_test_mode=_parse_bool(
                os.environ.get("DISPLAY_TEST_MODE", "false")
            ),
            display_backend=os.environ.get("DISPLAY_BACKEND", "qt").strip().lower(),
            display_web_host=os.environ.get("DISPLAY_WEB_HOST", "0.0.0.0"),
            display_web_port=int(os.environ.get("DISPLAY_WEB_PORT", "8000")),
        )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _parse_mini_monitors(value: str) -> tuple[int, int, int]:
    """Parse '1,2,3' into a 3-tuple of monitor indices (student/prof/mod)."""
    try:
        parts = [int(p.strip()) for p in value.split(",") if p.strip() != ""]
    except ValueError:
        parts = []

    if len(parts) >= 3:
        return (parts[0], parts[1], parts[2])

    # Pad/fallback so we always return three indices.
    defaults = [1, 2, 3]
    parts = (parts + defaults)[:3]
    return (parts[0], parts[1], parts[2])
