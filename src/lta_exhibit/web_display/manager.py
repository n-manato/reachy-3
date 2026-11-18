"""Serves the three seat screens and the operator page, and pushes state.

Pages:
    /seat/left  /seat/center  /seat/right   one per touch monitor (kiosk)
    /control                                staff only: start/stop, auto, volume
    /all                                    the three seats tiled, for testing

There is one shared state, broadcast whole to every page on every change;
each page renders its own seat's view of it. A page that (re)connects is sent
the latest state at once, and a new boot id makes stale browsers reload.

Only /seat/center makes sound: the background music, and any line whose robot
could not play it. Robot voices normally come from the robots themselves.

Carried over from the BOMA display because each fixes a real failure on the
exhibit floor: the boot id reload, the ?token=&v= cache buster on media URLs,
and the lazy WAV->MP3 transcode that keeps audio off the robots' Wi-Fi budget.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
import uuid
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from scipy.io import wavfile

from lta_exhibit.config import ROLE_OF_SEAT, SEAT_OF_ROLE
from lta_exhibit.theme import page_identity
from lta_exhibit.topics import OPTION_KEYS, public_topics
from lta_exhibit.web_display import pages

logger = logging.getLogger(__name__)

SEATS = ("left", "center", "right")
NO_STORE_HEADERS = {"Cache-Control": "no-store"}

try:
    import lameenc  # noqa: F401

    _MP3_AVAILABLE = True
except Exception:  # pragma: no cover
    _MP3_AVAILABLE = False


def _encode_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate_kbps: int) -> bool:
    try:
        import lameenc

        rate, data = wavfile.read(wav_path)
    except Exception:
        return False
    if data.dtype != np.int16:
        data = (np.clip(data.astype(np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    channels = 1 if data.ndim == 1 else int(data.shape[1])
    try:
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(bitrate_kbps)
        encoder.set_in_sample_rate(int(rate))
        encoder.set_channels(channels)
        encoder.set_quality(2)
        mp3 = encoder.encode(np.ascontiguousarray(data).tobytes()) + encoder.flush()
        tmp = mp3_path.with_suffix(mp3_path.suffix + ".tmp")
        tmp.write_bytes(mp3)
        tmp.replace(mp3_path)
    except Exception:
        return False
    return True


def _local_ip() -> str:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _display_addresses() -> list[tuple[str, str]]:
    from lta_exhibit.robot_admin import _VIRTUAL_IFACE, _interfaces_linux, _interfaces_windows

    try:
        raw = _interfaces_windows() if sys.platform == "win32" else _interfaces_linux()
    except Exception:
        raw = []
    found = []
    for name, cidr in raw:
        ip = cidr.split("/")[0]
        if ip.startswith(("127.", "169.254.")) or cidr.endswith("/32") or _VIRTUAL_IFACE.search(name):
            continue
        found.append((name, ip))
    return found or [("default route", _local_ip())]


class WebDisplayManager:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8020,
        speech_bitrate: int = 48,
        auto_start: bool = True,
        on_vote=None,
        on_topic_tap=None,
    ) -> None:
        self.host = host
        self.port = port
        self.speech_bitrate = speech_bitrate
        self.on_vote = on_vote            # callback(seat, option) -> dict | None
        self.on_topic_tap = on_topic_tap  # callback(seat, topic_id)
        self.on_reset_tally = None        # callback()
        self.on_settings_changed = None   # callback(), e.g. auto-start toggled
        self.on_home = None               # callback(seat): back to the topic cards
        self.on_volume_changed = None     # callback(volume 0..1): robots follow it
        # Monotonic deadlines behind the two countdowns, so a page that loads
        # mid-countdown is told what is LEFT, not the original length.
        self._vote_deadline: float | None = None
        self._attract_deadline: float | None = None
        self._line_t0: float | None = None

        self.speech_audio_path: Path | None = None
        self.bgm_audio_path: Path | None = None
        self._encode_lock = asyncio.Lock()
        self._boot_id = uuid.uuid4().hex[:8]

        # queue -> channel ("left" | "center" | "right" | "control" | "all")
        self._conns: dict[asyncio.Queue, str] = {}

        self._play_gate = asyncio.Event()
        self._stop_gate = asyncio.Event()
        # Set by a "back to topics" tap: ends the running topic (not a pause).
        self._home_gate = asyncio.Event()
        # Live by default: after a reboot (systemd) the exhibit comes back on
        # its own. Staff can pause it from /control.
        self._play_gate.set()

        self._state: dict = {
            "type": "state",
            "transport": "playing",
            "autoStart": bool(auto_start),
            "phase": "attract",          # attract|talk|vote|result|close
            "topicId": None,
            "attractRemaining": None,    # seconds until auto-start
            "attractEpoch": 0,
            # The line being spoken
            "speaking": None,
            "next": None,
            "text": "",
            "lineEpoch": 0,
            "lineDuration": 0.0,
            "levels": [],
            # Ballot
            "voteOpen": False,
            "voteEpoch": 0,
            "voteRemaining": None,       # countdown seconds, None = not started
            "counts": {k: 0 for k in OPTION_KEYS},
            "voteTotal": 0,
            "winner": None,
            "tonight": None,
            # Audio (center seat only)
            "speechAudioSrc": "",
            "speechAudioEpoch": 0,
            "speechLine": -1,            # lineEpoch the current clip belongs to
            "speechPan": 0.0,
            "bgmSrc": "",
            "bgmEpoch": 0,
            "bgmVolume": 0.22,
            "ducked": False,
            "muted": False,
            "volume": 1.0,
            "robots": {},
            "ready": False,
        }

        self._loop = asyncio.get_running_loop()
        self.app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._server_task = self._loop.create_task(self._serve())
        self._broadcast()
        self._print_urls()

    # ── Server ─────────────────────────────────────────────────────────────

    def _print_urls(self) -> None:
        addresses = _display_addresses()
        print("\n  [web] Exhibit screens served on the local network:")
        for name, ip in addresses:
            print(f"    http://{ip}:{self.port}/          ({name})")
        best = addresses[0][1]
        print(f"\n    Seats:   http://{best}:{self.port}/seat/left | /seat/center | /seat/right")
        print(f"    Staff:   http://{best}:{self.port}/control")
        print(f"    Preview: http://{best}:{self.port}/all\n")

    async def _serve(self) -> None:
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        try:
            await self._server.serve()
        except asyncio.CancelledError:
            pass

    def _build_app(self) -> FastAPI:
        app = FastAPI()
        identity = page_identity()
        topics = public_topics()

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return pages.page_index(self.port)

        @app.get("/seat/{seat}", response_class=HTMLResponse)
        async def seat(seat: str) -> Response:
            if seat not in SEATS:
                return Response(status_code=404)
            return HTMLResponse(pages.page_seat(seat, ROLE_OF_SEAT[seat], identity, topics))

        @app.get("/control", response_class=HTMLResponse)
        async def control() -> str:
            return pages.page_control(identity, topics)

        @app.get("/all", response_class=HTMLResponse)
        async def all_seats() -> str:
            return pages.page_all()

        @app.get("/speech/current")
        async def speech_current(request: Request) -> Response:
            wav = self.speech_audio_path
            if wav is None or not wav.exists():
                return Response(status_code=404)
            return FileResponse(str(await self._web_audio(wav)), headers=NO_STORE_HEADERS)

        @app.get("/bgm/current")
        async def bgm_current(request: Request) -> Response:
            bgm = self.bgm_audio_path
            if bgm is None or not bgm.exists():
                return Response(status_code=404)
            return FileResponse(str(bgm), headers=NO_STORE_HEADERS)

        @app.websocket("/ws/{channel}")
        async def ws(websocket: WebSocket, channel: str) -> None:
            await self._ws_endpoint(websocket, channel)

        return app

    @staticmethod
    def _mp3_fresh(mp3: Path, wav: Path) -> bool:
        try:
            return mp3.exists() and mp3.stat().st_size > 0 and mp3.stat().st_mtime >= wav.stat().st_mtime
        except OSError:
            return False

    async def _web_audio(self, wav: Path) -> Path:
        if not _MP3_AVAILABLE:
            return wav
        mp3 = wav.with_suffix(".mp3")
        if self._mp3_fresh(mp3, wav):
            return mp3
        async with self._encode_lock:
            if self._mp3_fresh(mp3, wav):
                return mp3
            ok = await asyncio.get_running_loop().run_in_executor(
                None, _encode_wav_to_mp3, wav, mp3, self.speech_bitrate
            )
            return mp3 if ok and self._mp3_fresh(mp3, wav) else wav

    # ── WebSocket ──────────────────────────────────────────────────────────

    async def _ws_endpoint(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        self._conns[queue] = channel
        client = websocket.client.host if websocket.client else "?"
        print(f"  [web] {channel} connected from {client}")
        await websocket.send_json({**self._snapshot(), "bootId": self._boot_id})

        async def sender() -> None:
            try:
                while True:
                    await websocket.send_json(await queue.get())
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass

        send_task = asyncio.create_task(sender())
        try:
            while True:
                self._handle(channel, await websocket.receive_json())
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover
            logger.debug("ws error: %s", exc)
        finally:
            send_task.cancel()
            self._conns.pop(queue, None)
            print(f"  [web] {channel} disconnected from {client}")

    def _send_to(self, channel: str, message: dict) -> None:
        for queue, ch in self._conns.items():
            if ch == channel:
                queue.put_nowait({**message, "bootId": self._boot_id})

    def _handle(self, channel: str, msg: dict) -> None:
        kind = msg.get("type")
        if channel in SEATS:
            try:
                if kind == "vote" and self.on_vote is not None:
                    feedback = self.on_vote(channel, str(msg.get("option", "")))
                    if feedback:
                        self._send_to(channel, {"type": "voted", **feedback})
                elif kind == "tap_topic" and self.on_topic_tap is not None:
                    self.on_topic_tap(channel, str(msg.get("topicId", "")))
                elif kind == "home" and self.on_home is not None:
                    self.on_home(channel)
                elif kind == "volume_set":
                    # The seat slider: same 10 % floor as the steps.
                    self.set_volume(max(0.1, min(1.0, float(msg.get("value", 1.0)))))
                elif kind == "mute":
                    self.toggle_mute()
                elif kind == "volume_step":
                    # Visitors: 10 % steps, never all the way to silent.
                    step = max(-0.1, min(0.1, float(msg.get("delta", 0))))
                    self.set_volume(max(0.1, min(1.0, round(self._state["volume"] + step, 2))))
            except Exception as exc:  # noqa: BLE001 - never drop the screen's socket
                logger.warning("seat %s message %s failed: %s", channel, kind, exc)
            return
        if channel != "control":
            return
        if kind == "play":
            self.request_play()
        elif kind == "stop":
            self.request_stop()
        elif kind == "auto":
            self._state["autoStart"] = bool(msg.get("value"))
            self._broadcast()
            if self.on_settings_changed is not None:
                self.on_settings_changed()
        elif kind == "start_topic" and self.on_topic_tap is not None:
            self.on_topic_tap("control", str(msg.get("topicId", "")))
        elif kind == "volume":
            try:
                self.set_volume(max(0.0, min(1.0, float(msg.get("value", 1.0)))))
            except (TypeError, ValueError):
                return
        elif kind == "home" and self.on_home is not None:
            self.on_home("control")
        elif kind == "mute":
            self.toggle_mute()
        elif kind == "reset_tally" and self.on_reset_tally is not None:
            self.on_reset_tally()


    # ── Broadcasting ───────────────────────────────────────────────────────

    def _snapshot(self) -> dict:
        boot = self._boot_id
        s = self._state
        s["speechAudioSrc"] = (
            f"/speech/current?token={s['speechAudioEpoch']}&v={boot}" if self.speech_audio_path else ""
        )
        s["bgmSrc"] = f"/bgm/current?token={s['bgmEpoch']}&v={boot}" if self.bgm_audio_path else ""
        now = self._loop.time()
        s["voteRemaining"] = (
            round(max(0.0, self._vote_deadline - now), 2) if self._vote_deadline is not None else None
        )
        s["attractRemaining"] = (
            round(max(0.0, self._attract_deadline - now), 2) if self._attract_deadline is not None else None
        )
        # How far into the current line we are, so a page joining mid-line
        # starts the fallback voice (and the orb) at the right place.
        s["lineElapsed"] = round(max(0.0, now - self._line_t0), 2) if self._line_t0 is not None else 0.0
        return dict(s)

    def _broadcast(self) -> None:
        message = {**self._snapshot(), "bootId": self._boot_id}
        for queue in self._conns:
            queue.put_nowait(message)

    def has_screens(self) -> bool:
        return any(ch in SEATS for ch in self._conns.values())

    # ── Show API (called by show.py / player.py) ───────────────────────────

    def set_ready(self, ready: bool) -> None:
        self._state["ready"] = bool(ready)
        self._broadcast()

    def set_robot_status(self, status: dict[str, str]) -> None:
        self._state["robots"] = dict(status)
        self._broadcast()

    def show_attract(self, remaining: float | None) -> None:
        self._state.update(
            phase="attract", topicId=None, speaking=None, next=None, text="",
            voteOpen=False, winner=None, ducked=False,
        )
        self._vote_deadline = None
        self._attract_deadline = self._loop.time() + remaining if remaining else None
        self._state["attractEpoch"] += 1
        self._broadcast()

    def begin_topic(self, topic_id: str, tonight: dict | None) -> None:
        self._state.update(
            phase="talk", topicId=topic_id, speaking=None, next=None, text="",
            voteOpen=False, winner=None,
            counts={k: 0 for k in OPTION_KEYS}, voteTotal=0, tonight=tonight,
        )
        self._vote_deadline = None
        self._attract_deadline = None
        self._broadcast()

    def show_line(self, role: str, text: str, duration: float, levels: list[float],
                  audio_path: Path | None, pan: float, next_role: str | None) -> None:
        self._state["lineEpoch"] += 1
        self._line_t0 = self._loop.time()
        if audio_path is not None and Path(audio_path).exists():
            self.speech_audio_path = Path(audio_path)
            self._state["speechAudioEpoch"] += 1
            # Which line this clip belongs to: a page that (re)loads during a
            # later, robot-spoken line must not replay it.
            self._state["speechLine"] = self._state["lineEpoch"]
            self._state["speechPan"] = pan
            self._loop.create_task(self._web_audio(self.speech_audio_path))
        self._state.update(speaking=role, next=next_role, text=text, lineDuration=duration,
                           levels=levels, ducked=True)
        self._broadcast()

    def end_line(self, next_role: str | None) -> None:
        self._state.update(speaking=None, next=next_role, ducked=False)
        self._broadcast()

    def open_vote(self) -> None:
        """Show the options and accept taps (the countdown starts later)."""
        self._state.update(phase="vote", voteOpen=True,
                           counts={k: 0 for k in OPTION_KEYS}, voteTotal=0, winner=None)
        self._vote_deadline = None
        self._state["voteEpoch"] += 1
        self._broadcast()

    def start_countdown(self, seconds: float) -> None:
        self._vote_deadline = self._loop.time() + float(seconds)
        self._state["voteSeconds"] = float(seconds)
        self._state["voteEpoch"] += 1
        self._broadcast()

    def update_counts(self, counts: dict, total: int) -> None:
        self._state.update(counts=dict(counts), voteTotal=total)
        self._broadcast()

    def show_result(self, winner: str | None, counts: dict, total: int, tonight: dict | None) -> None:
        self._state.update(phase="result", voteOpen=False, winner=winner, speaking=None,
                           next=None, text="", counts=dict(counts), voteTotal=total, tonight=tonight)
        self._vote_deadline = None
        self._broadcast()

    def show_close(self, tonight: dict | None) -> None:
        self._state.update(phase="close", tonight=tonight, speaking=None)
        self._broadcast()

    def set_volume(self, volume: float, notify: bool = True) -> None:
        """Master volume 0..1: the center screen's gain, and (through
        on_volume_changed) the robots' speaker volume."""
        volume = round(max(0.0, min(1.0, float(volume))), 2)
        changed = volume != self._state["volume"]
        self._state["volume"] = volume
        self._broadcast()
        if notify and self.on_volume_changed is not None and changed:
            self.on_volume_changed(volume)

    def toggle_mute(self) -> None:
        """Mute everything (center screen and robots) / unmute to the set volume."""
        self._state["muted"] = not self._state["muted"]
        self._broadcast()
        if self.on_volume_changed is not None:
            self.on_volume_changed(self._state["volume"])

    @property
    def effective_volume(self) -> float:
        return 0.0 if self._state["muted"] else float(self._state["volume"])

    def set_bgm(self, path: Path | None, volume: float = 0.22) -> None:
        self.bgm_audio_path = Path(path) if path else None
        self._state["bgmEpoch"] += 1
        self._state["bgmVolume"] = volume
        self._broadcast()

    # ── Transport ──────────────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self._state["transport"] == "playing"

    @property
    def auto_start(self) -> bool:
        return bool(self._state["autoStart"])

    def request_play(self) -> None:
        if self.is_playing:
            return
        self._state["transport"] = "playing"
        self._stop_gate.clear()
        self._play_gate.set()
        self._broadcast()
        print("  [web] play")

    def request_stop(self) -> None:
        if not self.is_playing:
            return
        self._state["transport"] = "stopped"
        self._play_gate.clear()
        self._stop_gate.set()
        self._broadcast()
        print("  [web] stop")

    async def wait_for_play(self) -> None:
        await self._play_gate.wait()

    async def wait_for_stop(self) -> None:
        await self._stop_gate.wait()

    def request_home(self) -> None:
        self._home_gate.set()

    def clear_home(self) -> None:
        self._home_gate.clear()

    async def wait_for_home(self) -> None:
        await self._home_gate.wait()

    def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._server_task.cancel()


def seat_role(seat: str) -> str | None:
    return ROLE_OF_SEAT.get(seat)


def role_seat(role: str) -> str | None:
    return SEAT_OF_ROLE.get(role)

