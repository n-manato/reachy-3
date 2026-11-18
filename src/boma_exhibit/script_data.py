"""The BOMA welcome sketch, transcribed from Scripts/Robot.pdf.

The script is FIXED: 34 turns, three robots, three languages. Only the six
lines flagged ``vary`` are ever rephrased, and even those are rephrased ahead
of time (see variator.py) -- nothing is generated while the show is running.

Three fields carry the multilingual load:

    text         what the audience reads, in the speaker's own language,
                 spelled correctly (Fulfulde keeps its hooked letters)
    tts_text     what ElevenLabs is asked to say. Defaults to ``text``; it is
                 set explicitly where the correct spelling would trip the
                 synthesiser, i.e. the Fulfulde implosives.
    translation  the English gloss shown under the original. English lines
                 have none.

``speakers`` is a list because two turns are spoken in chorus -- "KORAAAA!"
(Zuri + Pulaar) and the closing "Welcome to BOMA!" (all three).
"""

from __future__ import annotations

from typing import Any

# Role keys, left to right as the robots physically sit on stage.
ZURI = "zuri"
KORA = "kora"
PULAAR = "pulaar"

SESSION_NAME = "boma_welcome"
TITLE = "Welcome to BOMA"

# Fulfulde uses implosive consonants that ElevenLabs has no grapheme for.
# Displayed text keeps them; the TTS text gets the nearest plain letter.
FULFULDE_TRANSLITERATION = {
    "ɓ": "b",   # ɓ
    "ɗ": "d",   # ɗ
    "ƴ": "y",   # ƴ
    "ŋ": "ng",  # ŋ
    "Ɓ": "B",   # Ɓ
    "Ɗ": "D",   # Ɗ
    "Ƴ": "Y",   # Ƴ
    "Ŋ": "Ng",  # Ŋ
}


def transliterate_fulfulde(text: str) -> str:
    """Replace Fulfulde implosives with letters a TTS engine can pronounce."""
    for hooked, plain in FULFULDE_TRANSLITERATION.items():
        text = text.replace(hooked, plain)
    return text


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------
# emotions: [(recorded_move_name, position_in_turn)] where position is a
# fraction of the turn's audio duration. The generator turns these into the
# `at_secs` tool calls the player replays against the speech clock.
#
# vary: True lines are rephrased into VARIANT_COUNT alternatives ahead of time.
# Everything else is fixed, because the sketch runs on callbacks -- the USB-port
# joke in line 14 is what makes line 31 land, and "A little." only works if it
# stays two words.

LINES: list[dict[str, Any]] = [
    {
        "n": 1,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "Hello, BOMA! My name is Kora. These are my friends Zuri and Pulaar. "
            "We come from different parts of Africa… and apparently nobody "
            "thought it was important for us to speak the same language."
        ),
        "must_keep": (
            "Introduces Kora by name, names Zuri and Pulaar as friends, says they "
            "come from different parts of Africa, and ends on the joke that nobody "
            "arranged for them to share a language."
        ),
        "emotions": [("welcoming1", 0.0)],
    },
    {
        "n": 2,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Habari! Mimi ni Zuri. Nimefurahi sana kuwa hapa BOMA!",
        "translation": "Hello! I'm Zuri. I'm very happy to be here at BOMA!",
        "emotions": [("cheerful1", 0.0)],
    },
    {
        "n": 3,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "Zuri says hello, she's happy to be at BOMA… and I think she said "
            "I'm her favorite robot."
        ),
        "must_keep": (
            "Must translate Zuri's greeting AND end by claiming she called Kora her "
            "favorite robot -- line 4 is her denying exactly that."
        ),
        "emotions": [("proud1", 0.0)],
    },
    {
        "n": 4,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Hapana! Sikusema hivyo!",
        "translation": "No! I didn't say that!",
        "emotions": [("no_excited1", 0.0)],
    },
    {
        "n": 5,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "She confirmed it.",
        "emotions": [("proud2", 0.0)],
    },
    {
        "n": 6,
        "speakers": [PULAAR],
        "lang": "ff",
        "vary": False,
        "text": "Jam waali! Innde am Pulaar. Mi weltii wonde ɗoo.",
        "translation": "Hello! My name is Pulaar. I'm happy to be here.",
        "emotions": [("welcoming2", 0.0)],
    },
    {
        "n": 7,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "Pulaar says hello too. He's happy to be here… and obviously he "
            "also thinks I'm the best-looking robot."
        ),
        "must_keep": (
            "Must translate Pulaar's greeting AND end by claiming he thinks Kora is "
            "the best-looking robot -- line 8 is his one-word denial."
        ),
        "emotions": [("proud3", 0.0)],
    },
    {
        "n": 8,
        "speakers": [PULAAR],
        "lang": "ff",
        "vary": False,
        "text": "Alaa!",
        "translation": "No!",
        "emotions": [("no1", 0.0)],
    },
    {
        "n": 9,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "Wow. Thank you, guys.",
        "emotions": [("resigned1", 0.0)],
    },
    {
        "n": 10,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Kora, unapenda chakula cha Afrika?",
        "translation": "Kora, do you like African food?",
        "emotions": [("inquiring1", 0.0)],
    },
    {
        "n": 11,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": (
            "Zuri is asking if I like African food. OF COURSE! "
            "Wait… I'm a robot. I can't eat."
        ),
        "emotions": [("enthusiastic1", 0.0), ("oops1", 0.55)],
    },
    {
        "n": 12,
        "speakers": [PULAAR],
        "lang": "ff",
        "vary": False,
        "text": "Hahahaha!",
        "emotions": [("laughing1", 0.0)],
    },
    {
        "n": 13,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Pole sana!",
        "translation": "So sorry!",
        "emotions": [("understanding1", 0.0)],
    },
    {
        "n": 14,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": (
            "She said she feels sorry for me. And honestly… she should. "
            "Imagine smelling jollof rice, nyama choma, plantains… "
            "…and your mouth is a USB port."
        ),
        "emotions": [("sad1", 0.0), ("resigned1", 0.7)],
    },
    {
        "n": 15,
        "speakers": [PULAAR],
        "lang": "en",
        "vary": False,
        "text": "That is very sad.",
        "emotions": [("indifferent1", 0.0)],
    },
    {
        "n": 16,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "WAIT. You speak English?!",
        "emotions": [("surprised1", 0.0)],
    },
    {
        "n": 17,
        "speakers": [PULAAR],
        "lang": "en",
        "vary": False,
        "text": "A little.",
        "emotions": [("uncertain1", 0.0)],
    },
    {
        "n": 18,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "So this entire time you've been watching me struggle?!",
        "emotions": [("surprised2", 0.0)],
    },
    {
        "n": 19,
        "speakers": [PULAAR],
        "lang": "en",
        "vary": False,
        "text": "Yes.",
        "emotions": [("yes1", 0.0)],
    },
    {
        "n": 20,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Hahahaha!",
        "emotions": [("laughing2", 0.0)],
    },
    {
        "n": 21,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "I need new robot friends.",
        "emotions": [("downcast1", 0.0)],
    },
    {
        "n": 22,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": (
            "Lakini Afrika si chakula tu. Tuna lugha, muziki, sayansi na "
            "tamaduni nyingi."
        ),
        "translation": (
            "But Africa isn't only food. We have languages, music, science and "
            "many cultures."
        ),
        "emotions": [("enthusiastic1", 0.0)],
    },
    {
        "n": 23,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "Zuri says Africa is much more than food. It's languages, music, "
            "science, innovation and many cultures."
        ),
        "must_keep": (
            "Translates Zuri: Africa is much more than food -- languages, music, "
            "science, innovation, many cultures. Keep all of those nouns."
        ),
        "emotions": [("understanding1", 0.0)],
    },
    {
        "n": 24,
        "speakers": [PULAAR],
        "lang": "ff",
        "vary": False,
        "text": "Afrika ko yimɓe, ganndal e aadaaji.",
        "translation": "Africa is its people, knowledge and traditions.",
        "emotions": [("proud1", 0.0)],
    },
    {
        "n": 25,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "And Pulaar says Africa is its people, its knowledge and its "
            "traditions. See? Three robots. Three languages."
        ),
        "must_keep": (
            "Translates Pulaar (people, knowledge, traditions) and then lands the "
            "'three robots, three languages' beat that line 26 answers."
        ),
        "emotions": [("thoughtful1", 0.0), ("cheerful1", 0.65)],
    },
    {
        "n": 26,
        "speakers": [ZURI],
        "lang": "sw",
        "vary": False,
        "text": "Lakini Afrika moja!",
        "translation": "But one Africa!",
        "emotions": [("enthusiastic2", 0.0)],
    },
    {
        "n": 27,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "But one Africa.",
        "emotions": [("understanding2", 0.0)],
    },
    {
        "n": 28,
        "speakers": [PULAAR],
        "lang": "ff",
        "vary": False,
        "text": "Afrika!",
        "emotions": [("proud2", 0.0)],
    },
    {
        "n": 29,
        "speakers": [KORA],
        "lang": "en",
        "vary": True,
        "text": (
            "Exactly. So while you're walking around BOMA today, don't just look. "
            "Ask questions. Learn a word you didn't know. Discover a culture you "
            "haven't experienced. And please…"
        ),
        "must_keep": (
            "The call to action for visitors walking around BOMA: don't just look, "
            "ask questions, learn a new word, discover an unfamiliar culture. Must "
            "end on a trailing 'And please...' so line 30 can ask 'Please what?'."
        ),
        "emotions": [("helpful1", 0.0)],
    },
    {
        "n": 30,
        "speakers": [ZURI],
        "lang": "en",
        "vary": False,
        "text": "Please what?",
        "emotions": [("inquiring3", 0.0)],
    },
    {
        "n": 31,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "Somebody find me African food that comes with a USB adapter.",
        "emotions": [("cheerful1", 0.0)],
    },
    {
        "n": 32,
        "speakers": [ZURI, PULAAR],
        "lang": "en",
        "vary": False,
        "text": "KORAAAA!",
        "emotions": [("reprimand1", 0.0)],
    },
    {
        "n": 33,
        "speakers": [KORA],
        "lang": "en",
        "vary": False,
        "text": "What?! I'm hungry!",
        "emotions": [("oops1", 0.0)],
    },
    {
        "n": 34,
        "speakers": [KORA, ZURI, PULAAR],
        "lang": "en",
        "vary": False,
        "text": "Welcome to BOMA!",
        "emotions": [("welcoming1", 0.0)],
        "dance": "yeah_nod",
    },
]


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------

LANGUAGE_NAMES = {"en": "English", "sw": "Swahili", "ff": "Fulfulde"}


def tts_text_for(line: dict[str, Any], text: str | None = None) -> str:
    """The string to hand ElevenLabs for this line.

    Uses an explicit ``tts_text`` when the line sets one, otherwise falls back
    to the display text -- transliterated first if the line is Fulfulde.
    """
    if "tts_text" in line:
        return line["tts_text"]
    body = line["text"] if text is None else text
    if line["lang"] == "ff":
        return transliterate_fulfulde(body)
    return body


def audio_role(line: dict[str, Any]) -> str:
    """Filename role slug: the single speaker, or 'chorus' when several."""
    speakers = line["speakers"]
    return speakers[0] if len(speakers) == 1 else "chorus"


def audio_filename(line: dict[str, Any]) -> str:
    return f"turn_{line['n']:02d}_{audio_role(line)}.wav"


def vary_line_numbers() -> tuple[int, ...]:
    return tuple(line["n"] for line in LINES if line.get("vary"))


def line_by_number(n: int) -> dict[str, Any]:
    for line in LINES:
        if line["n"] == n:
            return line
    raise KeyError(f"no line numbered {n}")


def validate() -> None:
    """Fail loudly on a malformed script rather than half-way through TTS."""
    numbers = [line["n"] for line in LINES]
    if numbers != list(range(1, len(LINES) + 1)):
        raise ValueError(f"LINES must be numbered 1..N with no gaps, got {numbers}")
    for line in LINES:
        if not line["speakers"]:
            raise ValueError(f"line {line['n']} has no speakers")
        for role in line["speakers"]:
            if role not in (ZURI, KORA, PULAAR):
                raise ValueError(f"line {line['n']} has unknown speaker {role!r}")
        if line["lang"] not in LANGUAGE_NAMES:
            raise ValueError(f"line {line['n']} has unknown lang {line['lang']!r}")
        if line.get("vary") and not line.get("must_keep"):
            raise ValueError(f"line {line['n']} is varied but has no must_keep")
        if line.get("vary") and line["lang"] != "en":
            raise ValueError(f"line {line['n']} is varied but is not English")
        for name, pos in line["emotions"]:
            if not 0.0 <= pos < 1.0:
                raise ValueError(f"line {line['n']} emotion {name} at bad pos {pos}")


validate()
