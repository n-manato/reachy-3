"""Robot microphone / speaker over TCP, for use through the SSH tunnel (runs on Reachy Mini).

The SDK's WebRTC audio is UDP and does not get through the campus VPN, so the
tunnel carries raw PCM instead. Both use the shared ALSA devices that the
daemon also uses (dsnoop / dmix), so they do not block it.

    GET http://127.0.0.1:8092/mic          endless raw PCM: S16LE, 16 kHz, 2 channels
                                           (CORS open, so the viewer page can play it)
    GET http://127.0.0.1:8092/webcam_mic   same format, from the USB webcam's microphone
                                           (404 without a webcam, 503 while someone else listens)
    TCP 127.0.0.1:8093                     send raw PCM S16LE 16 kHz 2 ch -> robot speaker

The robot's own microphone barely picks up its own speaker (its audio is
processed, probably echo cancelled), so the webcam microphone is the way to
hear from afar what the robot plays.

Started automatically by reachy_kit.tunnel. Only listens on 127.0.0.1.
"""

import array
import http.server
import socketserver
import subprocess
import threading
from typing import Optional

RATE, CHANNELS = 16000, 2
FMT = ["-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"]
MIC_CMD = ["arecord", "-q", "-D", "reachymini_audio_src", *FMT]
ROBOT_CARD = "Reachy Mini Audio"
SPEAKER_CMD = ["aplay", "-q", "-D", "reachymini_audio_sink", "--buffer-time=200000", *FMT]
MIC_PORT, SPEAKER_PORT = 8092, 8093
CHUNK = RATE * CHANNELS * 2 // 50  # 20 ms


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


class MicHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/mic":
            cmd, convert, chunk = MIC_CMD, None, CHUNK
        elif self.path == "/webcam_mic":
            card = webcam_card()
            if card is None:
                self.send_error(404, "No webcam microphone")
                return
            # A webcam is a plain hw device: one reader at a time, mono.
            cmd = ["arecord", "-q", "-D", f"plughw:{card},0", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
            convert, chunk = mono_to_stereo, CHUNK // CHANNELS
        else:
            self.send_error(404)
            return
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            data = proc.stdout.read(chunk)
            if not data:  # device busy (someone else is listening) or gone
                self.send_error(503, "Microphone busy or unavailable")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Audio-Format", f"s16le;rate={RATE};channels={CHANNELS}")
            self.end_headers()
            while data:
                self.wfile.write(convert(data) if convert else data)
                data = proc.stdout.read(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()


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
    print(f"audio bridge: mic http://127.0.0.1:{MIC_PORT}/mic (webcam: /webcam_mic), "
          f"speaker tcp 127.0.0.1:{SPEAKER_PORT}", flush=True)
    ThreadingHTTP(("127.0.0.1", MIC_PORT), MicHandler).serve_forever()


if __name__ == "__main__":
    main()
