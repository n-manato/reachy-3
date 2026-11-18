"""WebDisplayManager — drives the browser exhibit UI.

A drop-in replacement for ``displays.manager.DisplayManager``: it exposes the
same high-level methods the playback loop calls (``play_video``,
``wait_until_near_end_and_pause``, ``show_topic``, ``show_subtitle``,
``finish_subtitle``, ``point_to``, ``load_video``, ``pump_events_forever``,
``close``) but, instead of manipulating Qt widgets, it serves web pages and
pushes declarative state to the connected browsers over WebSocket.

It runs a uvicorn server as a task on the *same* asyncio loop as the playback
loop, so no extra threads are involved. All UI methods are synchronous and just
update state + enqueue messages for the connected sockets — exactly the calling
contract the existing loop already relies on.

Audio is unchanged: robot-speech wavs still play on the server (sounddevice /
robot speakers) via ``player.py``. The browser only shows the visuals, and the
audio-reactive orb is driven by a server-side level streamer reading the same
wav, so it stays in sync with what the audience hears.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from scipy.io import wavfile

from ethics_podcast.config import (
    AUDIO_OUTPUTS,
    LOOP_MODES,
    normalize_audio_output,
    normalize_loop_mode,
)
from ethics_podcast.web_display import pages

logger = logging.getLogger(__name__)

# Browser speech audio is transcoded from the source WAV to MP3 before being
# served. The uncompressed 24 kHz int16 WAV is ~1-3 MB per turn; an MP3 at this
# bitrate is roughly 1/8 the size. On a shared/contended Wi-Fi link this removes
# the bandwidth spike that coincided with the robot control-link drops. The WAV
# is kept on disk for the server-side audio-level meter.
try:
    import lameenc  # noqa: F401

    _MP3_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _MP3_AVAILABLE = False

# Mono speech bitrate (kbps). Tune at the exhibit via DISPLAY_SPEECH_BITRATE.
SPEECH_WEB_BITRATE_KBPS = int(os.environ.get("DISPLAY_SPEECH_BITRATE", "48"))


def _encode_wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate_kbps: int) -> bool:
    """Transcode a WAV file to MP3, writing atomically. Returns True on success.

    Kept module-level (no ``self``) so it can run in a thread executor without
    touching loop-owned state.
    """
    try:
        import lameenc

        rate, data = wavfile.read(wav_path)
    except Exception:
        return False

    # Normalise to int16 PCM, the format lameenc expects.
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
        encoder.set_quality(2)  # 0=best/slowest .. 9=worst/fastest
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


ROLES = ("student", "professor", "moderator")
CHANNELS = ("top", *ROLES)
ICON_PATHS = {
    "student": Path("picture/Bentham.png"),
    "professor": Path("picture/korsgaard.jpg"),
    "moderator": Path("picture/Aquinas.jpg"),
}
SPEECH_PAN = {
    "student": -0.85,
    "professor": 0.0,
    "moderator": 0.85,
}

# How long to wait for the browser to report a video has ended before the
# playback loop gives up and moves on (in case no top display is connected).
VIDEO_END_TIMEOUT = 1800.0

# Media files are served from per-run URLs (tagged with the boot id), so the
# browser must never reuse a cached body: across runs the same ?token= number
# would otherwise hit a stale entry (e.g. a video whose file changed from mp4 to
# webm), splicing old bytes into the new stream and freezing playback.
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def _local_ip() -> str:
    """Best-effort LAN IP of this machine (no traffic is actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _local_hostname() -> str:
    """Best-effort hostname users can try on the same LAN."""
    try:
        return socket.gethostname()
    except Exception:
        return "localhost"


class WebDisplayManager:
    """Coordinates the four browser displays as one unit, over WebSocket."""

    plays_speech_audio = True
    # The top display can also play the looping background music (mono/centered),
    # so all exhibit audio can come from the browser instead of the PC mixer.
    plays_bgm_audio = True

    def __init__(
        self,
        video_path: Path | None = None,
        host: str = "0.0.0.0",
        port: int = 8000,
        # Accepted for signature parity with DisplayManager; unused here.
        top_monitor: int = 0,
        mini_monitors: tuple[int, int, int] = (1, 2, 3),
        test_mode: bool = False,
        loop_mode: str = "sequential",
        audio_output: str = "browser",
    ):
        self.host = host
        self.port = port
        self.video_path: Path | None = Path(video_path) if video_path else None
        self.speech_audio_path: Path | None = None
        # Background music served to (and looped by) the top browser.
        self.bgm_audio_path: Path | None = None
        self._bgm_epoch = 0
        # Serialises lazy WAV->MP3 transcodes for the browser speech stream.
        self._speech_encode_lock = asyncio.Lock()

        # Unique id for this program run. Every message sent to a browser is
        # tagged with it; the client reloads itself when it sees the id change,
        # so all already-open displays refresh automatically on each new run.
        self._boot_id = uuid.uuid4().hex[:8]

        # Connected browser send-queues, grouped by channel.
        self._conns: dict[str, set[asyncio.Queue]] = {c: set() for c in CHANNELS}
        # Last full "state" message per channel, replayed to new connections.
        self._latest: dict[str, dict] = {}

        # Declarative view-models.
        self._top = {
            "type": "state",
            "mode": "topic",
            "videoSrc": "",
            "videoEpoch": 0,
            "speechAudioSrc": "",
            "speechAudioEpoch": 0,
            "speechPan": 0.0,
            # Seconds into the clip to start from (non-zero only for a line
            # moved to the browser mid-way by the voice-output switch).
            "speechOffset": 0.0,
            "bgmSrc": "",
            "bgmVolume": 0.18,
            "muted": False,
            "title": "",
            "topic": "",
            "current": None,
            "next": None,
            # Transport, driven from the /top page's PLAY / STOP buttons. The
            # page's volume slider is page-local (browser audio only) and is
            # deliberately not mirrored here.
            "transport": "stopped",  # "stopped" | "playing"
            "loopMode": normalize_loop_mode(loop_mode),
            # Where the robots' voices play: "robot" speakers or this "browser".
            # Picked live on /top; the player reads it at the start of each turn.
            "audioOutput": normalize_audio_output(audio_output),
        }
        # 3-display setup: the big top screen plus 2 mini screens BETWEEN the
        # robots. Both minis show the SAME unified view — the current speaker's
        # subtitle, or a transition arrow — so there is a single mini view-model
        # broadcast identically to every mini channel.
        self._mini_state = {
            "type": "state",
            "state": "idle",       # "speaking" | "arrow" | "idle"
            "speaker": None,        # speaking: current speaker; arrow: next speaker
            "text": "",
        }
        self._last_speaker: str | None = None
        self._video_token = 0

        # Single audio-level streamer for the current speaker (drives every
        # mini orb identically).
        self._level_task: asyncio.Task | None = None

        # Set when a top browser reports the current video ended.
        self._video_ended = asyncio.Event()

        # The exhibit loop starts stopped and waits on _play_gate, so nothing
        # runs until someone presses PLAY on /top -- which doubles as the user
        # gesture the browser needs before it will play audio at all.
        self._play_gate = asyncio.Event()
        self._stop_gate = asyncio.Event()

        self._loop = asyncio.get_event_loop()
        self.app = self._build_app()
        self._server: uvicorn.Server | None = None
        self._server_task = self._loop.create_task(self._serve())

        if self.video_path is not None:
            self.load_video(self.video_path)

        # Seed the latest-state cache so a page opened before any event still
        # renders correctly (idle minis, topic/blank top) on first connect.
        self._broadcast_top()
        self._broadcast_minis()

        self._print_urls()

    # ── Server lifecycle ──────────────────────────────────────────────────────

    def _print_urls(self) -> None:
        ip = _local_ip()
        hostname = _local_hostname()
        print("\n  [web] Exhibit UI served on the local network:")
        for path in ("top", *ROLES, "all"):
            print(f"    http://{ip}:{self.port}/{path}")
        if hostname and hostname not in {ip, "localhost"}:
            print("    PC-name URLs you can also try:")
            for path in ("top", *ROLES, "all"):
                print(f"    http://{hostname}:{self.port}/{path}")
        print("    (any computer on the same LAN can open these)\n")

    async def _serve(self) -> None:
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            # We don't use ASGI lifespan events; disabling it avoids a noisy
            # CancelledError traceback when the server task is torn down.
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        # Running inside an existing loop — don't let uvicorn grab signals.
        self._server.install_signal_handlers = lambda: None
        try:
            await self._server.serve()
        except asyncio.CancelledError:
            pass

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def index(request: Request) -> str:
            self._log_http_hit("/", request)
            return pages.page_index([f"/{p}" for p in ("top", *ROLES, "all")])

        @app.get("/top", response_class=HTMLResponse)
        async def top(request: Request) -> str:
            self._log_http_hit("/top", request)
            return pages.page_top()

        @app.get("/all", response_class=HTMLResponse)
        async def all_page(request: Request) -> str:
            self._log_http_hit("/all", request)
            return pages.page_all()

        @app.get("/student", response_class=HTMLResponse)
        async def student(request: Request) -> str:
            self._log_http_hit("/student", request)
            return pages.page_mini("student")

        @app.get("/professor", response_class=HTMLResponse)
        async def professor(request: Request) -> str:
            self._log_http_hit("/professor", request)
            return pages.page_mini("professor")

        @app.get("/moderator", response_class=HTMLResponse)
        async def moderator(request: Request) -> str:
            self._log_http_hit("/moderator", request)
            return pages.page_mini("moderator")

        @app.get("/icon/{role}")
        async def icon(role: str) -> Response:
            path = ICON_PATHS.get(role)
            if path is None or not path.exists():
                return Response(status_code=404)
            return FileResponse(str(path))

        @app.get("/video/current")
        async def video_current(request: Request) -> Response:
            if self.video_path is None or not self.video_path.exists():
                self._log_media_hit("/video/current", request, missing=True)
                return Response(status_code=404)
            self._log_media_hit("/video/current", request)
            # FileResponse handles HTTP range requests (seeking) for us.
            return FileResponse(str(self.video_path), headers=NO_STORE_HEADERS)

        @app.get("/speech/current")
        async def speech_current(request: Request) -> Response:
            wav = self.speech_audio_path
            if wav is None or not wav.exists():
                self._log_media_hit("/speech/current", request, missing=True)
                return Response(status_code=404)
            # Serve a small MP3 instead of the multi-MB WAV when possible.
            served = await self._get_web_audio(wav)
            self._log_media_hit("/speech/current", request)
            return FileResponse(str(served), headers=NO_STORE_HEADERS)

        @app.get("/bgm/current")
        async def bgm_current(request: Request) -> Response:
            bgm = self.bgm_audio_path
            if bgm is None or not bgm.exists():
                self._log_media_hit("/bgm/current", request, missing=True)
                return Response(status_code=404)
            self._log_media_hit("/bgm/current", request)
            # BGM files are already MP3; FileResponse handles range requests
            # so the browser can loop them smoothly.
            return FileResponse(str(bgm), headers=NO_STORE_HEADERS)

        @app.websocket("/ws/{channel}")
        async def ws(websocket: WebSocket, channel: str) -> None:
            if channel not in CHANNELS:
                await websocket.close()
                return
            await self._ws_endpoint(websocket, channel)

        return app

    def _log_http_hit(self, path: str, request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        print(f"  [web] HTTP {path} from {client}")

    def _log_media_hit(
        self, path: str, request: Request, missing: bool = False
    ) -> None:
        client = request.client.host if request.client else "unknown"
        range_header = request.headers.get("range", "no-range")
        status = "missing" if missing else "ok"
        print(
            f"  [web] MEDIA {path} {status} from {client} "
            f"range={range_header}"
        )

    @staticmethod
    def _mp3_fresh(mp3_path: Path, wav_path: Path) -> bool:
        """True if a non-empty MP3 cache exists and is newer than its WAV."""
        try:
            return (
                mp3_path.exists()
                and mp3_path.stat().st_size > 0
                and mp3_path.stat().st_mtime >= wav_path.stat().st_mtime
            )
        except OSError:
            return False

    async def _get_web_audio(self, wav: Path) -> Path:
        """Return a compressed (MP3) copy of ``wav`` for the browser.

        Transcodes lazily and caches the result next to the WAV. Falls back to
        the original WAV if MP3 encoding is unavailable or fails, so audio is
        never lost — only made smaller when possible.
        """
        if not _MP3_AVAILABLE:
            return wav

        mp3 = wav.with_suffix(".mp3")
        if self._mp3_fresh(mp3, wav):
            return mp3

        async with self._speech_encode_lock:
            # Re-check inside the lock in case another request just built it.
            if self._mp3_fresh(mp3, wav):
                return mp3
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(
                None, _encode_wav_to_mp3, wav, mp3, SPEECH_WEB_BITRATE_KBPS
            )
            if ok and self._mp3_fresh(mp3, wav):
                try:
                    wav_kb = wav.stat().st_size / 1024
                    mp3_kb = mp3.stat().st_size / 1024
                    print(
                        f"  [web] speech encoded {wav.name}: "
                        f"{wav_kb:.0f}KB WAV -> {mp3_kb:.0f}KB MP3 "
                        f"@{SPEECH_WEB_BITRATE_KBPS}kbps"
                    )
                except OSError:
                    pass
                return mp3
            return wav

    async def _ws_endpoint(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        self._conns[channel].add(queue)
        client = websocket.client.host if websocket.client else "unknown"
        print(f"  [web] WS connected: {channel} from {client}")
        disconnect_reason = "normal"

        # Replay the latest known state so a freshly-opened page is correct.
        # Tag it with the boot id too: a display left open from a previous run
        # reconnects here and sees the new id, which triggers its reload.
        last = self._latest.get(channel)
        if last is not None:
            await websocket.send_json({**last, "bootId": self._boot_id})

        async def sender() -> None:
            try:
                while True:
                    msg = await queue.get()
                    await websocket.send_json(msg)
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass

        send_task = asyncio.create_task(sender())
        try:
            while True:
                msg = await websocket.receive_json()
                self._handle_client_msg(channel, msg)
        # CancelledError (BaseException) is raised when the server task is torn
        # down at shutdown — swallow it here so uvicorn doesn't log a traceback.
        except WebSocketDisconnect as e:
            disconnect_reason = f"websocket_disconnect code={getattr(e, 'code', None)}"
        except RuntimeError as e:
            disconnect_reason = f"runtime_error {e}"
        except asyncio.CancelledError:
            disconnect_reason = "server_cancelled"
            pass
        except Exception as e:  # pragma: no cover
            disconnect_reason = f"exception {type(e).__name__}: {e}"
            logger.debug("ws error on %s: %s", channel, e)
        finally:
            send_task.cancel()
            self._conns[channel].discard(queue)
            print(
                f"  [web] WS disconnected: {channel} from {client} "
                f"reason={disconnect_reason}"
            )

    def _handle_client_msg(self, channel: str, msg: dict) -> None:
        msg_type = msg.get("type")

        if channel == "top" and msg_type == "video_ended":
            current_time = msg.get("currentTime")
            duration = msg.get("duration")
            print(
                "  [web] top reported video ended"
                f" current={current_time} duration={duration}"
            )
            self._video_ended.set()
            return

        if msg_type == "toggle_mute":
            # A SPACE press in any browser toggles mute for every display.
            self.toggle_muted()
            return

        if msg_type == "play":
            self.request_play()
            return

        if msg_type == "stop":
            self.request_stop()
            return

        if msg_type == "cycle_loop":
            self.cycle_loop()
            return

        if msg_type == "set_output":
            self.set_audio_output(msg.get("output"))
            return

    # ── Broadcasting ──────────────────────────────────────────────────────────

    def _push(self, channel: str, msg: dict) -> None:
        tagged = {**msg, "bootId": self._boot_id}
        for queue in self._conns[channel]:
            queue.put_nowait(tagged)

    def _broadcast_top(self) -> None:
        # Tag every media URL with the boot id as well as the per-clip token.
        # The token alone resets to the same 1,2,3… each run, so a stale browser
        # cache from a previous run (different file, same ?token=) would be
        # reused; the boot id makes the URL unique per run and defeats that.
        boot = self._boot_id
        self._top["videoSrc"] = (
            f"/video/current?token={self._video_token}&v={boot}"
            if self.video_path
            else ""
        )
        self._top["speechAudioSrc"] = (
            f"/speech/current?token={self._top['speechAudioEpoch']}&v={boot}"
            if self.speech_audio_path
            else ""
        )
        self._top["bgmSrc"] = (
            f"/bgm/current?token={self._bgm_epoch}&v={boot}" if self.bgm_audio_path else ""
        )
        msg = dict(self._top)
        self._latest["top"] = msg
        self._push("top", msg)

    def set_bgm(self, path, volume: float = 0.18) -> None:
        """Start (or switch) the looping background music in the top browser.

        ``volume`` is the playback gain the browser applies to the <audio>
        element (0..1). The clip loops until ``stop_bgm`` is called.
        """
        self.bgm_audio_path = Path(path)
        self._bgm_epoch += 1
        self._top["bgmVolume"] = max(0.0, min(1.0, float(volume)))
        print(f"  [web] BGM -> browser: {self.bgm_audio_path} (vol={volume})")
        self._broadcast_top()

    def stop_bgm(self) -> None:
        """Stop the browser background music."""
        if self.bgm_audio_path is None:
            return
        self.bgm_audio_path = None
        self._bgm_epoch += 1
        self._broadcast_top()

    def toggle_muted(self) -> None:
        """Flip the mute state and tell every display to (un)mute its audio."""
        self.set_muted(not self._top["muted"])

    def set_muted(self, muted: bool) -> None:
        """Mute/unmute all browser audio (speech + BGM + video).

        Carried in the broadcast STATE (same proven path as video/speech), so it
        reaches every connected display and any that reconnect while muted.
        """
        self._top["muted"] = bool(muted)
        print(
            f"  [web] {'muted' if muted else 'unmuted'} "
            f"-> {len(self._conns['top'])} top display(s)"
        )
        self._broadcast_top()

    # ── Transport (PLAY / STOP / loop mode / volume on the /top page) ─────────

    @property
    def is_playing(self) -> bool:
        return self._top["transport"] == "playing"

    def request_play(self) -> None:
        if self.is_playing:
            return
        self._top["transport"] = "playing"
        self._stop_gate.clear()
        self._play_gate.set()
        self._broadcast_top()
        print("  [web] PLAY")

    def request_stop(self) -> None:
        """Ask the exhibit loop to wind down.

        The page silences its own audio at once; the loop cancels the running
        item, settles the robots and switches to the attract card.
        """
        if not self.is_playing:
            return
        self._top["transport"] = "stopped"
        # Leave nothing for a page (re)connecting in the wind-down gap to
        # resume: drop the video mode and the current line, idle the minis.
        # The loop follows up with the attract card once it has wound down.
        self._top.update(mode="topic", current=None, next=None)
        self.speech_audio_path = None
        self._play_gate.clear()
        self._stop_gate.set()
        self._broadcast_top()
        self._mini_idle()
        print("  [web] STOP")

    async def wait_for_play(self) -> None:
        await self._play_gate.wait()

    async def wait_for_stop(self) -> None:
        await self._stop_gate.wait()

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
        self._broadcast_top()
        print(f"  [web] loop mode: {self._top['loopMode']}")

    @property
    def audio_output(self) -> str:
        return self._top["audioOutput"]

    def set_audio_output(self, output) -> None:
        """Send the robots' voices to the robot speakers or to the /top browser.

        Takes effect at once: the page pauses its voice on "robot", and the
        player moves the line being spoken (and the BGM) to the new output.
        Video audio always plays in the browser either way.
        """
        if output not in AUDIO_OUTPUTS or output == self._top["audioOutput"]:
            return
        self._top["audioOutput"] = output
        if output != "browser":
            # Nothing for a (re)connecting page to pick up and replay.
            self.speech_audio_path = None
        self._broadcast_top()
        print(f"  [web] voice output: {output}")

    def _broadcast_minis(self) -> None:
        """Send the current unified mini view to EVERY mini display, identically."""
        msg = dict(self._mini_state)
        for role in ROLES:
            self._latest[role] = msg
            self._push(role, msg)

    def _broadcast_control(self, action: str) -> None:
        msg = {"type": "control", "action": action}
        for channel in CHANNELS:
            self._push(channel, msg)

    # ── Video (top display) ────────────────────────────────────────────────────

    def load_video(self, video_path: Path) -> None:
        self.video_path = Path(video_path)
        self._video_token += 1
        print(f"  [web] load_video token={self._video_token} path={self.video_path}")
        # Prime the next clip without starting playback yet. The browser should
        # only enter video mode when play_video() is called, otherwise a fresh
        # connection can begin playback twice and abort the first request.
        self._top["mode"] = "topic"
        self._broadcast_top()

    def play_video(self) -> None:
        self._video_ended.clear()
        self._top["mode"] = "video"
        self._top["videoEpoch"] += 1
        print(
            f"  [web] play_video epoch={self._top['videoEpoch']} "
            f"token={self._video_token} top_conns={len(self._conns['top'])}"
        )
        self._broadcast_top()
        # Minis sit idle while the video plays.
        self._mini_idle()

    async def wait_for_connection(
        self, channel: str = "top", timeout: float = 30.0
    ) -> bool:
        """Wait until at least one browser is connected to ``channel``.

        Lets a caller hold off starting the video until the top display page is
        actually open, so the video stage is never missed. Returns True if a
        connection arrived, False on timeout.
        """
        deadline = self._loop.time() + timeout
        while self._loop.time() < deadline:
            if self._conns.get(channel):
                return True
            await asyncio.sleep(0.2)
        connected = bool(self._conns.get(channel))
        if not connected:
            print(
                f"  [web] WARNING: no /{channel} WebSocket connected after "
                f"{timeout:.0f}s. Open the IP URL exactly as printed above "
                "(PC-name URLs may resolve to the wrong network adapter)."
            )
        return connected

    async def wait_until_near_end_and_pause(self) -> None:
        if self.video_path is None:
            return
        if not any(self._conns["top"]):
            print("  [web] No top display connected — waiting for the video to end.")
        try:
            await asyncio.wait_for(self._video_ended.wait(), timeout=VIDEO_END_TIMEOUT)
            print(
                f"  [web] video wait completed epoch={self._top['videoEpoch']} "
                f"top_conns={len(self._conns['top'])}"
            )
        except asyncio.TimeoutError:
            print("  [web] WARNING: timed out waiting for the video to end.")

    # ── Presentation flow ──────────────────────────────────────────────────────

    def show_topic(self, title: str, topic: str) -> None:
        self._top.update(mode="topic", title=title, topic=topic, current=None, next=None)
        self._broadcast_top()
        self._mini_idle()

    def point_to(self, next_role: str | None) -> None:
        self._top["current"] = None
        self._top["next"] = next_role
        self._broadcast_top()

        if next_role is None:
            self._mini_idle()
            return

        # Show the transition arrow on both minis. The arrow points TOWARD the
        # next speaker; each mini computes its own direction from its physical
        # position (it sits to the right of its namesake robot), so the two
        # minis can point different ways for a centre speaker. Fade happens on
        # the client only while the arrow is shown.
        self._stop_level()
        self._mini_state.update(state="arrow", speaker=next_role, text="")
        self._broadcast_minis()

    def show_subtitle(
        self,
        role: str,
        text: str,
        turn_number: int | None = None,
        audio_path: str | Path | None = None,
        speech_audio: bool | None = None,
    ) -> None:
        """A robot starts a line: subtitle everywhere, voice in the browser.

        ``speech_audio`` says whether the /top browser plays this line; the
        player passes the decision it made for the turn so the browser and the
        robot speaker never both (or neither) play it. ``None`` follows the
        current voice-output switch. ``audio_path`` still drives the mini orb.
        """
        if speech_audio is None:
            speech_audio = self.audio_output == "browser"
        if not speech_audio:
            self.speech_audio_path = None
        elif audio_path is not None and Path(audio_path).exists():
            self._set_speech(role, Path(audio_path), 0.0)
        self._top["current"] = role
        self._top["next"] = None
        self._broadcast_top()

        # Every mini shows the current speaker's name + subtitle, identically.
        self._last_speaker = role
        self._stop_level()
        self._mini_state.update(state="speaking", speaker=role, text=text)
        self._broadcast_minis()
        self._start_level(audio_path)

    def play_speech(self, role: str, audio_path: str | Path, offset: float = 0.0) -> None:
        """Play a line in the browser from ``offset`` seconds in.

        Used when the voice-output switch moves a line that is already being
        spoken on a robot over to the browser.
        """
        self._set_speech(role, Path(audio_path), offset)
        self._broadcast_top()

    def _set_speech(self, role: str, path: Path, offset: float) -> None:
        self.speech_audio_path = path
        self._top["speechAudioEpoch"] += 1
        self._top["speechPan"] = SPEECH_PAN.get(role, 0.0)
        self._top["speechOffset"] = round(max(0.0, float(offset)), 2)
        # Warm the MP3 cache off-loop so the browser's fetch hits a ready
        # file instead of waiting on the transcode.
        self._loop.create_task(self._get_web_audio(path))

    def finish_subtitle(self, role: str, next_role: str | None = None) -> None:
        self.point_to(next_role)

    # ── Mini state helpers ──────────────────────────────────────────────────────

    def _mini_idle(self) -> None:
        self._stop_level()
        self._mini_state.update(state="idle", speaker=None, text="")
        self._broadcast_minis()

    # ── Audio-level streaming (drives the mini audio orb, server-side) ──────────

    def _start_level(self, audio_path: str | Path | None) -> None:
        if audio_path is None:
            return
        path = Path(audio_path)
        if not path.exists():
            return
        self._level_task = self._loop.create_task(self._stream_levels(path))

    def _stop_level(self) -> None:
        task = self._level_task
        if task is not None and not task.done():
            task.cancel()
        self._level_task = None

    async def _stream_levels(self, path: Path) -> None:
        """Read the wav and push 0..1 levels timed off the wall clock.

        Mirrors displays/audio_level.WavAudioLevelFollower so the browser bar
        matches the Qt version (and the audio the audience hears).
        """
        try:
            rate, data = wavfile.read(path)
        except Exception:
            return

        if data.ndim > 1:
            data = data.mean(axis=1)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        else:
            data = data.astype(np.float32)

        rate = int(rate)
        window = max(1, int(rate * 0.045))
        start = self._loop.time()
        try:
            while True:
                elapsed = self._loop.time() - start
                center = int(elapsed * rate)
                lo = max(0, center - window // 2)
                hi = min(len(data), center + window // 2)
                if lo >= hi:
                    break
                chunk = data[lo:hi]
                rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
                level = min(1.0, rms * 10.0)
                if level > 0.01:
                    level = max(level, 0.12)
                self._push_level_all(level)
                await asyncio.sleep(1 / 30)
        except asyncio.CancelledError:
            pass
        finally:
            self._push_level_all(0.0)

    def _push_level_all(self, value: float) -> None:
        """Push the same audio level to every mini orb."""
        for role in ROLES:
            self._push(role, {"type": "level", "value": value})

    # ── Event-loop integration (no-ops; the browser does its own rendering) ─────

    def process_events(self) -> None:
        pass

    async def pump_events_forever(self, stop: asyncio.Event) -> None:
        await stop.wait()

    def close(self) -> None:
        self._broadcast_control("stop")
        self._stop_level()
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            self._server_task.cancel()
        print("  [web] Display server stopping.")
