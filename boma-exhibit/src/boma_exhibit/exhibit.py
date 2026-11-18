"""The show loop: wait for Play, run one variant, repeat until Stop.

Adapted from the Ethics Podcast exhibit's multi-display loop with the video
stage removed -- this show is not a response to anything, it is the welcome
itself -- and with the variant picker in its place.

Nothing here calls the LLM or the TTS API. Everything was rendered before the
doors opened, so a cold GPU, a down gateway or an expired API key cannot
interrupt a performance.
"""

from __future__ import annotations

import asyncio
import contextlib

from boma_exhibit.config import Config, ROLE_ORDER
from boma_exhibit.mock_robot import connect_mock_robots
from boma_exhibit.player import (
    AudioMode,
    ROLE_LABELS,
    fade_to_rest,
    run_playback_with_connected_robots,
    start_space_key_listener,
)
from boma_exhibit.robot_controller import connect_robots
from boma_exhibit.script_data import TITLE
from boma_exhibit.variants import VariantPicker
from boma_exhibit.web_display import WebDisplayManager

ATTRACT_SUBTITLE = "Three robots. Three languages. One Africa."



def _print_roster(config: Config) -> tuple[str, ...]:
    """Show each seat's live daemon state and return the unreachable roles.

    Worth the couple of seconds: a robot whose daemon has hung still accepts
    TCP on port 8000 but never answers, and the worker would otherwise spend
    around ninety seconds discovering that during startup. Better to say so up
    front, while the operator is still watching the terminal.
    """
    from boma_exhibit.robot_admin import daemon_status

    print("\n  Cast:")
    unreachable = []
    for role in ROLE_ORDER:
        robot = config.robot(role)
        if not config.is_role_active(role):
            print(f"    {ROLE_LABELS[role]:<22} {robot.host:<16} seat empty (voice only)")
            continue
        state = daemon_status(robot.host, timeout=4.0)
        if state is None:
            unreachable.append(role)
            print(f"    {ROLE_LABELS[role]:<22} {robot.host:<16} NOT RESPONDING")
        else:
            print(f"    {ROLE_LABELS[role]:<22} {robot.host:<16} {state.get('state', '?')}")

    if unreachable:
        print(
            f"\n  {len(unreachable)} robot(s) did not answer. The show will still run —\n"
            "  their lines play from the TV at the right stereo position, they just\n"
            "  will not move. Try menu option 3 (Restart Robots); if that does not\n"
            "  help, the daemon is hung and the unit needs an ssh restart:\n"
            "    ssh pollen@<host> 'sudo systemctl restart reachy-mini-daemon.service'"
        )
    return tuple(unreachable)


async def _reset_all(robots: dict) -> None:
    """Put every robot in the lowered idle pose before a run starts."""
    for robot in robots.values():
        try:
            robot.ensure_alive()
            robot.clear_moves()
            robot.queue_power_off_pose(duration=1.0, force=True)
        except Exception:
            pass
        await asyncio.sleep(0.08)


async def _run_one(
    config: Config,
    robots: dict,
    conversation,
    audio_mode: AudioMode,
    display,
) -> bool:
    """Play one variant. Returns True if the operator pressed Stop.

    The run and the Stop button race each other. Stop wins by cancelling the
    playback task, which is what makes the button feel immediate -- waiting for
    the current turn to end would mean up to twelve seconds of a line the
    operator has already decided to cut.
    """
    playback = asyncio.create_task(
        run_playback_with_connected_robots(
            config, robots, conversation, audio_mode=audio_mode, display=display
        )
    )
    stopper = asyncio.create_task(display.wait_for_stop())

    try:
        await asyncio.wait({playback, stopper}, return_when=asyncio.FIRST_COMPLETED)

        if playback.done():
            error = playback.exception()
            if error is not None:
                # One bad run must not end the exhibit. Report it and let the
                # loop move on to the next variant.
                print(f"\n  Run failed: {error}")
                await fade_to_rest(robots.values())
            return False

        playback.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await playback

        print("  Stopping — fading out...")
        await fade_to_rest(robots.values())
        display.show_attract(TITLE, ATTRACT_SUBTITLE)
        return True
    finally:
        stopper.cancel()


async def _sleep_unless_stopped(display, seconds: float) -> None:
    """Hold on the attract screen, but let Stop cut the wait short."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(display.wait_for_stop(), timeout=seconds)


async def run_exhibit(
    config: Config,
    audio_mode: AudioMode = "surround",
    demo_mode: bool = False,
    loop_forever: bool = True,
) -> None:
    """Run the exhibit until interrupted."""
    picker = VariantPicker(config.session_dir, mode=config.loop_mode)
    if not picker.available():
        print(
            f"\nNothing rendered in {config.session_dir}.\n"
            "Run menu option 1 (Generate Variants) first."
        )
        return

    print(f"\nBOMA exhibit — {len(picker.available())} variant(s) rendered")
    # Demo mode has no hardware to check, and the pre-flight costs a few
    # seconds per seat before the display page can even be served.
    unreachable = () if demo_mode else _print_roster(config)

    display = WebDisplayManager(
        host=config.display_web_host,
        port=config.display_web_port,
        speech_bitrate=config.display_speech_bitrate,
        title=TITLE,
        subtitle=ATTRACT_SUBTITLE,
        loop_mode=config.loop_mode,
    )

    # Only the robot-speaker modes need the robot's own audio pipeline. Leaving
    # it off means the workers connect with media_backend="no_media", which
    # skips GStreamer/WebRTC entirely and takes a large chunk of traffic off the
    # shared Wi-Fi that the motion control link depends on.
    use_media = audio_mode in ("robot", "hybrid_surround")

    if demo_mode:
        controllers = connect_mock_robots()
    else:
        # Stand a MockRobot in for anything that failed the pre-flight instead of
        # letting a worker discover it the slow way. A robot whose daemon has
        # hung still completes the TCP handshake, so the worker's reset sequence
        # waits out its full 30s + 60s timeouts before giving up -- per robot,
        # every time the show starts.
        skip = frozenset(config.disabled_roles()) | frozenset(unreachable)
        controllers = await connect_robots(
            *(config.robot(role) for role in ROLE_ORDER),
            use_media=use_media,
            disabled_roles=skip,
        )
    robots = {controller.role: controller for controller in controllers}

    key_stop = asyncio.Event()
    start_space_key_listener(
        asyncio.get_running_loop(), display.toggle_muted, key_stop
    )

    print(
        f"\n  Open http://<this-pc>:{config.display_web_port}/top and press PLAY."
        "\n  Nothing is heard until you do — that click is also what lets the"
        "\n  browser play audio at all.\n"
    )

    # Every run ends with the robots already lowered -- gently, by fade_to_rest
    # after a Stop, or by the last turn's own descent after a natural finish.
    # Re-parking them on top of that is not merely redundant: _reset_all calls
    # clear_moves(), which cuts the in-flight descent a third of the way down
    # and replaces it with a faster one, undoing the fade.
    parked = False

    try:
        while True:
            display.show_attract(TITLE, ATTRACT_SUBTITLE)
            if not parked:
                await _reset_all(robots)

            # Blocks until someone presses Play on the display page. Waiting for
            # a browser connection separately would be redundant: only a browser
            # can send this.
            await display.wait_for_play()
            parked = False

            # The operator can change the loop mode at any point; pick it up
            # here so the change lands on the very next variant.
            picker.set_mode(display.loop_mode)
            conversation = picker.next_conversation()
            if conversation is None:
                print("No variants left to play.")
                break

            stopped = await _run_one(
                config, robots, conversation, audio_mode, display
            )
            if not stopped:
                # Ran to the end. The last speaker is sitting in the listening
                # pose, so settle everyone down for the attract screen -- the
                # same gentle wind-down a Stop gets.
                await fade_to_rest(robots.values())
            parked = True
            if stopped or not loop_forever:
                if not loop_forever:
                    break
                continue

            # Finished on its own: hold the attract screen, then run the next
            # variant. Stop still works during the gap.
            display.show_attract(TITLE, ATTRACT_SUBTITLE)
            await _sleep_unless_stopped(display, config.attract_seconds)

        display.show_attract(TITLE, ATTRACT_SUBTITLE)

    except KeyboardInterrupt:
        print("\nStopping the exhibit...")
    finally:
        key_stop.set()
        await _reset_all(robots)
        for controller in controllers:
            try:
                controller.stop()
            except Exception:
                pass
        display.close()
