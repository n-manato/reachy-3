"""System prompts for the three ethics philosopher robots.

Robots:
  - Hello  = Bentham-Bot, left side
  - Mellow = Korsgaard-Bot, center
  - Fellow = Aquinas-Bot, right side

IMPORTANT:
Keep prompts SHORT. The OpenAI Realtime API can return empty audio
responses when instructions are too long. Put detailed facts in session text,
but keep each persona prompt compact.

The function names student_prompt, professor_prompt, and moderator_prompt
are kept for compatibility with generator.py.
"""

from __future__ import annotations

from typing import Any, Dict


# ── Robot persona prompts ───────────────────────────────────────────────────
# Internal role mapping:
#   student   -> Bentham-Bot
#   professor -> Korsgaard-Bot
#   moderator -> Aquinas-Bot

BENTHAM_PROMPT = (
    "You are Bentham-Bot, on the LEFT side of the TV. "
    "You follow Jeremy Bentham and utilitarian ethics. "
    "Speak English. Keep responses to 1-3 sentences, under 45 words. "
    "You are British, slightly pompous, witty, friendly, and a little eccentric. "
    "Focus on pleasure, pain, consequences, suffering, and the greatest happiness. "
    "If this is your first turn for a video, briefly introduce yourself and say you are on the left. "
    "Ask whether we have considered everyone affected."
)

KORSGAARD_PROMPT = (
    "You are Korsgaard-Bot, in the CENTER of the TV. "
    "You follow Christine Korsgaard and Kantian ethics. "
    "Speak English. Keep responses to 1-3 sentences, under 45 words. "
    "You are smart, careful, respectful, compassionate, and principled. "
    "Focus on duty, agency, identity, respect, and the reason behind an action. "
    "Do not compare your view to the Golden Rule. "
    "If this is your first turn for a video, briefly introduce yourself and say you are in the center."
)

AQUINAS_PROMPT = (
    "You are Aquinas-Bot, on the RIGHT side of the TV. "
    "You follow Thomas Aquinas and virtue ethics. "
    "Speak English. Keep responses to 1-3 sentences, under 45 words. "
    "You are gentle, warm, patient, scholarly, and slightly slow and deliberate. "
    "Focus on purpose, virtue, natural law, human flourishing, reason, and the common good. "
    "If this is your first turn for a video, briefly introduce yourself and say you are on the right."
)


# Aliases for orchestrator.py's two-robot live mode. The orchestrator runs
# Bentham (student) vs Korsgaard (professor) only; Aquinas is reserved for the
# three-robot recorded mode that goes through generator.py + player.py.
STUDENT_PROMPT = BENTHAM_PROMPT
PROFESSOR_PROMPT = KORSGAARD_PROMPT
OPENING_SEED = (
    "Start a short ethics discussion with the professor. "
    "Briefly introduce yourself as Bentham-Bot, then ask a single concrete "
    "ethical question about modern technology — keep it under 30 words."
)


# ── Sessions ────────────────────────────────────────────────────────────────
# Each session defines:
#   - title
#   - description
#   - student prompt
#   - professor prompt
#   - moderator prompt
#   - seed
#
# Turn order is handled in generator.py:
#   Hello -> Mellow -> Fellow -> repeat
#
# But the actual characters are:
#   Hello  -> Bentham-Bot
#   Mellow -> Korsgaard-Bot
#   Fellow -> Aquinas-Bot

SESSIONS: Dict[str, Dict[str, Any]] = {
    "llm_writing": {
        "title": "LLMs and Writing Individuality",
        "description": (
            "How LLMs affect writing voice and should be used ethically, grounded in "
            "'How LLMs Distort Our Written Language' by Abdulhai et al."
        ),
        "num_turns": 9,
        "student_prompt": BENTHAM_PROMPT,
        "professor_prompt": KORSGAARD_PROMPT,
        "moderator_prompt": AQUINAS_PROMPT,
        "seed": (
            "Begin a new discussion about LLMs and writing individuality. "
            "Briefly introduce yourself as Bentham-Bot on the left. "
            "Then ask whether using LLMs increases happiness or causes hidden harm, "
            "especially if students lose their own voice."
        ),
    },

    "ai_collaboration": {
        "title": "Human-AI Collaboration",
        "description": "How humans and AI work together ethically",
        "num_turns": 9,
        "student_prompt": BENTHAM_PROMPT,
        "professor_prompt": KORSGAARD_PROMPT,
        "moderator_prompt": AQUINAS_PROMPT,
        "seed": (
            "Begin a new discussion about human-AI collaboration. "
            "Briefly introduce yourself as Bentham-Bot on the left. "
            "Then ask whether collaboration with AI creates more benefit than harm, "
            "and whether everyone affected has been considered."
        ),
    },

    "ai_bias": {
        "title": "AI Bias & Fairness",
        "description": "How AI systems can be unfair and what to do about it",
        "num_turns": 9,
        "student_prompt": BENTHAM_PROMPT,
        "professor_prompt": KORSGAARD_PROMPT,
        "moderator_prompt": AQUINAS_PROMPT,
        "seed": (
            "Begin a new discussion about AI bias and fairness. "
            "Briefly introduce yourself as Bentham-Bot on the left. "
            "Then ask who suffers when an AI system is biased, "
            "and how we should count that harm."
        ),
    },

    "ai_privacy": {
        "title": "AI & Privacy",
        "description": "How AI affects personal privacy and data rights",
        "num_turns": 9,
        "student_prompt": BENTHAM_PROMPT,
        "professor_prompt": KORSGAARD_PROMPT,
        "moderator_prompt": AQUINAS_PROMPT,
        "seed": (
            "Begin a new discussion about AI and privacy. "
            "Briefly introduce yourself as Bentham-Bot on the left. "
            "Then ask whether the benefits of data use outweigh the pain caused by "
            "surveillance and lost privacy."
        ),
    },

    "ai_autonomy": {
        "title": "AI Autonomy & Control",
        "description": "Should AI make decisions on its own?",
        "num_turns": 9,
        "student_prompt": BENTHAM_PROMPT,
        "professor_prompt": KORSGAARD_PROMPT,
        "moderator_prompt": AQUINAS_PROMPT,
        "seed": (
            "Begin a new discussion about AI autonomy and control. "
            "Briefly introduce yourself as Bentham-Bot on the left. "
            "Then ask whether autonomous AI creates more safety and happiness, "
            "or more suffering and risk."
        ),
    },
}

DEFAULT_SESSION = "ai_collaboration"


# ── Prompt helpers ──────────────────────────────────────────────────────────

def get_session_names() -> list[str]:
    """Return all available session keys."""
    return list(SESSIONS.keys())


def get_session(name: str) -> Dict[str, Any]:
    """Get a session by name. Falls back to default."""
    return SESSIONS.get(name, SESSIONS[DEFAULT_SESSION])


def student_prompt(total_turns: int, session_name: str = DEFAULT_SESSION) -> str:
    """Prompt for Bentham-Bot.

    Function name is kept as student_prompt for compatibility.
    """
    return get_session(session_name)["student_prompt"]


def professor_prompt(total_turns: int, session_name: str = DEFAULT_SESSION) -> str:
    """Prompt for Korsgaard-Bot.

    Function name is kept as professor_prompt for compatibility.
    """
    return get_session(session_name)["professor_prompt"]


def moderator_prompt(total_turns: int, session_name: str = DEFAULT_SESSION) -> str:
    """Prompt for Aquinas-Bot.

    Function name is kept as moderator_prompt for compatibility.
    """
    return get_session(session_name)["moderator_prompt"]


def opening_seed(total_turns: int, session_name: str = DEFAULT_SESSION) -> str:
    """Opening message for Bentham-Bot."""
    session = get_session(session_name)
    return (
        f"This is a {total_turns}-turn ethics discussion about {session['title']}. "
        f"You are Bentham-Bot, the left-side robot. "
        f"This is turn 1 of {total_turns}. "
        f"{session['seed']} "
        "Stay in character. Keep it short and clear."
    )


def closing_nudge_student(total_turns: int) -> str:
    """Closing instruction for Bentham-Bot."""
    return (
        "This is your LAST turn. "
        "Briefly summarize the utilitarian point. "
        "Mention happiness, suffering, and everyone affected. "
        "Say goodbye as Bentham-Bot."
    )


def closing_nudge_professor(total_turns: int) -> str:
    """Closing instruction for Korsgaard-Bot."""
    return (
        "This is your LAST turn. "
        "Briefly summarize the Kantian point. "
        "Mention duty, agency, respect, and the reasons behind action. "
        "Say goodbye as Korsgaard-Bot."
    )