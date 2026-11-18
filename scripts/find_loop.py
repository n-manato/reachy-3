"""Find the best background-music loop inside an album track.

Scores every candidate window on three things a background bed has to get
right, and that a human would otherwise hunt for by ear:

  steadiness  the level must not swing, or the bed will pump under dialogue
  stillness   the spectrum must not change much, or it draws attention
  seam        the start and end must match, or the loop point is audible
"""
import subprocess, sys
from pathlib import Path
import numpy as np

SR = 22050
WIN = 40.0          # target loop length, seconds
HOP = 2.0           # how finely to search
EDGE = 15.0         # ignore the first/last seconds (intros, fades, applause)


def load(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def frames(x, n=2048, hop=1024):
    count = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    return x[idx] * np.hanning(n)


def analyse(path):
    x = load(path)
    n, hop = 2048, 1024
    F = frames(x, n, hop)
    spec = np.abs(np.fft.rfft(F, axis=1))
    rms = np.sqrt(np.mean(F ** 2, axis=1)) + 1e-9
    fps = SR / hop

    # Normalised spectra, for measuring how much the colour of the sound moves.
    norm = spec / (spec.sum(axis=1, keepdims=True) + 1e-9)
    flux = np.abs(np.diff(norm, axis=0)).sum(axis=1)
    flux = np.concatenate([flux[:1], flux])

    win_f = int(WIN * fps)
    hop_f = int(HOP * fps)
    edge_f = int(EDGE * fps)

    best = []
    for start in range(edge_f, len(rms) - win_f - edge_f, hop_f):
        end = start + win_f
        seg_rms = rms[start:end]
        seg_flux = flux[start:end]

        # Steadiness: spread of level in dB across the window.
        db = 20 * np.log10(seg_rms)
        steadiness = float(np.std(db))

        # Stillness: how much the spectrum churns.
        stillness = float(np.mean(seg_flux))

        # Seam: compare a second of audio at each end, in level and colour.
        tail = norm[end - int(fps):end].mean(axis=0)
        head = norm[start:start + int(fps)].mean(axis=0)
        seam_spec = float(np.abs(tail - head).sum())
        seam_level = abs(float(20 * np.log10(rms[end - int(fps):end].mean()
                                             / rms[start:start + int(fps)].mean())))

        # Loud enough to be worth using at all.
        level = float(np.mean(seg_rms))

        score = (steadiness / 3.0) + (stillness * 6.0) + (seam_spec * 2.5) + (seam_level / 3.0)
        best.append((score, start / fps, steadiness, stillness, seam_spec, seam_level, level))

    best.sort()
    return best


def main():
    print(f"searching for the calmest, best-looping {WIN:.0f}s in each track\n")
    print(f"  {'track':<16} {'best at':>9}  {'score':>6}  {'level swing':>11} "
          f"{'churn':>7} {'seam':>7}  {'rms':>6}")
    results = []
    for path in sorted(Path("music").glob("*.mp3")):
        rows = analyse(path)
        if not rows:
            continue
        s, at, steady, still, seam, seaml, level = rows[0]
        mm, ss = divmod(int(at), 60)
        print(f"  {path.stem:<16} {mm:>4}:{ss:02d}    {s:6.2f}  {steady:8.2f} dB "
              f"{still:7.4f} {seam:7.3f}  {level:6.3f}")
        results.append((s, path, at))
    results.sort(key=lambda r: r[0])
    print(f"\nbest overall: {results[0][1].stem} at {int(results[0][2])//60}:"
          f"{int(results[0][2])%60:02d}")


main()
