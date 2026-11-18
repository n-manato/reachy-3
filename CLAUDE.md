# BOMA Exhibit — CLAUDE.md

## Project Overview
Three Reachy Mini robots perform a fixed 34-turn comedy sketch welcoming
visitors to BOMA. Each speaks a different language.

| Robot | Seat | Host | Language | Character |
|---|---|---|---|---|
| ZURI | left | 192.168.3.163 | Swahili | cheerful, laughs at everything |
| KORA | center | 192.168.3.106 | English | host, and an unreliable interpreter |
| PULAAR | right | 192.168.3.132 | Fulfulde | deadpan; secretly understands English |

The script is `Scripts/Robot.pdf`, transcribed into `src/boma_exhibit/script_data.py`.
Derived from the Ethics Podcast exhibit at `../../Reachy_3`, which shares this
hardware, this Wi-Fi and most of this architecture.

## Architecture: pre-render everything, then play

```
1. Generate  (menu 1, once before the show)
     variator.py   -> six of Kora's lines, rephrased N ways by the local LLM
     generator.py  -> ElevenLabs renders every clip to output/boma_welcome/
2. Play  (menu 2)
     exhibit.py    -> attract screen, pick a variant, run it, repeat
     player.py     -> drives the robots turn by turn
     web_display/  -> the TV page, which plays all the audio
```

Neither the LLM nor the TTS API is touched while the show runs.

## Key decisions

- **Pre-rendered variants, not live generation.** The LLM produces N wordings
  of six lines ahead of time; the exhibit picks one at random (shuffle bag, so
  every variant plays before any repeats). Variant `v01` is always the verbatim
  script, so the show is complete even if the LLM is unreachable.
- **Only Kora's narration varies.** Lines 1, 3, 7, 23, 25, 29. Everything else
  is fixed because the sketch runs on callbacks — the USB-port joke in line 14
  is what makes line 31 land, and "A little." only works at two words. Non-
  English lines never vary: the translations cannot be quality-checked.
- **All audio comes from the TV.** The exhibit PC is headless with no sound
  card. `WebDisplayManager.plays_speech_audio` / `plays_bgm_audio` tell
  `player.py` to hand each turn to the browser, which plays it and pans it to
  where that robot stands. Robots connect with `media_backend="no_media"`.
- **Captions live on /top.** The reference exhibit put subtitles on small
  screens beside each robot; with one TV there is nowhere else, and this show
  needs them — two thirds of it is in languages the audience will not speak.
- **Chorus turns.** Lines 32 and 34 have several speakers. The generator renders
  each voice and sums them to one mono clip; `player._play_turn` takes a list of
  speakers and animates them all.
- **Emotions are hand-authored,** not inferred — the script is fixed, so a
  human choice beats a model's guess. Positions are fractions of the turn, so
  they stay correct whatever the rendered line turns out to be.
- **Fulfulde is transliterated for TTS only.** `text` keeps ɓ/ɗ for the screen;
  `tts_text` uses b/d so the synthesiser can pronounce it.
- **Short turns get tighter choreography** (`SHORT_TURN_SECS` in player.py). The
  reference exhibit's leisurely 0.83s head rise read as thoughtful in a
  philosophy discussion; in front of a two-word riposte it adds more silence
  than the joke has speech. Twelve of the 34 turns are under two seconds.
- **The show is driven from the page, and starts stopped.** `WebDisplayManager`
  holds a `transport` state plus two events; `exhibit.run_exhibit` waits on
  `wait_for_play()` and races each run against `wait_for_stop()`, cancelling the
  playback task so Stop is immediate rather than waiting out a turn. The PLAY
  button doubles as the user gesture browsers demand before a page may play
  audio — which is why the click-to-start overlay is gone. The buttons have to
  live on `/top` itself: only the document that received the click gets its
  audio unlocked, and `/top` is the one making the sound.
- **Shuffle is a bag with a minimum gap, remembered across restarts.** A plain
  shuffle bag only prevents *adjacent* repeats; in practice `v05, v04, v05`
  turned up, and a restart replayed the variant that had just been stopped.
  `variants.MIN_GAP` now sets a floor on the distance between two showings, the
  window is carried across the seam between bags, and the recent history is
  persisted to `.playback_state.json` in the session directory. The window
  shrinks automatically for small libraries — with N variants only N-1 can be
  held back, and asking for more would leave nothing to draw from.
- **The music ducks, and the page does it.** Kaira is solo kora — a plucked
  string sitting squarely in the speech band, the most speech-competitive bed we
  measured. EQ-carving the midrange only took masking from 158% to 94% of the
  worst generated candidate, so level automation does the rest: the page dips
  the bed to 30% whenever the speech element reports it is `playing` and
  releases it over 1.4s between turns. Driving it from the audio element rather
  than from server messages keeps it in step with what the room actually hears,
  and adds no traffic to the link the robots need.
- **Loop mode lives on the server, volume lives in the browser.** The mode
  decides which variant plays next, which is `VariantPicker`'s job, so the page
  only ever says "next mode" and `WebDisplayManager.cycle_loop` is the single
  source of truth. Volume is the opposite: the browser makes the sound, so it is
  a `GainNode` plus `localStorage`, never round-tripped to Python. The fade and
  the volume are separate gain nodes — one node doing both would mean a Stop
  wipes the operator's level, and a volume nudge cancels a fade.
- **Stopping fades out.** Cutting a run used to make the heads flinch by up to
  ~13°: `_drive_speech_sway`'s `finally` zeroed the sway offsets in one tick,
  and `queue_power_off_pose`'s `clear_first` did it again in the worker. Now the
  sway loop leaves the offsets alone when it is cancelled, and
  `player.fade_to_rest` eases them to zero over 0.6s before queueing a slower
  descent. The browser ramps a `GainNode` down over the same window. The head
  *descent* was always smooth — the worker interpolates from the live pose.
- **One robot dying must not end the show.** `RobotController.ensure_alive` used
  to let a failed restart propagate, which killed a running exhibit outright.
  It now swallows the failure and backs off for `RESTART_COOLDOWN_SECS` — vital,
  because a restart costs up to 8s in `stop()` alone and commands go out tens of
  times a second during sway. Commands to a dead worker are dropped by `_send`.
- **A robot that fails the pre-flight is replaced by a MockRobot before
  connecting.** A hung daemon still completes the TCP handshake, so a worker
  would otherwise wait out its full 30s + 60s reset timeouts on every start.
  The show runs without it: the line still plays from the TV, panned to that
  seat, the robot just does not move.

## Robot speakers: built, but blocked on this machine

`audio_mode="robot"` is implemented and unit-tested — `robot_audio.RobotAudioMixer`
streams one music bed to every robot, sums each line into the stream of whoever
is saying it, ducks that robot's bed to 30% while it talks, and paces all three
against one shared clock so they cannot drift apart. It does not run here, and
the reasons are worth recording so nobody rediscovers them:

- **This PC cannot speak WebRTC.** Ubuntu 20.04 ships GStreamer 1.16.3;
  `webrtcsrc` needs ≥1.20 plus `gst-plugins-rs`, and neither is in any
  configured apt repo. Without it `push_audio` cannot reach a robot at all.
- **`media_released` stays true.** Connecting with `media_backend="no_media"`
  makes the SDK release the daemon's media, and it stays released after that
  client exits — with the WebRTC signalling server on port 8443 shut down with
  it. The next client wanting audio then fails with a bare "Connection
  refused". `robot_worker._acquire_media` fixes that by calling
  `POST /api/media/acquire` and waiting for 8443 before handing over to the SDK.
- **The daemon's own `play_sound` queues; it does not mix.** Measured: a sound
  posted while another is playing waits for the first to finish. So the
  HTTP route cannot put music and speech on one robot either — a Reachy Mini
  has a single output and nothing on the robot side will blend two streams.
  It *can* carry speech alone (one robot only ever says one line at a time),
  at 0.09–0.21s trigger latency, if the music moves to the TV.

## Gotchas worth knowing

- **The LLM is a reasoning model.** Qwen3.6 spends 20k+ characters in a
  `reasoning` field before writing any `content`. Too small a `max_tokens`
  returns an *empty* content, not an error. `DEFAULT_MAX_TOKENS = 32000`.
- **Ollama evicts the model in about a minute on this host,** so a run making
  several requests pays a 32 GB cold load each time. We send a bounded
  `keep_alive: "5m"` — never Nexus's `-1`, which would pin it forever on a
  shared box.
- **The Nexus gateway (`:3000/v1`) is not usable for chat right now** — its
  LLM Chat route is Disabled and its vLLM backend is down. We talk to Ollama
  (`127.0.0.1:11434/v1`) directly. Swap `LLM_BASE_URL` when the gateway returns.
- **`eleven_v3` works** even though no library voice lists it in
  `verified_languages`, and the API key cannot call `/v1/models` to check.
  `scripts/probe_tts.py` settles it by trying a real synthesis.
- **Never rely on mDNS.** Twelve Reachy Minis share this Wi-Fi and all ship as
  `reachy-mini`; a name collision corrupts WebRTC until reboot. Hosts are IPs,
  and `robot_admin.arp_table()` re-maps them by MAC after a router reboot.
- **Robot workers are `subprocess.Popen` on a direct script path** — not
  `multiprocessing`, not `-m module`. GStreamer's global state breaks both.
- Re-running generation is free unless something changed. The LLM's wordings
  are cached in `output/boma_welcome/wordings.json` and reused (menu 1 offers to
  re-ask), and each clip carries a `.txt` sidecar fingerprinting its text, voice
  and model — so an interrupted run resumes instead of re-billing, and does not
  quietly reword lines the operator had already approved.

## Running

```bash
cp .env.example .env                        # add ELEVENLABS_API_KEY
uv sync
uv run python scripts/probe_tts.py          # choose a TTS model, hear samples
uv run python scripts/list_voices.py        # cast the three robots
uv run boma-exhibit                         # 1 to generate, then 2 -> 3 to play
uv run python scripts/preview_web.py        # or preview with no hardware
```

TV, full screen:
```bash
chrome --kiosk --autoplay-policy=no-user-gesture-required http://<ip>:8010/top
```

## Dependencies
Managed with `uv`. reachy-mini, reachy-mini-dances-library, httpx, fastapi,
uvicorn, websockets, numpy, scipy, lameenc. No Qt, no VLC, no pygame — the
browser is the entire UI and the entire sound system.
