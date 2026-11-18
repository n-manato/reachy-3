"""Robot microphone / speaker over TCP, for use through the SSH tunnel (runs on Reachy Mini).

The SDK's WebRTC audio is UDP and does not get through the campus VPN, so the
tunnel carries raw PCM instead. Both use the shared ALSA devices that the
daemon also uses (dsnoop / dmix), so they do not block it.

    GET http://127.0.0.1:8092/mic          endless raw PCM: S16LE, 16 kHz, 2 channels
                                           (CORS open, so the viewer page can play it)
    GET http://127.0.0.1:8092/webcam_mic   same format, from the USB webcam's microphone
                                           (404 without a webcam, 503 if it cannot be opened)
    GET http://127.0.0.1:8092/version      hash of this script, so a tunnel can replace an old bridge
    TCP 127.0.0.1:8093                     send raw PCM S16LE 16 kHz 2 ch -> robot speaker

The robot's own microphone barely picks up its own speaker (its audio is
processed, probably echo cancelled), so the webcam microphone is the way to
hear from afar what the robot plays.

Started automatically by reachy_kit.tunnel. Only listens on 127.0.0.1.
"""

import array
import hashlib
import http.server
import queue
import socketserver
import subprocess
import threading
import time
from typing import Optional

RATE, CHANNELS = 16000, 2
FMT = ["-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"]
MIC_CMD = ["arecord", "-q", "-D", "reachymini_audio_src", *FMT]
ROBOT_CARD = "Reachy Mini Audio"
SPEAKER_CMD = ["aplay", "-q", "-D", "reachymini_audio_sink", "--buffer-time=200000", *FMT]
MIC_PORT, SPEAKER_PORT = 8092, 8093
CHUNK = RATE * CHANNELS * 2 // 50  # 20 ms
with open(__file__, "rb") as f:
    VERSION = hashlib.sha256(f.read()).hexdigest()[:12]


def webcam_card() -> Optional[int]:
    """ALSA card number of the USB webcam's microphone (any USB audio card but the robot's)."""
    try:
        with open("/proc/asound/cards") as f:
            for line in f:
                # " 3 [E4K            ]: USB-Audio - EMEET SmartCam C960 4K"
                head, _, desc = line.partition("]:")
                if "USB-Audio" in desc and ROBOT_CARD not in desc:
                    return int(head.split("[")[0])
    except (OSError, ValueError):
        pass
    return None


def mono_to_stereo(data: bytes) -> bytes:
    mono = array.array("h", data)
    stereo = array.array("h", bytes(len(data) * 2))
    stereo[0::2] = mono
    stereo[1::2] = mono
    return stereo.tobytes()


class WebcamMic:
    """One arecord on the webcam, shared by every listener.

    The webcam is a plain hw device that only one process can open, so the
    capture runs once and each listener gets its own queue of chunks. It
    starts with the first listener, stops after the last one, and reopens the
    device if it goes away (e.g. the webcam is replugged).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: set = set()
        self._thread: Optional[threading.Thread] = None

    def subscribe(self) -> "queue.Queue[bytes]":
        q: "queue.Queue[bytes]" = queue.Queue(maxsize=100)  # 2 s
        with self._lock:
            self._listeners.add(q)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._capture, daemon=True)
                self._thread.start()
        return q

    def unsubscribe(self, q: "queue.Queue[bytes]") -> None:
        with self._lock:
            self._listeners.discard(q)

    def _capture(self) -> None:
        while True:
            with self._lock:
                if not self._listeners:
                    self._thread = None
                    return
            card = webcam_card()
            if card is None:
                time.sleep(1)
                continue
            cmd = ["arecord", "-q", "-D", f"plughw:{card},0", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                while True:
                    data = proc.stdout.read(CHUNK // CHANNELS)
                    if not data:
                        break
                    data = mono_to_stereo(data)
                    with self._lock:
                        if not self._listeners:
                            break
                        listeners = list(self._listeners)
                    for q in listeners:
                        if q.full():
                            try:
                                q.get_nowait()  # a slow listener drops its oldest chunk
                            except queue.Empty:
                                pass
                        q.put_nowait(data)
            finally:
                proc.kill()
                proc.wait()
            time.sleep(0.5)  # device gone or busy: retry while someone listens


WEBCAM_MIC = WebcamMic()


class MicHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def start_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Audio-Format", f"s16le;rate={RATE};channels={CHANNELS}")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/mic":
            self.robot_mic()
        elif self.path == "/webcam_mic":
            self.webcam_mic()
        elif self.path == "/version":
            body = VERSION.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def robot_mic(self) -> None:
        self.start_stream()
        proc = subprocess.Popen(MIC_CMD, stdout=subprocess.PIPE)
        try:
            while True:
                data = proc.stdout.read(CHUNK)
                if not data:
                    break
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()

    def webcam_mic(self) -> None:
        if webcam_card() is None:
            self.send_error(404, "No webcam microphone")
            return
        q = WEBCAM_MIC.subscribe()
        try:
            try:
                data = q.get(timeout=3)
            except queue.Empty:  # the device did not open
                self.send_error(503, "Webcam microphone unavailable")
                return
            self.start_stream()
            while True:
                self.wfile.write(data)
                data = q.get(timeout=5)  # no data for 5 s: end, the client reconnects
        except (BrokenPipeError, ConnectionResetError, queue.Empty):
            pass
        finally:
            WEBCAM_MIC.unsubscribe(q)


class SpeakerHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        # stderr off: aplay reports an "underrun" every time the stream pauses
        proc = subprocess.Popen(SPEAKER_CMD, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                data = self.request.recv(65536)
                if not data:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
                proc.wait(timeout=60)  # let queued audio finish playing
            except Exception:
                proc.kill()


class ThreadingHTTP(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ThreadingTCP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    speaker = ThreadingTCP(("127.0.0.1", SPEAKER_PORT), SpeakerHandler)
    threading.Thread(target=speaker.serve_forever, daemon=True).start()
    print(f"audio bridge {VERSION}: mic http://127.0.0.1:{MIC_PORT}/mic (webcam: /webcam_mic), "
          f"speaker tcp 127.0.0.1:{SPEAKER_PORT}", flush=True)
    ThreadingHTTP(("127.0.0.1", MIC_PORT), MicHandler).serve_forever()


if __name__ == "__main__":
    main()
