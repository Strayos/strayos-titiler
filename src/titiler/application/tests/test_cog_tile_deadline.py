"""Test the wall-clock deadline applied to COG tile responses."""

import asyncio

import pytest
from starlette.responses import PlainTextResponse

from titiler.application.middleware import CogTileDeadlineMiddleware


async def request(app, path: str) -> tuple[int, bytes]:
    """Issue an ASGI request and collect its status and response body."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {"type": "http", "path": path, "method": "GET", "headers": []},
        receive,
        send,
    )
    return next(
        m["status"] for m in messages if m["type"] == "http.response.start"
    ), b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )


@pytest.mark.asyncio
async def test_slow_tile_times_out_without_waiting_for_cancellation():
    """A handler finishing after the deadline cannot send its late tile."""

    async def slow_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)
        await send({"type": "http.response.body", "body": b"late tile"})

    app = CogTileDeadlineMiddleware(slow_app, timeout=0.01)
    status, body = await asyncio.wait_for(
        request(app, "/cog/tiles/WebMercatorQuad/8/87/48.png"), timeout=0.08
    )
    assert status == 504
    assert body == b"COG tile request timed out"


@pytest.mark.asyncio
async def test_tile_deadline_does_not_affect_other_routes_or_fast_tiles():
    """Fast tile responses and other slow endpoints keep their normal responses."""

    async def app_handler(scope, receive, send):
        if scope["path"] == "/cog/info":
            await asyncio.sleep(0.02)
        await PlainTextResponse("ok")(scope, receive, send)

    app = CogTileDeadlineMiddleware(app_handler, timeout=0.01)
    assert await request(app, "/cog/tiles/WebMercatorQuad/8/87/48") == (200, b"ok")
    assert await request(app, "/cog/info") == (200, b"ok")
