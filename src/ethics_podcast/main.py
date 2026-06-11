"""Entry point for the Ethics Podcast application.

Three-robot version.

Robots:
  - Hello  = Bentham-Bot
  - Mellow = Korsgaard-Bot
  - Fellow = Aquinas-Bot

Phases:
  1. Generate — runs the AI conversation, saves audio + transcripts to disk
  2. Play    — plays the saved conversation through the robots
  3. Restart & Wake — restarts daemons, wakes robots, verifies audio works
  4. Exit
"""

import asyncio
import logging
import os
import select
import subprocess
import sys
import time
from dataclasses import replace

import requests

from ethics_podcast.config import Config

TV_AUDIO_DELAY = 0.0
ROBOT_AUDIO_DELAY = 0.0


def _web_display_config(config: Config) -> Config:
    """Force the browser display backend for the multi-display exhibit."""
    return replace(config, display_backend="web")


def _emergency_cleanup() -> None:
    """Force-stop background audio and orphaned workers after a Ctrl+C.

    The async cleanup paths can be interrupted before they finish, leaving the
    pygame BGM playing on its own SDL thread and worker subprocesses alive.
    This runs synchronously after KeyboardInterrupt so the exhibit actually
    goes quiet and lets go of the robots. Best-effort and exception-proof.
    """
    try:
        from ethics_podcast.bgm_manager import stop_all_audio

        stop_all_audio()
    except Exception:
        pass

    try:
        from ethics_podcast.robot_controller import kill_stale_workers

        kill_stale_workers()
    except Exception:
        pass



# ── Wake word listener ─────────────────────────────────────────────────────

def _start_wake_listener(config: Config) -> subprocess.Popen | None:
    """Launch wake_word.py as a subprocess listening on Robot A's mic."""
    worker_path = os.path.join(os.path.dirname(__file__), "wake_word.py")
    cmd = [sys.executable, worker_path, config.robot_a.host]

    if config.robot_a.name:
        cmd.append(config.robot_a.name)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            env={**os.environ},
        )

        line = proc.stdout.readline().decode().strip()

        if line == "LISTENING":
            logging.info("Wake word listener active on %s", config.robot_a.host)
            return proc

        logging.warning("Wake listener unexpected output: %s", line)
        proc.terminate()

    except Exception as e:
        logging.warning("Could not start wake listener: %s", e)

    return None


def _stop_wake_listener(proc: subprocess.Popen | None) -> None:
    """Terminate the wake word listener subprocess."""
    if proc and proc.poll() is None:
        proc.terminate()

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _input_or_wake(prompt: str, wake_proc: subprocess.Popen | None) -> str:
    """Read user input.

    On Windows, select.select() does not work with sys.stdin or subprocess pipes.
    So Windows uses normal keyboard input.

    On macOS/Linux, this can also listen for the wake word subprocess.
    """

    # Windows fix
    if os.name == "nt":
        return input(prompt).strip()

    # macOS / Linux without wake listener
    if wake_proc is None:
        return input(prompt).strip()

    print(prompt, end="", flush=True)

    while True:
        if wake_proc.poll() is not None:
            try:
                line = wake_proc.stdout.readline().decode().strip()
                if line == "WAKE":
                    print("\n  [Voice activated!]")
                    return "2"
            except Exception:
                pass

            return input("").strip()

        readers = [sys.stdin, wake_proc.stdout]
        ready, _, _ = select.select(readers, [], [], 0.5)

        for reader in ready:
            if reader is sys.stdin:
                return sys.stdin.readline().strip()

            if reader is wake_proc.stdout:
                line = wake_proc.stdout.readline().decode().strip()

                if line == "WAKE":
                    print("\n  [Voice activated!]")
                    return "2"


# ── Audio mode picker ──────────────────────────────────────────────────────

def _pick_audio_mode() -> str:
    """Pick audio output mode.

    Returns:
      robot           = robot speakers only
      computer        = normal computer / TV output only
      surround        = TV surround-style output only
      hybrid_surround = TV surround-style output + robot speakers
    """
    audio_choice = input(
        "  Audio output:\n"
        "    1. Robot speakers\n"
        "    2. Computer / TV normal audio\n"
        "    3. TV surround-style audio\n"
        "    4. TV surround-style audio + robot speakers\n"
        "  Select audio output [1]: "
    ).strip() or "1"

    if audio_choice == "1":
        return "robot"

    if audio_choice == "2":
        return "computer"

    if audio_choice == "3":
        return "surround"

    if audio_choice == "4":
        return "hybrid_surround"

    print("  Invalid audio choice. Using robot speakers.")
    return "robot"


def _print_audio_mode(audio_mode: str) -> None:
    """Print selected audio mode."""
    if audio_mode == "robot":
        print("  → Audio on robot speakers.")

    elif audio_mode == "computer":
        print("  → Audio on computer or TV normal output.")
        print("  → Emotions and movements still go to robots.")

    elif audio_mode == "surround":
        print("  → Audio on TV surround-style output.")
        print("  → Bentham-Bot = left, Korsgaard-Bot = center, Aquinas-Bot = right.")
        print("  → Emotions and movements still go to robots.")

    elif audio_mode == "hybrid_surround":
        print("  → Audio on TV surround-style output and robot speakers.")
        print("  → Bentham-Bot = left, Korsgaard-Bot = center, Aquinas-Bot = right.")
        print("  → Robot speakers also play each robot voice.")
        print("  → TV audio delay can be adjusted.")
        print("  → Emotions and movements still go to robots.")

    else:
        print("  → Unknown audio mode. Using robot speakers.")


# ── Setup robots ───────────────────────────────────────────────────────────

def _setup_robots() -> Config | None:
    """Discover robots on the network, identify them, and write IPs to .env."""
    from pathlib import Path
    import subprocess as sp
    import concurrent.futures
    import json
    import socket
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"

    def _get_local_subnet() -> str | None:
        """Get the current local subnet like '10.58.209' from this computer."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # This does not actually send traffic to Google.
            # It only lets Windows choose the active network interface.
            s.connect(("8.8.8.8", 80))

            local_ip = s.getsockname()[0]
            s.close()

            parts = local_ip.split(".")

            if len(parts) == 4:
                return ".".join(parts[:3])

        except Exception:
            return None

        return None

    print("\n  Scanning network for Reachy Mini robots...")

    result = sp.run(
        [
            "python",
            "-c",
            "from reachy_mini.utils.discovery import find_robots\n"
            "import json\n"
            "robots = find_robots(timeout=8.0)\n"
            "print(json.dumps([{'name': r.name, 'host': r.host, 'addresses': r.addresses, 'props': r.properties} for r in robots]))\n",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ},
    )

    if result.returncode != 0 or not result.stdout.strip():
        subnet = _get_local_subnet()

        if subnet is None:
            print("  mDNS discovery failed.")
            print("  Could not detect this computer's local subnet.")
            print("  Falling back to 10.25.31.0/24.")
            subnet = "10.25.31"
        else:
            print(f"  mDNS discovery failed. Scanning {subnet}.0/24 for daemons...")

        found = []

        def _check(ip_num: int):
            ip = f"{subnet}.{ip_num}"

            try:
                r = requests.get(
                    f"http://{ip}:8000/api/daemon/status",
                    timeout=1.5,
                )

                if r.status_code == 200:
                    data = r.json()

                    robot_name = (
                        data.get("robot_name")
                        or data.get("name")
                        or "unknown"
                    )

                    return {
                        "name": robot_name,
                        "host": ip,
                        "addresses": [ip],
                        "props": {"robot_name": robot_name},
                    }

            except Exception:
                pass

            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            futures = {pool.submit(_check, i): i for i in range(1, 255)}

            for future in concurrent.futures.as_completed(futures):
                robot = future.result()

                if robot:
                    found.append(robot)

        robots_info = found

    else:
        robots_info = json.loads(result.stdout.strip())

    if len(robots_info) < 3:
        print(f"  Found only {len(robots_info)} robot(s). Need 3 for this podcast.")

        for robot in robots_info:
            print(f"    Found: {robot.get('name')} at {robot.get('addresses')}")

        print("  Make sure all three robots are powered on and connected to the same network.")
        return None

    print(f"  Found {len(robots_info)} robots:")

    for i, robot in enumerate(robots_info):
        addresses = robot.get("addresses") or []
        ip = addresses[0] if addresses else robot.get("host", "unknown")
        name = robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))

        print(f"    {i + 1}. {name} at {ip}")

    print("\n  Waking up robots...")

    for robot in robots_info[:3]:
        addresses = robot.get("addresses") or []
        ip = addresses[0] if addresses else robot.get("host")

        if not ip:
            print("    Skipping robot with no IP address.")
            continue

        try:
            requests.post(
                f"http://{ip}:8000/api/motors/set_mode/enabled",
                timeout=5,
            )
            requests.post(
                f"http://{ip}:8000/api/move/play/wake_up",
                timeout=5,
            )
            print(f"    Wake-up sent to {ip}")

        except Exception as e:
            print(f"    Wake-up failed for {ip}: {e}")

    print("  Waiting for wake-up animation...")
    time.sleep(5)

    print("\n  Identify each robot.")
    print("  You will wiggle each robot one by one.")

    for i, robot in enumerate(robots_info[:3]):
        addresses = robot.get("addresses") or []
        ip = addresses[0] if addresses else robot.get("host", "unknown")
        name = robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))

        input(f"\n  Press Enter to wiggle robot {i + 1} at {ip} ({name})...")

        try:
            for _ in range(3):
                requests.post(
                    f"http://{ip}:8000/api/move/set_target",
                    json={"antennas": [0.5, -0.5]},
                    timeout=3,
                )
                time.sleep(0.4)

                requests.post(
                    f"http://{ip}:8000/api/move/set_target",
                    json={"antennas": [-0.5, 0.5]},
                    timeout=3,
                )
                time.sleep(0.4)

            requests.post(
                f"http://{ip}:8000/api/move/set_target",
                json={"antennas": [0, 0]},
                timeout=3,
            )

            print(f"    Robot {i + 1} wiggled!")

        except Exception as e:
            print(f"    Could not wiggle: {e}")

    print("\n  Current detected robots:")

    for i, robot in enumerate(robots_info[:3]):
        addresses = robot.get("addresses") or []
        ip = addresses[0] if addresses else robot.get("host", "unknown")
        name = robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))

        print(f"    {i + 1}. {name} at {ip}")

    robot_a_idx = input("\n  Which robot is Bentham-Bot / Left? [1]: ").strip() or "1"
    robot_b_idx = input("  Which robot is Korsgaard-Bot / Center? [2]: ").strip() or "2"
    robot_c_idx = input("  Which robot is Aquinas-Bot / Right? [3]: ").strip() or "3"

    try:
        robot_a_i = int(robot_a_idx) - 1
        robot_b_i = int(robot_b_idx) - 1
        robot_c_i = int(robot_c_idx) - 1

        selected = [robot_a_i, robot_b_i, robot_c_i]

        if len(set(selected)) != 3:
            print("  Each role must use a different robot.")
            return None

        if not all(0 <= i < len(robots_info) for i in selected):
            print("  Invalid robot number.")
            return None

    except ValueError:
        print("  Invalid selection.")
        return None

    robot_a_info = robots_info[robot_a_i]
    robot_b_info = robots_info[robot_b_i]
    robot_c_info = robots_info[robot_c_i]

    def _robot_name(robot):
        return robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))

    def _robot_ip(robot):
        addresses = robot.get("addresses") or []
        return addresses[0] if addresses else robot.get("host", "unknown")

    ip_a = _robot_ip(robot_a_info)
    ip_b = _robot_ip(robot_b_info)
    ip_c = _robot_ip(robot_c_info)

    name_a = _robot_name(robot_a_info)
    name_b = _robot_name(robot_b_info)
    name_c = _robot_name(robot_c_info)

    print("\n  Assignment:")
    print(f"    Bentham-Bot / Left:      {name_a} at {ip_a}")
    print(f"    Korsgaard-Bot / Center:  {name_b} at {ip_b}")
    print(f"    Aquinas-Bot / Right:     {name_c} at {ip_c}")

    confirm = input("\n  Save this assignment to .env? (y/n) [y]: ").strip().lower()

    if confirm == "n":
        print("  Setup cancelled.")
        return None

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    def _set_env(lines: list[str], key: str, value: str) -> list[str]:
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                return lines

        lines.append(f"{key}={value}")
        return lines

    lines = _set_env(lines, "ROBOT_A_HOST", ip_a)
    lines = _set_env(lines, "ROBOT_B_HOST", ip_b)
    lines = _set_env(lines, "ROBOT_C_HOST", ip_c)

    # Keep physical robot names in .env.
    # The discussion names Bentham-Bot / Korsgaard-Bot / Aquinas-Bot are handled in prompts/player display.
    lines = _set_env(lines, "ROBOT_A_NAME", f'"{name_a}"')
    lines = _set_env(lines, "ROBOT_B_NAME", f'"{name_b}"')
    lines = _set_env(lines, "ROBOT_C_NAME", f'"{name_c}"')

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n  .env updated! Reloading config...")

    load_dotenv(env_path, override=True)

    return Config.from_env()


# ── Session picker ─────────────────────────────────────────────────────────

def _pick_session(action: str = "use") -> str | None:
    """Show session picker and return chosen session name, or None to cancel."""
    from pathlib import Path
    from ethics_podcast.prompts import SESSIONS, get_session_names

    names = get_session_names()
    generated = set()

    for name in names:
        if (Path("output") / name / "conversation.json").exists():
            generated.add(name)

    print(f"\n  Available sessions ({action}):")

    for i, name in enumerate(names):
        session = SESSIONS[name]
        status = " [generated]" if name in generated else ""

        print(f"    {i + 1}. {session['title']}{status}")
        print(f"       {session['description']}")

    if action == "play":
        print(f"    {len(names) + 1}. Video Discussion Loop")
        print("       Play each video, then let the three robots discuss it.")
        print(f"    {len(names) + 2}. Multi-Display Exhibit")
        print("       Same loop across 4 screens (big top + 3 mini under robots).")
        print(f"    {len(names) + 3}. Multi-Display Exhibit (Demo, no robots)")
        print("       4-screen UI with computer audio — no robots / no SDK needed.")

    if action == "play" and generated:
        print("    a. Play ALL generated sessions in sequence")

    max_number = len(names) + 3 if action == "play" else len(names)

    pick = input(
        f"\n  Select session (1-{max_number}"
        + (", or 'a' for all" if action == "play" and generated else "")
        + "): "
    ).strip()

    if action == "play" and pick.lower() == "a":
        return "ALL"

    if action == "play" and pick == str(len(names) + 1):
        return "VIDEO_DISCUSSION_LOOP"

    if action == "play" and pick == str(len(names) + 2):
        return "MULTI_DISPLAY_LOOP"

    if action == "play" and pick == str(len(names) + 3):
        return "MULTI_DISPLAY_DEMO"

    try:
        idx = int(pick) - 1

        if 0 <= idx < len(names):
            return names[idx]

    except ValueError:
        pass

    print("  Invalid selection.")
    return None


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        config = Config.from_env()

    except ValueError as e:
        logging.error("Configuration error: %s", e)
        sys.exit(1)

    wake_proc = None

    while True:
        print("\n--- ETHICS PODCAST ---")
        print(
            f"  Bentham-Bot / Left:      {config.robot_a.name or '?'} "
            f"({config.robot_a.host})  voice={config.robot_a.voice}"
        )
        print(
            f"  Korsgaard-Bot / Center:  {config.robot_b.name or '?'} "
            f"({config.robot_b.host})  voice={config.robot_b.voice}"
        )
        print(
            f"  Aquinas-Bot / Right:     {config.robot_c.name or '?'} "
            f"({config.robot_c.host})  voice={config.robot_c.voice}"
        )
        print(f"  Model: {config.openai_model}")

        if wake_proc and wake_proc.poll() is None:
            print("  (Voice activation ON, say 'Mellow, let's start the podcast')")

        print()
        print("0. Setup, Discover & assign robots")
        print("1. Generate Conversation")
        print("2. Play on Robots")
        print("3. Restart & Wake Robots")
        print("4. Exit")

        choice = _input_or_wake("\nSelect (0-4): ", wake_proc)

        if choice == "0":
            try:
                new_config = _setup_robots()

                if new_config:
                    config = new_config
                    print("  Config reloaded with new robot IPs.")

            except KeyboardInterrupt:
                print("\nSetup cancelled.")

            except Exception as e:
                logging.error("Setup failed: %s", e, exc_info=True)

        elif choice == "1":
            from ethics_podcast.generator import run_generate

            try:
                session_name = _pick_session("generate")

                if session_name is None:
                    continue

                exchanges = (
                    input("How many exchanges? (each = Bentham + Korsgaard + Aquinas turn) [5]: ")
                    .strip()
                    or "5"
                )

                n = int(exchanges)

                print(f"  → {n} exchanges = {n * 3} total speaking turns")

                asyncio.run(run_generate(config, n, session_name))

            except KeyboardInterrupt:
                print("\nGeneration interrupted.")

            except Exception as e:
                logging.error("Generation failed: %s", e, exc_info=True)

        elif choice == "2":
            from ethics_podcast.player import run_playback

            _stop_wake_listener(wake_proc)
            wake_proc = None

            try:
                session_name = _pick_session("play")

                if session_name is None:
                    wake_proc = None
                    continue

                # Demo mode forces computer audio — no audio picker needed.
                if session_name == "MULTI_DISPLAY_DEMO":
                    from ethics_podcast.multi_video_discussion import (
                        run_multi_display_discussion_loop,
                    )

                    asyncio.run(
                        run_multi_display_discussion_loop(
                            _web_display_config(config),
                            demo_mode=True,
                        )
                    )

                    wake_proc = None
                    continue

                audio_mode = _pick_audio_mode()
                _print_audio_mode(audio_mode)

                if session_name == "VIDEO_DISCUSSION_LOOP":
                    from ethics_podcast.video_discussion import run_video_discussion_loop

                    asyncio.run(
                        run_video_discussion_loop(
                            config,
                            audio_mode=audio_mode,
                            tv_audio_delay=TV_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=ROBOT_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                        )
                    )

                    wake_proc = None
                    continue

                if session_name == "MULTI_DISPLAY_LOOP":
                    from ethics_podcast.multi_video_discussion import (
                        run_multi_display_discussion_loop,
                    )

                    asyncio.run(
                        run_multi_display_discussion_loop(
                            _web_display_config(config),
                            audio_mode=audio_mode,
                            tv_audio_delay=TV_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=ROBOT_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                        )
                    )

                    wake_proc = None
                    continue

                if session_name == "ALL":
                    from pathlib import Path
                    from ethics_podcast.prompts import get_session_names, SESSIONS

                    generated_sessions = [
                        s for s in get_session_names()
                        if (Path("output") / s / "conversation.json").exists()
                    ]

                    played = 0

                    for session in generated_sessions:
                        played += 1

                        print(f"\n{'=' * 50}")
                        print(f"  Session {played}: {SESSIONS[session]['title']}")
                        print(f"{'=' * 50}")

                        asyncio.run(
                            run_playback(
                                config,
                                session,
                                audio_mode=audio_mode,
                                tv_audio_delay=TV_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                                robot_audio_delay=ROBOT_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                            )
                        )

                        if played < len(generated_sessions):
                            print("\n  Pause between sessions (5s)...")
                            time.sleep(5)

                    if played == 0:
                        print("  No generated sessions found. Generate some first.")

                else:
                    asyncio.run(
                        run_playback(
                            config,
                            session_name,
                            audio_mode=audio_mode,
                            tv_audio_delay=TV_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=ROBOT_AUDIO_DELAY if audio_mode == "hybrid_surround" else 0.0,
                        )
                    )

            except KeyboardInterrupt:
                print("\nPlayback stopped.")
                _emergency_cleanup()

            except Exception as e:
                logging.error("Playback failed: %s", e, exc_info=True)
                _emergency_cleanup()

            wake_proc = None

        elif choice == "3":
            _stop_wake_listener(wake_proc)
            wake_proc = None

            try:
                wake_proc = None

            except KeyboardInterrupt:
                print("\nRestart interrupted.")

            except Exception as e:
                logging.error("Restart failed: %s", e, exc_info=True)

            wake_proc = None

        elif choice == "4":
            _stop_wake_listener(wake_proc)
            print("Goodbye!")
            break

        else:
            print("Invalid selection.")


if __name__ == "__main__":
    main()
