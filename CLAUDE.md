# Ethics Podcast — CLAUDE.md

## Project Overview
Two Reachy Mini robots have an autonomous, ongoing conversation about ethics.
- **Robot A**: Confused ethics student (voice: coral)
- **Robot B**: Seasoned ethics professor (voice: sage)
- Runs as a live exhibit — auto-starts, no human input needed.

## Architecture: Orchestrator Pattern
A single Python app connects to both robots and opens two OpenAI Realtime API
WebSocket sessions. Transcripts are routed programmatically (text injection, NOT
audio loopback). No microphones are used. One robot speaks at a time.

```
Orchestrator
  ├── OpenAI Session A (student prompt, voice=coral)
  ├── OpenAI Session B (professor prompt, voice=sage)
  ├── Turn Manager: A speaks → transcript → inject into B → B speaks → repeat
  ├── RobotController A → Reachy Mini A (speaker + motion)
  └── RobotController B → Reachy Mini B (speaker + motion)
```

## Key Decisions
- **NOT two conversation apps** — avoids echo, VAD confusion, no auto-start.
- **Text injection** between sessions, not audio routing.
- **turn_detection: None** — no VAD, we control turns programmatically.
- **Reuse installed packages** — reachy_mini, reachy_mini_dances_library, openai.
- **Do NOT modify** the existing reachy_mini_conversation_app.

## Running
```bash
cp .env.example .env   # Add your OPENAI_API_KEY
uv run python scripts/test_connectivity.py  # Phase 0: verify robots
uv run ethics-podcast                        # Run the podcast
```

## Project Structure
```
src/ethics_podcast/
  config.py           — Environment config (RobotConfig, Config)
  prompts.py          — System prompts for student + professor
  robot_controller.py — Wraps Reachy Mini SDK (motion, audio, emotions, dance)
  tools.py            — Tool definitions for OpenAI + dispatch logic
  orchestrator.py     — Core: two sessions, turn-taking, audio routing
  main.py             — Entry point
scripts/
  test_connectivity.py — Phase 0 robot connectivity test
```

## Dependencies
Managed via `uv`. Key packages: reachy-mini, reachy-mini-dances-library, openai, numpy, scipy, python-dotenv.
