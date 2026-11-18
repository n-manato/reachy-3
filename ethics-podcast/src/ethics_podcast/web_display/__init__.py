"""Browser-based exhibit UI.

A drop-in alternative to the PySide6 ``displays`` package. Instead of four Qt
windows on locally-attached monitors, a small FastAPI/uvicorn server serves one
web page per display and pushes state to the browsers over WebSocket. Any
computer on the LAN can open a page:

    http://<server-ip>:8000/top
    http://<server-ip>:8000/student
    http://<server-ip>:8000/professor
    http://<server-ip>:8000/moderator
    http://<server-ip>:8000/all       (all four tiled — single-machine test)

``WebDisplayManager`` exposes the exact same high-level API as
``displays.manager.DisplayManager`` so the playback loop drives either backend
unchanged. Audio (robot speech wavs + video sound) stays on the server, exactly
as before — the browser only renders the visuals.
"""

from ethics_podcast.web_display.manager import WebDisplayManager

__all__ = ["WebDisplayManager"]
