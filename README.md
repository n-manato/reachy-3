# BOMA Exhibit — three robots, three languages

Three Reachy Mini robots perform a fixed comedy sketch that welcomes visitors
to BOMA. Each robot speaks a different language:

| Robot | Seat | Language | Character |
|---|---|---|---|
| ZURI | left | Swahili | cheerful, laughs at everything |
| KORA | center | English | the host, and an unreliable interpreter |
| PULAAR | right | Fulfulde | deadpan — and secretly understands English |

The script lives in [`Scripts/Robot.pdf`](Scripts/Robot.pdf) and is transcribed
into `src/boma_exhibit/script_data.py`.

## How it runs

Nothing is generated live. Everything is rendered to disk first, then played
back deterministically — the same generate-then-play design the Ethics Podcast
exhibit uses, for the same reason: no latency, no echo, no surprises on stage.

```
1. Generate  (once, before the show)
     variator.py   rephrases six English lines into N alternatives via the
                   local LLM (Nexus/Ollama)
     generator.py  renders every line with ElevenLabs and writes
                   output/boma_welcome/{common,v01..vNN}/
2. Play  (the exhibit loop)
     exhibit.py    picks a random pre-rendered variant each time through
     player.py     drives the three robots, one speaking at a time
     web_display/  serves the /top page to the TV, which plays the audio
```

The exhibit PC is headless and has no sound card. **All audio comes out of the
TV**: the kiosk browser showing `/top` fetches the speech as MP3 and pans each
speaker into the surround system with a Web Audio `StereoPanner`. The robots
connect with `media_backend="no_media"` — motion only, no WebRTC audio — which
also keeps the shared Wi-Fi quiet.

## Setup

```bash
cp .env.example .env          # add ELEVENLABS_API_KEY
uv sync
uv run python scripts/probe_tts.py        # pick a working TTS model
uv run python scripts/test_connectivity.py  # verify robots on the network
```

## Running

```bash
uv run boma-exhibit
#   0. Setup, discover, assign & refresh robots
#   1. Generate Variants        <- run once before the show
#   2. Play Exhibit             <- then choose 3 (TV surround)
#   3. Restart Robots
#   4. Exit
```

Then open the TV browser full-screen:

```bash
chrome --kiosk --autoplay-policy=no-user-gesture-required http://<this-pc-ip>:8010/top
```

Preview the whole show without robots or a TV:

```bash
uv run python scripts/preview_web.py
```

## Operating the exhibit

**Regenerating.** Menu 1 is safe to re-run: it reuses the saved wordings
(`output/boma_welcome/wordings.json`) and only synthesises clips whose text,
voice or model actually changed. Answer yes to "ask the LLM for new wordings"
only when you want different sentences — that does re-render those six lines
per variant.

**Curating the variants.** Generation writes `output/boma_welcome/v01 … vNN`.
`v01` is always the script exactly as written; the rest differ only in six of
Kora's lines. Listen to them, and delete any directory whose wording you do not
like — it drops out of the rotation immediately, no config change needed. Keep
`v01`.

**Rotation.** The exhibit deals variants from a shuffled bag: every one plays
before any repeats, and the same variant never runs twice in a row. A visitor
who watches the show twice hears something different.

**The controls.** Everything is on `/top`, in one bar along the bottom:

```
[volume] [ PLAY ] [ STOP ] [ SHUFFLE ]
```

The show starts *stopped*. Press **PLAY** and it runs variant after variant
until you press **STOP**. Stop takes effect immediately — the sound, the
caption and the robots all fade out together over about a second — and the next
PLAY begins a fresh variant from the top. There is no pause.

**Volume** belongs to the screen, not to the show: the browser is what makes
the sound. It is remembered per screen and survives a restart.

**Background music** fades in when the show starts, ducks out of the way while a
robot is speaking, drifts back up between turns, and fades out at the end. Drop
an `.mp3` into `music/` to enable it — `boma.mp3` if present, otherwise the first
one found. `scripts/make_loop.py` turns an album track into a seamless
speech-friendly loop; with `music/` empty the show simply runs without music.

**Loop mode** cycles with each click of the rightmost button:

| | |
|---|---|
| SHUFFLE | every variant once in a random order, then reshuffle — never the same one twice within three runs, including across a restart |
| ALL | v01, v02, … and back to v01 |
| ONE | always v01 — the script exactly as written |

The mode changes take effect on the next variant, so pressing it mid-run is
safe. `LOOP_MODE` in `.env` sets which one the exhibit starts in.

The bar, and the mouse cursor with it, hide themselves a few seconds into a run
and come back on any mouse or key activity. While stopped they stay put.

That PLAY click is also what lets the browser play audio at all, which is why
there is no separate "click to start" overlay any more. **Open `/top` on one
screen only** — every open copy plays the sound, so two of them means an echo.

**Mute.** SPACE, either in the operator's terminal or on the TV's keyboard.

**A robot that is down.** The show starts anyway. Its lines still play from the
TV, panned to that robot's seat, and the other two carry the performance — the
silent robot simply does not move. The startup roster prints which ones did not
answer. Menu option 3 restarts their daemons; if a daemon is wedged badly
enough that it stops answering HTTP entirely, it needs an ssh restart:

```bash
ssh pollen@<host> 'sudo systemctl restart reachy-mini-daemon.service'
```

To take a robot out deliberately, set `ROBOT_MODE=2` and `DISABLED_ROLE=<seat>`.

## Project structure

```
src/boma_exhibit/
  config.py            Environment config (RobotConfig, Config)
  script_data.py       The 34-turn sketch, transcribed from the PDF
  personas.py          Character profiles + the rephrasing prompt
  llm_client.py        OpenAI-compatible client for the local LLM
  variator.py          Fixed script -> N rephrasings (offline)
  elevenlabs_tts.py    ElevenLabs synthesis
  generator.py         Renders every variant to disk
  variants.py          Enumerates variants, picks one without repeating
  player.py            Plays a rendered variant through the robots
  robot_controller.py  Parent-side proxy to each robot worker process
  robot_worker.py      Per-robot worker (motion control loop)
  mock_robot.py        Robot stub for running without hardware
  theme.py             Colours, names, seat order
  web_display/         FastAPI + WebSocket display pages (the TV)
  exhibit.py           The show loop
  main.py              Interactive menu
scripts/
  probe_tts.py         Find a usable ElevenLabs model, render 3-language samples
  list_voices.py       List the voices available on the account
  test_connectivity.py Robot connectivity test
  preview_web.py       Run the display with mock robots
```

## Dependencies

Managed with `uv`. Key packages: reachy-mini, reachy-mini-dances-library, httpx,
fastapi, uvicorn, websockets, numpy, scipy, lameenc.
