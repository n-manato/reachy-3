"""Core orchestrator: manages two OpenAI Realtime sessions with turn-taking.

Architecture:
- Three persistent WebSocket connections to OpenAI Realtime API (one per persona),
  configured for TEXT-ONLY output so the LLM is only the script writer.
- Text transcript from Robot A is injected as a user message into the next
  robot's session. No microphone audio is used — all routing is programmatic.
- Audio is generated separately per turn via ElevenLabs streaming TTS, with
  per-persona voice IDs + tuning. PCM chunks are pushed to the robot's speaker
  as they arrive.
- Tool calls are dispatched to the correct robot's controller.
- Strict turn-taking: only one robot speaks at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any

import numpy as np
from openai import AsyncOpenAI

from ethics_podcast.config import Config
from ethics_podcast.elevenlabs_tts import (
    ELEVENLABS_SAMPLE_RATE,
    settings_for,
    stream_pcm,
)
from ethics_podcast.prompts import STUDENT_PROMPT, PROFESSOR_PROMPT, OPENING_SEED
from ethics_podcast.robot_controller import RobotController, connect_robots
from ethics_podcast.tools import TOOL_DEFINITIONS, dispatch_tool_call

logger = logging.getLogger(__name__)

# ElevenLabs PCM output is 24 kHz 16-bit mono — matches the rate the robot
# worker assumes (OPENAI_SAMPLE_RATE) so no resample is needed before push_audio.
AUDIO_SAMPLE_RATE = ELEVENLABS_SAMPLE_RATE


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
                # Configure sessions for text-only output
                await self._configure_session(conn_a, STUDENT_PROMPT)
                await self._configure_session(conn_b, PROFESSOR_PROMPT)
                logger.info("Both OpenAI Realtime sessions configured (text-only).")

                # Auto-start: seed the student with an opening message
                await self._inject_message(conn_a, OPENING_SEED)
                logger.info("Opening seed injected. Podcast starting...")

                # Run the alternating conversation
                await self._conversation_loop(conn_a, conn_b)

    async def _configure_session(self, conn: Any, instructions: str) -> None:
        """Configure a Realtime session for text-only generation."""
        await conn.session.update(
            session={
                "type": "realtime",
                "instructions": instructions,
                "output_modalities": ["text"],
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": AUDIO_SAMPLE_RATE},
                        "turn_detection": None,  # No VAD — we inject text
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
        transcript_a = await self._collect_response(conn_a, self._robot_a, "Student", "student")
        self._turn_count += 1
        logger.info("[Turn %d] Student: %s", self._turn_count, transcript_a[:100])

        while not self._shutdown.is_set():
            # Pause between turns for natural rhythm
            await asyncio.sleep(self.config.inter_turn_pause)

            # B responds to A's transcript
            await self._inject_message(conn_b, transcript_a)
            transcript_b = await self._collect_response(
                conn_b, self._robot_b, "Professor", "professor"
            )
            self._turn_count += 1
            logger.info("[Turn %d] Professor: %s", self._turn_count, transcript_b[:100])

            if self._shutdown.is_set():
                break

            await asyncio.sleep(self.config.inter_turn_pause)

            # A responds to B's transcript
            await self._inject_message(conn_a, transcript_b)
            transcript_a = await self._collect_response(
                conn_a, self._robot_a, "Student", "student"
            )
            self._turn_count += 1
            logger.info("[Turn %d] Student: %s", self._turn_count, transcript_a[:100])

    async def _collect_response(
        self,
        conn: Any,
        robot: RobotController,
        label: str,
        role: str,
    ) -> str:
        """Wait for a text response, then synthesize + stream audio to the robot.

        Handles in-flight tool calls. Returns the full transcript when audio
        playback has fully drained on the robot side.
        """
        transcript_parts: list[str] = []
        pending_tool_calls: dict[str, dict[str, str]] = {}

        async for event in conn:
            event_type = event.type

            if event_type == "response.output_text.delta":
                transcript_parts.append(event.delta)

            elif event_type == "response.output_audio_transcript.delta":
                # Defensive: in case the API ever emits audio transcript too
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

                result = await dispatch_tool_call(robot, tool_name, arguments)

                await conn.conversation.item.create(
                    item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    },
                )
                await conn.response.create()

            # ── Response complete ──────────────────────────────────────
            elif event_type == "response.done":
                full_transcript = "".join(transcript_parts).strip()
                if full_transcript:
                    await self._speak_text(robot, label, role, full_transcript)
                    return full_transcript
                continue

            # ── Session/housekeeping events — skip ────────────────────
            elif event_type in (
                "session.created", "session.updated",
                "response.created", "response.output_item.added",
                "response.output_item.done",
                "response.content_part.added", "response.content_part.done",
                "conversation.item.added", "conversation.item.done",
                "conversation.item.created",
                "response.output_text.done",
            ):
                pass

            elif event_type == "error":
                logger.error("[%s] API error: %s", label, event)
                break

        return "".join(transcript_parts).strip() or "[no response]"

    async def _speak_text(
        self,
        robot: RobotController,
        label: str,
        role: str,
        text: str,
    ) -> None:
        """Stream ElevenLabs TTS chunks to the robot's speaker, then drain.

        ElevenLabs returns raw 24 kHz int16 PCM; we convert per chunk to float32
        and push to the robot worker. After the stream ends we wait for the
        tail of audio to play out so the next turn doesn't overlap.
        """
        voice_id = (
            self.config.robot_a.voice if role == "student"
            else self.config.robot_b.voice if role == "professor"
            else self.config.robot_c.voice
        )
        settings = settings_for(role)

        total_samples = 0
        # ElevenLabs may yield odd-byte boundary chunks. Carry leftover bytes
        # across iterations so int16 framing stays aligned.
        leftover = b""

        async for chunk in stream_pcm(
            text=text,
            voice_id=voice_id,
            settings=settings,
            api_key=self.config.elevenlabs_api_key,
            model_id=self.config.elevenlabs_model,
        ):
            buf = leftover + chunk
            if len(buf) % 2:
                leftover = buf[-1:]
                buf = buf[:-1]
            else:
                leftover = b""

            if not buf:
                continue

            pcm_int16 = np.frombuffer(buf, dtype=np.int16)
            pcm_float32 = pcm_int16.astype(np.float32) / 32768.0
            total_samples += len(pcm_float32)
            robot.push_audio(pcm_float32, source_rate=AUDIO_SAMPLE_RATE)

        if total_samples > 0:
            playback_secs = total_samples / AUDIO_SAMPLE_RATE
            # Most audio was already streaming during ElevenLabs response.
            # Wait for the tail end + buffer so the next turn doesn't overlap.
            wait = max(0.0, playback_secs * 0.3 + 0.5)
            logger.info(
                "[%s] Audio done (%.1fs). Waiting %.1fs for playback drain.",
                label, playback_secs, wait,
            )
            await asyncio.sleep(wait)

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
