#!/usr/bin/env python3
"""Phase 0: Verify three Reachy Mini robots are reachable.

This script tests:
  - connection
  - wake up
  - face/head movement
  - antenna movement
  - audio output

Usage:
    python scripts/test_connectivity.py
    uv run python scripts/test_connectivity.py
"""

import os
import sys
import time

import numpy as np
from dotenv import load_dotenv

load_dotenv()


def safe_disconnect(robot) -> None:
    """Disconnect robot safely."""
    try:
        robot.media.close()
    except Exception:
        pass

    try:
        robot.client.disconnect()
    except Exception:
        pass


def wake_and_prepare_robot(robot) -> None:
    """Wake up robot and move face/head to a usable neutral position."""

    # Wake up robot
    try:
        print("  Waking up robot...")
        robot.wake_up()
        time.sleep(2)
        print("  [OK] Wake up command sent")
    except Exception as e:
        print(f"  [WARN] Wake up failed or not supported: {e}")

    # Move head/face to neutral or slightly raised position.
    # If the face goes DOWN instead of UP, change -0.3 to 0.3.
    try:
        print("  Moving face/head to start position...")
        robot.goto_target(
            head=[0.0, -0.3, 0.0],
            antennas=[0.0, 0.0],
            duration=1.0,
        )
        time.sleep(1.2)
        print("  [OK] Face/head moved to start position")
    except Exception as e:
        print(f"  [WARN] Start face/head movement failed: {e}")


def test_face_head(robot) -> None:
    """Test face/head movement."""

    try:
        print("  Testing face/head movement...")

        # Look slightly up/front
        robot.goto_target(head=[0.0, -0.3, 0.0], duration=1.0)
        time.sleep(1.2)

        # Look left
        robot.goto_target(head=[0.25, -0.3, 0.0], duration=0.7)
        time.sleep(0.9)

        # Look right
        robot.goto_target(head=[-0.25, -0.3, 0.0], duration=0.7)
        time.sleep(0.9)

        # Look center
        robot.goto_target(head=[0.0, -0.3, 0.0], duration=0.7)
        time.sleep(0.9)

        print("  [OK] Face/head moved")

    except Exception as e:
        print(f"  [WARN] Face/head movement failed: {e}")
        print("         If the robot is connected but face does not move,")
        print("         try changing head=[0.0, -0.3, 0.0] to head=[0.0, 0.3, 0.0].")


def test_antennas(robot) -> None:
    """Test antenna movement."""

    try:
        print("  Testing antennas...")

        robot.goto_target(antennas=[0.5, -0.5], duration=0.5)
        time.sleep(0.6)

        robot.goto_target(antennas=[-0.5, 0.5], duration=0.5)
        time.sleep(0.6)

        robot.goto_target(antennas=[0.0, 0.0], duration=0.5)
        time.sleep(0.6)

        print("  [OK] Antennas wiggled")

    except Exception as e:
        print(f"  [WARN] Antenna movement failed: {e}")


def test_audio(robot) -> None:
    """Test robot speaker audio."""

    try:
        print("  Testing audio...")

        sample_rate = robot.media.get_output_audio_samplerate()
        print(f"  Audio output sample rate: {sample_rate} Hz")

        duration = 0.8
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

        # First tone: 440 Hz
        tone1 = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        robot.media.push_audio_sample(tone1)
        time.sleep(1.5)

        # Second tone: 880 Hz
        tone2 = (0.5 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        robot.media.push_audio_sample(tone2)
        time.sleep(1.5)

        print("  [?] Two tones sent. Did you hear them from THIS robot?")

    except Exception as e:
        print(f"  [WARN] Audio test failed: {e}")


def test_robot(label: str, robot_name: str | None, host: str | None) -> bool:
    """Connect to one robot and run all tests."""

    from reachy_mini import ReachyMini

    print(f"\n{'=' * 50}")
    print(f"Testing {label}")
    print(f"host={host or 'auto'}, name={robot_name or 'auto'}")
    print(f"{'=' * 50}")

    if not host:
        print("  [FAIL] No host set in .env")
        return False

    # -- Connection --
    try:
        kwargs: dict = {
            "host": host,
            "connection_mode": "network",
        }

        if robot_name:
            kwargs["robot_name"] = robot_name

        robot = ReachyMini(**kwargs)
        print(f"  [OK] Connected: {robot}")

    except Exception as e:
        print(f"  [FAIL] Connection failed: {e}")
        print("  Troubleshooting:")
        print("    - Is the robot awake?")
        print("    - Is the robot on the same network as this PC?")
        print("    - Is the IP address correct in .env?")
        print("    - Is the daemon running?")
        return False

    try:
        # Prepare robot before movement tests
        wake_and_prepare_robot(robot)

        # Give audio pipeline time to start
        print("  ... waiting for audio pipeline to establish")
        time.sleep(3)

        # Movement and audio tests
        test_face_head(robot)
        test_antennas(robot)
        test_audio(robot)

    finally:
        safe_disconnect(robot)

    print(f"  [OK] {label} disconnected cleanly")
    return True


def main() -> None:
    robots = [
        (
            "Robot A (Hello / Confused Student)",
            os.environ.get("ROBOT_A_NAME"),
            os.environ.get("ROBOT_A_HOST"),
        ),
        (
            "Robot B (Mellow / Ethics Professor)",
            os.environ.get("ROBOT_B_NAME"),
            os.environ.get("ROBOT_B_HOST"),
        ),
        (
            "Robot C (Fellow / Third Robot)",
            os.environ.get("ROBOT_C_NAME"),
            os.environ.get("ROBOT_C_HOST"),
        ),
    ]

    results = []

    for label, name, host in robots:
        ok = test_robot(label, name, host)
        results.append((label, ok))

    print(f"\n{'=' * 50}")
    print("RESULTS:")
    for label, ok in results:
        print(f"  {label}: {'PASS' if ok else 'FAIL'}")
    print(f"{'=' * 50}")

    if not all(ok for _, ok in results):
        print("\nSome robots failed. Fix connection or movement issues before running the podcast application.")
        sys.exit(1)

    print("\nAll robots ready! Run the podcast with:")
    print("  uv run ethics-podcast")


if __name__ == "__main__":
    main()