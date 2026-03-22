#!/usr/bin/env python3
"""Phase 0: Verify both Reachy Mini robots are reachable.

Run this BEFORE the main application to confirm connectivity.
Set ROBOT_A_NAME and ROBOT_B_NAME in .env if you have two robots
on the same subnet that need to be addressed individually.

Usage:
    uv run python scripts/test_connectivity.py
"""

import os
import sys
import time

import numpy as np
from dotenv import load_dotenv

load_dotenv()


def test_robot(label: str, robot_name: str | None, host: str | None) -> bool:
    """Connect to a single robot, wiggle antennas, test audio."""
    from reachy_mini import ReachyMini

    print(f"\n{'='*50}")
    print(f"Testing {label} (host={host or 'auto'}, name={robot_name or 'auto'})...")
    print(f"{'='*50}")

    # -- Connection --
    try:
        kwargs: dict = {}
        if host:
            kwargs["host"] = host
            kwargs["connection_mode"] = "network"
        if robot_name:
            kwargs["robot_name"] = robot_name
        robot = ReachyMini(**kwargs)
        print(f"  [OK] Connected: {robot}")
        # Give WebRTC audio pipeline time to fully negotiate
        print("  ... waiting for audio pipeline to establish")
        time.sleep(3)
    except Exception as e:
        print(f"  [FAIL] Connection failed: {e}")
        print("  Troubleshooting:")
        print("    - Is the daemon running? (Wireless: auto-starts; Lite: run reachy-mini-daemon)")
        print("    - Is the robot on the same network as this machine?")
        print("    - If two robots on same subnet, set ROBOT_A_NAME / ROBOT_B_NAME in .env")
        return False

    # -- Antenna wiggle --
    try:
        robot.goto_target(antennas=[0.5, -0.5], duration=0.5)
        time.sleep(0.6)
        robot.goto_target(antennas=[-0.5, 0.5], duration=0.5)
        time.sleep(0.6)
        robot.goto_target(antennas=[0, 0], duration=0.5)
        time.sleep(0.6)
        print("  [OK] Antennas wiggled")
    except Exception as e:
        print(f"  [WARN] Antenna wiggle failed: {e}")

    # -- Audio test (play twice with different pitches to confirm) --
    try:
        sample_rate = robot.media.get_output_audio_samplerate()
        print(f"  Audio output sample rate: {sample_rate} Hz")

        # First tone: 440 Hz (A4)
        duration = 0.8
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        robot.media.push_audio_sample(tone)
        time.sleep(1.5)

        # Second tone: 880 Hz (A5) — confirms speaker is actually playing
        tone2 = (0.5 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        robot.media.push_audio_sample(tone2)
        time.sleep(1.5)

        print(f"  [??] Two tones sent — did you hear them from THIS robot?")
    except Exception as e:
        print(f"  [WARN] Audio test failed: {e}")

    # -- Cleanup --
    try:
        robot.media.close()
    except Exception:
        pass
    robot.client.disconnect()
    print(f"  [OK] {label} disconnected cleanly")
    return True


def main() -> None:
    robot_a_name = os.environ.get("ROBOT_A_NAME")
    robot_a_host = os.environ.get("ROBOT_A_HOST")
    robot_b_name = os.environ.get("ROBOT_B_NAME")
    robot_b_host = os.environ.get("ROBOT_B_HOST")

    ok_a = test_robot("Robot A (Confused Student)", robot_a_name, robot_a_host)
    ok_b = test_robot("Robot B (Ethics Professor)", robot_b_name, robot_b_host)

    print(f"\n{'='*50}")
    print("RESULTS:")
    print(f"  Robot A: {'PASS' if ok_a else 'FAIL'}")
    print(f"  Robot B: {'PASS' if ok_b else 'FAIL'}")
    print(f"{'='*50}")

    if not (ok_a and ok_b):
        print("\nFix connection issues before running the podcast application.")
        sys.exit(1)
    else:
        print("\nBoth robots ready! Run the podcast with: uv run ethics-podcast")


if __name__ == "__main__":
    main()
