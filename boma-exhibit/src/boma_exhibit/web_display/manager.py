"""Serves the /top display page and pushes state to it over a WebSocket.

Ported from the Ethics Podcast exhibit's web display, reduced to the single
screen this show needs, and extended in two ways the sketch requires:

  - captions live on /top. The reference put subtitles on small screens beside
    each robot; with one TV there is nowhere else to put them, and this show
    needs them badly -- two thirds of it is in languages the audience will not
    speak, so each line is shown in the original with an English gloss beneath.
  - a turn can have several speakers at once, so `current` is a list and more
    than one portrait can light up.

Three mechanics are carried over verbatim because each of them fixes a real
failure seen on the exhibit floor: the boot id that makes stale browsers
reload themselves, the `?token=&v=` cache-buster that stops a browser splicing
last run's audio into this one, and the lazy WAV->MP3 transcode that keeps the
speech stream from starving the robots' control link on the shared Wi-Fi.
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

from boma_exhibit.config import PICTURE_DIR, ROLE_ORDER
from boma_exhibit.variants import MODES as LOOP_MODES, normalize_mode
from boma_exhibit.web_display import pages

logger = logging.getLogger(__name__)

CHANNEL = "top"

ICON_PATHS = {role: PICTURE_DIR / f"{role}.svg" for role in ROLE_ORDER}

# Where each robot stands, so the browser can pan its voice there.
SPEECH_PAN = {"zuri": -0.85, "kora": 0.0, "pulaar": 0.85}

# Media is served from per-run URLs. The browser must never reuse a cached
# body: across runs the same ?token= number would otherwise hit a stale entry
# and splice old bytes into the new stream.
NO_STORE_HEADERS = {"Cache-Control": "no-store"}

try:
    import lameenc  # noqa: F401

    _MP3_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _MP3_AVAILABLE = False


def _encode_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate_kbps: int) -> bool:
    """Transcode WAV to MP3, written atomically. True on success.

    Module-level (no ``self``) so it can run in a thread executor without
    touching loop-owned state.
    """
    try:
        import lameenc

        rate, data = wavfile.read(wav_path)
    except Exception:
        return False

    if data.dtype == np.int16:
        pass
    elif data.dtype in (np.float32, np.float64):
        data = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
    elif data.dtype == np.int32:
        data = (data >> 16).astype(np.int16)
    else:
        data = data.astype(np.int16)

    channels = 1 if data.ndim == 1 else int(data.shape[1])

    try:
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(bitrate_kbps)
        encoder.set_in_sample_rate(int(rate))
        encoder.set_channels(channels)
        encoder.set_quality(2)  # 0 = best/slowest .. 9 = worst/fastest
        mp3 = encoder.encode(np.ascontiguousarray(data).tobytes())
        mp3 += encoder.flush()
    except Exception:
        return False

    try:
        tmp = mp3_path.with_suffix(mp3_path.suffix + ".tmp")
        tmp.write_bytes(mp3)
        tmp.replace(mp3_path)
    except Exception:
        return False

    return True


def _local_ip() -> str:
    """The address on this machine's default route. No traffic is actually sent.

    Note what this is NOT: the address the TV should use. It answers "which
    interface would reach the internet", and on the Windows exhibit PC that is
    the VPN tunnel (10.144.x), which no TV in the room can open. Use it only as
    a fallback for `_display_addresses`.
    """
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ip = probe.getsockname()[0]
        probe.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _display_addresses() -> list[tuple[str, str]]:
    """[(interface, ip)] a TV could plausibly open, best guess first.

    The operator, not this code, knows which network the TV is on -- so print
    every candidate rather than picking one and being confidently wrong. Ordered
    with the robot subnet first because that is where the TV usually sits.
    """
    from boma_exhibit.robot_admin import (
        _VIRTUAL_IFACE,
        _interfaces_linux,
        _interfaces_windows,
    )

    try:
        raw = _interfaces_windows() if sys.platform == "win32" else _interfaces_linux()
    except Exception:
        raw = []

    found = []
    for name, cidr in raw:
        ip = cidr.split("/")[0]
        if ip.startswith(("127.", "169.254.")):
            continue
        # A VPN's /32 and a container bridge are never where the TV is.
        if cidr.endswith("/32") or _VIRTUAL_IFACE.search(name):
            continue
        found.append((name, ip))

    found.sort(key=lambda item: not item[1].startswith("192.168.3."))
    return found or [("default route", _local_ip())]


class WebDisplayManager:
    """The TV display, and the exhibit's audio output.

    These two flags are how player.py knows it must NOT touch the server's
    sound devices: the browser is playing everything.
    """

    plays_speech_audio = True
    plays_bgm_audio = True

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8010,
        speech_bitrate: int = 48,
        title: str = "",
        subtitle: str = "",
        loop_mode: str = "shuffle",
    ) -> None:
        self.host = host
        self.port = port
        self.speech_bitrate = speech_bitrate

        self.speech_audio_path: Path | None = None
        self.bgm_audio_path: Path | None = None
        self._speech_encode_lock = asyncio.Lock()

        # Unique per program run. Every message carries it; a browser left open
        # from a previous run sees the id change and reloads itself.
        self._boot_id = uuid.uuid4().hex[:8]

        self._conns: set[asyncio.Queue] = set()
        self._latest: dict | None = None

        # Transport, driven entirely from the page. The exhibit starts stopped
        # and waits on _play_gate, so nothing is heard until someone presses
        # Play -- which doubles as the user gesture browsers demand before they
        # will let a page play audio at all.
        self._play_gate = asyncio.Event()
        self._stop_gate = asyncio.Event()

        self._top: dict = {
            "type": "state",
            "mode": "attract",       # "attract" | "show"
            "transport": "stopped",  # "stopped" | "playing"
            "loopMode": normalize_mode(loop_mode),
            "title": title,
            "subtitle": subtitle,
            "current": [],           # roles speaking right now
            "next": [],              # roles about to speak
            "text": "",              # the line, in the speaker's own language
            "translation": "",       # English gloss, for sw / ff lines
            "language": "",          # "Swahili" | "English" | "Fulfulde"
            "turn": None,
            "speechAudioSrc": "",
            "speechAudioEpoch": 0,
            "speechPan": 0.0,
            "bgmSrc": "",
            "bgmEpoch": 0,
            "bgmVolume": 0.18,
            "muted": False,
            # The page applies this to its own output; the robot mixer
            # reads it too, so one slider works whichever is speaking.
            "volume": 1.0,
        }

        self._loop = asyncio.get_running_loop()
        self.app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._server_task = self._loop.create_task(self._serve())

        self._broadcast()
        self._print_urls()

    # ── Server lifecycle ───────────────────────────────────────────────────

    def _print_urls(self) -> None:
        addresses = _display_addresses()
        print("\n  [web] Exhibit display served on the local network.")
        print("        Open whichever of these the TV can actually reach:")
        for name, ip in addresses:
            print(f"    http://{ip}:{self.port}/top      ({name})")
        best = addresses[0][1]
        print("\n    Full screen on the TV:")
        print(
            f"    chrome --kiosk --autoplay-policy=no-user-gesture-required "
            f"http://{best}:{self.port}/top\n"
        )

    async def _serve(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            # No ASGI lifespan events here; disabling it avoids a noisy
            # CancelledError traceback when the server task is torn down.
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        # Running inside an existing loop -- don't let uvicorn grab signals.
        self._server.install_signal_handlers = lambda: None
        try:
            await self._server.serve()
        except asyncio.CancelledError:
            pass

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return pages.page_index(f"http://{_local_ip()}:{self.port}/top")

        @app.get("/top", response_class=HTMLResponse)
        async def top() -> str:
            return pages.page_top()

        @app.get("/icon/{role}")
        async def icon(role: str) -> Response:
            path = ICON_PATHS.get(role)
            if path is None or not path.exists():
                return Response(status_code=404)
            return FileResponse(str(path), media_type="image/svg+xml")

        @app.get("/speech/current")
        async def speech_current(request: Request) -> Response:
            wav = self.speech_audio_path
            if wav is None or not wav.exists():
                return Response(status_code=404)
            served = await self._get_web_audio(wav)
            return FileResponse(str(served), headers=NO_STORE_HEADERS)

        @app.get("/bgm/current")
        async def bgm_current(request: Request) -> Response:
            bgm = self.bgm_audio_path
            if bgm is None or not bgm.exists():
                return Response(status_code=404)
            return FileResponse(str(bgm), headers=NO_STORE_HEADERS)

        @app.websocket("/ws/top")
        async def ws(websocket: WebSocket) -> None:
            await self._ws_endpoint(websocket)

        return app

    # ── Speech transcoding ─────────────────────────────────────────────────

    @staticmethod
    def _mp3_fresh(mp3_path: Path, wav_path: Path) -> bool:
        try:
            return (
                mp3_path.exists()
                and mp3_path.stat().st_size > 0
                and mp3_path.stat().st_mtime >= wav_path.stat().st_mtime
            )
        except OSError:
            return False

    async def _get_web_audio(self, wav: Path) -> Path:
        """A compressed copy of ``wav`` for the browser, built on first use.

        An uncompressed 24 kHz turn is 1-3 MB; at 48 kbps mono it is roughly an
        eighth of that. On a contended Wi-Fi link that difference is what stops
        the speech stream from starving the robots' control traffic. Falls back
        to the WAV if encoding is unavailable, so audio is never lost -- only
        made smaller when it can be.
        """
        if not _MP3_AVAILABLE:
            return wav

        mp3 = wav.with_suffix(".mp3")
        if self._mp3_fresh(mp3, wav):
            return mp3

        async with self._speech_encode_lock:
            if self._mp3_fresh(mp3, wav):  # another request may have built it
                return mp3
            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(
                None, _encode_wav_to_mp3, wav, mp3, self.speech_bitrate
            )
            return mp3 if ok and self._mp3_fresh(mp3, wav) else wav

    # ── WebSocket ──────────────────────────────────────────────────────────

    async def _ws_endpoint(self, websocket: WebSocket) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        self._conns.add(queue)
        client = websocket.client.host if websocket.client else "unknown"
        print(f"  [web] display connected from {client}")

        # Replay the latest state so a page opened mid-show renders correctly.
        # Tagging it with the boot id is what makes a browser left over from a
        # previous run notice and reload itself.
        if self._latest is not None:
            await websocket.send_json({**self._latest, "bootId": self._boot_id})

        async def sender() -> None:
            try:
                while True:
                    await websocket.send_json(await queue.get())
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass

        send_task = asyncio.create_task(sender())
        try:
            while True:
                self._handle_client_msg(await websocket.receive_json())
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover
            logger.debug("ws error: %s", exc)
        finally:
            send_task.cancel()
            self._conns.discard(queue)
            print(f"  [web] display disconnected from {client}")

    def _handle_client_msg(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "toggle_mute":
            self.toggle_muted()
        elif kind == "play":
            self.request_play()
        elif kind == "stop":
            self.request_stop()
        elif kind == "cycle_loop":
            self.cycle_loop()
        elif kind == "volume":
            try:
                self.set_volume(float(msg.get("value", 1.0)))
            except (TypeError, ValueError):
                pass

    # ── Broadcasting ───────────────────────────────────────────────────────

    def _broadcast(self) -> None:
        boot = self._boot_id
        # The per-clip token alone resets to 1, 2, 3... every run, so a cached
        # body from the previous run would be reused. The boot id makes each
        # URL unique per run and defeats that.
        self._top["speechAudioSrc"] = (
            f"/speech/current?token={self._top['speechAudioEpoch']}&v={boot}"
            if self.speech_audio_path
            else ""
        )
        self._top["bgmSrc"] = (
            f"/bgm/current?token={self._top['bgmEpoch']}&v={boot}"
            if self.bgm_audio_path
            else ""
        )
        message = dict(self._top)
        self._latest = message
        tagged = {**message, "bootId": boot}
        for queue in self._conns:
            queue.put_nowait(tagged)

    # ── Display API (called by exhibit.py and player.py) ───────────────────

    def show_attract(self, title: str, subtitle: str = "") -> None:
        """Between runs: the title card, with all three robots idle."""
        self._top.update(
            mode="attract",
            title=title,
            subtitle=subtitle,
            current=[],
            next=[],
            text="",
            translation="",
            language="",
            turn=None,
        )
        self._broadcast()

    def show_subtitle(
        self,
        roles: list[str],
        text: str,
        translation: str = "",
        language: str = "",
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
    ) -> None:
        """A turn begins: light up its speakers, show the line, play the audio."""
        if audio_path is not None and Path(audio_path).exists():
            self.speech_audio_path = Path(audio_path)
            self._top["speechAudioEpoch"] += 1
            # A chorus is pre-mixed and belongs in the centre.
            self._top["speechPan"] = (
                SPEECH_PAN.get(roles[0], 0.0) if len(roles) == 1 else 0.0
            )
            self._loop.create_task(self._prewarm(self.speech_audio_path))

        self._top.update(
            mode="show",
            current=list(roles),
            next=[],
            text=text,
            translation=translation or "",
            language=language or "",
            turn=turn_number,
        )
        self._broadcast()

    async def _prewarm(self, wav: Path) -> None:
        """Transcode ahead of the browser's request so it never waits."""
        try:
            await self._get_web_audio(wav)
        except Exception as exc:  # pragma: no cover
            logger.debug("speech prewarm failed: %s", exc)

    def finish_subtitle(
        self, roles: list[str], next_roles: list[str] | None = None
    ) -> None:
        """A turn ends: dim the speakers and hint at who is next."""
        self._top.update(current=[], next=list(next_roles or []))
        self._broadcast()

    def point_to(self, next_roles: list[str] | None) -> None:
        self._top.update(current=[], next=list(next_roles or []))
        self._broadcast()

    def set_bgm(self, path: str | Path | None, volume: float = 0.18) -> None:
        if path is None:
            return self.stop_bgm()
        self.bgm_audio_path = Path(path)
        self._top["bgmEpoch"] += 1
        self._top["bgmVolume"] = volume
        self._broadcast()

    def stop_bgm(self) -> None:
        self.bgm_audio_path = None
        self._broadcast()

    @property
    def volume(self) -> float:
        return float(self._top.get("volume", 1.0))

    def set_volume(self, value: float) -> None:
        """Store the level the operator set on the page.

        Not broadcast: the page that sent it already applied it, and echoing it
        back would fight the slider under the operator's finger.
        """
        self._top["volume"] = max(0.0, min(1.0, value))

    def set_muted(self, muted: bool) -> None:
        self._top["muted"] = bool(muted)
        self._broadcast()

    def toggle_muted(self) -> None:
        self.set_muted(not self._top["muted"])
        print(f"  [web] audio {'muted' if self._top['muted'] else 'unmuted'}")

    # ── Transport ──────────────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self._top["transport"] == "playing"

    def request_play(self) -> None:
        if self.is_playing:
            return
        self._top["transport"] = "playing"
        self._stop_gate.clear()
        self._play_gate.set()
        self._broadcast()
        print("  [web] play")

    def request_stop(self) -> None:
        """Ask the show to wind down.

        Deliberately leaves the caption and the lit-up speakers alone: the page
        fades them out along with the audio, and the exhibit loop switches to
        the attract screen once everything has settled. Clearing them here would
        make the screen snap to blank while the sound was still fading.
        """
        if not self.is_playing:
            return
        self._top["transport"] = "stopped"
        self._play_gate.clear()
        self._stop_gate.set()
        self._broadcast()
        print("  [web] stop")

    @property
    def loop_mode(self) -> str:
        return self._top["loopMode"]

    def cycle_loop(self) -> None:
        """Step to the next loop mode, the way a music player's button does.

        Cycled on the server so there is one source of truth: the page only
        ever says "next", and every connected display sees the same answer.
        """
        index = LOOP_MODES.index(self._top["loopMode"])
        self._top["loopMode"] = LOOP_MODES[(index + 1) % len(LOOP_MODES)]
        self._broadcast()
        print(f"  [web] loop mode: {self._top['loopMode']}")

    async def wait_for_play(self) -> None:
        await self._play_gate.wait()

    async def wait_for_stop(self) -> None:
        await self._stop_gate.wait()

    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Hold the show until the TV browser is actually up.

        Without this the first turn plays to an empty room: the audio only
        exists in the browser, so nothing is heard and nothing is seen.
        """
        deadline = self._loop.time() + timeout
        announced = False
        while self._loop.time() < deadline:
            if self._conns:
                return True
            if not announced:
                print(f"  [web] waiting up to {timeout:.0f}s for the display...")
                announced = True
            await asyncio.sleep(0.25)
        print("  [web] no display connected; continuing without one")
        return False

    # ── Parity with the Qt display API ─────────────────────────────────────

    def process_events(self) -> None:
        """No-op: the browser renders itself."""

    async def pump_events_forever(self, stop: asyncio.Event) -> None:
        await stop.wait()

    def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self._server_task.cancel()
