"""Draft each topic's spoken script with OpenAI, then save it for human review.

One request per topic, answered as strict JSON. The speaking order is fixed
here, not left to the model, so the choreography and the branch logic can rely
on it; the model only chooses the words and one emotion per line.

Output, per topic:
    output/<topic>/script.json   what generator.py renders (edit freely)
    output/<topic>/script.md     the same thing laid out for reading aloud

Nothing in this module runs during the show.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from lta_exhibit.config import OUTPUT_DIR, Config
from lta_exhibit.personas import AEGIS, NOVA, PROFILES, SAGE, SHARED_CONTEXT, describe
from lta_exhibit.topics import OPTION_KEYS, TOPICS, get_topic

# Emotions already proven on these robots by the BOMA exhibit.
EMOTIONS = (
    "cheerful1", "welcoming1", "understanding1", "proud1", "enthusiastic1",
    "thoughtful1", "inquiring1", "surprised1", "uncertain1", "yes1", "no1",
    "oops1", "laughing1", "helpful1", "resigned1", "reprimand1",
)

# Fixed speaking order. Discussion alternates the two officers with SAGE
# steering every third line.
DISCUSSION_ROLES = (NOVA, AEGIS, SAGE, AEGIS, NOVA, SAGE)


def branch_roles(option_key: str, topic: dict) -> tuple[str, str, str]:
    """Who reacts to a winning option, in order: champion, challenger, SAGE."""
    aligned = topic["options"][option_key]["role"]
    if aligned == NOVA:
        return (NOVA, AEGIS, SAGE)
    if aligned == AEGIS:
        return (AEGIS, NOVA, SAGE)
    # SAGE's own middle path: both officers weigh in, then SAGE lands it.
    return (NOVA, AEGIS, SAGE)


def _line_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["text", "emotion", "emotion_at"],
        "properties": {
            "text": {"type": "string"},
            "emotion": {"type": "string", "enum": list(EMOTIONS)},
            "emotion_at": {
                "type": "number",
                "description": "Where in the line the emotion fires, 0.0-0.9 as a fraction.",
            },
        },
    }


def _lines(n: int) -> dict:
    return {"type": "array", "items": _line_schema(), "minItems": n, "maxItems": n}


def response_schema() -> dict:
    line = _line_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "intro", "discussion", "dilemma",
            "reveal_A", "reveal_B", "reveal_C", "reveal_none",
            "branch_A", "branch_B", "branch_C", "close",
        ],
        "properties": {
            "intro": line,
            "discussion": _lines(len(DISCUSSION_ROLES)),
            "dilemma": line,
            "reveal_A": line,
            "reveal_B": line,
            "reveal_C": line,
            "reveal_none": line,
            "branch_A": _lines(3),
            "branch_B": _lines(3),
            "branch_C": _lines(3),
            "close": line,
        },
    }


SYSTEM_PROMPT = f"""\
You are an award-winning writer of short, sharp stage dialogue for business
audiences. You write lines to be SPOKEN by text-to-speech, so:
- plain spoken English, contractions welcome, short sentences;
- no stage directions, no brackets, no emoji, no markdown, no speaker names
  at the start of a line;
- numbers written the way a person would say them ("four million dollars");
- every line must stand on its own when heard once across a noisy room.

Setting: {SHARED_CONTEXT}

Cast:
- {describe(NOVA)}
- {describe(SAGE)}
- {describe(AEGIS)}

Quality bar: substantive enough that a CIO nods, light enough that someone
holding a glass of wine smiles. Concrete examples beat abstractions. Each
officer argues from their own lens and they genuinely disagree; nobody is a
straw man. Never name real companies, real people or the evening's panelists.
"""


def build_user_prompt(topic: dict) -> str:
    options = "\n".join(
        f"  {key}: {opt['label']}  (championed by {PROFILES[opt['role']]['name']})"
        for key, opt in topic["options"].items()
    )
    discussion = ", ".join(PROFILES[r]["name"] for r in DISCUSSION_ROLES)
    branches = "\n".join(
        f"  branch_{key}: "
        + " -> ".join(PROFILES[r]["name"] for r in branch_roles(key, topic))
        for key in OPTION_KEYS
    )
    return f"""\
Write the script for one ~3 minute segment.

TOPIC: {topic['title']} -- {topic['question']}
BRIEF: {topic['brief']}

THE DILEMMA the audience will vote on (shown on screen exactly like this):
"{topic['dilemma']}"
Options:
{options}

Structure and speakers (fixed -- write each line for the named speaker):
- intro (SAGE, 25-40 words): welcome the people gathered at the table, name
  the topic, set up the tension. Do not introduce the cast by role at length;
  one quick nod to NOVA and AEGIS is enough.
- discussion, six lines in this order: {discussion}.
  Each 20-40 words. Build an argument: each line answers the previous one.
  Include at least one concrete scenario or number per officer. SAGE's lines
  steer, sharpen the disagreement, and connect it to people and trust. The
  sixth line (SAGE) sets up the vote without stating the dilemma yet.
- dilemma (SAGE, 45-70 words): turn to the audience, read the dilemma in your
  own words, then the three options clearly as "A, ...; B, ...; or C, ...",
  and tell them to tap their call on any screen. Do not express a preference.
- reveal_A / reveal_B / reveal_C (SAGE, 8-18 words each): announce that most
  of the room chose that option, naming it briefly. No percentages.
- reveal_none (SAGE, 10-20 words): nobody voted; SAGE good-naturedly makes the
  call for the room and picks B.
- branch_A / branch_B / branch_C, three lines each, speakers in this order:
{branches}
  First line: the champion of the chosen option (or, for B, NOVA) reacts to
  the room's choice in character. Second: the other officer adds the cost or
  the condition that would make it work. Third (SAGE, 20-35 words): lands a
  practical leadership takeaway for that choice. Each line 15-35 words.
- close (SAGE, 25-40 words): thank the room, and invite them to bring this
  question to tonight's panel: "{topic['panel_question']}" -- then invite the
  next group to tap a screen and try another dilemma.

Pick one emotion per line from the allowed list that fits the delivery, and
where in the line it should fire (0.0 start - 0.9 near the end).
"""


def _client(config: Config):
    from openai import OpenAI

    if not config.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set in .env")
    return OpenAI(api_key=config.openai_api_key, timeout=600)


def _ask(config: Config, topic: dict) -> dict:
    client = _client(config)
    kwargs = dict(
        model=config.script_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(topic)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "topic_script",
                "schema": response_schema(),
                "strict": True,
            }
        },
    )
    if config.script_model.startswith(("gpt-5", "o")):
        kwargs["reasoning"] = {"effort": "medium"}
    response = client.responses.create(**kwargs)
    return json.loads(response.output_text)


def _clamp_at(value) -> float:
    try:
        return round(max(0.0, min(0.9, float(value))), 2)
    except (TypeError, ValueError):
        return 0.2


def _line(raw: dict, role: str) -> dict:
    emotion = raw.get("emotion") if raw.get("emotion") in EMOTIONS else "thoughtful1"
    return {
        "role": role,
        "text": " ".join(str(raw.get("text", "")).split()),
        "emotion": emotion,
        "emotion_at": _clamp_at(raw.get("emotion_at", 0.2)),
    }


def to_script(topic: dict, raw: dict, model: str) -> dict:
    """Attach the fixed speakers to the model's words."""
    return {
        "topic_id": topic["id"],
        "title": topic["title"],
        "question": topic["question"],
        "script_model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "segments": {
            "intro": [_line(raw["intro"], SAGE)],
            "discussion": [
                _line(item, role) for item, role in zip(raw["discussion"], DISCUSSION_ROLES)
            ],
            "dilemma": [_line(raw["dilemma"], SAGE)],
            **{f"reveal_{k}": [_line(raw[f"reveal_{k}"], SAGE)] for k in OPTION_KEYS},
            "reveal_none": [_line(raw["reveal_none"], SAGE)],
            **{
                f"branch_{k}": [
                    _line(item, role)
                    for item, role in zip(raw[f"branch_{k}"], branch_roles(k, topic))
                ]
                for k in OPTION_KEYS
            },
            "close": [_line(raw["close"], SAGE)],
        },
    }


# The order segments are read in when reviewing.
SEGMENT_ORDER = (
    "intro", "discussion", "dilemma",
    "reveal_A", "reveal_B", "reveal_C", "reveal_none",
    "branch_A", "branch_B", "branch_C", "close",
)


def to_markdown(script: dict) -> str:
    topic = get_topic(script["topic_id"])
    out = [f"# {script['title']} — {script['question']}", ""]
    out.append(f"**Dilemma:** {topic['dilemma']}")
    for key, opt in topic["options"].items():
        out.append(f"- **{key}** {opt['label']} ({PROFILES[opt['role']]['name']})")
    out.append("")
    for segment in SEGMENT_ORDER:
        out.append(f"## {segment}")
        for line in script["segments"].get(segment, []):
            name = PROFILES[line["role"]]["name"]
            out.append(f"**{name}** _({line.get('emotion', '-')})_: {line.get('text', '')}  ")
        out.append("")
    words = sum(
        len(line["text"].split())
        for seg in ("intro", "discussion", "dilemma", "reveal_B", "branch_B", "close")
        for line in script["segments"].get(seg, [])
    )
    out.append(f"_One path ≈ {words} words ≈ {words / 2.5 / 60:.1f} min of speech._")
    return "\n".join(out) + "\n"


def script_path(topic_id: str) -> Path:
    return OUTPUT_DIR / topic_id / "script.json"


def load_script(topic_id: str) -> dict | None:
    path = script_path(topic_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_script(script: dict) -> Path:
    path = script_path(script["topic_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    path.with_suffix(".md").write_text(to_markdown(script), encoding="utf-8")
    return path


def generate_one(config: Config, topic_id: str) -> Path:
    topic = get_topic(topic_id)
    raw = _ask(config, topic)
    return save_script(to_script(topic, raw, config.script_model))


def generate_scripts(
    config: Config,
    topic_ids: list[str] | None = None,
    *,
    overwrite: bool = False,
    progress=print,
) -> list[Path]:
    """Draft every requested topic in parallel. Existing scripts are kept
    unless ``overwrite`` -- they may already carry hand edits."""
    wanted = topic_ids or [t["id"] for t in TOPICS]
    todo = [t for t in wanted if overwrite or not script_path(t).exists()]
    for t in wanted:
        if t not in todo:
            progress(f"  {t}: keeping existing script.json (hand edits preserved)")
    if not todo:
        return [script_path(t) for t in wanted]

    progress(f"  Drafting {len(todo)} script(s) with {config.script_model}... (1-3 min)")
    results: list[Path] = []
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        futures = {t: pool.submit(generate_one, config, t) for t in todo}
        for t, future in futures.items():
            try:
                path = future.result()
                results.append(path)
                progress(f"  {t}: wrote {path.relative_to(OUTPUT_DIR.parent)}")
            except Exception as exc:  # noqa: BLE001
                progress(f"  {t}: FAILED -- {exc}")
    return results


if __name__ == "__main__":  # pragma: no cover
    import sys

    from lta_exhibit.config import load_config

    ids = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    generate_scripts(load_config(), ids, overwrite="--force" in sys.argv)
