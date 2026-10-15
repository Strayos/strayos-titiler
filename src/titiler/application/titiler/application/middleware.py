"""Application-specific ASGI middleware."""

import asyncio

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CogTileDeadlineMiddleware:
    """Return 504 when a COG tile has not finished within its wall-clock deadline."""

    def __init__(self, app: ASGIApp, timeout: float) -> None:
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/cog/tiles/"):
            await self.app(scope, receive, send)
            return

        # Hold tile response messages until the handler finishes, so a timeout
        # cannot occur after response headers have already reached the client.
        messages: list[Message] = []

        async def collect(message: Message) -> None:
            messages.append(message)

        task = asyncio.create_task(self.app(scope, receive, collect))
        try:
            done, _ = await asyncio.wait((task,), timeout=self.timeout)
            if task not in done:
                task.cancel()
                # Native raster reads running in a thread may outlive cancellation.
                # Consume any eventual exception without delaying the 504 response.
                task.add_done_callback(
                    lambda finished: (
                        finished.exception() if not finished.cancelled() else None
                    )
                )
                await PlainTextResponse("COG tile request timed out", status_code=504)(
                    scope, receive, send
                )
                return
            await task
        finally:
            if not task.done():
                task.cancel()

        for message in messages:
            await send(message)
