"""Wake word listener — runs as a standalone subprocess.

Connects to one Reachy Mini's microphone via WebRTC, accumulates audio,
and sends it to Google Speech-to-Text. Prints "WAKE" to stdout when the
activation phrase is detected, then exits cleanly.

Launched the same way as robot_worker.py (subprocess.Popen) to avoid
GStreamer single-pipeline conflicts.

Usage:
    python wake_word.py <robot_host> [robot_name]
"""

import logging
import sys
import time

import numpy as np

CHUNK_SECONDS = 4  # how much audio to accumulate before STT
# Google hears "mello" as "hello"/"mellow" sometimes — accept all variants
NAME_VARIANTS = ("mello", "melo", "mellow", "hello")
TRIGGER_WORD = "podcast"
ROLLING_WINDOW = 3  # check across this many recent transcripts


def main() -> None:
    host = sys.argv[1]
    robot_name = sys.argv[2] if len(sys.argv) > 2 else None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [wake] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("wake_word")

    from reachy_mini import ReachyMini

    kwargs = {"host": host, "connection_mode": "network"}
    if robot_name:
        kwargs["robot_name"] = robot_name

    log.info("Connecting to %s at %s for mic listening...", robot_name or "auto", host)
    robot = ReachyMini(**kwargs)

    # Wait for WebRTC audio to be ready
    time.sleep(6)
    if not robot.media.audio._audio_send_ready:
        log.warning("Audio not ready, polling up to 15s...")
        for i in range(15):
            time.sleep(1)
            if robot.media.audio._audio_send_ready:
                log.info("Audio ready after %ds.", i + 1)
                break

    sample_rate = robot.media.get_input_audio_samplerate()
    log.info("Mic ready. sample_rate=%d", sample_rate)

    # Signal main process we're listening
    sys.stdout.write("LISTENING\n")
    sys.stdout.flush()

    robot.media.start_recording()

    import speech_recognition as sr
    recognizer = sr.Recognizer()

    buffer: list[np.ndarray] = []
    total_samples = 0
    target_samples = sample_rate * CHUNK_SECONDS
    recent_texts: list[str] = []  # rolling window of recent transcripts

    while True:
        samples = robot.media.get_audio_sample()
        if samples is None:
            time.sleep(0.05)
            continue

        buffer.append(samples)
        total_samples += len(samples)

        if total_samples < target_samples:
            continue

        # Combine, convert stereo→mono, float32→int16
        audio = np.concatenate(buffer)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)

        peak = float(np.max(np.abs(audio)))
        if peak < 0.001:
            buffer = []
            total_samples = 0
            continue

        int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)

        audio_data = sr.AudioData(int16.tobytes(), sample_rate, 2)
        try:
            text = recognizer.recognize_google(audio_data).lower()
            recent_texts.append(text)
            if len(recent_texts) > ROLLING_WINDOW:
                recent_texts.pop(0)

            # Check across recent transcripts for both keywords
            combined_text = " ".join(recent_texts)
            has_name = any(v in combined_text for v in NAME_VARIANTS)
            has_trigger = TRIGGER_WORD in combined_text
            if has_name and has_trigger:
                log.info("Wake phrase detected!")
                # Play confirmation sound on the robot
                try:
                    import requests
                    requests.post(f"http://{host}:8000/api/volume/test-sound", timeout=3)
                except Exception:
                    pass
                sys.stdout.write("WAKE\n")
                sys.stdout.flush()
                break
        except sr.UnknownValueError:
            pass
        except sr.RequestError as e:
            log.warning("Google STT unavailable: %s", e)
            time.sleep(2)

        # Keep last 1 second of overlap for continuity
        keep = sample_rate
        combined_audio = np.concatenate(buffer)
        buffer = [combined_audio[-keep:]]
        total_samples = keep

    # Clean up
    robot.media.stop_recording()
    try:
        robot.media.close()
    except Exception:
        pass
    robot.client.disconnect()


if __name__ == "__main__":
    main()
