# Ethics Podcast

Two Reachy Mini robots having an autonomous, ongoing conversation about ethics —
one playing a confused ethics student, the other a seasoned ethics professor.
Runs as a live exhibit: powered by the OpenAI Realtime API, no microphones, no
human input.

## Requirements

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** for dependency management
- **OpenAI API key** with Realtime API access
- **One or two Reachy Mini robots** reachable on your network
- **Platform-specific GStreamer setup** — see below

## Quick start

```bash
# 1. One-shot setup (installs uv, system deps, configures lock for your OS)
python3 scripts/setup.py

# 2. Fill in your credentials
#    edit .env: OPENAI_API_KEY, ROBOT_A_HOST, ROBOT_B_HOST

# 3. Run
python3 scripts/run.py test       # verify robot connectivity first
python3 scripts/run.py            # start the podcast
```

`scripts/setup.py` is **idempotent** and **cross-platform** — running it on
macOS, Linux, or Windows installs the right system packages (via brew/apt/dnf
or skips on Windows where wheels are self-contained), installs `uv` if missing,
rewrites `pyproject.toml` to lock for the current platform, regenerates
`uv.lock` only when needed, and `uv sync`s the project.

`scripts/run.py` then handles per-OS GStreamer environment at runtime.

Flags:
```bash
python3 scripts/setup.py --dry-run    # preview without changing anything
python3 scripts/setup.py --no-system  # skip brew/apt step (deps already installed)
```

## What `setup.py` does per platform

You don't need to run any of these by hand — `setup.py` handles them — but
here's what happens under the hood.

**macOS (Apple Silicon or Intel)** — installs via Homebrew:
```
brew install gstreamer gst-plugins-base gst-plugins-good pygobject3 py3cairo
```
At runtime, `run.py` auto-exports `GI_TYPELIB_PATH`, `DYLD_LIBRARY_PATH`,
`GST_PLUGIN_PATH` based on the detected Homebrew prefix.

**Linux (Debian/Ubuntu)** — installs via apt:
```
sudo apt install python3-gi python3-cairo-dev libcairo2-dev \
  libgirepository1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good
```
System GStreamer is on the default loader path — no env vars needed at runtime.

**Linux (Fedora/RHEL)** — installs via dnf:
```
sudo dnf install python3-gobject cairo-devel gobject-introspection-devel \
  gstreamer1 gstreamer1-plugins-base gstreamer1-plugins-good
```

**Windows** — no system install. The `gstreamer-msvc-runtime` wheel is
self-contained. If `pygobject` / `pycairo` fail to build during `uv sync`,
install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
with the C++ workload, then re-run `setup.py`.

**Prerequisites** the setup script does not install:
- Python 3.12+ (install from python.org / `brew install python@3.12` /
  `sudo apt install python3.12`)
- Homebrew on macOS (install from https://brew.sh)

## How it works

A single Python orchestrator opens two OpenAI Realtime API sessions
(`coral` voice for the student, `sage` for the professor) and routes
transcripts between them via text injection — **no audio loopback, no
microphones**. One robot speaks at a time.

```
Orchestrator
  ├── OpenAI Session A (student prompt, voice=coral)
  ├── OpenAI Session B (professor prompt, voice=sage)
  ├── Turn Manager: A speaks → transcript → inject into B → B speaks → repeat
  ├── RobotController A → Reachy Mini A (speaker + motion)
  └── RobotController B → Reachy Mini B (speaker + motion)
```

## Project layout

```
src/ethics_podcast/
  config.py           — environment config (RobotConfig, Config)
  prompts.py          — system prompts for student + professor
  robot_controller.py — wraps Reachy Mini SDK (motion, audio, emotions, dance)
  tools.py            — tool definitions for OpenAI + dispatch logic
  orchestrator.py     — core: two sessions, turn-taking, audio routing
  main.py             — entry point
scripts/
  run.py              — cross-platform launcher (handles GStreamer env)
  test_connectivity.py— Phase 0 robot connectivity test
```

## Troubleshooting

- **`No route to host` to a robot** — check the IP in `.env`; it may have
  changed after a router reboot. Re-discover with `scripts/test_connectivity.py`.
- **Two robots not distinguishable on the same subnet** — each robot's hostname
  must be unique (`reachy-mini-1`, `reachy-mini-2`, …). See LEARNINGS.md for the
  rename procedure.
- **macOS: `ModuleNotFoundError: gi`** — install PyGObject via Homebrew (see
  Platform setup) and re-run via `python3 scripts/run.py`.
- **Windows: pygobject build fails** — install Visual Studio Build Tools with
  the C++ workload, then `uv sync --refresh`.
