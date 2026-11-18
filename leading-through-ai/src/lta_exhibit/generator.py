"""Render every line of every reviewed script to audio, before the show.

Layout, per topic:

    output/<topic>/script.json          the reviewed words (input)
    output/<topic>/clips/<seg>_<n>_<role>.wav  (+ .txt fingerprint sidecar)
    output/<topic>/conversation.json    script + audio file + duration + cues

A clip is only re-synthesised when its text, voice or model changes, so an
edited line costs one clip, and an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lta_exhibit.audio_utils import duration_secs, normalize_rms, read_wav, write_wav
from lta_exhibit.config import OUTPUT_DIR, Config
from lta_exhibit.elevenlabs_tts import ElevenLabsError, settings_for, synthesize_pcm
from lta_exhibit.script_gen import SEGMENT_ORDER, load_script, script_path, to_markdown
from lta_exhibit.topics import TOPIC_IDS

CLIPS_DIR = "clips"


def _fingerprint(text: str, voice_id: str, model_id: str) -> str:
    return f"model={model_id}\nvoice={voice_id}\ntext={text}\n"


def _render_clip(config: Config, path: Path, text: str, role: str, *, force: bool) -> tuple[bytes, bool]:
    """Return (pcm, freshly_rendered)."""
    voice_id = config.robot(role).voice
    model_id = config.elevenlabs_model
    sidecar = path.with_suffix(".txt")
    want = _fingerprint(text, voice_id, model_id)

    if not force and path.exists() and sidecar.exists():
        if sidecar.read_text(encoding="utf-8") == want:
            pcm, _ = read_wav(path)
            return pcm, False

    pcm = synthesize_pcm(
        text=text,
        voice_id=voice_id,
        settings=settings_for(role),
        api_key=config.elevenlabs_api_key,
        model_id=model_id,
        timeout=90.0,
    )
    pcm = normalize_rms(pcm)
    write_wav(path, pcm)
    sidecar.write_text(want, encoding="utf-8")
    return pcm, True


def _tool_calls(line: dict, duration: float) -> list[dict[str, Any]]:
    emotion = line.get("emotion")
    if not emotion:
        return []
    return [
        {
            "name": "play_emotion",
            "arguments": {"emotion": emotion},
            "at_secs": round(float(line.get("emotion_at", 0.2)) * duration, 2),
        }
    ]


def render_topic(config: Config, topic_id: str, *, force: bool = False, progress=print) -> Path | None:
    script = load_script(topic_id)
    if script is None:
        progress(f"  {topic_id}: no script.json yet -- run menu 1 first.")
        return None

    topic_dir = OUTPUT_DIR / topic_id
    # Keep the readable copy in step with any hand edits to script.json.
    script_path(topic_id).with_suffix(".md").write_text(to_markdown(script), encoding="utf-8")
    segments_out: dict[str, list[dict]] = {}
    previous = (load_conversation(topic_id) or {}).get("segments", {})
    try:
        _render_segments(config, script, topic_dir, segments_out, force, progress)
    except BaseException:
        # A clip that was re-rendered before the failure now has new audio.
        # Keep conversation.json in step with what is on disk -- new entries
        # for the lines already done, the old ones for the rest -- so the show
        # never plays new audio under an old caption and duration.
        if previous:
            for seg in SEGMENT_ORDER:
                done = segments_out.get(seg, [])
                segments_out[seg] = done + previous.get(seg, [])[len(done):]
            _write_conversation(config, script, topic_dir, segments_out)
            progress(f"  {topic_id}: render stopped; conversation.json kept consistent with the clips on disk")
        raise
    fresh = segments_out.pop("__fresh__", 0)
    out = _write_conversation(config, script, topic_dir, segments_out)
    total = sum(len(v) for v in segments_out.values())
    progress(f"  {topic_id}: {total} clips ({fresh} newly rendered) -> {out.name}")
    return out


def _render_segments(config, script, topic_dir, segments_out, force, progress) -> None:
    """Render every line, filling segments_out in place as each clip lands."""
    fresh = 0
    for segment in SEGMENT_ORDER:
        lines = script["segments"].get(segment, [])
        rendered: list[dict] = []
        segments_out[segment] = rendered
        for index, line in enumerate(lines, start=1):
            role = line["role"]
            filename = f"{segment}_{index:02d}_{role}.wav"
            path = topic_dir / CLIPS_DIR / filename
            pcm, new = _render_clip(config, path, line["text"], role, force=force)
            fresh += int(new)
            duration = duration_secs(pcm)
            rendered.append(
                {
                    "role": role,
                    "text": line["text"],
                    "audio_file": f"{CLIPS_DIR}/{filename}",
                    "duration_secs": round(duration, 2),
                    "tool_calls": _tool_calls(line, duration),
                }
            )
            if new:
                progress(f"    {segment:<12} {role:<6} {line['text'][:60]}")
    segments_out["__fresh__"] = fresh


def _write_conversation(config, script, topic_dir, segments_out) -> Path:
    meta = {
        "topic_id": script["topic_id"],
        "title": script["title"],
        "question": script.get("question", ""),
        "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tts_model": config.elevenlabs_model,
        "voices": {role: rc.voice for role, rc in config.robots.items()},
        "segments": segments_out,
    }
    out = topic_dir / "conversation.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)  # atomic: the show never reads a half-written file
    return out


def render_all(config: Config, topic_ids: list[str] | None = None, *, force: bool = False, progress=print) -> list[Path]:
    if not config.elevenlabs_api_key:
        raise ValueError("ELEVENLABS_API_KEY is not set in .env")
    progress(f"  TTS model: {config.elevenlabs_model}")
    written = []
    for topic_id in topic_ids or TOPIC_IDS:
        try:
            path = render_topic(config, topic_id, force=force, progress=progress)
        except ElevenLabsError as exc:
            progress(f"\n  TTS failed: {exc}\n  Re-run to resume from the last good clip.")
            raise
        if path:
            written.append(path)
    return written


def load_conversation(topic_id: str) -> dict | None:
    path = OUTPUT_DIR / topic_id / "conversation.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def rendered_topics() -> list[str]:
    return [t for t in TOPIC_IDS if (OUTPUT_DIR / t / "conversation.json").exists()]


if __name__ == "__main__":  # pragma: no cover
    import sys

    from lta_exhibit.config import load_config

    ids = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    render_all(load_config(), ids, force="--force" in sys.argv)
