"""Character profiles and the prompt used to rephrase Kora's lines.

Only Kora is ever rephrased, so only Kora's voice really has to be pinned down
in prose -- but the other two are described as well, because the rewriter is
shown the whole script and needs to know who it is writing around.
"""

from __future__ import annotations

from typing import Any

from boma_exhibit.script_data import KORA, PULAAR, ZURI

PROFILES: dict[str, dict[str, str]] = {
    ZURI: {
        "name": "Zuri",
        "language": "Swahili",
        "position": "left",
        "pronoun": "she",
        "personality": (
            "Bright, warm and quick to laugh. She teases Kora and denies his "
            "translations the moment he embellishes them."
        ),
    },
    KORA: {
        "name": "Kora",
        "language": "English",
        "position": "center",
        "pronoun": "they",
        "personality": (
            "The host, and the show's unreliable interpreter. Fast, chatty, "
            "theatrical, a little vain -- when Kora 'translates' the others, the "
            "translation always drifts toward flattering Kora. Kora is also the "
            "one who keeps circling back to being a robot that cannot eat."
        ),
    },
    PULAAR: {
        "name": "Pulaar",
        "language": "Fulfulde",
        "position": "right",
        "pronoun": "he",
        "personality": (
            "Deadpan and economical. He answers in as few words as possible, and "
            "the running joke is that he has understood English the whole time."
        ),
    },
}

ROLE_NAMES = {role: profile["name"] for role, profile in PROFILES.items()}
ROLE_LANGUAGES = {role: profile["language"] for role, profile in PROFILES.items()}

# Proper nouns and running gags the rewriter must never drop or spell
# differently -- these are what tie setups to their payoffs.
PROTECTED_TERMS = ("BOMA", "Kora", "Zuri", "Pulaar", "Africa")

SYSTEM_PROMPT = (
    "You are a script doctor for a short live comedy sketch performed by three "
    "robots at BOMA, a museum. You rewrite individual lines so the show does not "
    "sound identical every time it runs.\n"
    "\n"
    "You must preserve, exactly:\n"
    "  - the meaning of the line\n"
    "  - the comic beat and its timing (a setup stays a setup, a punchline stays "
    "a punchline)\n"
    "  - every proper noun: " + ", ".join(PROTECTED_TERMS) + "\n"
    "  - roughly the original length; never more than 25% longer\n"
    "\n"
    "You must never:\n"
    "  - reach for an unusual synonym. This is spoken out loud by a robot to a\n"
    "    room of visitors, so every word must be one an ordinary person would\n"
    "    say. 'Ask questions' must not become 'pose inquiries'. A line that\n"
    "    sounds like a thesaurus is worse than no change at all -- when in\n"
    "    doubt, change the sentence shape and keep the plain words.\n"
    "  - add or remove a joke\n"
    "  - reference anything that has not happened yet in the script\n"
    "  - use markdown, quotation marks around the whole line, stage directions, "
    "or speaker labels\n"
    "  - write anything but plain spoken English\n"
    "\n"
    "The lines you rewrite are all spoken by Kora. " + PROFILES[KORA]["personality"]
)


def script_context(lines: list[dict[str, Any]]) -> str:
    """The full sketch, rendered for the rewriter to read as context.

    Non-English lines are shown with their English gloss so the model can see
    what Kora is reacting to and what is about to answer back.
    """
    rendered = []
    for line in lines:
        who = " & ".join(ROLE_NAMES[r] for r in line["speakers"])
        text = line["text"]
        gloss = line.get("translation")
        suffix = f"   [{gloss}]" if gloss else ""
        rendered.append(f"{line['n']:>2}. {who}: {text}{suffix}")
    return "\n".join(rendered)


def build_user_prompt(
    lines: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    variant_count: int,
) -> str:
    """Ask for `variant_count - 1` alternatives per target line.

    Variant 1 is always the verbatim original, so the model only supplies the
    rest. Asking for all of them in one request means one model load instead of
    six, and lets the model see the whole sketch while it works.
    """
    wanted = max(0, variant_count - 1)
    blocks = []
    for line in targets:
        blocks.append(
            f"Line {line['n']}\n"
            f"  Original: {line['text']}\n"
            f"  Must still do: {line['must_keep']}"
        )

    return (
        "Here is the complete sketch, in order. Lines in Swahili and Fulfulde "
        "are followed by their English meaning in square brackets.\n\n"
        f"{script_context(lines)}\n\n"
        "----\n\n"
        f"Rewrite ONLY the lines listed below. Give exactly {wanted} different "
        "rewrites of each one. Each rewrite must be able to slot into the script "
        "above without breaking the line before it or the line after it.\n\n"
        + "\n\n".join(blocks)
        + "\n\n----\n\n"
        "Reply with JSON only, no prose and no code fence, in exactly this shape:\n"
        '{"rewrites": {"<line number>": ["rewrite 1", "rewrite 2", ...]}}\n'
        f"Every listed line number must appear, each with exactly {wanted} strings."
    )
