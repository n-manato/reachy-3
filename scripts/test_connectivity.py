"""Check that the three robots are reachable and can move.

Runs against the hosts in .env. Motion only -- the exhibit drives the robots
with media_backend="no_media" (all audio comes from the TV), so there is no
robot speaker to test in the configuration the show actually uses.

    uv run python scripts/test_connectivity.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boma_exhibit.config import ROLE_ORDER, load_config
from boma_exhibit.mock_robot import MockRobot
from boma_exhibit.player import ROLE_LABELS
from boma_exhibit.robot_admin import arp_table, daemon_status
from boma_exhibit.robot_controller import connect_robots


async def main() -> int:
    config = load_config()
    macs = arp_table()

    print("\nDaemon status")
    reachable = []
    for role in ROLE_ORDER:
        robot = config.robot(role)
        status = daemon_status(robot.host, timeout=5.0)
        state = str((status or {}).get("state", "NO REPLY"))
        print(
            f"  {ROLE_LABELS[role]:<22} {robot.host:<16} {state:<18} "
            f"{macs.get(robot.host, '?')}"
        )
        if status is not None:
            reachable.append(role)

    if not reachable:
        print("\nNo robot answered. Check the Wi-Fi adapter and the hosts in .env.")
        return 1

    print("\nConnecting workers and moving each robot...")
    controllers = await connect_robots(
        *(config.robot(role) for role in ROLE_ORDER),
        use_media=False,
        disabled_roles=config.disabled_roles(),
    )

    failures = 0
    try:
        for controller in controllers:
            label = ROLE_LABELS.get(controller.role, controller.role)
            # connect_robots substitutes a MockRobot for anything that failed
            # to start. A mock answers every call happily, which is the point
            # during a show and exactly wrong here -- this script exists to say
            # whether the hardware works.
            if isinstance(controller, MockRobot):
                print(f"  {label:<22} NOT CONNECTED (running as a mock)")
                failures += 1
                continue

            if not controller.is_alive():
                print(f"  {label:<22} NOT RUNNING ({controller.diagnostic_status()})")
                failures += 1
                continue

            print(f"  {label:<22} antennas + head...")
            controller.wiggle_antennas()
            await asyncio.sleep(1.2)
            controller.queue_speaking_ready_pose(duration=0.8, force=True)
            await asyncio.sleep(1.0)
            controller.queue_goto(head=(0, 0, 0, 0, 0, 25), duration=0.8)
            await asyncio.sleep(1.0)
            controller.queue_goto(head=(0, 0, 0, 0, 0, -25), duration=0.8)
            await asyncio.sleep(1.0)
            controller.queue_power_off_pose(duration=1.0, force=True)
            await asyncio.sleep(1.0)
    finally:
        for controller in controllers:
            try:
                controller.stop()
            except Exception:
                pass

    if failures:
        print(
            f"\n{failures} of {len(controllers)} robot(s) did not come up.\n"
            "The exhibit will still run without them -- their lines play from the\n"
            "TV at the right stereo position -- but they will not move."
        )
        return 1

    print("\nAll robots responded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
