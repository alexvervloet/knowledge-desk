"""A request body size limit, applied before the body is parsed.

The upload route enforces per-org storage and document caps, but only after
FastAPI has read and validated the whole request into Python objects. The upload
schema permits 1000 documents of 1,000,000 characters, so the request that gets
rejected can be about a gigabyte, and a measured 60 MB payload against a 50 MB
cap still took peak RSS from 66 MB to 373 MB before returning its 413. On a
512 MB machine, the rejection is the expensive part.

This sits at the ASGI layer, below FastAPI, because that is the only place the
decision can be made before the bytes are consumed. Content-Length settles it
without reading anything; a chunked request has no such header, so there the
body is counted as it streams and cut off at the same limit.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "request body too large"},
        )
        await response({"type": "http"}, _no_receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass  # malformed header; the counting path below still applies

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Truncate rather than hand the body on. The request is over
                    # the limit whatever follows, and the app is about to be told
                    # the stream ended.
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counting_receive, send)


async def _no_receive() -> Message:
    """A response that never reads its request still has to be given a receive
    channel. This one is never called."""
    return {"type": "http.disconnect"}  # pragma: no cover
