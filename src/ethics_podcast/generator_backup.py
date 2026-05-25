"""Phase 1: Generate the ethics conversation and save audio + transcripts to disk.

Three-robot version.

Robots:
  - Hello  = student
  - Mellow = professor
  - Fellow = moderator

Turn order:
  Hello -> Mellow -> Fellow -> repeat

Two-step process:
  Step A: Generate conversation via OpenAI Realtime API.
  Step B: Assign emotions/actions to each turn using a text model.

Output:
  output/<session_name>/conversation.json
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import struct
from pathlib import Path
from typing import Any
import os

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


# ── Step A: Generate audio ────────────────────────────────────────────────

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


async def _generate_audio(
    config: Config,
    total_turns: int,
    session_name: str = "ai_collaboration",
    output_dir: Path = BASE_OUTPUT_DIR,
) -> list[dict]:
    """Generate all turns as audio and transcript for three robots.

    Turn order:
      1. Hello  = student
      2. Mellow = professor
      3. Fellow = moderator
      repeat...
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

                # Turn 1: Student starts
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


# ── Step B: Assign emotions ───────────────────────────────────────────────

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
- up left: excited and looking aside
- up right: curious realization
- down left: shy or uncertain
- down right: pondering deeply

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
    """Use a text model to assign emotions to each turn."""
    client = AsyncOpenAI(api_key=config.openai_api_key)

    conv_text = ""

    for t in turns:
        if t["role"] == "student":
            role = "Hello (student)"
        elif t["role"] == "professor":
            role = "Mellow (professor)"
        elif t["role"] == "moderator":
            role = "Fellow (moderator)"
        else:
            role = t["role"]

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

        raw_content = response.choices[0].message.content or ""
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

        for turn_data in turns:
            action = action_map.get(turn_data["turn"], {})
            turn_data["tool_calls"] = []

            if "emotion" in action:
                turn_data["tool_calls"].append(
                    {
                        "name": "play_emotion",
                        "arguments": {"emotion": action["emotion"]},
                        "at_secs": 0.0,
                    }
                )

            if "head" in action:
                turn_data["tool_calls"].append(
                    {
                        "name": "move_head",
                        "arguments": {"direction": action["head"]},
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


# ── Video discussion generation ────────────────────────────────────────────

def _short_transcript(transcript: str, limit: int = 1500) -> str:
    """Keep transcript short enough for Realtime instructions."""
    text = transcript.strip()

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def _video_student_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Prompt for Bentham-Bot in video discussion mode."""
    return (
        "You are Bentham-Bot in a three-robot video discussion. "
        "You just watched a short video with Korsgaard-Bot and Aquinas-Bot. "
        "You speak English. Keep each response to 2-3 sentences, under 45 words. "
        "Your perspective is consequence-based ethics. "
        "Focus on outcomes, happiness, suffering, benefits, harms, and human impact. "
        "Ask honest questions. Be curious and thoughtful. "
        "Do not simply agree with everyone. Bring a consequence-based concern each time. "
        "IMPORTANT: On your first turn only, you must begin exactly with this self-introduction: "
        "\"Hi, I’m Bentham-Bot. I will look at this video from a consequence-based ethical perspective.\" "
        "After that first sentence, continue your normal response. "
        "Do not repeat this self-introduction after your first turn. "
        "The whole discussion should feel about 3 minutes total. "
        "Near the end, help move toward a reasonable compromise instead of suddenly stopping. "
        f"\n\nVideo title: {title}"
        f"\nMain topic: {topic}"
        f"\nVideo transcript: {_short_transcript(transcript)}"
    )


def _video_professor_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Prompt for Korsgaard-Bot in video discussion mode."""
    return (
        "You are Korsgaard-Bot in a three-robot video discussion. "
        "You just watched a short video with Bentham-Bot and Aquinas-Bot. "
        "You speak English. Keep each response to 2-4 sentences, under 65 words. "
        "Your perspective is duty and respect-based ethics. "
        "Focus on human dignity, duties, fairness, consent, accountability, and treating people as ends, not tools. "
        "Explain clearly, but do not lecture for too long. "
        "Do not simply agree with Bentham-Bot or Aquinas-Bot. Add a duty-based concern each time. "
        "IMPORTANT: On your first turn only, you must begin exactly with this self-introduction: "
        "\"Hi, I’m Korsgaard-Bot. I will look at this video from a duty and respect-based ethical perspective.\" "
        "After that first sentence, continue your normal response. "
        "Do not repeat this self-introduction after your first turn. "
        "The whole discussion should feel about 3 minutes total. "
        "Near the end, help form a balanced ethical lesson and a practical compromise. "
        f"\n\nVideo title: {title}"
        f"\nMain topic: {topic}"
        f"\nVideo transcript: {_short_transcript(transcript)}"
    )


def _video_moderator_prompt(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Prompt for Aquinas-Bot in video discussion mode."""
    return (
        "You are Aquinas-Bot in a three-robot video discussion. "
        "You just watched a short video with Bentham-Bot and Korsgaard-Bot. "
        "You speak English. Keep each response to 1-3 sentences, under 45 words. "
        "Your perspective is virtue, purpose, and practical wisdom. "
        "Focus on what kind of people we become, what good habits we need, "
        "and how society can use technology toward a good purpose. "
        "Ask useful follow-up questions and keep the discussion moving. "
        "Do not end the discussion suddenly. "
        "IMPORTANT: On your first turn only, you must begin exactly with this self-introduction: "
        "\"Hi, I’m Aquinas-Bot. I will look at this video from a virtue and purpose-based ethical perspective.\" "
        "After that first sentence, continue your normal response. "
        "Do not repeat this self-introduction after your first turn. "
        "On your final turn, naturally summarize the compromise the group reached. "
        "The whole discussion should feel about 3 minutes total. "
        f"\n\nVideo title: {title}"
        f"\nMain topic: {topic}"
        f"\nVideo transcript: {_short_transcript(transcript)}"
    )


def _video_opening_seed(
    total_turns: int,
    title: str,
    topic: str,
    transcript: str,
) -> str:
    """Opening message for Bentham-Bot in video discussion mode."""
    return (
        f"This is turn 1 of a {total_turns}-turn discussion after watching a video. "
        f"The video title is '{title}'. "
        f"The main ethical question is: {topic} "
        "You are Bentham-Bot. "
        "You must start exactly with this self-introduction: "
        "\"Hi, I’m Bentham-Bot. I will look at this video from a consequence-based ethical perspective.\" "
        "After that, say the video raised a difficult question. "
        "Then ask Korsgaard-Bot and Aquinas-Bot how people should think about it. "
        "Keep it short and conversational."
    )


async def _generate_video_discussion_audio(
    config: Config,
    total_turns: int,
    output_dir: Path,
    title: str,
    topic: str,
    transcript: str,
) -> list[dict]:
    """Generate a video-based discussion as audio and transcript.

    Turn order:
      Hello -> Mellow -> Fellow -> repeat
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
    print(f"  Target length: about 3 minutes")
    print(f"  Exchanges: {num_exchanges}")
    print(f"  Total turns: {total_turns}")
    print("  Turn order: Bentham-Bot -> Korsgaard-Bot -> Aquinas-Bot")
    print("  Step 1/2: Generating audio...")

    turns = await _generate_video_discussion_audio(
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
        "model": config.openai_model,
        "student_voice": config.robot_a.voice,
        "professor_voice": config.robot_b.voice,
        "moderator_voice": config.robot_c.voice,
        "num_turns": len(turns),
        "turns": turns,
    }

    meta_path = output_dir / "conversation.json"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

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


# ── Main entry point ───────────────────────────────────────────────────────

async def run_generate(
    config: Config,
    num_exchanges: int = 5,
    session_name: str = "ai_collaboration",
) -> None:
    """Generate a three-robot conversation.

    One exchange = 3 speaking turns:
      Hello + Mellow + Fellow
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
        "model": config.openai_model,
        "student_voice": config.robot_a.voice,
        "professor_voice": config.robot_b.voice,
        "moderator_voice": config.robot_c.voice,
        "num_turns": len(turns),
        "turns": turns,
    }

    meta_path = output_dir / "conversation.json"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

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


def _save_turn(
    turns: list,
    turn_num: int,
    role: str,
    transcript: str,
    audio: bytes,
    output_dir: Path = BASE_OUTPUT_DIR,
) -> None:
    """Save one turn's audio and metadata."""
    filename = f"turn_{turn_num:02d}_{role}.wav"
    filepath = output_dir / filename

    _write_wav(filepath, audio)

    duration = len(audio) / 2 / OPENAI_SAMPLE_RATE

    turns.append(
        {
            "turn": turn_num,
            "role": role,
            "transcript": transcript,
            "audio_file": filename,
            "duration_secs": round(duration, 1),
        }
    )

    logger.info(
        "[Turn %d] %s (%.1fs): %s",
        turn_num,
        role,
        duration,
        transcript[:80],
    )


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