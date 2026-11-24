"""Refuse request bodies over a size limit with a 413, before anything parses them.

Neither FastAPI nor uvicorn bounds a request body. A body that declares its length is refused from the header alone;
a chunked body is read up to the limit and refused as soon as it passes it, so the API never holds more than the
limit of one request. Like the other HTTP errors (404, 409, 422), a refused request never reaches an episode and is
not recorded.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            # uvicorn refuses a body longer than its Content-Length, so a declared length within the limit is enough
            if declared.isdigit() and int(declared) > self.max_bytes:
                await self.refuse(send)
                return
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away
                await self.app(scope, _replay([message], receive), send)
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self.refuse(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body: Message = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        await self.app(scope, _replay([body], receive), send)

    async def refuse(self, send: Send) -> None:
        detail = {"detail": f"request body over {self.max_bytes} bytes"}
        content = json.dumps(detail).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(content)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": content})


def _replay(messages: list[Message], receive: Receive) -> Receive:
    pending = list(messages)

    async def replayed() -> Message:
        return pending.pop(0) if pending else await receive()

    return replayed
