"""Turn an album track into a seamless background loop for the exhibit.

Run scripts/find_loop.py first -- it reports where the calmest, best-looping
passage of each track begins.

    uv run python scripts/make_loop.py "music/Alla L'aa Ke.mp3" 118
    uv run python scripts/make_loop.py TRACK START [--length 40] [--out music/boma.mp3]

The result is mono, quiet, midrange-carved and about half a megabyte: it is
streamed to the TV over the same Wi-Fi the robots use for motion control, so
size matters as much as sound.
"""
import argparse, subprocess, sys
from pathlib import Path
import numpy as np

SR = 44100

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path, help="album track to cut from")
parser.add_argument("start", type=float, help="seconds into the track")
parser.add_argument("--length", type=float, default=40.0, help="loop length")
parser.add_argument("--xfade", type=float, default=2.0, help="crossfade at the seam")
parser.add_argument("--out", type=Path, default=Path("music/boma.mp3"))
args = parser.parse_args()

SRC, START, LENGTH, XFADE, OUT = (
    args.source, args.start, args.length, args.xfade, args.out
)


def decode(path, start, dur):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", str(path),
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype="<f4").astype(np.float64)


n_len = int(LENGTH * SR)
n_x = int(XFADE * SR)
audio = decode(SRC, START, LENGTH + XFADE + 0.5)
assert len(audio) >= n_len + n_x, "source too short"

body = audio[:n_len].copy()
tail = audio[n_len:n_len + n_x]

# Blend what comes *after* the loop into its opening, so the moment the player
# wraps from the end back to the start there is no step in the waveform.
# Equal-power ramps keep the level constant through the blend.
t = np.linspace(0, 1, n_x, endpoint=False)
body[:n_x] = body[:n_x] * np.sin(t * np.pi / 2) ** 2 + tail * np.cos(t * np.pi / 2) ** 2

peak = np.max(np.abs(body))
body = body / peak * 0.89          # headroom; the player attenuates further

seam = abs(body[0] - body[-1])
print(f"source     : {SRC.name} @ {int(START)//60}:{int(START)%60:02d}")
print(f"loop       : {LENGTH:.0f}s, {XFADE:.0f}s crossfade")
print(f"seam step  : {seam:.5f}  (0 = perfectly continuous)")

raw = (body * 32767).astype("<i2").tobytes()
subprocess.run(
    ["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
     # A broad, shallow dip through the speech band. The kora keeps its body and
     # its sparkle; what comes out is the middle, which is where the robots live.
     "-af", "equalizer=f=1400:width_type=o:width=2.2:g=-4.5,"
            "equalizer=f=600:width_type=o:width=1.4:g=-2.0,"
            "loudnorm=I=-20:TP=-2:LRA=7",
     "-c:a", "libmp3lame", "-b:a", "96k", str(OUT)],
    input=raw, check=True)
print(f"written    : {OUT}  {OUT.stat().st_size/1024:.0f} KB")
