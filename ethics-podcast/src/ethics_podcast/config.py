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

    # ── Robot count mode ────────────────────────────────────────────────────
    # robot_mode = 3 (default) drives all three robots.
    # robot_mode = 2 runs the exhibit with one robot physically absent (e.g. a
    # broken unit). The conversation content stays 3-person: the absent robot's
    # persona still speaks through the TV / browser audio, but no physical robot
    # is connected or moved for it — its controller becomes a MockRobot.
    # ``disabled_role`` names which role is absent (only meaningful when
    # robot_mode == 2); the default is the center robot, Korsgaard-Bot.
    robot_mode: int = 3
    disabled_role: str | None = None

    # Turn management
    inter_turn_pause: float = 1.0
    max_response_tokens: int = 200

    # Extra playback delays (seconds) applied only in the "hybrid_surround"
    # audio mode, to align the TV/room speakers with the robot speakers.
    tv_audio_delay: float = 0.0
    robot_audio_delay: float = 0.0

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
    # Order of the multi-display exhibit loop: "sequential" | "shuffle" | "one".
    # The loop button on the /top page cycles it live; this is the start value.
    loop_mode: str = "sequential"

    # Internal roles, in fixed left→center→right order.
    ROLE_ORDER = ("student", "professor", "moderator")

    @classmethod
    def from_env(cls) -> "Config":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        eleven_key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY environment variable is required")

        robot_mode = _parse_robot_mode(os.environ.get("ROBOT_MODE", "3"))
        # Only meaningful in 2-robot mode; default absent seat is the center
        # robot (Korsgaard-Bot / professor).
        disabled_role = (
            _parse_disabled_role(os.environ.get("DISABLED_ROLE", "professor"))
            if robot_mode == 2
            else None
        )

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

            robot_mode=robot_mode,
            disabled_role=disabled_role,

            inter_turn_pause=float(os.environ.get("INTER_TURN_PAUSE", "1.0")),

            tv_audio_delay=float(os.environ.get("TV_AUDIO_DELAY", "0.0")),
            robot_audio_delay=float(os.environ.get("ROBOT_AUDIO_DELAY", "0.0")),

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
            loop_mode=normalize_loop_mode(os.environ.get("LOOP_MODE")),
        )

    # ── Robot-mode helpers ──────────────────────────────────────────────────

    def role_configs(self) -> dict[str, RobotConfig]:
        """Map each internal role to its RobotConfig (all three, in order)."""
        return {
            "student": self.robot_a,
            "professor": self.robot_b,
            "moderator": self.robot_c,
        }

    def disabled_roles(self) -> frozenset[str]:
        """Roles with no physical robot (empty in 3-robot mode)."""
        if self.robot_mode == 2 and self.disabled_role:
            return frozenset({self.disabled_role})
        return frozenset()

    def is_role_active(self, role: str) -> bool:
        """True when a physical robot is expected for this role."""
        return role not in self.disabled_roles()

    def active_roles(self) -> tuple[str, ...]:
        """Roles that have a physical robot, in left→center→right order."""
        disabled = self.disabled_roles()
        return tuple(r for r in self.ROLE_ORDER if r not in disabled)


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


# Loop modes for the multi-display exhibit, in the order the /top page's loop
# button cycles through them. "sequential" plays the videos in order (the
# original behaviour), "shuffle" draws each round in a random order, "one"
# repeats the current video.
LOOP_MODES = ("sequential", "shuffle", "one")


def normalize_loop_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in LOOP_MODES else LOOP_MODES[0]


# Where the robots' voices come out in the web multi-display exhibit, picked
# live on the /top page: the robots' own speakers, or the /top browser (the
# same output as the video). The menu / EXHIBIT_AUDIO_MODE choice only sets
# the start value.
AUDIO_OUTPUTS = ("robot", "browser")


def normalize_audio_output(value: str | None) -> str:
    output = (value or "").strip().lower()
    return output if output in AUDIO_OUTPUTS else AUDIO_OUTPUTS[0]


def audio_output_for_mode(audio_mode: str) -> str:
    """Start value of the /top voice-output switch for a configured audio mode."""
    return "browser" if audio_mode in ("computer", "surround") else "robot"


def _parse_robot_mode(value: str) -> int:
    """Parse ROBOT_MODE into 2 or 3 (defaults to 3 for anything unexpected)."""
    return 2 if value.strip() == "2" else 3


# Accept a few friendly aliases for the disabled seat so .env can say either the
# internal role or the philosopher/position.
_ROLE_ALIASES = {
    "student": "student",
    "bentham": "student",
    "bentham-bot": "student",
    "left": "student",
    "a": "student",
    "professor": "professor",
    "korsgaard": "professor",
    "korsgaard-bot": "professor",
    "center": "professor",
    "centre": "professor",
    "b": "professor",
    "moderator": "moderator",
    "aquinas": "moderator",
    "aquinas-bot": "moderator",
    "right": "moderator",
    "c": "moderator",
}


def _parse_disabled_role(value: str) -> str:
    """Normalize DISABLED_ROLE to an internal role name (default professor)."""
    return _ROLE_ALIASES.get(value.strip().lower(), "professor")


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
