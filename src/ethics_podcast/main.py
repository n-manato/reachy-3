"""Entry point for the Ethics Podcast application.

Three-robot version.

Robots:
  - Hello  = Bentham-Bot
  - Mellow = Korsgaard-Bot
  - Fellow = Aquinas-Bot

Phases:
  0. Setup — discover robots, assign roles, and refresh IPs
  1. Generate — runs the AI conversation, saves audio + transcripts to disk
  2. Play    — plays the saved conversation through the robots
  3. Restart Robots — stops daemons, starts them again, wakes robots
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

def _env_path() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent / ".env"


def _set_env(lines: list[str], key: str, value: str) -> list[str]:
    """Replace KEY=... in place, or append it if missing."""
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return lines

    lines.append(f"{key}={value}")
    return lines


def _discover_robots() -> list[dict]:
    """Find Reachy Mini robots on the network.

    Tries mDNS discovery first, then falls back to scanning the local /24
    subnet for daemons on port 8000. Returns a list of dicts shaped like
    {name, host, addresses, props}.
    """
    import subprocess as sp
    import concurrent.futures
    import json
    import socket

    def _local_subnets() -> list[str]:
        """Return every local IPv4 /24 prefix (e.g. '192.168.137') this PC has.

        Robots may live on a different interface than the default route (e.g.
        the Windows mobile hotspot at 192.168.137.x while the internet goes
        out over campus Wi-Fi), so we scan all of them, not just the route to
        8.8.8.8.
        """
        prefixes: list[str] = []

        def _add(ip: str) -> None:
            parts = ip.split(".")
            if len(parts) == 4 and not ip.startswith("127."):
                prefix = ".".join(parts[:3])
                if prefix not in prefixes:
                    prefixes.append(prefix)

        # Default-route interface first (most likely to also host robots).
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # no traffic sent; picks an interface
            _add(s.getsockname()[0])
            s.close()
        except Exception:
            pass

        # All addresses bound to this host (covers hotspot, Ethernet, etc.).
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                _add(info[4][0])
        except Exception:
            pass

        return prefixes

    print("\n  Scanning network for Reachy Mini robots...")

    robots_info: list[dict] = []
    seen_ips: set[str] = set()

    def _add_robot(robot: dict) -> None:
        ip = _robot_ip(robot)
        if ip and ip not in seen_ips:
            seen_ips.add(ip)
            robots_info.append(robot)

    # 1) Try the SDK's discovery first.
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

    if result.returncode == 0 and result.stdout.strip():
        try:
            for robot in json.loads(result.stdout.strip()):
                _add_robot(robot)
        except Exception:
            pass

    # 2) Always scan local subnets too, because mDNS often misses some robots.
    subnets = _local_subnets()

    if not subnets:
        print("  Could not detect this computer's local subnet.")
        print("  Falling back to 10.25.31.0/24.")
        subnets = ["10.25.31"]
    else:
        joined = ", ".join(f"{s}.0/24" for s in subnets)
        print(f"  Scanning {joined} for daemons...")

    def _check(prefix: str, ip_num: int):
        ip = f"{prefix}.{ip_num}"

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        futures = [
            pool.submit(_check, prefix, i)
            for prefix in subnets
            for i in range(1, 255)
        ]

        for future in concurrent.futures.as_completed(futures):
            robot = future.result()

            if robot:
                _add_robot(robot)

    return robots_info


def _robot_name(robot: dict) -> str:
    return robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))


def _robot_ip(robot: dict) -> str:
    addresses = robot.get("addresses") or []
    return addresses[0] if addresses else robot.get("host", "unknown")


def _print_discovered_robot_dashboards(robots_info: list[dict]) -> None:
    """Print dashboard URLs for robots found during discovery."""
    print("\n  Detected robot dashboards:")

    for i, robot in enumerate(robots_info):
        ip = _robot_ip(robot)
        name = _robot_name(robot)
        print(f"    {i + 1}. {name}: http://{ip}:8000")


def _restart_robots(config: Config) -> None:
    """Restart configured robot daemons and print status URLs."""
    import concurrent.futures

    targets = [
        ("Bentham-Bot / Left", config.robot_a.name, config.robot_a.host),
        ("Korsgaard-Bot / Center", config.robot_b.name, config.robot_b.host),
        ("Aquinas-Bot / Right", config.robot_c.name, config.robot_c.host),
    ]

    by_host: dict[str, list[tuple[str, str | None]]] = {}
    for label, name, host in targets:
        by_host.setdefault(host, []).append((label, name))

    print("\n  Restarting configured robots:")
    for host, roles in by_host.items():
        role_text = ", ".join(
            f"{label} ({name or '?'})" for label, name in roles
        )
        print(f"    {role_text}: http://{host}:8000")
        if len(roles) > 1:
            print(f"      WARNING: multiple roles use the same host {host}; restarting once.")

    def _run_one(host: str) -> tuple[str, bool, str]:
        base = f"http://{host}:8000/api"

        try:
            status = requests.get(f"{base}/daemon/status", timeout=5)
            status.raise_for_status()

            stop_resp = requests.post(
                f"{base}/daemon/stop",
                params={"goto_sleep": "true"},
                timeout=10,
            )
            if stop_resp.status_code != 409:
                stop_resp.raise_for_status()

            stop_deadline = time.monotonic() + 30.0
            while True:
                try:
                    status = requests.get(f"{base}/daemon/status", timeout=5)
                    if status.status_code == 200:
                        state = str((status.json() or {}).get("state", "unknown"))
                        if state in ("stopped", "not_initialized"):
                            break
                except Exception:
                    pass

                if time.monotonic() >= stop_deadline:
                    break

                time.sleep(1.0)

            time.sleep(2.0)

            start_resp = requests.post(
                f"{base}/daemon/start",
                params={"wake_up": "true"},
                timeout=10,
            )
            if start_resp.status_code != 409:
                start_resp.raise_for_status()

            time.sleep(2.0)

            media_resp = requests.post(f"{base}/media/acquire", timeout=10)
            if media_resp.status_code != 409:
                media_resp.raise_for_status()

            deadline = time.monotonic() + 60.0
            motor_text = "unknown"
            media_text = "unknown"

            while True:
                try:
                    motors = requests.get(f"{base}/motors/status", timeout=5)
                    if motors.status_code == 200:
                        motor_text = str(
                            (motors.json() or {}).get("mode", "unknown")
                        )
                        break
                except Exception:
                    pass

                if time.monotonic() >= deadline:
                    break

                time.sleep(1.0)

            requests.post(
                f"{base}/motors/set_mode/enabled",
                timeout=5,
            ).raise_for_status()
            time.sleep(3.0)

            status = requests.get(f"{base}/daemon/status", timeout=5)
            status.raise_for_status()
            state = str((status.json() or {}).get("state", "unknown"))

            motors = requests.get(f"{base}/motors/status", timeout=5)
            if motors.status_code == 200:
                motor_text = str((motors.json() or {}).get("mode", "unknown"))

            media_status = requests.get(f"{base}/media/status", timeout=5)
            if media_status.status_code == 200:
                media = media_status.json() or {}
                media_text = (
                    f"available={media.get('available')}, "
                    f"released={media.get('released')}, "
                    f"no_media={media.get('no_media')}"
                )

            return host, True, f"daemon={state}, motors={motor_text}, media={media_text}"
        except Exception as e:
            return host, False, str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(by_host)) as pool:
        futures = [pool.submit(_run_one, host) for host in by_host]

        for future in concurrent.futures.as_completed(futures):
            host, ok, detail = future.result()
            prefix = "OK" if ok else "FAILED"
            print(f"    {prefix}: http://{host}:8000 ({detail})")


def _auto_assign_robots(config: Config) -> Config | None:
    """Discover robots and refresh their IPs in .env, no manual identification.

    Keeps the existing role→robot assignment by matching each robot's name
    (ROBOT_A/B/C_NAME already in .env) to a discovered robot, then rewrites
    only the host IPs. Any role whose name is not matched is filled, in
    discovery order, from the leftover robots. Prints each robot's daemon
    dashboard URL when done.
    """
    from dotenv import load_dotenv

    env_path = _env_path()
    robots_info = _discover_robots()

    if not robots_info:
        print("  No robots found. Make sure they are powered on and on this network.")
        return None

    # Map discovered robots by (lowercased) name.
    by_name: dict[str, dict] = {}
    for robot in robots_info:
        name = _robot_name(robot)
        if name and name != "unknown":
            by_name[name.strip().lower()] = robot

    targets = [
        ("ROBOT_A_HOST", config.robot_a.name, "Bentham-Bot / Left"),
        ("ROBOT_B_HOST", config.robot_b.name, "Korsgaard-Bot / Center"),
        ("ROBOT_C_HOST", config.robot_c.name, "Aquinas-Bot / Right"),
    ]

    resolved: dict[str, str] = {}
    used_ips: set[str] = set()

    # Pass 1: match each role to a robot by name.
    for key, name, _label in targets:
        nl = (name or "").strip().lower()
        if nl and nl in by_name:
            ip = _robot_ip(by_name[nl])
            resolved[key] = ip
            used_ips.add(ip)

    # Pass 2: fill unmatched roles from leftover robots (discovery order).
    leftover = [
        _robot_ip(r) for r in robots_info if _robot_ip(r) not in used_ips
    ]
    for key, name, label in targets:
        if key not in resolved and leftover:
            ip = leftover.pop(0)
            resolved[key] = ip
            used_ips.add(ip)
            print(f"    {label}: name '{name}' not found; assigned {ip} by order.")

    if not resolved:
        print("  Could not resolve any robot IPs.")
        return None

    # Write the refreshed IPs to .env.
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    for key, ip in resolved.items():
        lines = _set_env(lines, key, ip)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_dotenv(env_path, override=True)

    new_config = Config.from_env()

    print("\n  .env updated. Robot dashboards:")
    print(f"    Bentham-Bot / Left:      http://{new_config.robot_a.host}:8000")
    print(f"    Korsgaard-Bot / Center:  http://{new_config.robot_b.host}:8000")
    print(f"    Aquinas-Bot / Right:     http://{new_config.robot_c.host}:8000")

    return new_config


def _setup_robots() -> Config | None:
    """Discover robots on the network, identify them, and write IPs to .env."""
    from dotenv import load_dotenv

    env_path = _env_path()

    robots_info = _discover_robots()

    if not robots_info:
        print("  No robots found. Make sure they are powered on and connected to this network.")
        return None

    print(f"  Found {len(robots_info)} robots:")

    for i, robot in enumerate(robots_info):
        addresses = robot.get("addresses") or []
        ip = addresses[0] if addresses else robot.get("host", "unknown")
        name = robot.get("props", {}).get("robot_name", robot.get("name", "unknown"))

        print(f"    {i + 1}. {name} at {ip}")

    _print_discovered_robot_dashboards(robots_info)

    if len(robots_info) < 3:
        print(f"\n  Found only {len(robots_info)} robot(s). Need 3 to assign all podcast roles.")
        print("  Check the missing robots, then run setup again.")
        return None

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

    new_config = Config.from_env()

    print("\n  Robot dashboards:")
    print(f"    Bentham-Bot / Left:      http://{new_config.robot_a.host}:8000")
    print(f"    Korsgaard-Bot / Center:  http://{new_config.robot_b.host}:8000")
    print(f"    Aquinas-Bot / Right:     http://{new_config.robot_c.host}:8000")

    return new_config


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

    if action == "play" and generated:
        print("    a. Play ALL generated sessions in sequence")

    max_number = len(names) + 2 if action == "play" else len(names)

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
        print("0. Setup, Discover, assign & refresh robots")
        print("1. Generate Conversation")
        print("2. Play on Robots")
        print("3. Restart Robots")
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

                audio_mode = _pick_audio_mode()
                _print_audio_mode(audio_mode)

                if session_name == "VIDEO_DISCUSSION_LOOP":
                    from ethics_podcast.video_discussion import run_video_discussion_loop

                    asyncio.run(
                        run_video_discussion_loop(
                            config,
                            audio_mode=audio_mode,
                            tv_audio_delay=config.tv_audio_delay if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=config.robot_audio_delay if audio_mode == "hybrid_surround" else 0.0,
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
                            tv_audio_delay=config.tv_audio_delay if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=config.robot_audio_delay if audio_mode == "hybrid_surround" else 0.0,
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
                                tv_audio_delay=config.tv_audio_delay if audio_mode == "hybrid_surround" else 0.0,
                                robot_audio_delay=config.robot_audio_delay if audio_mode == "hybrid_surround" else 0.0,
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
                            tv_audio_delay=config.tv_audio_delay if audio_mode == "hybrid_surround" else 0.0,
                            robot_audio_delay=config.robot_audio_delay if audio_mode == "hybrid_surround" else 0.0,
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
                _restart_robots(config)

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
