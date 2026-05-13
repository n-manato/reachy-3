#!/usr/bin/env python3
"""One-shot cross-platform setup for the Ethics Podcast.

This script installs everything needed to run the project on the user's
machine. Safe to run multiple times — every step is idempotent.

It handles:
  - Python version sanity check (>= 3.12)
  - uv install (if missing) via the official installer
  - System dependencies:
      macOS  : brew install gstreamer, gst-plugins-*, pygobject3, py3cairo
      Linux  : apt install python3-gi + cairo + gstreamer dev packages
      Windows: nothing — wheels handle it
  - pyproject.toml: rewrites [tool.uv].environments to the current platform
    (only if it differs from the current value — macOS clones are a no-op)
  - uv.lock: regenerate only when pyproject changed
  - .env: copy from .env.example if missing
  - uv sync
  - Import smoke test

Usage:
    python3 scripts/setup.py             # full setup
    python3 scripts/setup.py --no-system # skip brew/apt step (deps already installed)
    python3 scripts/setup.py --dry-run   # show what would happen, change nothing
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── Layout ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"

# ── Cosmetic output ───────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()
if _USE_COLOR and platform.system() == "Windows":
    # Enable ANSI on Windows 10+ cmd.exe
    try:
        os.system("")
    except Exception:
        _USE_COLOR = False


def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m" if _USE_COLOR else msg


def step(msg: str) -> None: print(f"\n{_c('1', '==> ' + msg)}")
def ok(msg: str) -> None:   print(f"  {_c('92', 'OK')}   {msg}")
def warn(msg: str) -> None: print(f"  {_c('93', 'WARN')} {msg}")
def err(msg: str) -> None:  print(f"  {_c('91', 'ERR')}  {msg}")
def info(msg: str) -> None: print(f"       {_c('2', msg)}")


# ── Subprocess helpers ────────────────────────────────────────────────────
def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = False, dry: bool = False) -> subprocess.CompletedProcess[str] | None:
    print(f"  {_c('2', '$ ' + ' '.join(str(c) for c in cmd))}")
    if dry:
        return None
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, check=check,
        capture_output=capture, text=True,
    )


def which_uv() -> str | None:
    """Locate uv even right after install (it may not be on PATH yet)."""
    if (p := shutil.which("uv")):
        return p
    home = Path.home()
    for cand in (
        home / ".local" / "bin" / "uv",
        home / ".local" / "bin" / "uv.exe",
        home / ".cargo" / "bin" / "uv",
        home / ".cargo" / "bin" / "uv.exe",
    ):
        if cand.exists():
            os.environ["PATH"] = f"{cand.parent}{os.pathsep}{os.environ['PATH']}"
            return str(cand)
    return None


# ── Platform detection ────────────────────────────────────────────────────
def current_marker() -> str:
    """Return the PEP 508 marker for the current OS+arch."""
    system = platform.system()
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        norm = "arm64"
    elif machine in ("x86_64", "amd64"):
        norm = "x86_64"
    else:
        norm = machine

    if system == "Darwin":
        return f"sys_platform == 'darwin' and platform_machine == '{norm}'"
    if system == "Linux":
        return "sys_platform == 'linux'"
    if system == "Windows":
        return "sys_platform == 'win32'"
    raise RuntimeError(f"Unsupported OS: {system}")


# ── Setup steps ───────────────────────────────────────────────────────────
def check_python() -> None:
    step("Checking Python version")
    if sys.version_info < (3, 12):
        err(f"Python 3.12+ required (got {sys.version.split()[0]})")
        s = platform.system()
        if s == "Darwin":
            info("Install: brew install python@3.12")
        elif s == "Linux":
            info("Install: sudo apt install python3.12 python3.12-venv")
        elif s == "Windows":
            info("Install from https://www.python.org/downloads/")
        sys.exit(1)
    ok(f"Python {sys.version.split()[0]} at {sys.executable}")


def install_uv(dry: bool) -> None:
    step("Ensuring uv is installed")
    if which_uv():
        ok(f"uv found at {which_uv()}")
        return
    warn("uv not found — installing via official script")
    if platform.system() == "Windows":
        run(["powershell", "-NoProfile", "-Command",
             "irm https://astral.sh/uv/install.ps1 | iex"], dry=dry)
    else:
        # curl piped to sh — same as the docs' recommended one-liner
        run(["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"], dry=dry)
    if dry:
        return
    if not which_uv():
        err("uv installation completed but binary not found on PATH.")
        info("Open a new shell and re-run this script.")
        sys.exit(1)
    ok(f"uv installed at {which_uv()}")


def install_system_deps(dry: bool) -> None:
    step("Installing system-level dependencies")
    system = platform.system()

    if system == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            err("Homebrew not found.")
            info("Install: https://brew.sh")
            info('  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
            sys.exit(1)
        pkgs = ["gstreamer", "gst-plugins-base", "gst-plugins-good",
                "pygobject3", "py3cairo"]
        # brew install is idempotent ("already installed" is fine).
        run([brew, "install", *pkgs], check=False, dry=dry)
        ok("Homebrew packages ready")

    elif system == "Linux":
        if shutil.which("apt-get"):
            pkgs = ["python3-gi", "python3-cairo-dev", "libcairo2-dev",
                    "libgirepository1.0-dev", "gstreamer1.0-tools",
                    "gstreamer1.0-plugins-base", "gstreamer1.0-plugins-good"]
            warn("apt requires sudo — you may be prompted for your password")
            run(["sudo", "apt-get", "update"], check=False, dry=dry)
            run(["sudo", "apt-get", "install", "-y", *pkgs], check=False, dry=dry)
            ok("apt packages ready")
        elif shutil.which("dnf"):
            pkgs = ["python3-gobject", "cairo-devel", "gobject-introspection-devel",
                    "gstreamer1", "gstreamer1-plugins-base", "gstreamer1-plugins-good"]
            run(["sudo", "dnf", "install", "-y", *pkgs], check=False, dry=dry)
            ok("dnf packages ready")
        else:
            warn("Unknown Linux package manager — install GStreamer + PyGObject manually.")

    elif system == "Windows":
        info("Windows: GStreamer is bundled via gstreamer-msvc-runtime wheel.")
        info("If pygobject/pycairo build fails later, install Visual Studio Build Tools")
        info("  with the 'Desktop development with C++' workload, then rerun setup.")


def reconfigure_pyproject(marker: str, dry: bool) -> bool:
    """Set environments=[marker] in pyproject.toml. Returns True if changed."""
    step("Configuring pyproject.toml for this platform")
    text = PYPROJECT.read_text()
    new_line = f'environments = ["{marker}"]'

    pattern = re.compile(r'^environments\s*=\s*\[[^\]]*\]', re.MULTILINE)
    match = pattern.search(text)
    if not match:
        warn("No `environments = [...]` line in pyproject.toml — skipping")
        return False

    if match.group(0).strip() == new_line:
        ok(f"Already configured for {marker}")
        return False

    if dry:
        info(f"Would change: {match.group(0).strip()}")
        info(f"          to: {new_line}")
        return True

    PYPROJECT.write_text(pattern.sub(new_line, text))
    ok(f"Set environments = {marker}")
    return True


def ensure_env_file(dry: bool) -> None:
    step("Ensuring .env exists")
    if ENV_FILE.exists():
        ok(".env present")
        return
    if not ENV_EXAMPLE.exists():
        warn(".env.example missing — create .env manually")
        return
    if dry:
        info(f"Would copy {ENV_EXAMPLE.name} -> {ENV_FILE.name}")
        return
    shutil.copy(ENV_EXAMPLE, ENV_FILE)
    ok(".env created from .env.example")
    warn("Edit .env to set OPENAI_API_KEY, ROBOT_A_HOST, ROBOT_B_HOST.")


def relock_and_sync(needs_relock: bool, dry: bool) -> None:
    uv = which_uv()
    if not uv:
        err("uv not on PATH — cannot install Python deps")
        sys.exit(1)
    if needs_relock:
        step("Regenerating uv.lock for current platform")
        run([uv, "lock"], cwd=ROOT, dry=dry)
        ok("uv.lock regenerated")
    step("Installing Python dependencies (uv sync)")
    run([uv, "sync"], cwd=ROOT, dry=dry)
    ok("Python dependencies installed")


def verify(dry: bool) -> None:
    step("Verifying install")
    uv = which_uv()
    if not uv or dry:
        info("(skipped in dry-run)")
        return
    result = run(
        [uv, "run", "--no-sync", "python", "-c", "import ethics_podcast.main"],
        cwd=ROOT, check=False, capture=True,
    )
    if result and result.returncode == 0:
        ok("ethics_podcast imports successfully")
    else:
        err("Import failed:")
        if result and result.stdout: print(result.stdout)
        if result and result.stderr: print(result.stderr)
        info("Re-run setup or check Platform setup section in README.")
        sys.exit(1)


def print_next_steps() -> None:
    print()
    print(_c("1", "Setup complete."))
    print()
    print("Next steps:")
    print(f"  1. Edit {_c('1', '.env')} — set OPENAI_API_KEY and robot IPs (ROBOT_A_HOST, ROBOT_B_HOST).")
    print(f"  2. Verify robots are reachable:")
    print(f"       {_c('1', 'python3 scripts/run.py test')}")
    print(f"  3. Start the podcast:")
    print(f"       {_c('1', 'python3 scripts/run.py')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-system", action="store_true",
                        help="Skip the brew/apt/dnf system-deps step")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show actions without changing anything")
    args = parser.parse_args()

    print(_c("1", "Ethics Podcast — Cross-Platform Setup"))
    print(f"Project: {ROOT}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    if args.dry_run:
        print(_c("93", "[dry-run mode — no changes will be made]"))

    check_python()
    install_uv(args.dry_run)
    if not args.no_system:
        install_system_deps(args.dry_run)
    needs_relock = reconfigure_pyproject(current_marker(), args.dry_run)
    ensure_env_file(args.dry_run)
    relock_and_sync(needs_relock, args.dry_run)
    verify(args.dry_run)
    print_next_steps()
    return 0


if __name__ == "__main__":
    sys.exit(main())
