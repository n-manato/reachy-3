"""The show loop.

    ATTRACT  each screen shows its robot and the three topic cards; a tap on
             any screen starts that topic, or after ATTRACT_SECONDS the next
             topic in a shuffle bag starts by itself (AUTO_START=1)
    TALK     SAGE's intro and the six-line discussion
    VOTE     SAGE reads the dilemma while the screens turn into A/B/C pads;
             the countdown runs once the line ends
    RESULT   SAGE reads the outcome, then the winning branch plays
    CLOSE    SAGE's close, with tonight's tally and the panel question

Staff can pause from /control at any moment: the running topic is cancelled,
every robot fades to rest, and the screens show a break card.

Nothing here calls the LLM or the TTS API.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import shutil
import subprocess
import uuid

from lta_exhibit.audio_utils import robot_clip
from lta_exhibit.bgm import get_bgm_path
from lta_exhibit.config import OUTPUT_DIR, ROLE_ORDER, SETTINGS_PATH, VOTES_PATH, Config
from lta_exhibit.generator import load_conversation, rendered_topics
from lta_exhibit.mock_robot import MockRobot, connect_mock_robots
from lta_exhibit.personas import PROFILES
from lta_exhibit.player import LinePlayer, _diag, fade_to_rest, stop_all_sound
from lta_exhibit.robot_controller import connect_robots
from lta_exhibit.topics import get_topic
from lta_exhibit.votes import VoteLog, VoteSession
from lta_exhibit.web_display import WebDisplayManager

CLOSE_HOLD_SECS = 9.0


class TopicBag:
    """Every rendered topic once, in random order, before any repeats --
    and never the same topic twice in a row across the seam."""

    def __init__(self, topics: list[str]) -> None:
        self.topics = list(topics)
        self.bag: list[str] = []
        self.last: str | None = None

    def next(self) -> str:
        if not self.bag:
            self.bag = random.sample(self.topics, len(self.topics))
            if len(self.bag) > 1 and self.bag[0] == self.last:
                self.bag.append(self.bag.pop(0))
        choice = self.bag.pop(0)
        self.last = choice
        return choice

    def mark(self, topic_id: str) -> None:
        """A visitor picked this one: take it out of the current bag."""
        if topic_id in self.bag:
            self.bag.remove(topic_id)
        self.last = topic_id


class Exhibit:
    def __init__(self, config: Config, demo: bool = False) -> None:
        self.config = config
        self.demo = demo
        self.log = VoteLog(VOTES_PATH)
        self.session: VoteSession | None = None
        # The latest topic a visitor tapped, plus an event to wake the attract
        # wait. A single slot rather than a queue: a request left over from a
        # lost race must never start a topic out of the blue later.
        self.pending_topic: str | None = None
        self.wake = asyncio.Event()
        self.in_topic = False
        # False until robots are connected and their audio uploaded; taps on
        # the topic cards are ignored (and the screens say so) until then.
        self.ready = False
        self.display: WebDisplayManager | None = None
        self.robots: dict = {}
        self.controllers: tuple = ()

    # ── callbacks from the screens ─────────────────────────────────────────

    def on_topic_tap(self, seat: str, topic_id: str) -> None:
        if not self.ready or self.in_topic or topic_id not in rendered_topics():
            return
        if self.display is not None and not self.display.is_playing:
            return
        # Latest tap wins: two visitors tapping different cards at once start
        # one topic, not two.
        self.pending_topic = topic_id
        self.wake.set()
        print(f"  [tap] {seat} chose {topic_id}")

    def on_home(self, seat: str) -> None:
        """Back to the topic cards: end the running topic (the show stays live)."""
        if self.in_topic and self.display is not None and self.display.is_playing:
            print(f"  [home] {seat}: back to the topics")
            self.display.request_home()

    def on_volume_changed(self, volume: float) -> None:
        self._apply_robot_volume(self.display.effective_volume if self.display else volume)
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps({"volume": volume}), encoding="utf-8")
        except OSError:
            pass
        muted = bool(self.display and self.display._state.get("muted"))
        print(f"  [volume] {round(volume * 100)}%" + (" (muted)" if muted else ""))

    def _apply_robot_volume(self, volume: float) -> None:
        # A pipe command per robot: never blocks, no test sound.
        for robot in list(self.robots.values()):
            with contextlib.suppress(Exception):
                robot.set_volume(round(volume * 100))

    @staticmethod
    def _saved_volume() -> float:
        try:
            return max(0.0, min(1.0, float(json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))["volume"])))
        except (OSError, ValueError, KeyError, TypeError):
            return 1.0

    def on_settings_changed(self) -> None:
        """Staff toggled auto-start: re-arm the attract wait at once."""
        self.wake.set()

    def on_vote(self, seat: str, option: str) -> dict | None:
        session = self.session
        if session is None or not session.open:
            return {"accepted": False, "reason": "closed"}
        if not session.cast(seat, option):
            return {"accepted": False, "reason": "wait"}
        self.log.record_vote(session, seat, option)
        self.display.update_counts(session.as_dict(), session.total)
        aligned = get_topic(session.topic_id)["options"][option]["role"]
        print(f"  [vote] {seat} -> {option} ({session.total} total)")
        return {"accepted": True, "option": option, "role": aligned}

    def on_reset_tally(self) -> None:
        self.log.reset()
        self._push_tonight()
        print("  [tally] reset")

    def _push_tonight(self) -> None:
        if self.display is not None:
            self.display._state["tonightAll"] = self.log.tonight_all()
            self.display._broadcast()

    # ── setup ──────────────────────────────────────────────────────────────

    async def _connect(self) -> None:
        if self.demo:
            self.controllers = connect_mock_robots()
        else:
            from lta_exhibit.robot_admin import daemon_status

            unreachable = set()
            print("\n  Cast:")
            # All three at once and off the event loop: the screens' web server
            # shares this loop and must keep answering while robots are checked.
            loop = asyncio.get_running_loop()
            states = await asyncio.gather(*(
                loop.run_in_executor(None, daemon_status, self.config.robot(role).host, 4.0)
                for role in ROLE_ORDER
            ))
            for role, state in zip(ROLE_ORDER, states):
                rc = self.config.robot(role)
                if not self.config.is_role_active(role):
                    print(f"    {PROFILES[role]['name']:<6} {rc.host:<16} seat empty (voice from screen)")
                    continue
                if state is None:
                    unreachable.add(role)
                print(f"    {PROFILES[role]['name']:<6} {rc.host:<16} {(state or {}).get('state', 'NOT RESPONDING')}")
            skip = frozenset(self.config.disabled_roles()) | frozenset(unreachable)
            self.controllers = await connect_robots(
                *(self.config.robot(role) for role in ROLE_ORDER),
                use_media=False,
                disabled_roles=skip,
            )
        self.robots = {c.role: c for c in self.controllers}
        self.display.set_robot_status(
            {r: ("mock" if isinstance(c, MockRobot) else "connected") for r, c in self.robots.items()}
        )

    def _clip_paths(self, role: str) -> list:
        """This robot's lines, as the louder robot copies (built if missing)."""
        paths = []
        for topic_id in rendered_topics():
            conv = load_conversation(topic_id) or {}
            for lines in conv.get("segments", {}).values():
                paths += [robot_clip(OUTPUT_DIR / topic_id / l["audio_file"], self.config.robot_gain_db)
                          for l in lines if l["role"] == role]
        return paths

    async def _upload_clips(self, role: str, robot) -> None:
        """Upload one robot's own lines to it, so playback is one tiny POST."""
        if self.config.audio_output != "robot" or isinstance(robot, MockRobot):
            return
        loop = asyncio.get_running_loop()
        paths = await loop.run_in_executor(None, self._clip_paths, role)
        ready = await asyncio.get_running_loop().run_in_executor(None, robot.preload_sound_files, paths)
        print(f"  [audio] {PROFILES[role]['name']}: {ready}/{len(paths)} clips on the robot")
        status = dict(self.display._state.get("robots", {}))
        status[role] = f"connected · {ready}/{len(paths)} clips" + ("" if ready == len(paths) else " (rest from screen)")
        self.display.set_robot_status(status)

    async def _preload_robot_audio(self) -> None:
        await asyncio.gather(*(self._upload_clips(role, robot) for role, robot in list(self.robots.items())))

    async def _retry_absent_robots(self, every: float = 45.0) -> None:
        """Bring in a robot that was off or unreachable at start-up.

        Without this a seat that missed the start stays a stand-in all night
        (its lines play from the center screen), even after the robot is
        switched on. Checked every ``every`` seconds; the new controller is
        swapped in as soon as it is connected and has its clips.
        """
        from lta_exhibit.robot_admin import daemon_status
        from lta_exhibit.robot_controller import RobotController

        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(every)
            for role in ROLE_ORDER:
                if not isinstance(self.robots.get(role), MockRobot):
                    continue
                if not self.config.is_role_active(role):
                    continue
                rc = self.config.robot(role)
                controller = None
                try:
                    state = await loop.run_in_executor(None, daemon_status, rc.host, 3.0)
                    if state is None:
                        continue
                    print(f"  [robots] {PROFILES[role]['name']} is back at {rc.host}; connecting...")
                    controller = RobotController(rc.host, rc.name, role)
                    await loop.run_in_executor(None, controller.start)
                    self.controllers = (*self.controllers, controller)
                    # Clips first, THEN swap it in: a line that reached the new
                    # robot mid-upload would time out, skip its speaker for a
                    # minute, and abort the upload as well.
                    await self._upload_clips(role, controller)
                    # It missed every volume / mute change made so far.
                    controller.set_volume(round(self.display.effective_volume * 100))
                    old = self.robots[role]
                    self.robots[role] = controller
                    with contextlib.suppress(Exception):
                        old.stop()
                    print(f"  [robots] {PROFILES[role]['name']} joined the show")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep retrying all night
                    print(f"  [robots] {PROFILES[role]['name']} could not join yet: {exc}")
                    if controller is not None and self.robots.get(role) is not controller:
                        # stop() can wait several seconds on the worker: not here.
                        with contextlib.suppress(Exception):
                            await loop.run_in_executor(None, controller.stop)

    # ── one topic ──────────────────────────────────────────────────────────

    async def _run_topic(self, topic_id: str) -> None:
        conv = load_conversation(topic_id)
        topic_dir = OUTPUT_DIR / topic_id
        seg = conv["segments"]
        player = LinePlayer(self.config, self.robots, self.display)
        _diag(f"topic_start {topic_id}")

        self.display.begin_topic(topic_id, self.log.tonight(topic_id))
        await player.play_lines(topic_dir, seg["intro"] + seg["discussion"], next_after="sage")

        # The ballot opens as SAGE starts reading the dilemma.
        self.session = VoteSession(topic_id=topic_id, run_id=uuid.uuid4().hex[:8])
        self.display.open_vote()
        await player.play_lines(topic_dir, seg["dilemma"])

        self.display.start_countdown(self.config.vote_seconds)
        watch_stop = asyncio.Event()
        watcher = asyncio.create_task(player.watch_screens(watch_stop))
        try:
            await asyncio.sleep(self.config.vote_seconds)
        finally:
            watch_stop.set()
            with contextlib.suppress(Exception):
                await watcher

        winner = self.session.close()
        self.log.record_result(self.session, winner)
        counts, total = self.session.as_dict(), self.session.total
        self.session = None
        print(f"  [vote] closed: {counts} -> {winner or 'no votes'}")
        self._push_tonight()
        self.display.show_result(winner, counts, total, self.log.tonight(topic_id))

        reveal = seg[f"reveal_{winner}"] if winner else seg["reveal_none"]
        branch = seg[f"branch_{winner or 'B'}"]
        await player.play_lines(topic_dir, reveal + branch, next_after="sage")

        self.display.show_close(self.log.tonight(topic_id))
        await player.play_lines(topic_dir, seg["close"])
        await asyncio.sleep(CLOSE_HOLD_SECS)
        _diag(f"topic_done {topic_id}")

    async def _run_cancellable(self, topic_id: str) -> bool:
        """Run one topic; True if staff paused it.

        Also ends early (returning False, the show stays live) when someone
        taps "back to topics" on a screen.
        """
        self.in_topic = True
        self.display.clear_home()
        task = asyncio.create_task(self._run_topic(topic_id))
        stopper = asyncio.create_task(self.display.wait_for_stop())
        homer = asyncio.create_task(self.display.wait_for_home())
        try:
            await asyncio.wait({task, stopper, homer}, return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                if task.exception() is not None:
                    print(f"\n  Topic failed: {task.exception()!r}")
                    stop_all_sound(self.robots.values())
                return False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            stop_all_sound(self.robots.values())
            if stopper.done():
                print("  Paused -- fading out...")
                return True
            print("  Back to the topics -- fading out...")
            return False
        finally:
            stopper.cancel()
            homer.cancel()
            self.display.clear_home()
            self.in_topic = False
            if self.session is not None:
                self.session.open = False
                self.session = None
            # Straight to the attract/break card: otherwise the screens sit on
            # a frozen caption or a dead ballot until staff press Start again.
            self.display.show_attract(None)
            await fade_to_rest(self.robots.values())

    async def _wait_for_topic(self, rendered: list[str], bag: TopicBag) -> str:
        """Hold the attract screen until a tap, the auto-start timer, or forever.

        Woken early by a tap, by staff toggling auto-start, or by Pause; the
        timer restarts whenever auto-start is switched back on.
        """
        loop = asyncio.get_running_loop()
        # pending_topic is deliberately NOT cleared here: taps are refused while
        # a topic runs, so anything set now came from a visitor who tapped the
        # attract screen while the robots were still settling -- a real request.
        while True:
            await self.display.wait_for_play()
            auto = self.display.auto_start
            seconds = self.config.attract_seconds
            self.display.show_attract(seconds if auto else None)
            deadline = loop.time() + seconds if auto else None
            while True:
                self.wake.clear()
                if self.pending_topic and self.display.is_playing:
                    topic_id, self.pending_topic = self.pending_topic, None
                    bag.mark(topic_id)
                    return topic_id
                if not self.display.is_playing or self.display.auto_start != auto:
                    break  # paused, or auto toggled: re-arm from the top
                timeout = None if deadline is None else max(0.0, deadline - loop.time())
                if timeout == 0.0:
                    return bag.next()
                stop = asyncio.create_task(self.display.wait_for_stop())
                woke = asyncio.create_task(self.wake.wait())
                try:
                    await asyncio.wait({stop, woke}, timeout=timeout,
                                       return_when=asyncio.FIRST_COMPLETED)
                finally:
                    stop.cancel()
                    woke.cancel()
            if not self.display.is_playing:
                self.pending_topic = None
                self.display.show_attract(None)

    # ── main loop ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        rendered = rendered_topics()
        if not rendered:
            print("\nNo rendered topics in output/. Run menu 1 (scripts) and 2 (audio) first.")
            return
        print(f"\nLeading Through AI exhibit — {len(rendered)} topic(s): {', '.join(rendered)}")

        self.display = WebDisplayManager(
            host=self.config.display_web_host,
            port=self.config.display_web_port,
            speech_bitrate=self.config.display_speech_bitrate,
            auto_start=self.config.auto_start,
            on_vote=self.on_vote,
            on_topic_tap=self.on_topic_tap,
        )
        self.display.on_reset_tally = self.on_reset_tally
        self.display.on_settings_changed = self.on_settings_changed
        self.display.set_ready(False)
        self._push_tonight()
        bgm = get_bgm_path()
        if bgm:
            self.display.set_bgm(bgm, volume=0.22)
        self.display.show_attract(None)

        self.display.on_home = self.on_home
        self.display.on_volume_changed = self.on_volume_changed
        self.display.set_volume(self._saved_volume(), notify=False)

        bag = TopicBag(rendered)
        inhibitor = _inhibit_sleep()
        # The finally must also cover start-up: Ctrl+C or a service stop while
        # the robots are still connecting would otherwise leave workers running.
        try:
            await self._connect()
            await fade_to_rest(self.robots.values(), offset_fade=0, lower=1.0)
            self._apply_robot_volume(self.display.effective_volume)
            await self._preload_robot_audio()
            self.ready = True
            self.display.set_ready(True)
            if not self.demo:
                retry = asyncio.create_task(self._retry_absent_robots())
            while True:
                topic_id = await self._wait_for_topic(rendered, bag)
                await self._run_cancellable(topic_id)
        finally:
            print("\nShutting down the exhibit...")
            if "retry" in locals():
                retry.cancel()
            with contextlib.suppress(Exception):
                await fade_to_rest(self.robots.values(), offset_fade=0.2, lower=1.0)
            for controller in self.controllers:
                with contextlib.suppress(Exception):
                    controller.stop()
            self.display.close()
            if inhibitor is not None:
                with contextlib.suppress(Exception):
                    inhibitor.terminate()


def _inhibit_sleep() -> subprocess.Popen | None:
    """Keep the PC awake for as long as the exhibit runs.

    A logind "block" inhibitor (sleep, idle, lid switch), held by a child
    process that lives exactly as long as we do. GNOME's idle-suspend honours
    it. An unprivileged user without a local desktop session needs a polkit
    rule for this (see RUNBOOK_ja.md); without one we say so and carry on.
    """
    exe = shutil.which("systemd-inhibit")
    if not exe:
        return None
    try:
        proc = subprocess.Popen(
            [exe, "--what=sleep:idle:handle-lid-switch", "--who=Leading Through AI exhibit",
             "--why=Exhibit running", "--mode=block", "sleep", "infinity"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env={**os.environ, "SYSTEMD_COLORS": "0"},
            start_new_session=True,  # Ctrl+C reaches us, not it; we end it ourselves
        )
    except OSError:
        return None
    try:
        _, err = proc.communicate(timeout=1.5)  # exits at once only if refused
    except subprocess.TimeoutExpired:
        print("  [power] sleep blocked while the exhibit runs")
        return proc
    text = re.sub(r"\x1b\[[0-9;]*m", "", (err or b"").decode(errors="replace")).strip()
    reason = text.splitlines()[-1:] or ["?"]
    print(f"  [power] WARNING: could not block sleep ({reason[0]}).\n"
          "          The PC may suspend when idle. Once: sudo scripts/allow_sleep_block.sh")
    return None


async def run_exhibit(config: Config, demo: bool = False) -> None:
    await Exhibit(config, demo=demo).run()
