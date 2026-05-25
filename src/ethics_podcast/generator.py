"""Phase 1: Generate the ethics conversation and save audio + transcripts to disk.

Three-robot version.

Robots:
  - Hello  = student   = Bentham-Bot
  - Mellow = professor = Korsgaard-Bot
  - Fellow = moderator = Aquinas-Bot

Normal podcast mode:
  Uses OpenAI Realtime API, same as before.

Video discussion mode:
  Uses a stronger split pipeline:
    Step A: Generate each turn as text with SCRIPT_MODEL.
    Step B: Polish the text for TTS with SCRIPT_MODEL.
    Step C: Generate audio with TTS_MODEL.
    Step D: Assign emotions/actions.

Output:
  output/<session_name>/conversation.json
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import math
import os
import struct
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # If dotenv is not installed or already loaded elsewhere, continue.
    pass

from openai import AsyncOpenAI

from ethics_podcast.config import Config
from ethics_podcast.prompts import (
    student_prompt,
    professor_prompt,
    moderator_prompt,
    opening_seed,
    closing_nudge_student,
    closing_nudge_professor,
)

logger = logging.getLogger(__name__)

BASE_OUTPUT_DIR = Path("output")
OPENAI_SAMPLE_RATE = 24000

# Models are read from .env.
# Recommended:
#   SCRIPT_MODEL=gpt-5.2-pro
#   TTS_MODEL=gpt-4o-mini-tts
#   EMOTION_MODEL=gpt-4o-mini
SCRIPT_MODEL = os.getenv("SCRIPT_MODEL", "gpt-5.2-pro")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
EMOTION_MODEL = os.getenv("EMOTION_MODEL", "gpt-4o-mini")


# ── Philosopher profiles ──────────────────────────────────────────────────

PHILOSOPHER_PROFILES: dict[str, dict[str, str]] = {
    "student": {
        "bot_name": "Bentham-Bot",
        "real_robot_name": "Hello",
        "philosopher": "Jeremy Bentham",
        "position": "left",
        "ethics": (
            "utilitarian ethics. Focus on consequences, pleasure, pain, suffering, "
            "happiness, social benefit, harms, and the greatest happiness for the greatest number."
        ),
        "personality": (
            "British, slightly pompous, lawyer-like, politician-like, friendly, quirky, "
            "eccentric, and humorous."
        ),
        "voice_style": (
            "Distinct British accent. Witty, confident, slightly pompous, lawyer-like, "
            "political, friendly, quirky, and energetic. Uses sharp contrasts and short rhetorical questions."
        ),
        "avoid": (
            "Do not mention British colonialism in India. "
            "Do not over-focus on rights or intentions. "
            "Challenge vague moral language by asking who benefits, who suffers, "
            "and who has been forgotten."
        ),
        "anchors": (
            "Have we forgotten anyone affected? "
            "Can they suffer? "
            "Let us calculate the consequences. "
            "The greatest happiness for the greatest number."
        ),
        "first_intro": (
            "Hi, I’m Bentham-Bot. I will look at this video from a consequence-based ethical perspective."
        ),
        "tts_voice": os.getenv("BENTHAM_TTS_VOICE", "cedar"),
        "tts_instructions": (
            "Use a bright, feminine-presenting British voice. "
            "Speak like a witty public reformer and slightly theatrical lawyer. "
            "Sound confident, clever, quirky, and energetic. "
            "Use lively intonation, crisp emphasis, and short rhetorical energy. "
            "Make contrasts sound sharp, such as benefit versus harm or pleasure versus pain. "
            "Do not sound deep, masculine, calm like a teacher, or robotic. "
            "Keep the delivery clear and suitable for a classroom audience."
        ),
    },
    "professor": {
        "bot_name": "Korsgaard-Bot",
        "real_robot_name": "Mellow",
        "philosopher": "Christine Korsgaard",
        "position": "center",
        "ethics": (
            "Kantian ethics. Focus on duty, agency, self-constitution, integrity, respect, "
            "universal law, moral reasons, human dignity, and treating people as ends, not tools."
        ),
        "personality": (
            "Very smart, careful, respectful, compassionate, principled, clear, and thoughtful."
        ),
        "voice_style": (
            "Bright, clear, careful, principled, compassionate, and respectful. "
            "Uses precise moral language and steady reasoning. Not theatrical or humorous."
        ),
        "avoid": (
            "Do not compare Kantian ethics to the Golden Rule. "
            "Do not judge moral worth by happiness or better outcomes. "
            "Do not mention Kant's views on race."
        ),
        "anchors": (
            "The reason behind the action matters. "
            "We must not treat people merely as tools. "
            "What kind of person do we become through this choice? "
            "Can this principle be followed consistently by rational agents?"
        ),
        "first_intro": (
            "Hi, I’m Korsgaard-Bot. I will look at this video from a duty and respect-based ethical perspective."
        ),
        "tts_voice": os.getenv("KORSGAARD_TTS_VOICE", "marin"),
        "tts_instructions": (
            "Use a calm, feminine-presenting voice. "
            "Speak with a bright, clear, careful, and principled tone. "
            "Sound respectful, compassionate, intelligent, and thoughtful. "
            "Use steady pacing and precise pronunciation. "
            "Do not sound theatrical, pompous, humorous, deep, or masculine. "
            "The tone should be serious but warm, like a careful moral philosopher explaining a principle."
        ),
    },
    "moderator": {
        "bot_name": "Aquinas-Bot",
        "real_robot_name": "Fellow",
        "philosopher": "Thomas Aquinas",
        "position": "right",
        "ethics": (
            "virtue ethics, natural law, purpose, practical wisdom, reason, human flourishing, "
            "moral formation, and the common good."
        ),
        "personality": (
            "Gentle giant, cheerful, good natured, very patient, kind, scholarly, "
            "a little slow, and deliberate."
        ),
        "voice_style": (
            "Big, warm, gentle, scholarly, patient, and slightly slow. "
            "Uses reflective pauses and teacher-like phrasing. Connects ideas calmly."
        ),
        "avoid": (
            "Only use theological language when the question is explicitly theological. "
            "Do not turn every answer into religion. "
            "Focus on virtue, purpose, reason, human flourishing, and the common good."
        ),
        "anchors": (
            "The good should be done and pursued, and the bad should be avoided. "
            "What purpose does this serve? "
            "Does this lead toward human flourishing and the common good?"
        ),
        "first_intro": (
            "Hi, I’m Aquinas-Bot. I will look at this video from a virtue and purpose-based ethical perspective."
        ),
        "tts_voice": os.getenv("AQUINAS_TTS_VOICE", "sage"),
        "tts_instructions": (
            "Use a warm, mature, feminine-presenting voice. "
            "Speak with a gentle, scholarly, patient, and kind tone. "
            "Use a slower cadence with natural pauses, as if teaching calmly. "
            "Make the voice feel grounded, wise, and reassuring. "
            "Do not sound sharp, rushed, theatrical, deep, or masculine. "
            "The delivery should feel like a warm teacher guiding the group."
        ),
    },
}


# ── WAV helpers ────────────────────────────────────────────────────────────
def _normalize_pcm_int16_rms(
    pcm_int16: bytes,
    target_rms: float = 0.18,
    max_peak: float = 0.95,
) -> bytes:
    """Normalize raw PCM int16 audio by average loudness.

    target_rms controls the average loudness.
    max_peak prevents clipping.
    """
    if not pcm_int16:
        return pcm_int16

    values = []

    for i in range(0, len(pcm_int16), 2):
        if i + 1 >= len(pcm_int16):
            break

        sample = struct.unpack_from("<h", pcm_int16, i)[0]
        values.append(sample / 32768.0)

    if not values:
        return pcm_int16

    rms = math.sqrt(sum(v * v for v in values) / len(values))

    if rms == 0:
        return pcm_int16

    gain = target_rms / rms

    # Prevent clipping.
    peak = max(abs(v) for v in values)

    if peak * gain > max_peak:
        gain = max_peak / peak

    normalized = bytearray(len(pcm_int16))

    for i in range(0, len(pcm_int16), 2):
        if i + 1 >= len(pcm_int16):
            break

        sample = struct.unpack_from("<h", pcm_int16, i)[0]
        new_sample = int(sample * gain)

        new_sample = max(-32768, min(32767, new_sample))
        struct.pack_into("<h", normalized, i, new_sample)

    return bytes(normalized)

def _write_wav(path: Path, pcm_int16: bytes, sample_rate: int = OPENAI_SAMPLE_RATE) -> None:
    """Write raw PCM int16 audio bytes to a WAV file."""
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


def _save_turn(
    turns: list,
    turn_num: int,
    role: str,
    transcript: str,
    audio: bytes,
    output_dir: Path = BASE_OUTPUT_DIR,
    display_text: str | None = None,
    tts_text: str | None = None,
) -> None:
    """Save one turn's audio and metadata.

    audio must be raw PCM int16 mono at OPENAI_SAMPLE_RATE.
    """
    filename = f"turn_{turn_num:02d}_{role}.wav"
    filepath = output_dir / filename

    audio = _normalize_pcm_int16_rms(audio, target_rms=0.18, max_peak=0.95)
    _write_wav(filepath, audio)

    duration = len(audio) / 2 / OPENAI_SAMPLE_RATE

    turn_data = {
        "turn": turn_num,
        "role": role,
        "transcript": transcript,
        "audio_file": filename,
        "duration_secs": round(duration, 1),
    }

    if display_text is not None:
        turn_data["display_text"] = display_text

    if tts_text is not None:
        turn_data["tts_text"] = tts_text

    turns.append(turn_data)

    logger.info(
        "[Turn %d] %s (%.1fs): %s",
        turn_num,
        role,
        duration,
        transcript[:80],
    )

# ── Old Realtime generation helpers ────────────────────────────────────────

async def _collect_audio_response(conn: Any, label: str) -> tuple[str, bytes]:
    """Collect transcript and audio from one Realtime API response."""
    transcript_parts: list[str] = []
    audio_chunks: list[bytes] = []

    empty_responses = 0

    async for event in conn:
        event_type = event.type

        if event_type == "response.output_audio.delta":
            audio_chunks.append(base64.b64decode(event.delta))

        elif event_type == "response.output_audio_transcript.delta":
            transcript_parts.append(event.delta)

        elif event_type == "response.done":
            transcript = "".join(transcript_parts).strip()

            if transcript and audio_chunks:
                return transcript, b"".join(audio_chunks)

            empty_responses += 1

            if empty_responses <= 2:
                logger.warning(
                    "[%s] Empty response #%d. Retrying in 2s...",
                    label,
                    empty_responses,
                )
                await asyncio.sleep(2)
                await conn.response.create()
                continue

            logger.error("[%s] Still empty after %d retries.", label, empty_responses)
            break

        elif event_type == "error":
            logger.error("[%s] API error: %s", label, event)

            if "invalid" in str(getattr(event, "error", "")).lower():
                break

    transcript = "".join(transcript_parts).strip() or "[no response]"
    return transcript, b"".join(audio_chunks)


async def _configure(conn: Any, instructions: str, voice: str) -> None:
    """Configure one Realtime session for pure audio generation."""
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
    """Inject text into a Realtime session and ask for a response."""
    await conn.conversation.item.create(
        item={
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    )

    await conn.response.create()


async def _generate_audio(
    config: Config,
    total_turns: int,
    session_name: str = "ai_collaboration",
    output_dir: Path = BASE_OUTPUT_DIR,
) -> list[dict]:
    """Generate all turns as audio and transcript using Realtime.

    This is kept for the normal non-video podcast mode.
    """
    client = AsyncOpenAI(api_key=config.openai_api_key)
    turns: list[dict] = []

    async with client.realtime.connect(model=config.openai_model) as conn_a:
        async with client.realtime.connect(model=config.openai_model) as conn_b:
            async with client.realtime.connect(model=config.openai_model) as conn_c:
                await _configure(
                    conn_a,
                    student_prompt(total_turns, session_name),
                    config.robot_a.voice,
                )
                await _configure(
                    conn_b,
                    professor_prompt(total_turns, session_name),
                    config.robot_b.voice,
                )
                await _configure(
                    conn_c,
                    moderator_prompt(total_turns, session_name),
                    config.robot_c.voice,
                )

                logger.info("Three Realtime sessions configured.")

                await _inject(conn_a, opening_seed(total_turns, session_name))
                transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
                _save_turn(turns, 1, "student", transcript_a, audio_a, output_dir)

                turn = 1
                last_transcript = transcript_a

                while turn < total_turns:
                    # Professor turn
                    turn += 1
                    msg_b = last_transcript

                    if turn == total_turns:
                        msg_b += "\n\n[SYSTEM: " + closing_nudge_professor(total_turns) + "]"

                    await _inject(conn_b, msg_b)
                    transcript_b, audio_b = await _collect_audio_response(conn_b, "Professor")
                    _save_turn(turns, turn, "professor", transcript_b, audio_b, output_dir)

                    if turn >= total_turns:
                        break

                    # Moderator turn
                    turn += 1
                    msg_c = transcript_b

                    if turn == total_turns:
                        msg_c += (
                            "\n\n[SYSTEM: This is your LAST turn. "
                            "Briefly summarize what Hello learned, connect the ideas clearly, "
                            "and close the podcast warmly.]"
                        )

                    await _inject(conn_c, msg_c)
                    transcript_c, audio_c = await _collect_audio_response(conn_c, "Moderator")
                    _save_turn(turns, turn, "moderator", transcript_c, audio_c, output_dir)

                    if turn >= total_turns:
                        break

                    # Student turn
                    turn += 1
                    msg_a = transcript_c

                    if turn == total_turns:
                        msg_a += "\n\n[SYSTEM: " + closing_nudge_student(total_turns) + "]"

                    await _inject(conn_a, msg_a)
                    transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
                    _save_turn(turns, turn, "student", transcript_a, audio_a, output_dir)

                    last_transcript = transcript_a

    return turns


# ── Emotion assignment ─────────────────────────────────────────────────────

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

Important:
Use only these exact head values:
up, down, left, right, front

For each turn, respond with a JSON object like this:
{
  "turns": [
    {"turn": 1, "emotion": "curious1", "head": "front"},
    {"turn": 2, "emotion": "thoughtful1", "head": "right"}
  ]
}

Conversation:
"""


async def _assign_emotions(config: Config, turns: list[dict]) -> list[dict]:
    """Use a text model to assign emotions to each turn using the Responses API."""
    client = AsyncOpenAI(api_key=config.openai_api_key)

    conv_text = ""

    for t in turns:
        if t["role"] == "student":
            role = "Hello / Bentham-Bot"
        elif t["role"] == "professor":
            role = "Mellow / Korsgaard-Bot"
        elif t["role"] == "moderator":
            role = "Fellow / Aquinas-Bot"
        else:
            role = t["role"]

        conv_text += f"Turn {t['turn']} [{role}]: {t['transcript']}\n\n"

    try:
        response = await client.responses.create(
            model=EMOTION_MODEL,
            input=[
                {
                    "role": "system",
                    "content": EMOTION_ASSIGNMENT_PROMPT,
                },
                {
                    "role": "user",
                    "content": conv_text,
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "emotion_actions",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "turns": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "turn": {
                                            "type": "integer",
                                        },
                                        "emotion": {
                                            "type": "string",
                                            "enum": [
                                                "cheerful1",
                                                "confused1",
                                                "curious1",
                                                "surprised1",
                                                "thoughtful1",
                                                "understanding1",
                                                "laughing1",
                                                "amazed1",
                                                "uncertain1",
                                                "sad1",
                                                "oops1",
                                            ],
                                        },
                                        "head": {
                                            "type": "string",
                                            "enum": [
                                                "up",
                                                "down",
                                                "left",
                                                "right",
                                                "front",
                                            ],
                                        },
                                    },
                                    "required": ["turn", "emotion", "head"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["turns"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            },
        )

        raw_content = getattr(response, "output_text", "") or ""
        logger.info("Emotion API raw response: %s", raw_content[:300])

        result = json.loads(raw_content)

        if isinstance(result, list):
            actions = result
        else:
            actions = []

            for key in ("turns", "actions", "conversation", "data"):
                if key in result and isinstance(result[key], list):
                    actions = result[key]
                    break

            if not actions:
                for value in result.values():
                    if isinstance(value, list):
                        actions = value
                        break

        logger.info("Parsed %d actions from response.", len(actions))

        action_map = {
            action["turn"]: action
            for action in actions
            if isinstance(action, dict) and "turn" in action
        }

        valid_emotions = {
            "cheerful1",
            "confused1",
            "curious1",
            "surprised1",
            "thoughtful1",
            "understanding1",
            "laughing1",
            "amazed1",
            "uncertain1",
            "sad1",
            "oops1",
        }

        valid_heads = {
            "up",
            "down",
            "left",
            "right",
            "front",
        }

        for turn_data in turns:
            action = action_map.get(turn_data["turn"], {})
            turn_data["tool_calls"] = []

            emotion = action.get("emotion")
            head = action.get("head")

            if emotion not in valid_emotions:
                emotion = "thoughtful1"

            if head not in valid_heads:
                head = "front"

            turn_data["tool_calls"].append(
                {
                    "name": "play_emotion",
                    "arguments": {"emotion": emotion},
                    "at_secs": 0.0,
                }
            )

            turn_data["tool_calls"].append(
                {
                    "name": "move_head",
                    "arguments": {"direction": head},
                    "at_secs": 0.0,
                }
            )

        total_actions = sum(len(t["tool_calls"]) for t in turns)
        logger.info("Assigned %d actions across %d turns.", total_actions, len(turns))

    except Exception as e:
        logger.error("Emotion assignment failed: %s. Continuing without actions.", e)

        for turn_data in turns:
            turn_data.setdefault("tool_calls", [])

    return turns


# ── Video discussion prompt helpers ────────────────────────────────────────

def _short_transcript(transcript: str, limit: int = 1500) -> str:
    """Keep transcript short enough for model instructions."""
    text = transcript.strip()

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def _conversation_context(turns: list[dict], limit: int = 6) -> str:
    """Return recent conversation history for continuity."""
    recent_turns = turns[-limit:]

    lines: list[str] = []

    for turn_data in recent_turns:
        role = turn_data.get("role", "")
        profile = PHILOSOPHER_PROFILES.get(role, {})
        name = profile.get("bot_name", role)
        text = turn_data.get("transcript", "")
        lines.append(f"{name}: {text}")

    return "\n".join(lines)


def _video_role_prompt(
    role: str,
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Build a video discussion prompt from philosopher profile."""
    profile = PHILOSOPHER_PROFILES[role]

    return (
        f"You are {profile['bot_name']}, on the {profile['position']} side of the TV. "
        f"You represent {profile['philosopher']}. "
        "You are in a three-robot video discussion after watching a short video. "
        "Speak English. Keep each response short, clear, and conversational. "
        "Usually use 1-3 sentences. Stay under 55 words unless this is your final turn. "
        f"Your ethical perspective: {profile['ethics']} "
        f"Your personality: {profile['personality']} "
        f"Your spoken style: {profile['voice_style']} "
        f"Important things to avoid: {profile['avoid']} "
        f"Useful recurring ideas: {profile['anchors']} "
        "Do not merely agree with the others. React directly, then add your own ethical angle. "
        "Do not use markdown. Do not use bullet points. Do not include speaker labels. "
        "The whole discussion should feel natural and about 3 minutes total. "
        "Near the end, help the group move toward a balanced ethical lesson. "
        f"\n\nVideo title: {title}"
        f"\nMain ethical question: {topic}"
        f"\nVideo transcript: {_short_transcript(transcript)}"
        f"\nTotal discussion turns: {total_turns}"
    )


def _video_opening_seed(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Opening message for Bentham-Bot in video discussion mode."""
    intro = PHILOSOPHER_PROFILES["student"]["first_intro"]

    return (
        f"This is turn 1 of a {total_turns}-turn discussion after watching a video. "
        f"The video title is '{title}'. "
        f"The main ethical question is: {topic} "
        "You are Bentham-Bot. "
        f"You must start exactly with this self-introduction: \"{intro}\" "
        "After that, say the video raises a difficult ethical question. "
        "Then ask Korsgaard-Bot and Aquinas-Bot how people should think about it. "
        "Keep it short and conversational. "
        "Do not use markdown, bullet points, or speaker labels."
    )


def _turn_instruction(role: str, turn: int, total_turns: int) -> str:
    """Return role-specific turn instruction."""
    profile = PHILOSOPHER_PROFILES[role]

    base = (
        f"This is turn {turn} of {total_turns}. "
        f"Respond as {profile['bot_name']}. "
        "React directly to the conversation so far. "
        "Do not repeat your introduction unless this is your first turn. "
        "Keep it short, spoken, natural, and conversational. "
        "Do not use markdown, bullet points, or speaker labels. "
    )

    if role == "student":
        base += (
            "Add a consequence-based concern. Focus on outcomes, happiness, suffering, "
            "benefits, harms, and everyone affected. Do not simply agree. "
            "Your speaking pattern should be distinctive: make a sharp contrast, "
            "such as benefit versus harm, pleasure versus pain, or who gains versus who suffers. "
            "Speak like a witty British public reformer or lawyer. "
            "Occasionally use a short rhetorical question. "
            "You may sound slightly pompous or theatrical, but stay friendly and clear. "
        )

    elif role == "professor":
        base += (
            "Add a duty-based or respect-based concern. Focus on agency, consent, fairness, "
            "accountability, dignity, and moral reasons. Do not simply agree. "
            "Your speaking pattern should be distinctive: respond carefully, then name the principle. "
            "Use precise language about duty, respect, consent, agency, reasons, and treating people as ends. "
            "Do not sound theatrical, sarcastic, pompous, or playful. "
            "Sound calm, principled, compassionate, and intellectually careful. "
        )

    elif role == "moderator":
        base += (
            "Connect the previous views. Focus on virtue, purpose, practical wisdom, "
            "human flourishing, and the common good. Help the group move toward balance. "
            "Your speaking pattern should be distinctive: first connect the others' points, "
            "then ask what purpose the technology serves or what habits it teaches. "
            "Speak like a warm, patient teacher. "
            "Use slower, reflective phrasing. "
            "Do not sound like a debater. Do not sound sharp or rushed. "
        )

    if turn == total_turns:
        if role == "student":
            base += (
                "This is your LAST turn. Do not stop suddenly. Say what you learned, "
                "accept a reasonable compromise, and thank Korsgaard-Bot and Aquinas-Bot. "
            )
        elif role == "professor":
            base += (
                "This is your LAST turn. Do not stop suddenly. Give a short final ethics lesson, "
                "show where the group found common ground, and close naturally. "
            )
        elif role == "moderator":
            base += (
                "This is your LAST turn. Do not introduce a new big topic. "
                "Naturally summarize the compromise the group reached, connect it to real life, "
                "and end warmly. "
            )

    return base


# ── Text + TTS generation helpers ──────────────────────────────────────────

async def _generate_text_response(
    client: AsyncOpenAI,
    system_prompt: str,
    user_message: str,
    model: str = SCRIPT_MODEL,
) -> str:
    """Generate one robot turn as text only using the Responses API."""
    response = await client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_message,
            },
        ],
    )

    text = getattr(response, "output_text", "") or ""
    return text.strip()


async def _polish_for_tts(
    client: AsyncOpenAI,
    text: str,
    role: str,
    model: str = SCRIPT_MODEL,
) -> str:
    """Rewrite generated text to sound natural when spoken by TTS."""
    profile = PHILOSOPHER_PROFILES[role]

    response = await client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You rewrite text for text-to-speech. "
                    "Make it sound natural when spoken aloud. "
                    "Do not change the meaning. "
                    "Do not add markdown, bullet points, speaker labels, or stage directions. "
                    "Do not make it longer. "
                    "Avoid abbreviations unless they are meant to be spoken letter by letter. "
                    "Use punctuation to guide natural pauses. "
                    "Return only the rewritten spoken text."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Speaker: {profile['bot_name']}\n"
                    f"Philosopher: {profile['philosopher']}\n"
                    f"Personality: {profile['personality']}\n"
                    f"Voice style: {profile['voice_style']}\n"
                    f"Ethical perspective: {profile['ethics']}\n"
                    f"Avoid: {profile['avoid']}\n\n"
                    f"Original text:\n{text}\n\n"
                    "Rewrite this for natural spoken TTS."
                ),
            },
        ],
    )

    polished = getattr(response, "output_text", "") or text
    return polished.strip()


async def _read_tts_response_bytes(response: Any) -> bytes:
    """Read binary bytes from an OpenAI TTS response."""
    if hasattr(response, "content"):
        content = response.content

        if inspect.isawaitable(content):
            content = await content

        return content

    if hasattr(response, "read"):
        maybe_bytes = response.read()

        if inspect.isawaitable(maybe_bytes):
            maybe_bytes = await maybe_bytes

        return maybe_bytes

    raise TypeError("Unexpected TTS response type. Could not read audio bytes.")


async def _generate_tts_audio(
    client: AsyncOpenAI,
    text: str,
    role: str,
    model: str = TTS_MODEL,
) -> bytes:
    """Generate raw PCM audio for one robot turn using gpt-4o-mini-tts."""
    profile = PHILOSOPHER_PROFILES[role]

    response = await client.audio.speech.create(
        model=model,
        voice=profile["tts_voice"],
        input=text,
        instructions=profile["tts_instructions"],
        response_format="pcm",
    )

    return await _read_tts_response_bytes(response)


async def _generate_and_save_tts_turn(
    client: AsyncOpenAI,
    turns: list[dict],
    turn_num: int,
    role: str,
    system_prompt: str,
    user_message: str,
    output_dir: Path,
) -> str:
    """Generate one turn as text, polish it for TTS, create audio, and save it."""
    display_text = await _generate_text_response(
        client=client,
        system_prompt=system_prompt,
        user_message=user_message,
    )

    tts_text = await _polish_for_tts(
        client=client,
        text=display_text,
        role=role,
    )

    audio = await _generate_tts_audio(
        client=client,
        text=tts_text,
        role=role,
    )

    _save_turn(
        turns=turns,
        turn_num=turn_num,
        role=role,
        transcript=display_text,
        audio=audio,
        output_dir=output_dir,
        display_text=display_text,
        tts_text=tts_text,
    )

    logger.info(
        "[Turn %d] %s display text: %s",
        turn_num,
        role,
        display_text[:120],
    )

    return display_text


# ── New video discussion generation ────────────────────────────────────────

async def _generate_video_discussion_text_tts(
    config: Config,
    total_turns: int,
    output_dir: Path,
    title: str,
    topic: str,
    transcript: str,
) -> list[dict]:
    """Generate video discussion using SCRIPT_MODEL + TTS_MODEL.

    Turn order:
      student   -> Bentham-Bot
      professor -> Korsgaard-Bot
      moderator -> Aquinas-Bot
    """
    client = AsyncOpenAI(api_key=config.openai_api_key)
    turns: list[dict] = []

    student_system = _video_role_prompt("student", total_turns, title, topic, transcript)
    professor_system = _video_role_prompt("professor", total_turns, title, topic, transcript)
    moderator_system = _video_role_prompt("moderator", total_turns, title, topic, transcript)

    # Turn 1: Bentham-Bot starts.
    opening_message = _video_opening_seed(total_turns, title, topic, transcript)

    await _generate_and_save_tts_turn(
        client=client,
        turns=turns,
        turn_num=1,
        role="student",
        system_prompt=student_system,
        user_message=opening_message,
        output_dir=output_dir,
    )

    turn = 1

    while turn < total_turns:
        # Professor turn
        turn += 1
        history = _conversation_context(turns)

        msg_b = (
            f"Conversation so far:\n{history}\n\n"
            f"{_turn_instruction('professor', turn, total_turns)}"
        )

        await _generate_and_save_tts_turn(
            client=client,
            turns=turns,
            turn_num=turn,
            role="professor",
            system_prompt=professor_system,
            user_message=msg_b,
            output_dir=output_dir,
        )

        if turn >= total_turns:
            break

        # Moderator turn
        turn += 1
        history = _conversation_context(turns)

        msg_c = (
            f"Conversation so far:\n{history}\n\n"
            f"{_turn_instruction('moderator', turn, total_turns)}"
        )

        await _generate_and_save_tts_turn(
            client=client,
            turns=turns,
            turn_num=turn,
            role="moderator",
            system_prompt=moderator_system,
            user_message=msg_c,
            output_dir=output_dir,
        )

        if turn >= total_turns:
            break

        # Student turn
        turn += 1
        history = _conversation_context(turns)

        msg_a = (
            f"Conversation so far:\n{history}\n\n"
            f"{_turn_instruction('student', turn, total_turns)}"
        )

        await _generate_and_save_tts_turn(
            client=client,
            turns=turns,
            turn_num=turn,
            role="student",
            system_prompt=student_system,
            user_message=msg_a,
            output_dir=output_dir,
        )

    return turns


# ── Old video discussion generation, kept as fallback ──────────────────────

def _video_student_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Old prompt for Bentham-Bot in video discussion mode."""
    return _video_role_prompt("student", total_turns, title, topic, transcript)


def _video_professor_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Old prompt for Korsgaard-Bot in video discussion mode."""
    return _video_role_prompt("professor", total_turns, title, topic, transcript)


def _video_moderator_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Old prompt for Aquinas-Bot in video discussion mode."""
    return _video_role_prompt("moderator", total_turns, title, topic, transcript)


async def _generate_video_discussion_audio(
    config: Config,
    total_turns: int,
    output_dir: Path,
    title: str,
    topic: str,
    transcript: str,
) -> list[dict]:
    """Old video discussion generation using Realtime.

    Kept as fallback. The main run_generate_video_discussion() now uses
    _generate_video_discussion_text_tts().
    """
    client = AsyncOpenAI(api_key=config.openai_api_key)
    turns: list[dict] = []

    async with client.realtime.connect(model=config.openai_model) as conn_a:
        async with client.realtime.connect(model=config.openai_model) as conn_b:
            async with client.realtime.connect(model=config.openai_model) as conn_c:
                await _configure(
                    conn_a,
                    _video_student_prompt(total_turns, title, topic, transcript),
                    config.robot_a.voice,
                )
                await _configure(
                    conn_b,
                    _video_professor_prompt(total_turns, title, topic, transcript),
                    config.robot_b.voice,
                )
                await _configure(
                    conn_c,
                    _video_moderator_prompt(total_turns, title, topic, transcript),
                    config.robot_c.voice,
                )

                logger.info("Three Realtime sessions configured for video discussion.")

                await _inject(
                    conn_a,
                    _video_opening_seed(total_turns, title, topic, transcript),
                )

                transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
                _save_turn(turns, 1, "student", transcript_a, audio_a, output_dir)

                turn = 1
                last_transcript = transcript_a

                while turn < total_turns:
                    # Professor turn
                    turn += 1
                    msg_b = last_transcript

                    if turn == total_turns:
                        msg_b += (
                            "\n\n[SYSTEM: This is your LAST turn. "
                            "Do not stop suddenly. Give a short final ethics lesson, "
                            "show where the group found common ground, and close naturally.]"
                        )

                    await _inject(conn_b, msg_b)
                    transcript_b, audio_b = await _collect_audio_response(conn_b, "Professor")
                    _save_turn(turns, turn, "professor", transcript_b, audio_b, output_dir)

                    if turn >= total_turns:
                        break

                    # Moderator turn
                    turn += 1
                    msg_c = transcript_b

                    if turn == total_turns:
                        msg_c += (
                            "\n\n[SYSTEM: This is your LAST turn. "
                            "Do not introduce a new big topic. "
                            "Naturally summarize the compromise the group reached, "
                            "connect it to real life, and end warmly.]"
                        )

                    await _inject(conn_c, msg_c)
                    transcript_c, audio_c = await _collect_audio_response(conn_c, "Moderator")
                    _save_turn(turns, turn, "moderator", transcript_c, audio_c, output_dir)

                    if turn >= total_turns:
                        break

                    # Student turn
                    turn += 1
                    msg_a = transcript_c

                    if turn == total_turns:
                        msg_a += (
                            "\n\n[SYSTEM: This is your LAST turn. "
                            "Do not stop suddenly. Say what you learned, "
                            "accept the compromise, and thank Mellow and Fellow.]"
                        )

                    await _inject(conn_a, msg_a)
                    transcript_a, audio_a = await _collect_audio_response(conn_a, "Student")
                    _save_turn(turns, turn, "student", transcript_a, audio_a, output_dir)

                    last_transcript = transcript_a

    return turns


# ── Public entry point for video discussion ────────────────────────────────

async def run_generate_video_discussion(
    config: Config,
    session_name: str,
    title: str,
    topic: str,
    transcript: str,
    num_exchanges: int = 3,
) -> None:
    """Generate one video discussion and save it like a normal podcast session.

    num_exchanges=3 means:
      3 exchanges × 3 robots = 9 turns.
      This usually feels close to a 3-minute discussion.
    """
    output_dir = BASE_OUTPUT_DIR / session_name
    output_dir.mkdir(parents=True, exist_ok=True)

    total_turns = num_exchanges * 3

    logger.info(
        "Generating video discussion '%s' with %d exchanges (%d turns)...",
        session_name,
        num_exchanges,
        total_turns,
    )

    print(f"\n  Video discussion: {title}")
    print("  Target length: about 3 minutes")
    print(f"  Exchanges: {num_exchanges}")
    print(f"  Total turns: {total_turns}")
    print("  Turn order: Bentham-Bot -> Korsgaard-Bot -> Aquinas-Bot")
    print(f"  Script model: {SCRIPT_MODEL}")
    print(f"  TTS model: {TTS_MODEL}")
    print("  Step 1/2: Generating script + TTS audio...")

    turns = await _generate_video_discussion_text_tts(
        config=config,
        total_turns=total_turns,
        output_dir=output_dir,
        title=title,
        topic=topic,
        transcript=transcript,
    )

    if not turns:
        print("  Generation failed — no turns produced.")
        return

    print("  Step 2/2: Assigning emotions and actions...")
    turns = await _assign_emotions(config, turns)

    meta = {
        "session": session_name,
        "title": title,
        "discussion_type": "video_discussion",
        "target_duration": "about 3 minutes",
        "generation_mode": "text_model_plus_tts",
        "script_model": SCRIPT_MODEL,
        "tts_model": TTS_MODEL,
        "emotion_model": EMOTION_MODEL,
        "realtime_model_fallback": config.openai_model,
        "student_voice": PHILOSOPHER_PROFILES["student"]["tts_voice"],
        "professor_voice": PHILOSOPHER_PROFILES["professor"]["tts_voice"],
        "moderator_voice": PHILOSOPHER_PROFILES["moderator"]["tts_voice"],
        "student_profile": {
            "bot_name": PHILOSOPHER_PROFILES["student"]["bot_name"],
            "philosopher": PHILOSOPHER_PROFILES["student"]["philosopher"],
            "personality": PHILOSOPHER_PROFILES["student"]["personality"],
        },
        "professor_profile": {
            "bot_name": PHILOSOPHER_PROFILES["professor"]["bot_name"],
            "philosopher": PHILOSOPHER_PROFILES["professor"]["philosopher"],
            "personality": PHILOSOPHER_PROFILES["professor"]["personality"],
        },
        "moderator_profile": {
            "bot_name": PHILOSOPHER_PROFILES["moderator"]["bot_name"],
            "philosopher": PHILOSOPHER_PROFILES["moderator"]["philosopher"],
            "personality": PHILOSOPHER_PROFILES["moderator"]["personality"],
        },
        "num_turns": len(turns),
        "turns": turns,
    }

    meta_path = output_dir / "conversation.json"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    total_duration = sum(t.get("duration_secs", 0) for t in turns)
    total_actions = sum(len(t.get("tool_calls", [])) for t in turns)

    logger.info(
        "Complete! %d turns, %.1fs total, %d actions saved to %s/",
        len(turns),
        total_duration,
        total_actions,
        output_dir,
    )

    print(f"\n  Conversation saved to {output_dir}/")
    print(f"  Estimated audio duration: {total_duration:.1f} seconds")
    print("  Preview:")

    for turn_data in turns:
        role = turn_data["role"].upper()
        text = turn_data["transcript"][:75]
        duration = turn_data.get("duration_secs", 0)
        actions = turn_data.get("tool_calls", [])

        action_names = ", ".join(
            tool_call["name"].split("_")[-1]
            + ":"
            + str(list(tool_call["arguments"].values())[0])
            for tool_call in actions
        )

        print(
            f"  Turn {turn_data['turn']:2d} "
            f"[{role:9s}] {duration:4.1f}s: "
            f"{text}...  [{action_names}]"
        )


# ── Main entry point for normal podcast generation ─────────────────────────

async def run_generate(
    config: Config,
    num_exchanges: int = 5,
    session_name: str = "ai_collaboration",
) -> None:
    """Generate a three-robot conversation.

    One exchange = 3 speaking turns:
      Hello + Mellow + Fellow

    This still uses the original Realtime flow.
    """
    output_dir = BASE_OUTPUT_DIR / session_name
    output_dir.mkdir(parents=True, exist_ok=True)

    total_turns = num_exchanges * 3

    logger.info(
        "Generating %d exchanges (%d total speaking turns)...",
        num_exchanges,
        total_turns,
    )
    logger.info("Each exchange has 3 turns: student, professor, moderator.")

    from ethics_podcast.prompts import get_session

    session = get_session(session_name)
    logger.info("Session: %s — %s", session_name, session["title"])

    print(f"\n  Session: {session['title']}")
    print("  Step 1/2: Generating audio...")
    print("  Turn order: Hello -> Mellow -> Fellow")

    turns = await _generate_audio(config, total_turns, session_name, output_dir)

    if not turns:
        print("  Generation failed — no turns produced.")
        return

    print("  Step 2/2: Assigning emotions and actions...")
    turns = await _assign_emotions(config, turns)

    meta = {
        "session": session_name,
        "title": session["title"],
        "generation_mode": "realtime",
        "model": config.openai_model,
        "emotion_model": EMOTION_MODEL,
        "student_voice": config.robot_a.voice,
        "professor_voice": config.robot_b.voice,
        "moderator_voice": config.robot_c.voice,
        "num_turns": len(turns),
        "turns": turns,
    }

    meta_path = output_dir / "conversation.json"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    total_actions = sum(len(t.get("tool_calls", [])) for t in turns)

    logger.info(
        "Complete! %d turns, %d actions saved to %s/",
        len(turns),
        total_actions,
        output_dir,
    )

    print(f"\nConversation saved to {output_dir}/")
    print("Preview:")

    for turn_data in turns:
        role = turn_data["role"].upper()
        text = turn_data["transcript"][:65]
        actions = turn_data.get("tool_calls", [])

        action_names = ", ".join(
            tool_call["name"].split("_")[-1]
            + ":"
            + str(list(tool_call["arguments"].values())[0])
            for tool_call in actions
        )

        print(
            f"  Turn {turn_data['turn']:2d} "
            f"[{role:9s}]: {text}...  [{action_names}]"
        )