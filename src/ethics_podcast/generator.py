"""Phase 1: Generate the ethics conversation and save audio + transcripts to disk.

Two-step process:
  Step A: Generate conversation via OpenAI Realtime API (audio + transcript only, NO tools)
          This is fast and reliable — single response per turn, no multi-response loops.
  Step B: Assign emotions/actions to each turn using a fast text completion.
          Analyzes each transcript and picks appropriate emotions and gestures.

Output: output/conversation.json with audio files + tool_calls per turn.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np
from openai import AsyncOpenAI

from ethics_podcast.config import Config
from ethics_podcast.prompts import (
    student_prompt,
    professor_prompt,
    opening_seed,
    closing_nudge_student,
    closing_nudge_professor,
)

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("output")
OPENAI_SAMPLE_RATE = 24000


def _write_wav(path: Path, pcm_int16: bytes, sample_rate: int = OPENAI_SAMPLE_RATE) -> None:
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_int16)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))
        f.write(struct.pack("<H", 1))
        f.write(struct.pack("<H", num_channels))
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", bits_per_sample))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(pcm_int16)


# ── Step A: Generate audio (no tools — simple and reliable) ────────────────

async def _collect_audio_response(conn: Any, label: str) -> tuple[str, bytes]:
    """Collect transcript + audio. No tool calls — single response, never hangs."""
    transcript_parts: list[str] = []
    audio_chunks: list[bytes] = []

    empty_responses = 0

    async for event in conn:
        t = event.type

        if t == "response.output_audio.delta":
            audio_chunks.append(base64.b64decode(event.delta))
        elif t == "response.output_audio_transcript.delta":
            transcript_parts.append(event.delta)
        elif t == "response.done":
            transcript = "".join(transcript_parts).strip()
            if transcript and audio_chunks:
                return transcript, b"".join(audio_chunks)

            # Empty response — retry up to 2 times
            empty_responses += 1
            if empty_responses <= 2:
                logger.warning("[%s] Empty response #%d. Retrying in 2s...", label, empty_responses)
                await asyncio.sleep(2)
                await conn.response.create()
                continue
            else:
                logger.error("[%s] Still empty after %d retries.", label, empty_responses)
                break
        elif t == "error":
            logger.error("[%s] API error: %s", label, event)
            # Don't break on non-fatal errors — some errors are warnings
            if "invalid" in str(getattr(event, 'error', '')).lower():
                break

    return "".join(transcript_parts).strip() or "[no response]", b"".join(audio_chunks)


async def _generate_audio(config: Config, total_turns: int) -> list[dict]:
    """Generate all turns as audio + transcript. No tools = reliable."""
    client = AsyncOpenAI(api_key=config.openai_api_key)
    turns: list[dict] = []

    async with client.realtime.connect(model=config.openai_model) as conn_a:
        async with client.realtime.connect(model=config.openai_model) as conn_b:
            await _configure(conn_a, student_prompt(total_turns), config.robot_a.voice)
            await _configure(conn_b, professor_prompt(total_turns), config.robot_b.voice)
            logger.info("Both sessions configured.")

            # Turn 1
            await _inject(conn_a, opening_seed(total_turns))
            transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
            _save_turn(turns, 1, "student", transcript_a, audio_a)

            turn = 1
            while turn < total_turns:
                turn += 1
                msg_b = transcript_a
                if turn == total_turns:
                    msg_b += "\n\n[SYSTEM: " + closing_nudge_professor(total_turns) + "]"
                await _inject(conn_b, msg_b)
                transcript_b, audio_b = await _collect_audio_response(conn_b, "Professor")
                _save_turn(turns, turn, "professor", transcript_b, audio_b)

                if turn >= total_turns:
                    break

                turn += 1
                msg_a = transcript_b
                if turn == total_turns - 1:
                    msg_a += "\n\n[SYSTEM: " + closing_nudge_student(total_turns) + "]"
                await _inject(conn_a, msg_a)
                transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
                _save_turn(turns, turn, "student", transcript_a, audio_a)

    return turns


# ── Step B: Assign emotions using text model ───────────────────────────────

EMOTION_ASSIGNMENT_PROMPT = """\
You are assigning physical robot actions to a conversation transcript.
For each turn, pick ONE emotion and ONE head direction that best matches the content.

Available emotions:
- cheerful1: happy, excited, greeting
- confused1: baffled, lost, doesn't understand
- curious1: intrigued, wants to know more
- surprised1: shocked, didn't expect that
- thoughtful1: pondering, considering deeply
- understanding1: getting it, empathy, agreement
- laughing1: amused, funny moment
- amazed1: impressed, wow
- uncertain1: unsure, hesitant
- sad1: disappointed, concerned
- oops1: made a mistake, embarrassed

Available head directions:
- up: having an idea, excited
- down: thinking, nodding in agreement
- left: considering alternatives, confused
- right: presenting another view, curious
- front: neutral, direct

For each turn, respond with a JSON array. Each entry:
{"turn": N, "emotion": "emotion_name", "head": "direction"}

Conversation:
"""


async def _assign_emotions(config: Config, turns: list[dict]) -> list[dict]:
    """Use a text model to assign emotions to each turn."""
    client = AsyncOpenAI(api_key=config.openai_api_key)

    # Build the conversation text
    conv_text = ""
    for t in turns:
        role = "Hello (student)" if t["role"] == "student" else "Mellow (professor)"
        conv_text += f"Turn {t['turn']} [{role}]: {t['transcript']}\n\n"

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": EMOTION_ASSIGNMENT_PROMPT},
                {"role": "user", "content": conv_text},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw_content = response.choices[0].message.content
        logger.info("Emotion API raw response: %s", raw_content[:300])
        result = json.loads(raw_content)
        # The model returns different keys — try all common ones
        if isinstance(result, list):
            actions = result
        else:
            for key in ("turns", "actions", "conversation", "data"):
                if key in result and isinstance(result[key], list):
                    actions = result[key]
                    break
            else:
                # Last resort: grab the first list value in the dict
                actions = []
                for v in result.values():
                    if isinstance(v, list):
                        actions = v
                        break
        logger.info("Parsed %d actions from response.", len(actions))

        # Merge actions into turns
        action_map = {a["turn"]: a for a in actions if isinstance(a, dict) and "turn" in a}
        for t in turns:
            a = action_map.get(t["turn"], {})
            t["tool_calls"] = []
            if "emotion" in a:
                t["tool_calls"].append({
                    "name": "play_emotion",
                    "arguments": {"emotion": a["emotion"]},
                    "at_secs": 0.0,
                })
            if "head" in a:
                t["tool_calls"].append({
                    "name": "move_head",
                    "arguments": {"direction": a["head"]},
                    "at_secs": 0.0,
                })

        total_actions = sum(len(t["tool_calls"]) for t in turns)
        logger.info("Assigned %d actions across %d turns.", total_actions, len(turns))

    except Exception as e:
        logger.error("Emotion assignment failed: %s. Continuing without actions.", e)
        for t in turns:
            t.setdefault("tool_calls", [])

    return turns


# ── Main entry point ───────────────────────────────────────────────────────

async def run_generate(config: Config, num_exchanges: int = 5) -> None:
    """Generate a conversation in two steps: audio first, then emotions."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    total_turns = num_exchanges * 2

    logger.info("Generating %d exchanges (%d total speaking turns)...", num_exchanges, total_turns)

    # Step A: Generate audio (reliable, no tool calls)
    print("\n  Step 1/2: Generating audio...")
    turns = await _generate_audio(config, total_turns)

    if not turns:
        print("  Generation failed — no turns produced.")
        return

    # Step B: Assign emotions
    print("  Step 2/2: Assigning emotions and actions...")
    turns = await _assign_emotions(config, turns)

    # Save
    meta = {
        "model": config.openai_model,
        "student_voice": config.robot_a.voice,
        "professor_voice": config.robot_b.voice,
        "num_turns": len(turns),
        "turns": turns,
    }
    meta_path = OUTPUT_DIR / "conversation.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    total_actions = sum(len(t.get("tool_calls", [])) for t in turns)
    logger.info("Complete! %d turns, %d actions saved to %s/", len(turns), total_actions, OUTPUT_DIR)
    print(f"\nConversation saved to {OUTPUT_DIR}/")
    print("Preview:")
    for t in turns:
        role = t["role"].upper()
        text = t["transcript"][:65]
        actions = t.get("tool_calls", [])
        action_names = ", ".join(tc["name"].split("_")[-1] + ":" + str(list(tc["arguments"].values())[0]) for tc in actions)
        print(f"  Turn {t['turn']:2d} [{role:9s}]: {text}...  [{action_names}]")


def _save_turn(turns: list, turn_num: int, role: str, transcript: str, audio: bytes) -> None:
    filename = f"turn_{turn_num:02d}_{role}.wav"
    filepath = OUTPUT_DIR / filename
    _write_wav(filepath, audio)
    duration = len(audio) / 2 / OPENAI_SAMPLE_RATE
    turns.append({
        "turn": turn_num,
        "role": role,
        "transcript": transcript,
        "audio_file": filename,
        "duration_secs": round(duration, 1),
    })
    logger.info("[Turn %d] %s (%.1fs): %s", turn_num, role, duration, transcript[:80])


async def _configure(conn: Any, instructions: str, voice: str) -> None:
    """Configure session WITHOUT tools — pure audio generation."""
    await conn.session.update(
        session={
            "type": "realtime",
            "instructions": instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": OPENAI_SAMPLE_RATE},
                    "turn_detection": None,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": OPENAI_SAMPLE_RATE},
                    "voice": voice,
                },
            },
        },
    )


async def _inject(conn: Any, text: str) -> None:
    await conn.conversation.item.create(
        item={
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    )
    await conn.response.create()
