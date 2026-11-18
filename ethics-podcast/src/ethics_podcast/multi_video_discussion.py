"""Multi-display video discussion loop.

Same overall flow as video_discussion.run_video_discussion_loop, but drives
the four-window DisplayManager (or its browser twin, WebDisplayManager)
instead of the single ControlledVideoWindow:

  1. Top display plays each video.
  2. When the video ends, the top display shows the topic huge and a gaze
     flow guides the audience toward the first robot.
  3. Each robot speaks; its mini display shows subtitles, the others dim.
  4. On each turn end, gaze guidance flows toward the next robot.
  5. Move to the next video.

With the web backend the loop is driven from the /top page's transport bar
(ported from the BOMA exhibit): it starts on the attract card and waits for
PLAY, STOP cancels the running item and settles the robots, the loop
button picks the order of the videos (ALL / SHUFFLE / ONE), and the voice
switch sends the robots' voices to the ROBOT speakers or the BROWSER (the
configured audio mode is its start value). The Qt backend
has no controls and runs straight through, as it always did; it follows the
LOOP_MODE start value.

The discussion generation / robot reset / intro helpers are reused as-is from
video_discussion so behaviour stays consistent with the legacy mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import random
import traceback

from ethics_podcast.config import audio_output_for_mode
from ethics_podcast.player import (
    AudioMode,
    run_playback_with_connected_robots,
    start_space_key_listener,
    _diag,
    _robot_diag,
)
from ethics_podcast.video_discussion import (
    VIDEO_ITEMS,
    add_introductions_to_conversation,
    conversation_exists,
    load_transcript,
    prepare_all_discussions,
    reset_then_power_off_all,
    _print_audio_mode,
    _print_robot_roster,
)

ATTRACT_TITLE = "Ethics Podcast"
ATTRACT_SUBTITLE = "▶ PLAY を押すと開始します  /  Press PLAY to start"

# Pause on the last topic card before the next video starts.
BETWEEN_VIDEOS_SECONDS = 2.0

# A failed run (robot HTTP timeout, missing file...) is reported and the loop
# moves on; this many failures in a row means something is really broken, and
# the error is raised so the menu logs it and runs the emergency cleanup.
MAX_CONSECUTIVE_FAILURES = 3

# How long STOP waits for a robot's in-flight upload/play request before
# sending stop_sound, so the play request cannot land after the stop.
STOP_SOUND_WAIT_SECONDS = 30.0


class _VideoPicker:
    """Chooses the next index among the playable VIDEO_ITEMS per loop mode.

    sequential: in order, wrapping around (the original behaviour)
    shuffle:    a shuffle bag, so every video plays before any repeats
    one:        the same video again
    """

    def __init__(self, candidates: list[int]) -> None:
        self._candidates = list(candidates)
        self._last = -1
        self._bag: list[int] = []

    def next(self, mode: str) -> int:
        candidates = self._candidates
        if mode == "one" and self._last in candidates:
            index = self._last
        elif mode == "shuffle":
            if not self._bag:
                order = candidates[:]
                random.shuffle(order)
                # Never play the same video twice in a row across bag refills.
                if len(order) > 1 and order[0] == self._last:
                    order.append(order.pop(0))
                self._bag = order
            index = self._bag.pop(0)
        else:
            later = [i for i in candidates if i > self._last]
            index = later[0] if later else candidates[0]
        self._last = index
        return index


def _playable_indices() -> list[int]:
    """Indices of VIDEO_ITEMS that have both a video file and a discussion.

    Decided once before the loop: the item list is static and the discussions
    were generated (or not) by prepare_all_discussions just before this.
    """
    playable: list[int] = []
    for index, item in enumerate(VIDEO_ITEMS):
        if not item["video_path"].exists():
            print(f"  WARNING: Video file not found: {item['video_path']}. Skipping.")
        elif not conversation_exists(item["playback_session"]):
            print(f"  WARNING: No valid discussion for {item['playback_session']}. Skipping.")
        else:
            playable.append(index)
    return playable


async def _never() -> None:
    """A STOP that never comes (backends without a STOP button)."""
    await asyncio.Event().wait()


async def _play_item(
    config,
    display,
    robots: tuple,
    item: dict,
    index: int,
    audio_mode: AudioMode,
    tv_audio_delay: float,
    robot_audio_delay: float,
) -> None:
    """Video, then the discussion about it, on every display."""
    robot_a, robot_b, robot_c = robots

    display.load_video(item["video_path"])

    # Reset robots fully, then lower before the video starts.
    _diag(f"pre_video_reset_start index={index}")
    await reset_then_power_off_all(robot_a, robot_b, robot_c)
    _diag(
        f"pre_video_reset_done index={index} "
        f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
    )

    if hasattr(display, "wait_for_connection"):
        print("  Waiting briefly for the /top browser to connect...")
        _diag("wait_top_connection_start")
        await display.wait_for_connection("top", timeout=30.0)
        _diag("wait_top_connection_done")

    _diag(f"video_play_start index={index}")
    display.play_video()
    _diag(f"video_wait_end_start index={index}")
    await display.wait_until_near_end_and_pause()
    _diag(f"video_wait_end_done index={index}")

    # Video ended — show the topic huge on the top display.
    _diag(f"show_topic index={index}")
    display.show_topic(title=item["title"], topic=item["topic"])

    add_introductions_to_conversation(item["playback_session"])

    await run_playback_with_connected_robots(
        config=config,
        robot_a=robot_a,
        robot_b=robot_b,
        robot_c=robot_c,
        session_name=item["playback_session"],
        audio_mode=audio_mode,
        tv_audio_delay=tv_audio_delay,
        robot_audio_delay=robot_audio_delay,
        display=display,
    )
    _diag(
        f"discussion_done index={index} "
        f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
    )


async def _run_item(coro, wait_for_stop) -> bool:
    """Run one video + discussion. Returns True if STOP cut it short.

    The run and the STOP button race each other. STOP wins by cancelling the
    playback task, which is what makes the button feel immediate: waiting for
    the current turn (or the video) to end would mean tens of seconds of
    something the operator has already decided to cut. A failed run raises.
    """
    playback = asyncio.create_task(coro)
    stopper = asyncio.create_task(wait_for_stop())
    try:
        await asyncio.wait({playback, stopper}, return_when=asyncio.FIRST_COMPLETED)

        if playback.done():
            playback.result()  # re-raises a failed run
            return False

        playback.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await playback
        return True
    finally:
        # Ctrl+C lands here with the playback still running: unwind it before
        # the caller tears down the display and the workers it is using.
        for task in (playback, stopper):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


async def _wind_down(display, robots: tuple) -> None:
    """After STOP (or a failed run): silence and park the robots, drop the BGM."""
    print("  Stopping — settling the robots...")
    loop = asyncio.get_running_loop()
    # Speech played through the daemon HTTP fallback keeps going on the robot's
    # own speaker after the cancel unless it is told to stop. Waiting for an
    # in-flight upload/play call first means its play request cannot land
    # after this stop; the three robots are handled concurrently.
    await asyncio.gather(
        *[
            loop.run_in_executor(
                None, functools.partial(robot.stop_sound, STOP_SOUND_WAIT_SECONDS)
            )
            for robot in robots
        ],
        return_exceptions=True,
    )
    try:
        display.stop_bgm()
    except Exception:
        pass
    try:
        await reset_then_power_off_all(*robots)
    except Exception as e:
        print(f"  WARNING: Could not settle the robots cleanly: {e}")


async def _sleep_unless_stopped(wait_for_stop, seconds: float) -> None:
    """Hold on the topic card, but let STOP cut the wait short."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(wait_for_stop(), timeout=seconds)


async def run_multi_display_discussion_loop(
    config,
    laptop_audio: bool = False,
    audio_mode: AudioMode | None = None,
    tv_audio_delay: float = 0.0,
    robot_audio_delay: float = 0.0,
    demo_mode: bool = False,
) -> None:
    """Run the video discussion loop across the four exhibit displays.

    When demo_mode is True, no real robots are used: MockRobots stand in for
    the hardware and audio is forced to the computer speakers. This lets the
    full exhibit flow run on a machine with no robots and without the
    reachy_mini SDK installed.
    """

    if demo_mode:
        # No robot speakers / motion — everything plays on the computer.
        audio_mode = "computer"

    if audio_mode is None:
        audio_mode = "computer" if laptop_audio else "robot"

    print("\n--- MULTI-DISPLAY VIDEO DISCUSSION LOOP ---")
    if demo_mode:
        print("  DEMO MODE: mock robots, audio on computer speakers.")
    _print_audio_mode(audio_mode)

    _print_robot_roster(config)
    print(
        f"  Displays: top=monitor {config.display_top_monitor}, "
        f"mini={config.display_mini_monitors}, "
        f"test_mode={config.display_test_mode}"
    )

    await prepare_all_discussions(config)

    playable = _playable_indices()
    if not playable:
        print("  No playable videos (missing video files or discussions). Nothing to run.")
        return

    if demo_mode:
        from ethics_podcast.mock_robot import connect_mock_robots

        print("\n--- USING MOCK ROBOTS (DEMO) ---")
        robot_a, robot_b, robot_c = connect_mock_robots()
    else:
        # Imported lazily so the module loads on machines without reachy_mini.
        from ethics_podcast.robot_controller import connect_robots

        print("\n--- CONNECTING TO ROBOTS ---")

        use_media = audio_mode in ("robot", "hybrid_surround")
        print(f"  Robot media backend: {'ON' if use_media else 'OFF'}")

        robot_a, robot_b, robot_c = await connect_robots(
            config.robot_a,
            config.robot_b,
            config.robot_c,
            use_media=use_media,
            disabled_roles=config.disabled_roles(),
        )

        print("  Robots are connected and ready.")

    robots = (robot_a, robot_b, robot_c)

    def _make_display(video_path):
        """Build the configured display backend (Qt windows or browser pages)."""
        if getattr(config, "display_backend", "qt") == "web":
            from ethics_podcast.web_display import WebDisplayManager

            print("\n--- DISPLAY BACKEND: WEB (browser, LAN-accessible) ---")
            return WebDisplayManager(
                video_path=video_path,
                host=config.display_web_host,
                port=config.display_web_port,
                loop_mode=config.loop_mode,
                audio_output=audio_output_for_mode(audio_mode),
            )

        from ethics_podcast.displays.manager import DisplayManager

        return DisplayManager(
            video_path=video_path,
            top_monitor=config.display_top_monitor,
            mini_monitors=config.display_mini_monitors,
            test_mode=config.display_test_mode,
        )

    display = _make_display(VIDEO_ITEMS[playable[0]]["video_path"])

    # The web backend has PLAY / STOP on its /top page and gates the loop on
    # them; the Qt windows have no controls and run straight through.
    gated = hasattr(display, "wait_for_play")
    wait_for_stop = display.wait_for_stop if gated else _never
    if gated:
        print(
            f"\n  Open http://<this-pc>:{config.display_web_port}/top and press PLAY."
            "\n  Nothing starts until you do; that click also lets the browser play audio.\n"
        )

    # SPACE (in the terminal or any browser) toggles mute for all displays.
    import threading

    key_stop = threading.Event()

    def _toggle_mute() -> None:
        if hasattr(display, "toggle_muted"):
            display.toggle_muted()

    start_space_key_listener(asyncio.get_running_loop(), _toggle_mute, key_stop)

    picker = _VideoPicker(playable)
    failures = 0

    try:
        while True:
            if gated and not display.is_playing:
                display.show_topic(ATTRACT_TITLE, ATTRACT_SUBTITLE)
                await display.wait_for_play()

            # The operator can change the loop mode at any point; read it here
            # so the change lands on the very next video.
            mode = getattr(display, "loop_mode", config.loop_mode)
            video_index = picker.next(mode)
            item = VIDEO_ITEMS[video_index]
            i = video_index + 1

            print(f"\n{'=' * 60}")
            print(f"  Video {i}: {item['title']}")
            print(f"  Video file: {item['video_path']}")
            print(f"{'=' * 60}")
            _diag(
                f"video_loop_start index={i} title={item['title']!r} mode={mode} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            if not load_transcript(item["transcript_path"]):
                print("  WARNING: Transcript is missing or empty.")

            try:
                stopped = await _run_item(
                    _play_item(
                        config, display, robots, item, i, audio_mode,
                        tv_audio_delay, robot_audio_delay,
                    ),
                    wait_for_stop,
                )
            except Exception as error:
                failures += 1
                print(f"\n  Run failed ({failures}/{MAX_CONSECUTIVE_FAILURES}): {error!r}")
                traceback.print_exception(error)
                await _wind_down(display, robots)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    raise
                continue

            failures = 0
            if stopped:
                await _wind_down(display, robots)
                continue

            print("\n  Moving to next video...")
            await _sleep_unless_stopped(wait_for_stop, BETWEEN_VIDEOS_SECONDS)

    finally:
        key_stop.set()

        try:
            display.close()
        except Exception as e:
            print(f"  WARNING: Could not close displays cleanly: {e}")

        print("\n--- STOPPING ROBOTS ---")

        for label, robot in (("A", robot_a), ("B", robot_b), ("C", robot_c)):
            try:
                robot.stop()
            except Exception as e:
                print(f"  WARNING: Could not stop Robot {label} cleanly: {e}")

        print("\n  Multi-display discussion loop finished.")
