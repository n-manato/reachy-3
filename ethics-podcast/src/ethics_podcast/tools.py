"""Tool definitions for the OpenAI Realtime API sessions and dispatch logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ethics_podcast.robot_controller import RobotController

logger = logging.getLogger(__name__)


# ── Tool schemas (sent to OpenAI) ──────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "move_head",
        "description": (
            "Move your head in a direction. Use this to express yourself physically: "
            "look left/right when confused, nod (down then front) to agree, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["left", "right", "up", "down", "front"],
                    "description": "Direction to move the head.",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "type": "function",
        "name": "play_emotion",
        "description": (
            "Play an emotion animation. Use to express feelings during conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "enum": [
                        "cheerful1",
                        "confused1",
                        "curious1",
                        "surprised1",
                        "surprised2",
                        "thoughtful1",
                        "enthusiastic1",
                        "laughing1",
                        "sad1",
                        "amazed1",
                        "frustrated1",
                        "understanding1",
                        "uncertain1",
                        "scared1",
                        "oops1",
                    ],
                    "description": "The emotion to express.",
                },
            },
            "required": ["emotion"],
        },
    },
    {
        "type": "function",
        "name": "dance",
        "description": (
            "Do a little dance. Use to celebrate, express excitement, "
            "or lighten the mood."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dance_name": {
                    "type": "string",
                    "enum": [
                        "simple_nod",
                        "head_tilt_roll",
                        "side_to_side_sway",
                        "dizzy_spin",
                        "stumble_and_recover",
                        "headbanger_combo",
                        "yeah_nod",
                        "groovy_sway_and_roll",
                        "chicken_peck",
                        "pendulum_swing",
                        "jackson_square",
                    ],
                    "description": "Which dance to perform.",
                },
            },
            "required": ["dance_name"],
        },
    },
]


# ── Dispatch ──────────────────────────────────────────────────────────────

async def dispatch_tool_call(
    robot: RobotController,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Execute a tool call on the given robot. Returns a result string for the API."""
    try:
        if tool_name == "move_head":
            direction = arguments.get("direction", "front")
            robot.move_head(direction)
            return f"Moved head {direction}."

        elif tool_name == "play_emotion":
            emotion = arguments.get("emotion", "happy")
            robot.play_emotion(emotion)
            return f"Played emotion: {emotion}."

        elif tool_name == "dance":
            dance_name = arguments.get("dance_name", "simple_nod")
            robot.play_dance(dance_name)
            return f"Danced: {dance_name}."

        else:
            logger.warning("Unknown tool: %s", tool_name)
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error("Tool %s failed: %s", tool_name, e)
        return f"Tool error: {e}"
