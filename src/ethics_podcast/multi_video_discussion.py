"""Multi-display video discussion loop.

Same overall flow as video_discussion.run_video_discussion_loop, but drives
the new four-window DisplayManager instead of the single ControlledVideoWindow:

  1. Top display plays each video.
  2. When the video ends, the top display shows the topic huge and a gaze
     flow guides the audience toward the first robot.
  3. Each robot speaks; its mini display shows subtitles, the others dim.
  4. On each turn end, gaze guidance flows toward the next robot.
  5. Move to the next topic.

The discussion generation / robot reset / intro helpers are reused as-is from
video_discussion so behaviour stays consistent with the legacy mode.
"""

from __future__ import annotations

import asyncio

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

    def _make_display(video_path):
        """Build the configured display backend (Qt windows or browser pages)."""
        if getattr(config, "display_backend", "qt") == "web":
            from ethics_podcast.web_display import WebDisplayManager

            print("\n--- DISPLAY BACKEND: WEB (browser, LAN-accessible) ---")
            return WebDisplayManager(
                video_path=video_path,
                host=config.display_web_host,
                port=config.display_web_port,
            )

        from ethics_podcast.displays.manager import DisplayManager

        return DisplayManager(
            video_path=video_path,
            top_monitor=config.display_top_monitor,
            mini_monitors=config.display_mini_monitors,
            test_mode=config.display_test_mode,
        )

    display = None

    # SPACE (in the terminal or any browser) toggles mute for all displays.
    import threading

    key_stop = threading.Event()

    def _toggle_mute() -> None:
        if display is not None and hasattr(display, "toggle_muted"):
            display.toggle_muted()

    start_space_key_listener(asyncio.get_running_loop(), _toggle_mute, key_stop)

    try:
        video_index = 0

        while True:
            item = VIDEO_ITEMS[video_index]
            i = video_index + 1

            title = item["title"]
            video_path = item["video_path"]
            transcript_path = item["transcript_path"]
            playback_session = item["playback_session"]

            print(f"\n{'=' * 60}")
            print(f"  Video {i}: {title}")
            print(f"  Video file: {video_path}")
            print(f"{'=' * 60}")
            _diag(
                f"video_loop_start index={i} title={title!r} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            transcript = load_transcript(transcript_path)
            if not transcript:
                print("  WARNING: Transcript is missing or empty.")

            if not video_path.exists():
                print(f"  WARNING: Video file not found: {video_path}. Skipping.")
                video_index = (video_index + 1) % len(VIDEO_ITEMS)
                continue

            if not conversation_exists(playback_session):
                print(f"  WARNING: No valid discussion for {playback_session}. Skipping.")
                video_index = (video_index + 1) % len(VIDEO_ITEMS)
                continue

            if display is None:
                display = _make_display(video_path)
            else:
                display.load_video(video_path)

            # Reset robots fully, then lower before the video starts.
            _diag(f"pre_video_reset_start index={i}")
            await reset_then_power_off_all(robot_a, robot_b, robot_c)
            _diag(
                f"pre_video_reset_done index={i} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            if getattr(config, "display_backend", "qt") == "web":
                print("  Waiting briefly for the /top browser to connect...")
                _diag("wait_top_connection_start")
                await display.wait_for_connection("top", timeout=30.0)
                _diag("wait_top_connection_done")

            _diag(f"video_play_start index={i}")
            display.play_video()
            _diag(f"video_wait_end_start index={i}")
            await display.wait_until_near_end_and_pause()
            _diag(f"video_wait_end_done index={i}")

            # Video ended — show the topic huge on the top display.
            _diag(f"show_topic index={i}")
            display.show_topic(title=title, topic=item["topic"])

            add_introductions_to_conversation(playback_session)

            await run_playback_with_connected_robots(
                config=config,
                robot_a=robot_a,
                robot_b=robot_b,
                robot_c=robot_c,
                session_name=playback_session,
                audio_mode=audio_mode,
                tv_audio_delay=tv_audio_delay,
                robot_audio_delay=robot_audio_delay,
                display=display,
            )
            _diag(
                f"discussion_done index={i} "
                f"robots={_robot_diag(robot_a, robot_b, robot_c)}"
            )

            video_index = (video_index + 1) % len(VIDEO_ITEMS)

            if video_index == 0:
                print("\n  Finished the last video. Returning to the first...")
            else:
                print("\n  Moving to next video...")

            await asyncio.sleep(2)

    finally:
        key_stop.set()

        if display is not None:
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
