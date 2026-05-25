# Ethics Podcast — Technical Learnings & Operations Guide

**Project**: Dual Reachy Mini Robot Ethics Podcast
**Date**: March 2026
**Platform**: macOS (Apple Silicon) + 2x Reachy Mini Wireless robots

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [The mDNS Hostname Problem](#the-mdns-hostname-problem)
3. [The GStreamer WebRTC Single-Pipeline Limit](#the-gstreamer-webrtc-single-pipeline-limit)
4. [The Daemon Restart Protocol](#the-daemon-restart-protocol)
5. [OpenAI Realtime API Constraints](#openai-realtime-api-constraints)
6. [The Two-Step Generation Pattern](#the-two-step-generation-pattern)
7. [Motion System Architecture](#motion-system-architecture)
8. [Robot Wake-Up Sequence](#robot-wake-up-sequence)
9. [Network & Connection Troubleshooting](#network--connection-troubleshooting)
10. [Adding More Robots to the Subnet](#adding-more-robots-to-the-subnet)
11. [Quick Reference Commands](#quick-reference-commands)

---

## System Architecture

```
Main Process (ethics-podcast CLI)
  ├── Menu: Setup / Generate / Play / Restart & Wake / Exit
  │
  ├── Generator (Phase 1)
  │   ├── OpenAI Realtime API Session A (student voice)
  │   ├── OpenAI Realtime API Session B (professor voice)
  │   └── gpt-4o-mini for emotion assignment
  │
  └── Player (Phase 2)
      ├── Subprocess A (robot_worker.py) ← subprocess.Popen
      │   ├── ReachyMini SDK connection to Robot A
      │   ├── WebRTC audio pipeline (GStreamer)
      │   ├── 100Hz control loop (set_target every 10ms)
      │   └── Move queue + speech sway + auto-breathing
      │
      └── Subprocess B (robot_worker.py) ← subprocess.Popen
          ├── ReachyMini SDK connection to Robot B
          ├── WebRTC audio pipeline (GStreamer)
          ├── 100Hz control loop (set_target every 10ms)
          └── Move queue + speech sway + auto-breathing
```

**Critical**: Each robot MUST run in its own fully independent OS process (via `subprocess.Popen`, NOT `multiprocessing.Process`). This is non-negotiable on macOS.

---

## The mDNS Hostname Problem

### What Happens

All Reachy Mini robots ship with identical hostnames:
- Hostname: `reachy-mini`
- mDNS service: `reachy-mini.local`
- Robot name: `reachy_mini`
- Daemon service type: `_reachy-mini._tcp.local.`

When two robots join the same WiFi network, the second robot's mDNS registration collides with the first. Only one robot appears in mDNS discovery. The collision corrupts the WebRTC signaling for whichever robot connects to the network second — permanently, until reboot.

### Symptoms

- `find_robots()` returns only 1 robot even though 2 are on the network
- `_audio_send_ready = False` on one robot regardless of connection order
- The "broken" robot changes depending on which one booted first
- The issue persists even in separate processes

### The Fix

SSH into one robot and give it a unique hostname:

```bash
# SSH credentials for all Reachy Minis: user=pollen, password=root
sshpass -p 'root' ssh pollen@<ROBOT_IP>

# 1. Change /etc/hostname
echo 'reachy-mini-2' | sudo tee /etc/hostname

# 2. Update /etc/hosts
sudo sed -i 's/127.0.1.1.*reachy-mini$/127.0.1.1\t\treachy-mini-2/' /etc/hosts

# 3. Add --robot-name to the daemon launcher
sudo sed -i 's|--no-wake-up-on-start|--no-wake-up-on-start --robot-name reachy_mini_2|' \
  /venvs/mini_daemon/lib/python3.12/site-packages/reachy_mini/daemon/app/services/wireless/launcher.sh

# 4. Reboot the robot
sudo reboot
```

After reboot, both robots register unique mDNS names and coexist.

### Verification

```python
from reachy_mini.utils.discovery import find_robots
robots = find_robots(timeout=5.0)
# Should show 2 robots with different names and addresses
```

---

## The GStreamer WebRTC Single-Pipeline Limit

### What Happens

On macOS, GStreamer's WebRTC stack (`webrtcsrc` element) cannot run two concurrent pipelines in a single process. The first pipeline's ICE negotiation succeeds; the second always fails silently.

### Symptoms

- First robot connected: `_audio_send_ready = True`
- Second robot connected: `_audio_send_ready = False`
- The `Audio send chain ready` log never appears for the second robot
- This happens regardless of connection order or timing
- Even `multiprocessing.Process` doesn't help (shares GStreamer global state via fork)

### The Fix

Each robot MUST run in a completely independent OS process via `subprocess.Popen`:

```python
# This works:
subprocess.Popen([sys.executable, "robot_worker.py", host, role], ...)

# This does NOT work:
multiprocessing.Process(target=worker_fn, args=(host, role))

# This also does NOT work:
subprocess.Popen([sys.executable, "-m", "ethics_podcast.robot_worker", ...])
```

**Important**: The worker must be launched as a direct script path (`python robot_worker.py`), not as a module (`python -m module`). Module invocation was found to fail in testing.

Communication between the main process and each worker uses a stdin pipe with length-prefixed pickled messages.

---

## The Daemon Restart Protocol

### What Works

```bash
# SSH restart — completely kills and restarts the daemon process
sshpass -p 'root' ssh pollen@<IP> "sudo systemctl restart reachy-mini-daemon.service"
# Wait 30 seconds for full initialization
# Then enable motors
curl -X POST http://<IP>:8000/api/motors/set_mode/enabled
# Wait 10+ seconds for WebRTC producers to register
```

### What Does NOT Work

```bash
# API stop/start — BREAKS WebRTC signaling state
curl -X POST http://<IP>:8000/api/daemon/stop?goto_sleep=true
curl -X POST http://<IP>:8000/api/daemon/start?wake_up=true
# ^ This corrupts WebRTC. Audio will not work after this sequence.
```

The API stop/start is fine for the dashboard ON/OFF toggle (which only controls one robot at a time from the robot's own CM4). But when controlling two robots remotely from a Mac, the API stop/start corrupts the WebRTC signaling state. Always use `systemctl restart` via SSH.

### After Many Test Connections

The robots' WebRTC signaling servers accumulate stale sessions. If audio stops working after many connection attempts, restart the daemons via SSH.

---

## OpenAI Realtime API Constraints

### Prompt Length Limit

The Realtime API returns **empty audio responses** when session instructions exceed approximately 1000 characters. There is no error — the response simply has 0 audio chunks and 0 transcript.

**Solution**: Keep session instructions under 400 characters. Put detailed instructions in the injected user messages instead.

```python
# BAD — 3450 chars, returns empty audio
instructions = """## IDENTITY\nYou are Hello, a lovable robot...\n## CONVERSATION STRUCTURE\n..."""

# GOOD — 389 chars, works reliably
instructions = "You are Hello, a lovable robot confused about AI ethics. You speak English. Keep responses to 1-3 sentences..."
```

### Tool Calls Cause Hangs

When tool definitions are included in the session config, the model generates tool calls that create a multi-response flow:

1. Model generates tool calls (no audio)
2. Client sends tool results
3. Client calls `response.create()` for the follow-up
4. Model generates more tool calls or audio
5. Steps 2-4 repeat

This flow reliably hangs after 7-8 turns. The WebSocket connection stalls and never produces another event.

**Solution**: Do not use tools during Realtime API generation. Use the two-step pattern instead.

### Empty First Responses Under Rate Limiting

After many API calls, the first response on a new session may return empty. The retry logic (wait 2 seconds, call `response.create()` again) resolves this.

### Modalities

- `output_modalities: ["audio"]` is the only valid audio option
- `output_modalities: ["text", "audio"]` is **invalid** and causes errors
- The default (no `output_modalities` specified) produces audio

---

## The Two-Step Generation Pattern

### Step 1: Generate Audio (Realtime API)

- Open two Realtime API WebSocket sessions (one per voice)
- Configure with SHORT instructions (< 400 chars), NO tools
- Alternate turns: inject transcript from A into B's session, and vice versa
- Each turn is a single request → single response (no multi-response loops)
- Save audio as WAV files + transcript to `output/conversation.json`

### Step 2: Assign Emotions (gpt-4o-mini)

- Send all transcripts to `gpt-4o-mini` as a single chat completion
- Request JSON output with emotion + head direction per turn
- The model returns unpredictable JSON key names (`"turns"`, `"conversation"`, `"actions"`, etc.) — always search for the first list value
- Merge the assigned actions into `conversation.json`

This approach is:
- **Reliable** — no tool call hangs
- **Cheap** — gpt-4o-mini for emotion assignment costs almost nothing
- **Flexible** — re-run emotions without regenerating audio

---

## Motion System Architecture

The robot worker implements a simplified version of Pollen Robotics' `MovementManager`:

### 100Hz Control Loop

A dedicated thread runs at 100Hz, calling `robot.set_target(head, antennas, body_yaw)` exactly once per tick. This is the ONLY code that calls `set_target`.

### Primary Moves (Sequential)

Emotions, dances, and goto-pose moves go through a move queue:
- Only one primary move plays at a time
- When a move finishes, the next one starts automatically
- Each move implements `evaluate(t) -> (head_pose, antennas, body_yaw)`

### Secondary Offsets (Additive)

Speech sway offsets (x, y, z, roll, pitch, yaw in meters/radians) are layered on top of the primary pose using `compose_world_offset()`. This means the robot can nod while playing an emotion.

### Auto-Breathing

When no moves are queued and no speech sway is active for 0.3 seconds, the robot automatically starts a gentle breathing animation:
- Z-axis oscillation (5mm amplitude, 0.1 Hz)
- Antenna sway (15° amplitude, 0.5 Hz)

This ensures the robot is **always alive** — never static.

### Network Safety

The main process sends only lightweight commands over the pipe:
- `push_audio` — PCM chunks (buffered by WebRTC)
- `set_speech_offsets` — 6-float tuple at ~20Hz
- `queue_emotion` / `queue_dance` — one-shot, infrequent
- `queue_goto` — one-shot with SDK interpolation

Never send raw `set_target` or `set_full` commands from the main process — this floods the WebSocket and drops the connection.

---

## Robot Wake-Up Sequence

The Reachy Mini has a sleep/wake concept:
- **Sleep**: Motors disabled, head droops down
- **Awake**: Motors enabled, head upright

### Correct Wake-Up (after systemctl restart)

```bash
# Motors are disabled after daemon restart
curl -X POST http://<IP>:8000/api/motors/set_mode/enabled
```

### Dashboard ON Toggle (for reference)

The web dashboard at `http://<IP>:8000/` has an ON/OFF toggle. Internally:
- OFF: `POST /api/daemon/stop?goto_sleep=true`
- ON: `POST /api/daemon/start?wake_up=true`

This works for single-robot use but **breaks WebRTC** when controlling two robots remotely. Do not use for multi-robot setups.

### Wake-Up in the Application

The robot worker automatically enables motors if they're disabled:
```python
requests.post(f"http://{host}:8000/api/motors/set_mode/enabled", timeout=5)
```

---

## Network & Connection Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `find_robots()` returns 1 robot | mDNS hostname collision | Rename one robot's hostname via SSH |
| `audio_ready=False` on both | Stale WebRTC sessions | SSH restart both daemons, wait 30s |
| `audio_ready=False` on one | GStreamer single-pipeline limit | Use `subprocess.Popen`, not `multiprocessing` |
| `Producer reachymini not found` | Daemon not fully started | Wait 30+ seconds after restart |
| `Lost connection with server` | Too many commands over WebSocket | Only send high-level commands, not raw set_target |
| `Motors: disabled` | Daemon restarted without wake-up | `POST /api/motors/set_mode/enabled` |
| Empty audio from Realtime API | Prompt too long | Keep instructions < 400 chars |
| Generation hangs at turn 8+ | Tool call multi-response loop | Don't use tools in Realtime API sessions |

---

## Adding More Robots to the Subnet

To add robots 3 and 4 to the existing two-robot setup:

### Step 1: Assign Unique Hostnames

Each robot needs a unique hostname. SSH into each new robot:

```bash
# Robot 3
sshpass -p 'root' ssh pollen@<ROBOT_3_IP>
echo 'reachy-mini-3' | sudo tee /etc/hostname
sudo sed -i 's/127.0.1.1.*reachy-mini$/127.0.1.1\t\treachy-mini-3/' /etc/hosts
sudo sed -i 's|--no-wake-up-on-start|--no-wake-up-on-start --robot-name reachy_mini_3|' \
  /venvs/mini_daemon/lib/python3.12/site-packages/reachy_mini/daemon/app/services/wireless/launcher.sh
sudo reboot

# Robot 4
sshpass -p 'root' ssh pollen@<ROBOT_4_IP>
echo 'reachy-mini-4' | sudo tee /etc/hostname
sudo sed -i 's/127.0.1.1.*reachy-mini$/127.0.1.1\t\treachy-mini-4/' /etc/hosts
sudo sed -i 's|--no-wake-up-on-start|--no-wake-up-on-start --robot-name reachy_mini_4|' \
  /venvs/mini_daemon/lib/python3.12/site-packages/reachy_mini/daemon/app/services/wireless/launcher.sh
sudo reboot
```

Naming convention:
| Robot | Hostname | Robot Name | mDNS Address |
|-------|----------|------------|--------------|
| 1 | reachy-mini | reachy_mini | reachy-mini.local |
| 2 | reachy-mini-2 | reachy_mini_2 | reachy-mini-2.local |
| 3 | reachy-mini-3 | reachy_mini_3 | reachy-mini-3.local |
| 4 | reachy-mini-4 | reachy_mini_4 | reachy-mini-4.local |

### Step 2: Verify mDNS Discovery

After all robots reboot:

```python
from reachy_mini.utils.discovery import find_robots
robots = find_robots(timeout=8.0)
for r in robots:
    print(f"{r.name} at {r.addresses[0]}")
# Should show all 4 robots with unique names
```

### Step 3: Update .env

Add the new robots:

```env
ROBOT_C_NAME="Robot3Name"
ROBOT_C_HOST=192.168.0.XXX
ROBOT_C_VOICE=alloy

ROBOT_D_NAME="Robot4Name"
ROBOT_D_HOST=192.168.0.YYY
ROBOT_D_VOICE=echo
```

### Step 4: Update Code

1. **config.py**: Add `robot_c` and `robot_d` to the `Config` class
2. **robot_controller.py**: No changes needed — `RobotController` is generic
3. **player.py**: Add the new robots to the playback logic with their roles
4. **generator.py**: Add more Realtime API sessions for additional voices

### Step 5: Critical Subprocess Constraint

Each robot MUST have its own `subprocess.Popen` process. With 4 robots, you need 4 independent worker processes:

```python
ctrl_a = RobotController(host_a, name_a, "role_a")
ctrl_b = RobotController(host_b, name_b, "role_b")
ctrl_c = RobotController(host_c, name_c, "role_c")
ctrl_d = RobotController(host_d, name_d, "role_d")

# Each .start() launches a separate subprocess.Popen
```

### Step 6: Daemon Restart Protocol

Restart all 4 daemons via SSH before connecting:

```bash
for ip in 192.168.0.A 192.168.0.B 192.168.0.C 192.168.0.D; do
    sshpass -p 'root' ssh pollen@$ip "sudo systemctl restart reachy-mini-daemon.service" &
done
wait
sleep 30  # Wait for all to initialize
for ip in 192.168.0.A 192.168.0.B 192.168.0.C 192.168.0.D; do
    curl -X POST http://$ip:8000/api/motors/set_mode/enabled
done
```

### Potential Issues with 4 Robots

- **WebRTC audio**: Each robot needs its own process. 4 processes should work since each has its own GStreamer state.
- **Network bandwidth**: 4 concurrent WebRTC streams. Ensure your WiFi can handle it (5GHz recommended).
- **OpenAI sessions**: The Realtime API may have concurrent session limits. Test with 4 sessions before committing.
- **IP changes**: IPs may change after router/robot reboots. Use option 0 (Setup) or `find_robots()` to rediscover.

---

## Quick Reference Commands

```bash
# Launch the application (cross-platform launcher — auto-detects macOS
# Homebrew prefix, no-ops on Linux/Windows)
cd ethics-podcast
python3 scripts/run.py            # start the podcast
python3 scripts/run.py test       # connectivity test

# SSH into a robot
sshpass -p 'root' ssh pollen@192.168.0.XXX

# Restart daemon (the ONLY safe way)
sshpass -p 'root' ssh pollen@<IP> "sudo systemctl restart reachy-mini-daemon.service"

# Enable motors
curl -X POST http://<IP>:8000/api/motors/set_mode/enabled

# Check daemon status
curl -s http://<IP>:8000/api/daemon/status | python3 -m json.tool

# Check motor status
curl -s http://<IP>:8000/api/motors/status

# Check WebRTC producers
python -c "from reachy_mini.media.webrtc_utils import get_producer_list; print(get_producer_list('<IP>', 8443))"

# Play test sound (confirms speaker works)
curl -X POST http://<IP>:8000/api/volume/test-sound

# Scan subnet for robots
for i in $(seq 1 254); do
    curl -s --connect-timeout 1 http://192.168.0.$i:8000/api/daemon/status > /dev/null 2>&1 && echo "FOUND: 192.168.0.$i" &
done; wait

# macOS system deps (install once)
brew install cairo pkg-config gobject-introspection gstreamer libnice-gstreamer
pip install pygobject  # or: uv pip install pygobject
```

---

## Robot Credentials

All Reachy Mini robots use the same default credentials:
- **Username**: `pollen`
- **Password**: `root`
- **SSH**: `sshpass -p 'root' ssh pollen@<IP>`

---

## File Structure

```
ethics-podcast/
├── CLAUDE.md                     # Project context for AI assistants
├── LEARNINGS.md                  # This document
├── .env                          # Robot IPs, API keys, voices
├── run.sh                        # Launch wrapper with GStreamer env vars
├── src/ethics_podcast/
│   ├── main.py                   # CLI menu (setup/generate/play/restart)
│   ├── config.py                 # Environment config
│   ├── prompts.py                # AI personas (keep SHORT < 400 chars)
│   ├── generator.py              # Phase 1: audio + emotion generation
│   ├── player.py                 # Phase 2: playback with motion
│   ├── robot_controller.py       # Subprocess proxy (sends commands via pipe)
│   ├── robot_worker.py           # Worker process (100Hz loop, WebRTC audio)
│   ├── orchestrator.py           # Original real-time orchestrator (unused)
│   └── tools.py                  # Tool definitions for emotions/dances
├── output/                       # Generated conversation files
│   ├── conversation.json         # Transcripts + tool calls + metadata
│   ├── turn_01_student.wav       # Audio files
│   └── ...
└── scripts/
    └── test_connectivity.py      # Phase 0 connectivity test
```
