"""One public port, two exhibits.

The Tunnelin tunnel behind https://rm2.aiccore-uno.ai/ points at this PC's
port 8010. This small reverse proxy owns that port and splits it by path:

    /lta/...   -> the Leading Through AI exhibit   (127.0.0.1:8020, prefix stripped)
    /...       -> the Ethics Podcast (Reachy_3)    (127.0.0.1:8011)

HTTP (including audio Range requests) and WebSockets are both forwarded, so
every page works through it exactly as it does directly. It holds no state
and knows nothing about either exhibit, so either one can be stopped or
restarted without touching the other's URL.

    uv run lta-gateway            # GATEWAY_PORT, GATEWAY_ROOT_PORT, DISPLAY_WEB_PORT
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

PREFIX = "lta"

# Hop-by-hop headers (RFC 7230 6.1) are per connection and never forwarded.
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host",
}


def _down_page(name: str, port: int) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5>"
        "<body style='font-family:system-ui;background:#0a0c11;color:#f3efe6;padding:40px'>"
        f"<h2>{name} is not running right now</h2>"
        f"<p style='color:#9aa3b2'>Nothing answered on port {port}. This page retries every 5 seconds.</p>",
        status_code=503,  # 502 bodies are replaced by the tunnel; 503 passes through
    )


def build_app(root_port: int, lta_port: int) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # trust_env=False: the campus proxy variables must never catch localhost.
    client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(30.0, connect=3.0))

    def route(path: str) -> tuple[int, str, str]:
        """(upstream port, upstream path, exhibit name) for a request path."""
        if path == PREFIX or path.startswith(PREFIX + "/"):
            return lta_port, "/" + path[len(PREFIX):].lstrip("/"), "The Leading Through AI exhibit"
        return root_port, "/" + path, "The Ethics Podcast exhibit"

    @app.api_route("/" + PREFIX, methods=["GET", "HEAD"])
    async def lta_slash() -> Response:
        # Pages use relative links, which need the trailing slash.
        return RedirectResponse("/" + PREFIX + "/", status_code=308)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy_http(path: str, request: Request) -> Response:
        port, upstream_path, name = route(path)
        url = f"http://127.0.0.1:{port}{upstream_path}"
        if request.url.query:
            url += "?" + request.url.query
        headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _HOP]
        forwarded = request.headers.get("x-forwarded-proto") or request.url.scheme
        headers.append(("x-forwarded-proto", forwarded))
        upstream_req = client.build_request(request.method, url, headers=headers, content=await request.body())
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.ConnectError:
            return _down_page(name, port)
        except httpx.HTTPError as exc:
            return Response(f"Upstream error: {exc}", status_code=502)
        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
        location = out_headers.get("location")
        if location and port == lta_port and location.startswith("/"):
            out_headers["location"] = "/" + PREFIX + location
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=out_headers,
            background=BackgroundTask(upstream.aclose),
        )

    @app.websocket("/{path:path}")
    async def proxy_ws(websocket: WebSocket, path: str) -> None:
        port, upstream_path, _name = route(path)
        url = f"ws://127.0.0.1:{port}{upstream_path}"
        if websocket.url.query:
            url += "?" + websocket.url.query
        try:
            upstream = await websockets.connect(url, open_timeout=5, max_size=None, proxy=None)
        except Exception:
            await websocket.close(code=1013)  # try again later: the page reconnects
            return
        await websocket.accept()

        async def browser_to_upstream() -> None:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])

        async def upstream_to_browser() -> None:
            async for data in upstream:
                if isinstance(data, bytes):
                    await websocket.send_bytes(data)
                else:
                    await websocket.send_text(data)

        tasks = [asyncio.create_task(browser_to_upstream()), asyncio.create_task(upstream_to_browser())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(BaseException):
                    await task
            with contextlib.suppress(Exception):
                await upstream.close()
            with contextlib.suppress(Exception, WebSocketDisconnect):
                await websocket.close()

    @app.on_event("shutdown")
    async def _close_client() -> None:
        await client.aclose()

    return app


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    port = int(os.getenv("GATEWAY_PORT", "8010"))
    root_port = int(os.getenv("GATEWAY_ROOT_PORT", "8011"))
    lta_port = int(os.getenv("DISPLAY_WEB_PORT", "8020"))
    print(f"  [gateway] :{port}  /{PREFIX}/ -> :{lta_port}   / -> :{root_port}", flush=True)
    uvicorn.run(build_app(root_port, lta_port), host="0.0.0.0", port=port,
                log_level="warning", proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
