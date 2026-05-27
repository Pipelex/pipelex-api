"""ASGI middleware shared across the FastAPI app."""

import re
import secrets
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.error_types import ErrorType
from api.limits import MAX_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_MIB
from api.logging_context import bound_request_context, get_request_id, get_route_path
from api.problem_document import PROBLEM_JSON_MEDIA_TYPE, build_problem_document_from_api_error


def _too_large_response() -> JSONResponse:
    """Build the 413 RFC 7807 problem response for an over-limit request body.

    The body-size check runs in middleware, before routing, so it cannot go
    through the `api.errors` helpers — a middleware must `return` a response,
    not raise. It builds the same problem document directly.
    `RequestIdMiddleware` runs outermost, so the request-scoped contextvars are
    already bound and feed `instance` / `request_id`; that middleware's `send`
    wrapper also stamps the `X-Request-ID` header onto this response.
    """
    document = build_problem_document_from_api_error(
        ErrorType.PAYLOAD_TOO_LARGE,
        f"Request body exceeds {MAX_REQUEST_BODY_MIB} MiB limit",
        413,
        instance=get_route_path(),
        request_id=get_request_id(),
    )
    return JSONResponse(status_code=413, content=document, media_type=PROBLEM_JSON_MEDIA_TYPE)


async def request_body_size_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Reject requests whose body exceeds MAX_REQUEST_BODY_BYTES.

    Two layers of defense:
      1. Trust `Content-Length` header when present — fast reject before any body is read.
      2. For chunked / missing-header requests, wrap `receive` so we count bytes as
         they arrive. When the cumulative count crosses the cap, the over-limit
         chunk is NOT forwarded: it is replaced with an end-of-stream marker so
         `await request.body()` returns a bounded (often empty) body rather than
         the full oversized payload, and any further read returns `http.disconnect`.
         The 413 response then overrides whatever the route returned on its
         truncated input.
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
        if too_large:
            return {"type": "http.disconnect"}
        message = await original_receive()
        if message.get("type") == "http.request":
            body = message.get("body", b"")
            if isinstance(body, (bytes, bytearray)):
                bytes_seen += len(body)
                if bytes_seen > MAX_REQUEST_BODY_BYTES:
                    too_large = True
                    return {"type": "http.request", "body": b"", "more_body": False}
        return message

    request._receive = counting_receive  # type: ignore[assignment]  # noqa: SLF001

    response = await call_next(request)
    if too_large:
        return _too_large_response()
    return response


# --- Request correlation -----------------------------------------------------

REQUEST_ID_HEADER = "X-Request-ID"

# Crockford's Base32 alphabet — the ULID encoding. Excludes I, L, O, U so a
# request id stays unambiguous if a human transcribes it out of a log.
_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH = 26
_ULID_RANDOM_BITS = 80
_ULID_TIMESTAMP_MASK = (1 << 48) - 1

# An inbound X-Request-ID is reflected into response headers, logs, and error
# bodies, so a client-supplied value is never trusted verbatim: it must be a
# bounded run of injection-safe characters or it is discarded. ULIDs and UUIDs
# both satisfy this.
_REQUEST_ID_MAX_LENGTH = 128
_REQUEST_ID_CHARSET = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def generate_request_id() -> str:
    """Generate a fresh ULID: a 26-char, time-sortable, URL-safe identifier.

    The high 48 bits encode a millisecond Unix timestamp, so ids sort by
    creation time; the low 80 bits are cryptographically random. The 128-bit
    value is rendered in Crockford Base32.
    """
    timestamp_ms = (time.time_ns() // 1_000_000) & _ULID_TIMESTAMP_MASK
    value = (timestamp_ms << _ULID_RANDOM_BITS) | secrets.randbits(_ULID_RANDOM_BITS)
    digits = [""] * _ULID_LENGTH
    for index in range(_ULID_LENGTH - 1, -1, -1):
        value, remainder = divmod(value, 32)
        digits[index] = _CROCKFORD_BASE32[remainder]
    return "".join(digits)


def _is_valid_request_id(candidate: str) -> bool:
    """Return whether a client-supplied request id is safe to reflect back verbatim."""
    return 0 < len(candidate) <= _REQUEST_ID_MAX_LENGTH and _REQUEST_ID_CHARSET.match(candidate) is not None


def _resolve_request_id(scope: Scope) -> str:
    """Return the request id for this request.

    Reuses a valid inbound `X-Request-ID` header; otherwise — absent,
    malformed, or over-long — mints a fresh ULID.
    """
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate: str = value.decode("latin-1").strip()
            if _is_valid_request_id(candidate):
                return candidate
            break
    return generate_request_id()


class RequestIdMiddleware:
    """Pure-ASGI middleware that assigns a correlation id to every HTTP request.

    For each request it reuses a valid inbound `X-Request-ID` or mints a fresh
    ULID, stores it on `request.state.request_id`, binds the `request_id` /
    `route_path` logging contextvars for the duration of the request, and
    echoes `X-Request-ID` on the response (success and error alike).

    Applied in `api.main` by wrapping the whole FastAPI app
    (`app = RequestIdMiddleware(app)`), NOT via `app.add_middleware()`.
    `add_middleware` always nests a middleware *inside* Starlette's
    `ServerErrorMiddleware`, which would leave it unable to bind the contextvars
    for — or set a header on — the catch-all 500 that `ServerErrorMiddleware`
    emits. Wrapping the app puts this middleware genuinely outermost, outside
    `ServerErrorMiddleware`, so the contextvars are bound and `X-Request-ID` is
    echoed on every response, the catch-all 500 included. Raw ASGI (rather than
    `BaseHTTPMiddleware`) keeps a single contextvar context across the whole
    stack and lets the `send` wrapper inject the header on any response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with bound_request_context(request_id=request_id, route_path=scope.get("path", "")):
            await self.app(scope, receive, send_with_request_id)
