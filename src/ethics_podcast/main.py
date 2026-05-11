"""Entry point for the Ethics Podcast application.

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

import requests

from ethics_podcast.config import Config


# ── Wake word listener (subprocess) ─────────────────────────────────────

def _start_wake_listener(config: Config) -> subprocess.Popen | None:
    """Launch wake_word.py as a subprocess listening on Robot A's mic."""
    worker_path = os.path.join(os.path.dirname(__file__), "wake_word.py")
    cmd = [sys.executable, worker_path, config.robot_a.host]
    if config.robot_a.name:
        cmd.append(config.robot_a.name)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=None, env={**os.environ},
        )
        # Wait for LISTENING signal (up to 30s for WebRTC setup)
        line = proc.stdout.readline().decode().strip()
        if line == "LISTENING":
            logging.info("Wake word listener active on %s", config.robot_a.host)
            return proc
        else:
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
    """Read from stdin, but also return '2' if wake word subprocess fires."""
    if wake_proc is None:
        return input(prompt).strip()

    print(prompt, end="", flush=True)
    while True:
        # Check if wake word subprocess printed "WAKE"
        if wake_proc.poll() is not None:
            # Process exited — read whatever it wrote
            line = wake_proc.stdout.readline().decode().strip()
            if line == "WAKE":
                print("\n  [Voice activated!]")
                return "2"
            return input("").strip()  # subprocess died, fall back to keyboard

        # Non-blocking check on both stdin and wake subprocess stdout
        readers = [sys.stdin, wake_proc.stdout]
        ready, _, _ = select.select(readers, [], [], 0.5)
        for r in ready:
            if r is sys.stdin:
                return sys.stdin.readline().strip()
            if r is wake_proc.stdout:
                line = wake_proc.stdout.readline().decode().strip()
                if line == "WAKE":
                    print("\n  [Voice activated!]")
                    return "2"


def _setup_robots() -> Config | None:
    """Discover robots on the network, identify them, and write IPs to .env."""
    from pathlib import Path
    import subprocess as sp
    import os

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"

    print("\n  Scanning network for Reachy Mini robots...")

    # Use a subprocess to avoid GStreamer init in the main process
    result = sp.run(
        ["uv", "run", "python", "-c",
         "from reachy_mini.utils.discovery import find_robots\n"
         "import json\n"
         "robots = find_robots(timeout=8.0)\n"
         "print(json.dumps([{'name': r.name, 'host': r.host, 'addresses': r.addresses, 'props': r.properties} for r in robots]))\n"],
        capture_output=True, text=True, timeout=20, env={**os.environ},
    )

    if result.returncode != 0 or not result.stdout.strip():
        # Fallback: scan common subnet
        print("  mDNS discovery failed. Scanning 192.168.0.0/24 for daemons...")
        found = []
        import concurrent.futures

        def _check(ip):
            try:
                r = requests.get(f"http://192.168.0.{ip}:8000/api/daemon/status", timeout=1.5)
                if r.status_code == 200:
                    data = r.json()
                    return {"ip": f"192.168.0.{ip}", "name": data.get("robot_name", "unknown")}
            except Exception:
                pass
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            futures = {pool.submit(_check, i): i for i in range(1, 255)}
            for f in concurrent.futures.as_completed(futures):
                r = f.result()
                if r:
                    found.append(r)

        if not found:
            print("  No robots found on the network.")
            return None

        robots_info = [{"name": r["name"], "addresses": [r["ip"]]} for r in found]
    else:
        import json
        robots_info = json.loads(result.stdout.strip())

    if len(robots_info) < 2:
        print(f"  Found only {len(robots_info)} robot(s). Need 2 for the podcast.")
        if robots_info:
            print(f"    Found: {robots_info[0]['name']} at {robots_info[0]['addresses']}")
        print("  Make sure both robots are powered on and connected to the network.")
        return None

    print(f"  Found {len(robots_info)} robots:")
    for i, r in enumerate(robots_info):
        ip = r["addresses"][0] if r["addresses"] else "unknown"
        print(f"    {i + 1}. {r['name']} at {ip}")

    # Wake up all found robots first so heads are up
    print("\n  Waking up robots (heads will rise)...")
    for r in robots_info[:2]:
        ip = r["addresses"][0]
        try:
            # Enable motors
            requests.post(f"http://{ip}:8000/api/motors/set_mode/enabled", timeout=5)
            # Play wake_up animation (head rises smoothly)
            requests.post(f"http://{ip}:8000/api/move/play/wake_up", timeout=5)
        except Exception as e:
            print(f"    Wake-up failed for {ip}: {e}")
    print("  Waiting for wake-up animation...")
    time.sleep(5)

    # Now identify which is which by wiggling antennas one at a time
    print("\n  Let's identify which is the Student and which is the Professor.")
    for i, r in enumerate(robots_info[:2]):
        ip = r["addresses"][0]
        input(f"\n  Press Enter to wiggle robot {i + 1} at {ip} ({r['name']})...")
        try:
            for _ in range(3):
                requests.post(f"http://{ip}:8000/api/move/set_target",
                    json={"antennas": [0.5, -0.5]}, timeout=3)
                time.sleep(0.4)
                requests.post(f"http://{ip}:8000/api/move/set_target",
                    json={"antennas": [-0.5, 0.5]}, timeout=3)
                time.sleep(0.4)
            requests.post(f"http://{ip}:8000/api/move/set_target",
                json={"antennas": [0, 0]}, timeout=3)
            print(f"    Robot {i + 1} wiggled!")
        except Exception as e:
            print(f"    Could not wiggle: {e}")

    ip_a = robots_info[0]["addresses"][0]
    ip_b = robots_info[1]["addresses"][0]
    name_a = robots_info[0].get("props", {}).get("robot_name", robots_info[0]["name"])
    name_b = robots_info[1].get("props", {}).get("robot_name", robots_info[1]["name"])

    print(f"\n  Robot 1: {name_a} at {ip_a}")
    print(f"  Robot 2: {name_b} at {ip_b}")
    swap = input("\n  Is Robot 1 the Student? (y/n) [y]: ").strip().lower()
    if swap == "n":
        ip_a, ip_b = ip_b, ip_a
        name_a, name_b = name_b, name_a
        print(f"  Swapped! Student={name_a} ({ip_a}), Professor={name_b} ({ip_b})")
    else:
        print(f"  OK! Student={name_a} ({ip_a}), Professor={name_b} ({ip_b})")

    # Write to .env — update only the robot host/name lines
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        lines = []

    def _set_env(lines, key, value):
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                return lines
        lines.append(f"{key}={value}")
        return lines

    lines = _set_env(lines, "ROBOT_A_HOST", ip_a)
    lines = _set_env(lines, "ROBOT_B_HOST", ip_b)
    lines = _set_env(lines, "ROBOT_A_NAME", f'"{name_a}"')
    lines = _set_env(lines, "ROBOT_B_NAME", f'"{name_b}"')

    env_path.write_text("\n".join(lines) + "\n")
    print(f"\n  .env updated! Reloading config...")

    # Reload config
    from dotenv import load_dotenv
    load_dotenv(env_path, override=True)
    return Config.from_env()


def _restart_and_wake(config: Config) -> None:
    """Restart both daemons, wake up robots, verify audio is ready."""
    hosts = [
        (config.robot_a.host, config.robot_a.name or "Robot A"),
        (config.robot_b.host, config.robot_b.name or "Robot B"),
    ]

    import subprocess as sp

    # Step 1: Hard restart daemons via SSH (kills process, clears all WebRTC state)
    print("  Restarting daemons via SSH...")
    for host, name in hosts:
        print(f"    {name} ({host})...")
        try:
            sp.run(
                ["sshpass", "-p", "root", "ssh", "-o", "StrictHostKeyChecking=no",
                 f"pollen@{host}", "sudo systemctl restart reachy-mini-daemon.service"],
                timeout=15, capture_output=True,
            )
        except Exception as e:
            print(f"    SSH restart failed for {name}: {e}")

    # Step 2: Wait for daemons to come up
    print("  Waiting for daemons to start (30s)...")
    time.sleep(30)

    for host, name in hosts:
        try:
            r = requests.get(f"http://{host}:8000/api/daemon/status", timeout=3)
            print(f"    {name}: state={r.json().get('state')}")
        except Exception:
            print(f"    {name}: not responding yet")

    # Step 3: Enable motors directly (DO NOT use stop/start — it breaks WebRTC)
    print("  Enabling motors...")
    for host, name in hosts:
        try:
            requests.post(f"http://{host}:8000/api/motors/set_mode/enabled", timeout=5)
        except Exception as e:
            print(f"    Motor enable failed for {name}: {e}")
    time.sleep(2)

    # Step 4: Verify motors
    all_ready = True
    for host, name in hosts:
        try:
            m = requests.get(f"http://{host}:8000/api/motors/status", timeout=3)
            mode = m.json().get("mode")
            print(f"    {name}: motors={mode}")
            if mode != "enabled":
                all_ready = False
        except Exception:
            print(f"    {name}: unreachable")
            all_ready = False
    if all_ready:
        print("  Motors enabled on both robots!")

    if not all_ready:
        for host, name in hosts:
            try:
                r = requests.get(f"http://{host}:8000/api/daemon/status", timeout=3)
                m = requests.get(f"http://{host}:8000/api/motors/status", timeout=3)
                print(f"  {name}: state={r.json().get('state')}, motors={m.json().get('mode')}")
            except Exception:
                print(f"  {name}: unreachable")

    # Step 5: Wait for WebRTC audio producers (can take 30+ seconds after daemon start)
    print("  Waiting for WebRTC audio producers...")
    import subprocess as sp
    import os

    for attempt in range(20):
        time.sleep(3)
        all_have_producers = True
        for host, name in hosts:
            try:
                result = sp.run(
                    ["uv", "run", "python", "-c",
                     f"from reachy_mini.media.webrtc_utils import get_producer_list; "
                     f"p = get_producer_list('{host}', 8443); "
                     f"print(len(p))"],
                    capture_output=True, text=True, timeout=10, env={**os.environ},
                )
                count = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
                if count == 0:
                    all_have_producers = False
            except Exception:
                all_have_producers = False

        if all_have_producers:
            print("  Both robots have WebRTC producers ready!")
            break
        else:
            print(f"    Attempt {attempt + 1}/20 — producers not ready yet...")
    else:
        print("  WARNING: WebRTC producers may not be ready. Audio might not work.")

    # Final status summary
    print("\n  Final status:")
    for host, name in hosts:
        try:
            r = requests.get(f"http://{host}:8000/api/daemon/status", timeout=3)
            m = requests.get(f"http://{host}:8000/api/motors/status", timeout=3)
            print(f"    {name}: state={r.json().get('state')}, motors={m.json().get('mode')}")
        except Exception:
            print(f"    {name}: unreachable")

    print("\n  Done! Use option 2 to play.")


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

    if action == "play" and generated:
        print(f"    a. Play ALL generated sessions in sequence")

    pick = input(f"\n  Select session (1-{len(names)}" + (", or 'a' for all" if action == "play" and generated else "") + "): ").strip()

    if action == "play" and pick.lower() == "a":
        return "ALL"

    try:
        idx = int(pick) - 1
        if 0 <= idx < len(names):
            return names[idx]
    except ValueError:
        pass

    print("  Invalid selection.")
    return None


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

    # Start wake word listener (subprocess on Robot A's mic)
    wake_proc = _start_wake_listener(config)

    while True:
        print("\n--- ETHICS PODCAST ---")
        print(f"  Student: {config.robot_a.name or '?'} ({config.robot_a.host})  voice={config.robot_a.voice}")
        print(f"  Professor: {config.robot_b.name or '?'} ({config.robot_b.host})  voice={config.robot_b.voice}")
        print(f"  Model: {config.openai_model}")
        if wake_proc and wake_proc.poll() is None:
            print("  (Voice activation ON — say 'Mello, let's start the podcast')")
        print()
        print("0. Setup — Discover & assign robots")
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
            from ethics_podcast.prompts import SESSIONS, get_session_names
            try:
                session_name = _pick_session("generate")
                if session_name is None:
                    continue
                exchanges = input("How many exchanges? (each = student + professor turn) [5]: ").strip() or "5"
                n = int(exchanges)
                print(f"  → {n} exchanges = {n * 2} total speaking turns")
                asyncio.run(run_generate(config, n, session_name))
            except KeyboardInterrupt:
                print("\nGeneration interrupted.")
            except Exception as e:
                logging.error("Generation failed: %s", e, exc_info=True)

        elif choice == "2":
            from ethics_podcast.player import run_playback
            _stop_wake_listener(wake_proc)  # free WebRTC before player connects
            wake_proc = None
            try:
                session_name = _pick_session("play")
                if session_name is None:
                    wake_proc = _start_wake_listener(config)
                    continue

                audio_choice = input("  Audio output — (1) Robot speakers  (2) Laptop speaker [1]: ").strip()
                laptop_audio = audio_choice == "2"
                if laptop_audio:
                    print("  → Audio on laptop. Emotions & movements still go to robots.")

                if session_name == "ALL":
                    from pathlib import Path
                    from ethics_podcast.prompts import get_session_names, SESSIONS
                    played = 0
                    for sn in get_session_names():
                        if (Path("output") / sn / "conversation.json").exists():
                            played += 1
                            print(f"\n{'='*50}")
                            print(f"  Session {played}: {SESSIONS[sn]['title']}")
                            print(f"{'='*50}")
                            asyncio.run(run_playback(config, sn, laptop_audio=laptop_audio))
                            if played < len([s for s in get_session_names() if (Path("output") / s / "conversation.json").exists()]):
                                print("\n  Pause between sessions (5s)...")
                                time.sleep(5)
                    if played == 0:
                        print("  No generated sessions found. Generate some first.")
                else:
                    asyncio.run(run_playback(config, session_name, laptop_audio=laptop_audio))
            except KeyboardInterrupt:
                print("\nPlayback stopped.")
            except Exception as e:
                logging.error("Playback failed: %s", e, exc_info=True)
            wake_proc = _start_wake_listener(config)  # resume listening

        elif choice == "3":
            _stop_wake_listener(wake_proc)  # free WebRTC before daemon restart
            wake_proc = None
            try:
                _restart_and_wake(config)
            except KeyboardInterrupt:
                print("\nRestart interrupted.")
            except Exception as e:
                logging.error("Restart failed: %s", e, exc_info=True)
            wake_proc = _start_wake_listener(config)  # resume listening

        elif choice == "4":
            _stop_wake_listener(wake_proc)
            print("Goodbye!")
            break
        else:
            print("Invalid selection.")


if __name__ == "__main__":
    main()
