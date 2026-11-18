# Ethics Podcast — CLAUDE.md

## Project Overview
Three Reachy Mini robots hold an autonomous conversation about ethics, each
playing a philosopher persona. Runs as a live exhibit (TV/video + robots).

| Robot (real name) | Persona       | Philosopher        | Position |
|-------------------|---------------|--------------------|----------|
| Hello  (robot_a)  | Bentham-Bot   | Jeremy Bentham     | left     |
| Mellow (robot_b)  | Korsgaard-Bot | Christine Korsgaard| center   |
| Fellow (robot_c)  | Aquinas-Bot   | Thomas Aquinas     | right     |

## Architecture: Generate-then-Play
The exhibit is NOT live audio. A conversation is first generated and rendered to
disk, then played back through the robots. This avoids latency/echo on stage.

```
1. Generate (generator.py)
     OpenAI script model writes the multi-turn discussion (prompts.py personas)
       → ElevenLabs TTS (elevenlabs_tts.py) renders each line to 24 kHz PCM
       → saved to output/<session>/  (conversation.json + audio)
2. Play (player.py)
     loads the saved conversation → robot_controller.py / robot_worker.py
     drive each Reachy Mini (speaker + motion), one robot speaking at a time
3. Display modes (optional, run alongside playback)
     video_discussion.py / video_window.py — VLC video + robot discussion
     multi_video_discussion.py + displays/ (PySide6) or web_display/ (browser)
```

`orchestrator.py` + `tools.py` are the older two-robot LIVE mode (OpenAI Realtime,
text injection between sessions, no VAD). It is kept but not the main exhibit path.

## Key Decisions
- **Pre-render, don't stream** — generate audio to disk, then play deterministically.
- **ElevenLabs for TTS**, OpenAI for the script. Sample rate is 24 kHz everywhere
  (`ELEVENLABS_SAMPLE_RATE` in elevenlabs_tts.py is the single source of truth).
- **Persona data lives in two places by design**: short Realtime prompts in
  `prompts.py` (`BENTHAM_PROMPT` etc. + `SESSIONS`) for the live/script path, and
  rich `PHILOSOPHER_PROFILES` (voice + TTS instructions) in `generator.py`.
- **Reuse installed packages** — reachy_mini, reachy_mini_dances_library, openai.
- **Robot speaker audio has two paths.** The SDK streams PCM over WebRTC, which
  needs the GStreamer `webrtcsrc` element (gst-plugins-rs) on the PC. When the
  worker reports `audio_ready=False` (e.g. the Ubuntu 20.04 exhibit PC, GStreamer
  1.16), `player.py` falls back to uploading each turn's WAV to the robot daemon
  and triggering `/api/media/play_sound` (`RobotController.play_sound_file`).

## Running
```bash
cp .env.example .env   # Add OPENAI_API_KEY and ELEVENLABS_API_KEY
uv run python scripts/test_connectivity.py  # verify robots on the network
uv run ethics-podcast                        # interactive menu: Generate / Play / Exit
uv run ethics-podcast --exhibit              # headless Multi-Display Exhibit (waits for PLAY on /top)
scripts/install_service.sh                   # run --exhibit resident as systemd user service
```

## Project Structure
```
src/ethics_podcast/
  config.py            — Environment config (RobotConfig, Config)
  prompts.py           — Personas + SESSIONS (topics, seeds, turn counts)
  generator.py         — Generates the conversation (OpenAI script + ElevenLabs TTS)
  elevenlabs_tts.py    — ElevenLabs synthesis (blocking + streaming)
  player.py            — Plays a saved conversation through the robots
  robot_controller.py  — Wraps Reachy Mini SDK (motion, audio, emotions, dance)
  robot_worker.py      — Per-robot worker process (audio push + motion)
  orchestrator.py      — Legacy two-robot live mode (OpenAI Realtime)
  tools.py             — Tool definitions + dispatch (live mode)
  video_discussion.py  — Single-screen video + discussion loop
  video_window.py      — VLC video window (PySide6)
  multi_video_discussion.py — Multi-display exhibit loop
  displays/            — PySide6 windows (top + mini per robot)
  web_display/         — Browser-served display pages (LAN); /top carries the
                         PLAY / STOP / loop / volume transport that gates the loop,
                         plus the ROBOT / BROWSER switch for where voices play
  bgm_manager.py       — Background music
  wake_word.py         — Optional voice activation
  input_utils.py       — Shared console input helpers (arrow keys)
  mock_robot.py        — Robot stub for testing without hardware
  main.py              — Entry point (interactive menu)
scripts/
  test_connectivity.py — Robot connectivity test
  test_elevenlabs.py   — TTS smoke test
  rerender_audio.py    — Re-render saved conversation audio
  preview_displays.py / preview_web.py — Preview the display layouts
  transcribe.py, run.py, setup.py
```

## Dependencies
Managed via `uv`. Key packages: reachy-mini, reachy-mini-dances-library, openai,
httpx (ElevenLabs), PySide6, python-vlc, numpy, scipy, python-dotenv.
