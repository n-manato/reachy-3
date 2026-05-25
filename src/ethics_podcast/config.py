"""Configuration for the Ethics Podcast application."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class RobotConfig:
    """Connection config for a single Reachy Mini."""

    name: str | None  # robot_name label
    host: str  # hostname or IP address of the robot's daemon
    voice: str  # OpenAI Realtime voice id
    role: str  # "student", "professor", or "moderator"


@dataclass(frozen=True)
class Config:
    """Top-level application config."""

    openai_api_key: str
    openai_model: str

    robot_a: RobotConfig  # Hello, Confused Student
    robot_b: RobotConfig  # Mellow, Ethics Professor
    robot_c: RobotConfig  # Fellow, Moderator

    # Turn management
    inter_turn_pause: float = 1.0
    max_response_tokens: int = 200

    @classmethod
    def from_env(cls) -> "Config":
        api_key = os.environ.get("OPENAI_API_KEY", "")

        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        return cls(
            openai_api_key=api_key,
            openai_model=os.environ.get("MODEL_NAME", "gpt-4o-realtime-preview"),

            robot_a=RobotConfig(
                name=os.environ.get("ROBOT_A_NAME"),
                host=os.environ.get("ROBOT_A_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_A_VOICE", "echo"),
                role="student",
            ),

            robot_b=RobotConfig(
                name=os.environ.get("ROBOT_B_NAME"),
                host=os.environ.get("ROBOT_B_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_B_VOICE", "sage"),
                role="professor",
            ),

            robot_c=RobotConfig(
                name=os.environ.get("ROBOT_C_NAME"),
                host=os.environ.get("ROBOT_C_HOST", "reachy-mini.local"),
                voice=os.environ.get("ROBOT_C_VOICE", "coral"),
                role="moderator",
            ),

            inter_turn_pause=float(os.environ.get("INTER_TURN_PAUSE", "1.0")),
        )