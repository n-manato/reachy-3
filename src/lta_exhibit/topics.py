"""The three topics, each built around one leadership dilemma.

Everything a visitor reads on the touch screens comes from here, so wording
changes are made in this file and never have to wait on the LLM. The LLM only
writes the spoken lines (script_gen.py), and it is handed these facts so the
robots and the screens always agree.

Each option is "aligned" with one robot -- that is the robot who cheers when
the room picks it, and the robot a voter is told their call matches.
"""

from __future__ import annotations

from lta_exhibit.personas import AEGIS, NOVA, SAGE

OPTION_KEYS = ("A", "B", "C")

TOPICS: list[dict] = [
    {
        "id": "agents_keys",
        "title": "AI Safety: Agents with the Keys",
        "question": "What happens when AI stops talking and starts acting?",
        "brief": (
            "AI agents that act, not just chat: issuing refunds, changing "
            "accounts, moving money. Prompt injection, where a cleverly worded "
            "customer email or web page gives the agent instructions it obeys. "
            "Testing and outside red-teaming before launch, dollar caps and "
            "approval thresholds, who holds the off switch and whether frontline "
            "staff may overrule the agent, and who is accountable when it acts "
            "wrongly. AEGIS may use a what-if about a deepfake of the CEO's voice "
            "approving a wire transfer. NOVA argues human agents make mistakes "
            "too and competitors already run agents in production. The numbers "
            "in the dilemma describe the fictional company; never present them "
            "as real-world statistics."
        ),
        "dilemma": (
            "Your new AI agent can issue refunds and change accounts on its own. "
            "In testing it solved 9 of 10 cases, but one cleverly worded email "
            "tricked it into a $9,000 refund. Launch is Monday. What's your call?"
        ),
        "options": {
            "A": {"label": "Launch Monday; monitor and fix as we go", "role": NOVA},
            "B": {"label": "Launch with human sign-off on anything over $500", "role": SAGE},
            "C": {"label": "Hold until it passes an outside red-team test", "role": AEGIS},
        },
        "panel_question": (
            "When an AI system in your company acts on its own, who can switch "
            "it off, and how fast?"
        ),
    },
    {
        "id": "first_rung",
        "title": "AI Replacement: The First Rung",
        "question": "If AI does the entry-level work, where do future leaders come from?",
        "brief": (
            "AI replacing work, framed as the vanishing first rung of the career "
            "ladder rather than a headcount story. The cost math and AI-native "
            "junior staff who outproduce larger teams; the risk that if nobody "
            "learns the job, nobody can check the machine, and tomorrow's "
            "managers never did the work they supervise; a contact center where "
            "AI takes the routine calls and people are left with only the "
            "hardest ones; tasks versus jobs; how leaders redesign entry-level "
            "roles and grow judgment. SAGE may ask the room how they learned "
            "their own jobs. A light self-aware joke fits here: three AIs "
            "debating whether to replace humans. The numbers in the dilemma "
            "describe the fictional company; never present them as real-world "
            "statistics."
        ),
        "dilemma": (
            "AI now handles about 60% of the work your first-year analysts do. "
            "You planned to hire 10 graduates this year. What's your call?"
        ),
        "options": {
            "A": {"label": "Hire 3 and invest the savings in AI", "role": NOVA},
            "B": {"label": "Hire all 10 and rebuild their jobs around AI", "role": SAGE},
            "C": {"label": "Hire all 10, but train them without AI first", "role": AEGIS},
        },
        "panel_question": (
            "If AI takes over entry-level work, how will your organization grow "
            "its next generation of leaders?"
        ),
    },
    {
        "id": "say_its_ai",
        "title": "AI Ethics: Should AI Say It's AI?",
        "question": "Do your customers have a right to know they're talking to AI?",
        "brief": (
            "Disclosure and honesty when AI talks to customers. Natural AI voices "
            "on the phone, in chat and in sales; callers who are happier and "
            "served faster when they don't know; the sense of deception when "
            "they find out later; some states already have bot-disclosure rules; "
            "the line between a friendly voice and a manipulative one, such as "
            "an AI that sounds caring while it sells; cloned voices; which calls "
            "are sensitive enough to need a human. The live moment: the audience "
            "has been listening to AI-generated voices from three robots. SAGE's "
            "third discussion line should point this out and ask whether knowing "
            "it changed how they heard the robots. The numbers in the dilemma "
            "describe the fictional company; never present them as real-world "
            "statistics."
        ),
        "dilemma": (
            "Your AI phone agent sounds so natural that most callers can't tell. "
            "When it opens with \"I'm an AI assistant,\" satisfaction drops 20% "
            "and hang-ups double. What's your call?"
        ),
        "options": {
            "A": {"label": "Only say it's AI if a caller asks", "role": NOVA},
            "B": {"label": "Say so up front, and offer a human anytime", "role": SAGE},
            "C": {"label": "Say so up front, and keep humans on sensitive calls", "role": AEGIS},
        },
        "panel_question": (
            "Where do your customers have a right to know they're dealing with "
            "AI, and where does it not matter?"
        ),
    },
]

TOPIC_IDS = tuple(topic["id"] for topic in TOPICS)


def get_topic(topic_id: str) -> dict:
    for topic in TOPICS:
        if topic["id"] == topic_id:
            return topic
    raise KeyError(topic_id)


def public_topics() -> list[dict]:
    """What the touch screens need, without the LLM-only brief."""
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "question": t["question"],
            "dilemma": t["dilemma"],
            "options": {k: dict(v) for k, v in t["options"].items()},
            "panelQuestion": t["panel_question"],
        }
        for t in TOPICS
    ]
