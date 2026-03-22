"""Core orchestrator: manages two OpenAI Realtime sessions with turn-taking.

Architecture:
- Two persistent WebSocket connections to OpenAI Realtime API (one per persona).
- Text transcript from Robot A is injected as a user message into Robot B's session,
  and vice versa. No microphone audio is used — all routing is programmatic.
- Audio output from each session is streamed to the corresponding robot's speaker.
- Tool calls are dispatched to the correct robot's controller.
- Strict turn-taking: only one robot speaks at a time.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import signal
from typing import Any

import numpy as np
from openai import AsyncOpenAI

from ethics_podcast.config import Config
from ethics_podcast.prompts import STUDENT_PROMPT, PROFESSOR_PROMPT, OPENING_SEED
from ethics_podcast.robot_controller import RobotController, connect_robots
from ethics_podcast.tools import TOOL_DEFINITIONS, dispatch_tool_call

logger = logging.getLogger(__name__)

# OpenAI Realtime outputs 24 kHz 16-bit PCM
OPENAI_SAMPLE_RATE = 24000


class PodcastOrchestrator:
    """Runs the dual-robot ethics podcast."""

    def __init__(self, config: Config):
        self.config = config
        self._client = AsyncOpenAI(api_key=config.openai_api_key)
        self._robot_a: RobotController | None = None
        self._robot_b: RobotController | None = None
        self._shutdown = asyncio.Event()
        self._turn_count = 0

    async def run(self) -> None:
        """Main entry point — connects robots, opens sessions, runs the podcast."""
        logger.info("Connecting to robots...")
        self._robot_a, self._robot_b = await connect_robots(
            self.config.robot_a,
            self.config.robot_b,
        )
        logger.info("Both robots connected.")

        # Wiggle antennas as visual confirmation
        self._robot_a.wiggle_antennas()
        self._robot_b.wiggle_antennas()

        try:
            await self._run_sessions()
        finally:
            self._robot_a.stop()
            self._robot_b.stop()

    async def _run_sessions(self) -> None:
        """Open both Realtime sessions and run the conversation loop."""
        async with self._client.realtime.connect(model=self.config.openai_model) as conn_a:
            async with self._client.realtime.connect(model=self.config.openai_model) as conn_b:
                # Configure sessions
                await self._configure_session(conn_a, STUDENT_PROMPT, self.config.robot_a.voice)
                await self._configure_session(conn_b, PROFESSOR_PROMPT, self.config.robot_b.voice)
                logger.info("Both OpenAI Realtime sessions configured.")

                # Auto-start: seed the student with an opening message
                await self._inject_message(conn_a, OPENING_SEED)
                logger.info("Opening seed injected. Podcast starting...")

                # Run the alternating conversation
                await self._conversation_loop(conn_a, conn_b)

    async def _configure_session(self, conn: Any, instructions: str, voice: str) -> None:
        """Configure a single Realtime API session."""
        await conn.session.update(
            session={
                "type": "realtime",
                "instructions": instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": OPENAI_SAMPLE_RATE},
                        "turn_detection": None,  # No VAD — we inject text
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": OPENAI_SAMPLE_RATE},
                        "voice": voice,
                    },
                },
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
            },
        )

    async def _inject_message(self, conn: Any, text: str) -> None:
        """Inject a text message into a session and trigger a response."""
        await conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        )
        await conn.response.create()

    async def _conversation_loop(self, conn_a: Any, conn_b: Any) -> None:
        """Alternating A → B → A → B conversation loop."""
        # First turn: A speaks (already triggered by seed injection)
        transcript_a = await self._collect_response(conn_a, self._robot_a, "Student")
        self._turn_count += 1
        logger.info("[Turn %d] Student: %s", self._turn_count, transcript_a[:100])

        while not self._shutdown.is_set():
            # Pause between turns for natural rhythm
            await asyncio.sleep(self.config.inter_turn_pause)

            # B responds to A's transcript
            await self._inject_message(conn_b, transcript_a)
            transcript_b = await self._collect_response(conn_b, self._robot_b, "Professor")
            self._turn_count += 1
            logger.info("[Turn %d] Professor: %s", self._turn_count, transcript_b[:100])

            if self._shutdown.is_set():
                break

            await asyncio.sleep(self.config.inter_turn_pause)

            # A responds to B's transcript
            await self._inject_message(conn_a, transcript_b)
            transcript_a = await self._collect_response(conn_a, self._robot_a, "Student")
            self._turn_count += 1
            logger.info("[Turn %d] Student: %s", self._turn_count, transcript_a[:100])

    async def _collect_response(
        self,
        conn: Any,
        robot: RobotController,
        label: str,
    ) -> str:
        """Listen for a complete response from one session.

        Streams audio to the robot's speaker, handles tool calls, and returns
        the full text transcript when the response is done.
        """
        transcript_parts: list[str] = []
        pending_tool_calls: dict[str, dict[str, str]] = {}  # call_id -> {name, arguments}
        total_audio_samples = 0  # track samples to wait for playback

        async for event in conn:
            event_type = event.type

            # Log non-audio events for debugging
            if not event_type.startswith("response.output_audio.delta"):
                logger.debug("[%s] Event: %s", label, event_type)

            # ── Audio delta: stream to speaker ──────────────────────────
            if event_type == "response.output_audio.delta":
                audio_bytes = base64.b64decode(event.delta)
                pcm_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                pcm_float32 = pcm_int16.astype(np.float32) / 32768.0
                total_audio_samples += len(pcm_float32)
                robot.push_audio(pcm_float32, source_rate=OPENAI_SAMPLE_RATE)

            # ── Audio transcript delta ─────────────────────────────────
            elif event_type == "response.output_audio_transcript.delta":
                transcript_parts.append(event.delta)

            # ── Tool call argument accumulation ────────────────────────
            elif event_type == "response.function_call_arguments.delta":
                call_id = event.call_id
                if call_id not in pending_tool_calls:
                    pending_tool_calls[call_id] = {"name": "", "arguments": ""}
                pending_tool_calls[call_id]["arguments"] += event.delta

            # ── Tool call complete ─────────────────────────────────────
            elif event_type == "response.function_call_arguments.done":
                call_id = event.call_id
                tool_name = event.name
                try:
                    arguments = json.loads(event.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                logger.info("[%s] Tool call: %s(%s)", label, tool_name, arguments)

                # Dispatch tool to the correct robot
                result = await dispatch_tool_call(robot, tool_name, arguments)

                # Send tool result back to the session
                await conn.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    },
                )
                # After tool output, trigger continuation
                await conn.response.create()

            # ── Response complete ──────────────────────────────────────
            elif event_type == "response.done":
                full_transcript = "".join(transcript_parts).strip()
                if full_transcript:
                    # Wait for the robot to finish playing all queued audio
                    # before returning, so turns don't overlap.
                    if total_audio_samples > 0:
                        playback_secs = total_audio_samples / OPENAI_SAMPLE_RATE
                        # Audio was streamed as it arrived, so most has
                        # already played. Wait for the tail end + buffer.
                        wait = max(0, playback_secs * 0.3 + 0.5)
                        logger.info(
                            "[%s] Audio done (%.1fs). Waiting %.1fs for playback drain.",
                            label, playback_secs, wait,
                        )
                        await asyncio.sleep(wait)
                    return full_transcript
                # If no transcript yet (e.g., tool-only response), keep listening
                continue

            # ── Session/housekeeping events — skip ────────────────────
            elif event_type in ("session.created", "session.updated",
                                "response.created", "response.output_item.added",
                                "response.output_item.done",
                                "response.content_part.added", "response.content_part.done",
                                "conversation.item.added", "conversation.item.done",
                                "conversation.item.created",
                                "response.output_audio.done",
                                "response.output_audio_transcript.done"):
                pass

            # ── Error handling ─────────────────────────────────────────
            elif event_type == "error":
                logger.error("[%s] API error: %s", label, event)
                break

        # Fallback if we exit the loop without a transcript
        return "".join(transcript_parts).strip() or "[no response]"

    def request_shutdown(self) -> None:
        """Signal the orchestrator to stop after the current turn."""
        logger.info("Shutdown requested.")
        self._shutdown.set()


async def run_podcast(config: Config) -> None:
    """Top-level async entry point."""
    orchestrator = PodcastOrchestrator(config)

    # Handle graceful shutdown on Ctrl+C
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, orchestrator.request_shutdown)

    await orchestrator.run()
