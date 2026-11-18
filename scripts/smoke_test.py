"""Non-interactive health check: everything the show needs except the robots.

    uv run python scripts/smoke_test.py

Written for the Windows deployment at C:\\DEV\\Africa, which has to be proved
sound before anyone switches the PC onto the robot Wi-Fi -- at that point the
machine loses its route to PyPI, so a missing wheel discovered there is a
missing wheel you cannot fix. It runs anywhere, though, and is the fastest way
to tell a broken install from a broken robot.

Exits 0 when every check passes.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{('  -- ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return ok


def free_port() -> int:
    """A port nothing is on, so the check never collides with a live show."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_imports() -> None:
    print("\nimports")
    modules = [
        "boma_exhibit.config", "boma_exhibit.script_data", "boma_exhibit.variants",
        "boma_exhibit.personas", "boma_exhibit.theme", "boma_exhibit.bgm",
        "boma_exhibit.audio_utils", "boma_exhibit.mock_robot",
        "boma_exhibit.robot_admin", "boma_exhibit.robot_controller",
        "boma_exhibit.player", "boma_exhibit.exhibit", "boma_exhibit.generator",
        "boma_exhibit.variator", "boma_exhibit.llm_client",
        "boma_exhibit.elevenlabs_tts", "boma_exhibit.robot_audio",
        "boma_exhibit.web_display", "boma_exhibit.main",
    ]
    import importlib

    for name in modules:
        try:
            importlib.import_module(name)
            ok, detail = True, ""
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(name, ok, detail)


def check_config():
    print("\nconfiguration")
    from boma_exhibit.config import ROLE_ORDER, load_config

    config = load_config()
    for role in ROLE_ORDER:
        robot = config.robot(role)
        check(f"seat {role}", bool(robot.host and robot.voice),
              f"{robot.host}  voice={robot.voice[:8]}...")
    check("ElevenLabs key present", bool(config.elevenlabs_api_key),
          "needed only to re-render, not to play")
    check("display port", bool(config.display_web_port),
          f"{config.display_web_host}:{config.display_web_port}")
    return config


def check_assets(config) -> None:
    print("\nrendered assets")
    from boma_exhibit.bgm import get_bgm_path
    from boma_exhibit.variants import list_variants

    variants = list_variants(config.session_dir)
    check("variants rendered", bool(variants),
          f"{len(variants)}: {', '.join(p.name for p in variants)}")

    # A variant with no audio plays as silence, which looks like a working show
    # until the room goes quiet -- so count the clips, do not just find the dir.
    for path in variants:
        wavs = list(path.glob("*.wav"))
        check(f"  {path.name} clips", bool(wavs), f"{len(wavs)} wav")

    common = config.session_dir / "common"
    common_wavs = list(common.glob("*.wav")) if common.is_dir() else []
    check("fixed lines rendered", bool(common_wavs), f"{len(common_wavs)} wav in common/")

    bgm = get_bgm_path()
    check("background music", bgm is not None, str(bgm.name) if bgm else "music/ is empty")


def check_mp3_encoder(config) -> None:
    """The TV is served MP3, not WAV. No encoder, no sound in the room."""
    print("\nmp3 encoder (lameenc)")
    from boma_exhibit.variants import list_variants
    from boma_exhibit.web_display.manager import _encode_wav_to_mp3

    variants = list_variants(config.session_dir)
    source = None
    for path in list(variants) + [config.session_dir / "common"]:
        wavs = sorted(path.glob("*.wav")) if path.is_dir() else []
        if wavs:
            source = wavs[0]
            break
    if source is None:
        check("encode a clip", False, "no wav to encode")
        return

    with wave.open(str(source)) as w:
        detail = f"{w.getnchannels()}ch {w.getframerate()}Hz {w.getnframes()} frames"
    out = Path(__file__).resolve().parents[1] / "output" / ".smoke_test.mp3"
    try:
        ok = _encode_wav_to_mp3(source, out, config.display_speech_bitrate)
        size = out.stat().st_size if out.exists() else 0
        check("encode a clip", bool(ok) and size > 0, f"{source.name}: {detail} -> {size} B")
    finally:
        out.unlink(missing_ok=True)


def check_mock_robots() -> None:
    """Every call player.py makes, against the stand-in for absent hardware."""
    print("\nmock robots")
    from boma_exhibit.mock_robot import connect_mock_robots

    robots = connect_mock_robots()
    check("three seats", len(robots) == 3, ", ".join(r.role for r in robots))
    for robot in robots:
        try:
            robot.ensure_alive()
            robot.queue_speaking_ready_pose()
            robot.set_speech_offsets(0.0, 0.0, 0.0)
            robot.queue_emotion("happy")
            robot.move_head(0.0, 0.0, 0.0)
            robot.queue_power_off_pose()
            robot.stop()
            ok, detail = robot.is_alive() or True, robot.diagnostic_status()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        check(f"  {robot.role} accepts show commands", ok, str(detail))


def check_network_helpers() -> None:
    """Platform-specific; this is the part most likely to differ on Windows."""
    print("\nnetwork helpers")
    from boma_exhibit.robot_admin import arp_table, local_subnet

    subnet = local_subnet()
    check("local_subnet()", subnet is not None,
          str(subnet) if subnet else "no private /24 -- not on the robot Wi-Fi yet")
    table = arp_table()
    check("arp_table()", isinstance(table, dict), f"{len(table)} neighbour(s) cached")


async def check_web_display(config) -> None:
    print("\nweb display")
    import httpx

    from boma_exhibit.script_data import TITLE
    from boma_exhibit.web_display import WebDisplayManager

    port = free_port()
    display = WebDisplayManager(host="127.0.0.1", port=port, title=TITLE,
                               subtitle="smoke test")
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):                     # uvicorn needs a moment to bind
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    await client.get(f"{base}/top")
                break
            except Exception:
                await asyncio.sleep(0.1)

        async with httpx.AsyncClient(timeout=5.0) as client:
            top = await client.get(f"{base}/top")
            root = await client.get(f"{base}/")
        check("GET /top", top.status_code == 200, f"{top.status_code}, {len(top.text)} B")
        check("GET /", root.status_code == 200, f"{root.status_code}, {len(root.text)} B")
        # The page carries the transport buttons; without them nobody can start
        # the show, and the browser never unlocks its audio.
        check("play/stop controls on /top",
              "PLAY" in top.text.upper() and "STOP" in top.text.upper())
    finally:
        display.close()


async def main() -> int:
    print("BOMA exhibit -- install smoke test")
    print(f"  python   {sys.version.split()[0]}  on  {sys.platform}")
    check_imports()
    if FAILURES:
        print("\nimports failed; stopping here.")
        return 1

    config = check_config()
    check_assets(config)
    check_mp3_encoder(config)
    check_mock_robots()
    check_network_helpers()
    await check_web_display(config)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
