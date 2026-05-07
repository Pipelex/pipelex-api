"""ASGI middleware shared across the FastAPI app."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.types import Message

from api.error_types import ErrorType
from api.limits import MAX_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_MIB


def _too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "detail": {
                "error_type": ErrorType.PAYLOAD_TOO_LARGE,
                "message": f"Request body exceeds {MAX_REQUEST_BODY_MIB} MiB limit",
            }
        },
    )


async def request_body_size_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Reject requests whose body exceeds MAX_REQUEST_BODY_BYTES.

    Two layers of defense:
      1. Trust `Content-Length` header when present — fast reject before any body is read.
      2. For chunked / missing-header requests, wrap `receive` so we count bytes as
         they arrive and bail out as soon as the limit is exceeded. This avoids
         buffering the full body before refusing.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = -1
        if declared > MAX_REQUEST_BODY_BYTES:
            return _too_large_response()

    original_receive = request.receive
    bytes_seen = 0
    too_large = False

    async def counting_receive() -> Message:
        nonlocal bytes_seen, too_large
        message = await original_receive()
        if message.get("type") == "http.request":
            body = message.get("body", b"")
            if isinstance(body, (bytes, bytearray)):
                bytes_seen += len(body)
                if bytes_seen > MAX_REQUEST_BODY_BYTES:
                    too_large = True
        return message

    request._receive = counting_receive  # type: ignore[assignment]  # noqa: SLF001

    response = await call_next(request)
    if too_large:
        return _too_large_response()
    return response
