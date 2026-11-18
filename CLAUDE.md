# Leading Through AI exhibit — CLAUDE.md

## Project Overview
Three Reachy Mini robots play fictional AI executives debating AI-leadership
dilemmas. Visitors vote on three 4K touch monitors, and the debate branches on
the room's choice. Built for *Leading Through AI: A C-Suite Conversation*
(UNO Executive MBA, 2026-11-10, Mammel Hall 162), in the AI-CORE pop-up
showcase slot "AI-powered leadership and ethics game".

| Robot | Seat / screen | Host | Persona |
|---|---|---|---|
| NOVA  | left   `/seat/left`   | 192.168.3.163 | Chief Innovation Officer, *The Accelerator* |
| SAGE  | center `/seat/center` | 192.168.3.106 | Chief People Officer, *The Bridge* (host) |
| AEGIS | right  `/seat/right`  | 192.168.3.132 | Chief Risk & Trust Officer, *The Guardian* |

Topics (`topics.py`, replaced 2026-11-09 for the registered audience; the
old set is in `output/topics_v1_backup.py`): **AI Safety: Agents with the
Keys** (`agents_keys`, an AI agent that acts on its own), **AI Replacement:
The First Rung** (`first_rung`, entry-level work and the leadership
pipeline), **AI Ethics: Should AI Say It's AI?** (`say_its_ai`, disclosure to
customers; SAGE points out the room has been hearing AI voices). Each option
A/B/C is "aligned" with one robot (A NOVA, B SAGE, C AEGIS).
`personas.SHARED_CONTEXT` lists the audience by industry so lines can address
groups ("the bankers in the room"), never individuals.

Derived from `../Africa/Reachy_03` (BOMA: robot workers, sway, fade-to-rest,
web display mechanics) and `../Reachy_3` (OpenAI scripts, HTTP robot-speaker
playback).

## Architecture: draft → review → render → play
```
1. Draft   script_gen.py  OpenAI (SCRIPT_MODEL) writes output/<topic>/script.json
                          + script.md for review. Speaker order is fixed in code.
2. Render  generator.py   ElevenLabs -> output/<topic>/clips/*.wav + conversation.json
                          (.txt fingerprint sidecars: only changed lines re-render)
3. Play    show.py        attract -> talk -> vote -> result/branch -> close -> attract
           player.py      one line at a time: pose, robot speaker, sway, emotion
           votes.py       ballot + output/votes.jsonl (append-only) + CSV export
           web_display/   FastAPI + one WebSocket state broadcast to all pages
```
No LLM or TTS call happens during the show; the venue can be offline.

Per topic the script has: intro, discussion×6, dilemma, reveal_A/B/C/none,
branch_A/B/C (×3 lines), close = 22 clips. One path ≈ 3 min speech + 20 s vote.

## Key decisions
- **Voices come from the robots** via the daemon HTTP API (upload once at
  startup with `preload_sound_files`, then `play_sound` per line). Workers
  connect `no_media`, which releases daemon media, so `RobotController.ensure_media`
  POSTs `/api/media/acquire` first. **Any failure falls back per line** to the
  center screen's browser, panned to that seat (`AUDIO_OUTPUT=display` forces it).
- **Only `/seat/center` makes sound** (music + fallback voice). Music ducks
  from the server's `ducked` flag because robot speech is invisible to the page.
- **Seat toolbar** (bottom-centre pill, Ethics-player style: fades out 3 s after
  the last touch while playing, always shown while paused; hidden = no
  pointer events, cursor hidden): "☰ TOPICS" (confirm sheet → `home` →
  `display.request_home()`; `_run_cancellable` ends the topic, show stays live),
  volume slider (`volume_set`, floor 10 %) and speaker icon / SPACE (`mute`,
  robots go to 0). Views cross-fade (opacity/visibility/scale, no display:none). Volume is one master 0..1:
  center-screen gain + robot speaker volume via the worker's `set_volume`
  (SDK `SetVolumeCmd` — NOT REST `/api/volume/set`, which plays a test sound).
  Saved in `output/settings.json`.
- **Robot clips are louder copies:** `audio_utils.robot_clip` writes
  `output/<topic>/robot_p8dB/*.wav` (+`ROBOT_GAIN_DB`, default 8, through a
  look-ahead limiter ≈ 2× RMS, peaks ≤ 0.95); only those go to the robots.
- **Sleep:** `show._inhibit_sleep` holds a logind block inhibitor while running;
  needs `sudo scripts/allow_sleep_block.sh` once (polkit .pkla), else warns.
- **Touch screens carry no transport.** Start/pause/auto/volume/reset live on
  `/control` (staff laptop/phone). The exhibit starts *live* so a systemd
  restart brings it back without anyone touching it.
- **Auto-start:** after `ATTRACT_SECONDS` idle the next topic from a shuffle
  bag starts, so the table always looks alive. A tap on any topic card wins.
- **Votes:** any seat, any number of people; one tap per screen per 1.2 s
  (debounce). Tie = random among leaders; no votes = `reveal_none` + branch B.
  The voter's screen says which robot their call aligns with.
- **Hand edits survive:** menu 1 never overwrites an existing script.json
  unless asked; menu 2 only re-renders lines whose text/voice/model changed.

## Gotchas (inherited, still true)
- Never use mDNS — every unit is `reachy-mini`. Hosts are IPs; menu 0 rescans.
- Robot workers are `subprocess.Popen` on the script path (GStreamer state).
- HTTP to robots uses `trust_env=False` (campus proxy).
- The daemon's `play_sound` queues rather than mixes — fine, one line at a time.
- Touch panels run a **normal browser, not kiosk**. Each seat page shows a
  "Tap to start" overlay: that one staff tap (pointerup/click — touch
  pointerdown carries no user activation) enters full screen and unlocks
  audio on /seat/center. Pages therefore never reload on a server restart
  (that would drop both); they reset their epoch trackers instead. Wake lock
  falls back to a 2px looping canvas video on plain http. `?nostart` skips
  the overlay (used by /all and the snapshot script).
- If the 3 monitors hang off one X11 PC, bind each touch panel with
  `scripts/launch_kiosks.sh --touch-only` (xinput map-to-output).
- Other devices need TCP 8020 through ufw (`sudo ufw allow 8020/tcp`).
- **Public URL:** https://rm2.aiccore-uno.ai/ is a Tunnelin tunnel to this PC's
  port 8010, owned by `lta-gateway` (`gateway.py`, user unit
  `deploy/lta-gateway.service`): `/lta/...` -> LTA :8020 (prefix stripped),
  everything else -> Reachy_3 Ethics Podcast :8011 (its .env was moved off
  8010). Pages derive `BASE` from their own path, so they work at `/` and
  under `/lta/`. The down page is 503 (the tunnel replaces 502 bodies).
- Ethics Podcast drives the same robot (.163): stop `ethics-podcast` before
  running LTA with robots (LTA's `kill_stale_workers` also kills its
  `robot_worker.py`, and its service would respawn them).
- Bring our own router to the venue; robots are on 192.168.3.0/24.
- **Nothing robot-related may block the asyncio thread.** Worker revival runs
  in a background thread (`ensure_alive`, cooldown 20 s, no full reset);
  commands are written to a non-blocking pipe and dropped if it is full;
  robot `play_sound` is capped at 2 s, after which the line goes to the screen
  and that robot's speaker is skipped for 60 s (`player.speaker_down`).
- `RobotController.stop()` is final (`_closing`); revival uses
  `_stop_process()`. Workers ignore SIGINT so Ctrl+C / systemd stop shuts
  them down in order via the parent.
- A robot that was down at start-up is retried every 45 s and swapped in
  (`show._retry_absent_robots`) only after its clips are uploaded.
- The fallback clip is tagged with its line (`speechLine` == `lineEpoch`) and
  `lineElapsed` is sent, so a center page that (re)loads mid-show never
  replays an old clip and joins a fallback line at the right point.
- `play_sound_file` marks `_sound_started` before the POST so a lost reply
  still gets a `stop_sound`; `sound_generation` drops plays issued before a stop.

## Running
```bash
uv sync
uv run lta-exhibit                         # menu: 0 setup 1 draft 2 render 3 play 4 demo 5 restart 6 export
uv run python scripts/preview_web.py --auto-vote   # no robots; open http://<pc>:8020/all
uv run python scripts/test_robot_audio.py         # each robot speaks one line (do this first on site)
# each touch panel: open http://<pc>:8020/seat/{left,center,right}, tap once
scripts/launch_kiosks.sh --list            # (one PC, 3 monitors) monitors + touch ids
scripts/launch_kiosks.sh --touch-only      # bind touch panels only
uv run python scripts/snapshot_pages.py --size 3840x2160   # screenshots of every state
scripts/install_service.sh                 # optional: resident systemd user service
```
