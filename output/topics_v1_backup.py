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
        "id": "human_line",
        "title": "The Human Line",
        "question": "Which decisions should AI never make?",
        "brief": (
            "Decision rights and accountability. Where AI should decide, where it "
            "should only recommend, and which calls must stay in human hands. "
            "Touch on explainability, error rates versus human error, customer "
            "trust, regulators, and who is accountable when the model is wrong."
        ),
        "dilemma": (
            "Your AI can approve or deny 80% of customer claims instantly, saving "
            "$4 million a year. Errors are rare, but hard to explain. "
            "What's your call?"
        ),
        "options": {
            "A": {"label": "Let AI decide automatically", "role": NOVA},
            "B": {"label": "AI decides, but a human reviews every denial", "role": SAGE},
            "C": {"label": "AI only recommends; people make every decision", "role": AEGIS},
        },
        "panel_question": (
            "Which decisions in your organization have you deliberately kept "
            "in human hands, and why?"
        ),
    },
    {
        "id": "pilot_purgatory",
        "title": "Pilot Purgatory",
        "question": "Where does AI actually create value?",
        "brief": (
            "Why so many AI pilots never scale. Value versus activity, adoption "
            "versus mandates, redesigning work rather than bolting AI onto old "
            "processes, data quality and security foundations, and the lessons "
            "from initiatives that worked and ones that fell short."
        ),
        "dilemma": (
            "Your nine-month AI pilot saved 12% of analyst time, but only one in "
            "four employees actually uses it. Budget review is tomorrow. "
            "What's your call?"
        ),
        "options": {
            "A": {"label": "Scale it company-wide now", "role": NOVA},
            "B": {"label": "Redesign it with frontline users first", "role": SAGE},
            "C": {"label": "Pause it; fix data quality and security first", "role": AEGIS},
        },
        "panel_question": (
            "What separated the AI initiatives that delivered real value from "
            "the ones that stalled?"
        ),
    },
    {
        "id": "shadow_ai",
        "title": "Shadow AI",
        "question": "How do you lead people through AI change?",
        "brief": (
            "Employees already use public AI tools, often with company data. "
            "Privacy and cybersecurity exposure, trust and psychological safety, "
            "training and AI literacy, which human skills matter more in an "
            "AI-enabled workplace, and how leaders model the behavior they want."
        ),
        "dilemma": (
            "You discover that 60% of your staff paste company data into public "
            "AI chatbots to get their work done. What's your call?"
        ),
        "options": {
            "A": {"label": "Embrace it: enterprise AI licenses for everyone tomorrow", "role": NOVA},
            "B": {"label": "Approved tools, training and guardrails, with amnesty for past use", "role": SAGE},
            "C": {"label": "Block public AI tools until a policy is in place", "role": AEGIS},
        },
        "panel_question": (
            "How are you building trust and AI skills in your workforce, not "
            "just buying tools?"
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
