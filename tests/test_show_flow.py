"""Show-loop behaviour: taps, auto-start toggling, pause, stale requests."""

import asyncio
import dataclasses
import socket

from lta_exhibit.config import load_config
from lta_exhibit.mock_robot import connect_mock_robots
from lta_exhibit.show import Exhibit, TopicBag
from lta_exhibit.web_display import WebDisplayManager

TOPICS = ["agents_keys", "first_rung", "say_its_ai"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _exhibit(auto: bool, attract: float = 0.4) -> Exhibit:
    config = dataclasses.replace(load_config(), attract_seconds=attract, auto_start=auto)
    ex = Exhibit(config, demo=True)
    ex.display = WebDisplayManager(host="127.0.0.1", port=_free_port(), auto_start=auto,
                                   on_vote=ex.on_vote, on_topic_tap=ex.on_topic_tap)
    ex.display.on_settings_changed = ex.on_settings_changed
    ex.display.on_home = ex.on_home
    ex.robots = {c.role: c for c in connect_mock_robots()}
    return ex


def run(coro):
    return asyncio.run(coro)


def test_tap_ignored_until_ready_then_wins():
    async def go():
        ex = await _exhibit(auto=False)
        ex.on_topic_tap("left", "say_its_ai")          # still warming up
        assert ex.pending_topic is None
        ex.ready = True
        waiter = asyncio.create_task(ex._wait_for_topic(TOPICS, TopicBag(TOPICS)))
        await asyncio.sleep(0.1)
        ex.on_topic_tap("left", "say_its_ai")
        assert await asyncio.wait_for(waiter, 2) == "say_its_ai"
        ex.display.close()
    run(go())


def test_auto_start_toggled_on_takes_effect():
    async def go():
        ex = await _exhibit(auto=False)
        ex.ready = True
        waiter = asyncio.create_task(ex._wait_for_topic(TOPICS, TopicBag(TOPICS)))
        await asyncio.sleep(0.6)
        assert not waiter.done()                       # auto off: waits forever
        ex.display._handle("control", {"type": "auto", "value": True})
        topic = await asyncio.wait_for(waiter, 2)      # attract timer, then a topic
        assert topic in TOPICS
        ex.display.close()
    run(go())


def test_tap_while_paused_does_not_start_later():
    async def go():
        ex = await _exhibit(auto=False)
        ex.ready = True
        ex.display.request_stop()
        waiter = asyncio.create_task(ex._wait_for_topic(TOPICS, TopicBag(TOPICS)))
        await asyncio.sleep(0.1)
        ex.on_topic_tap("left", "agents_keys")          # ignored: paused
        ex.display.request_play()
        await asyncio.sleep(0.3)
        assert not waiter.done()
        waiter.cancel()
        ex.display.close()
    run(go())


def test_pause_mid_topic_shows_break_card():
    async def go():
        ex = await _exhibit(auto=False)

        async def endless(topic_id):
            ex.display.begin_topic(topic_id, None)
            ex.display.open_vote()
            await asyncio.sleep(60)
        ex._run_topic = endless
        task = asyncio.create_task(ex._run_cancellable("agents_keys"))
        await asyncio.sleep(0.1)
        assert ex.display._state["phase"] == "vote"
        ex.display.request_stop()
        assert await asyncio.wait_for(task, 5) is True
        assert ex.display._state["phase"] == "attract"
        assert ex.display._state["voteOpen"] is False
        ex.display.close()
    run(go())


def test_vote_reject_reasons_and_remaining_countdown():
    async def go():
        ex = await _exhibit(auto=False)
        assert ex.on_vote("left", "A")["reason"] == "closed"
        from lta_exhibit.votes import VoteSession
        ex.session = VoteSession("agents_keys", "t")
        ex.log.record_vote = lambda *a, **k: None       # keep the real log clean
        assert ex.on_vote("left", "A")["accepted"]
        assert ex.on_vote("left", "B")["reason"] == "wait"
        ex.display.start_countdown(20)
        await asyncio.sleep(0.3)
        remaining = ex.display._snapshot()["voteRemaining"]
        assert 19.0 < remaining < 19.8
        ex.display.close()
    run(go())


def test_tap_just_after_topic_end_is_kept():
    async def go():
        ex = await _exhibit(auto=False)
        ex.ready = True
        ex.in_topic = False                              # topic over, robots still settling
        ex.on_topic_tap("right", "first_rung")
        topic = await asyncio.wait_for(ex._wait_for_topic(TOPICS, TopicBag(TOPICS)), 2)
        assert topic == "first_rung"
        ex.display.close()
    run(go())


def test_fallback_clip_belongs_to_its_line_only():
    """A page that loads during a robot-spoken line must not replay the last
    screen-fallback clip: the clip is tagged with the line it belongs to."""
    async def go():
        ex = await _exhibit(auto=False)
        d = ex.display
        from lta_exhibit.config import OUTPUT_DIR
        clip = next((OUTPUT_DIR / "agents_keys" / "clips").glob("*.wav"))
        d.show_line("nova", "x", 3.0, [], clip, -0.85, "aegis")      # fallback line
        s = d._snapshot()
        assert s["speechLine"] == s["lineEpoch"] and s["speechAudioSrc"]
        d.show_line("aegis", "y", 3.0, [], None, 0.85, "sage")        # robot line
        s = d._snapshot()
        assert s["speechLine"] != s["lineEpoch"]
        await asyncio.sleep(0.3)
        assert 0.2 < d._snapshot()["lineElapsed"] < 1.0
        d.close()
    run(go())


def test_late_robot_joins_only_after_its_clips_are_uploaded(monkeypatch):
    import lta_exhibit.robot_admin as admin
    import lta_exhibit.robot_controller as rcmod

    events = []

    class FakeController:
        def __init__(self, host, name, role):
            self.role = role
        def start(self):
            events.append("start")
        def preload_sound_files(self, paths):
            import time
            time.sleep(0.2)
            events.append(("uploaded", ex.robots["sage"] is self))
            return len(paths)
        def set_volume(self, volume):
            events.append(("volume", self.role, volume))
        def stop(self):
            events.append("stop")

    monkeypatch.setattr(admin, "daemon_status", lambda host, timeout=3.0: {"state": "running"})
    monkeypatch.setattr(rcmod, "RobotController", FakeController)

    async def go():
        global ex
        ex = await _exhibit(auto=False)
        ex.config = dataclasses.replace(ex.config, audio_output="robot")
        ex.robots = {c.role: c for c in connect_mock_robots()}
        ex.display.set_volume(0.4, notify=False)
        ex.display.toggle_mute()          # staff muted before the robots joined
        task = asyncio.create_task(ex._retry_absent_robots(every=0.05))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if all(isinstance(r, FakeController) for r in ex.robots.values()):
                break
        task.cancel()
        assert all(isinstance(r, FakeController) for r in ex.robots.values())
        # Uploaded while still out of the show, never after being swapped in.
        assert ("uploaded", True) not in events
        # A late robot takes the room's volume, here muted, not its own.
        assert {e for e in events if e[0] == "volume"} == {("volume", r, 0) for r in ("nova", "sage", "aegis")}
        ex.display.close()
    run(go())


def test_home_ends_topic_but_stays_live():
    async def go():
        ex = await _exhibit(auto=False)
        ex.ready = True

        async def endless(topic_id):
            ex.display.begin_topic(topic_id, None)
            await asyncio.sleep(60)
        ex._run_topic = endless
        task = asyncio.create_task(ex._run_cancellable("agents_keys"))
        await asyncio.sleep(0.1)
        ex.display._handle("left", {"type": "home"})
        assert await asyncio.wait_for(task, 5) is False      # not a pause
        assert ex.display.is_playing
        assert ex.display._state["phase"] == "attract"
        ex.on_topic_tap("left", "say_its_ai")                  # cards work again
        assert ex.pending_topic == "say_its_ai"
        ex.display.close()
    run(go())


def test_home_ignored_outside_a_topic():
    async def go():
        ex = await _exhibit(auto=False)
        ex.display._handle("left", {"type": "home"})
        assert not ex.display._home_gate.is_set()
        ex.display.close()
    run(go())


def test_volume_steps_reach_robots_and_persist(monkeypatch, tmp_path):
    import lta_exhibit.show as show_mod
    monkeypatch.setattr(show_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    async def go():
        ex = await _exhibit(auto=False)
        ex.display.on_volume_changed = ex.on_volume_changed
        got = []
        for r in ex.robots.values():
            r.set_volume = lambda v, got=got: got.append(v)
        ex.display._state["volume"] = 1.0
        ex.display._handle("center", {"type": "volume_step", "delta": -0.1})
        ex.display._handle("center", {"type": "volume_step", "delta": -5})   # clamped to one step
        assert ex.display._state["volume"] == 0.8
        assert got[-3:] == [80, 80, 80]
        for _ in range(20):
            ex.display._handle("left", {"type": "volume_step", "delta": -0.1})
        assert ex.display._state["volume"] == 0.1                           # never silent from a seat
        assert ex._saved_volume() == 0.1
        ex.display.close()
    run(go())


def test_robot_copy_is_louder_and_never_clips(tmp_path):
    import numpy as np
    from lta_exhibit.audio_utils import float_to_pcm_bytes, pcm_bytes_to_float, read_wav, robot_clip, write_wav
    t = np.arange(24000 * 2) / 24000
    # Like the rendered clips: moderate level, normalised so rare peaks hit 0.95.
    speechy = 0.12 * np.sin(2 * np.pi * 180 * t)
    speechy[::4800] = 0.95                                                   # sharp peaks
    speechy = speechy.astype(np.float32)
    src = tmp_path / "clips" / "x.wav"
    write_wav(src, float_to_pcm_bytes(speechy))
    out = robot_clip(src, 8.0)
    assert out != src and out.parent.name == "robot_p8dB"
    a = pcm_bytes_to_float(read_wav(src)[0]); b = pcm_bytes_to_float(read_wav(out)[0])
    assert np.abs(b).max() <= 0.951
    assert np.sqrt((b * b).mean()) > 1.9 * np.sqrt((a * a).mean())           # about twice as loud
    assert robot_clip(src, 8.0) == out                                       # cached


def test_mute_silences_robots_and_unmute_restores(monkeypatch, tmp_path):
    import lta_exhibit.show as show_mod
    monkeypatch.setattr(show_mod, "SETTINGS_PATH", tmp_path / "settings.json")

    async def go():
        ex = await _exhibit(auto=False)
        ex.display.on_volume_changed = ex.on_volume_changed
        got = []
        for r in ex.robots.values():
            r.set_volume = lambda v, got=got: got.append(v)
        ex.display._handle("center", {"type": "volume_set", "value": 0.6})
        assert got[-1] == 60
        ex.display._handle("right", {"type": "mute"})
        assert ex.display._state["muted"] and got[-1] == 0
        ex.display._handle("control", {"type": "mute"})
        assert not ex.display._state["muted"] and got[-1] == 60
        ex.display._handle("left", {"type": "volume_set", "value": 0.0})     # seat floor
        assert ex.display._state["volume"] == 0.1
        ex.display.close()
    run(go())
