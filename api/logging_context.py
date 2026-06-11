"""Per-request logging context — request id and route path as contextvars.

`api.middleware.RequestIdMiddleware` binds both values at request entry, for
the duration of the request. Downstream code reads them through the getters
below without threading a `Request` object through its signatures — the global
exception handlers, the 4xx helpers in `api.errors`, and structured-log call
sites all rely on this.

Both contextvars default to `None`, so the getters are safe to call outside a
request scope (a CLI import of an `api` module, a unit test that issues no
request).
"""

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

_request_id_ctxvar: contextvars.ContextVar[str | None] = contextvars.ContextVar("pipelex_api_request_id", default=None)
_route_path_ctxvar: contextvars.ContextVar[str | None] = contextvars.ContextVar("pipelex_api_route_path", default=None)


def get_request_id() -> str | None:
    """Return the current request's correlation id, or `None` outside a request scope."""
    return _request_id_ctxvar.get()


def get_route_path() -> str | None:
    """Return the current request's URL path, or `None` outside a request scope."""
    return _route_path_ctxvar.get()


@contextmanager
def bound_request_context(*, request_id: str, route_path: str) -> Generator[None]:
    """Bind the request-scoped logging contextvars for the duration of the `with` block.

    Resets both contextvars to their prior state on exit — including when the
    wrapped request raises — so a value never leaks into an unrelated context.
    """
    request_id_token = _request_id_ctxvar.set(request_id)
    route_path_token = _route_path_ctxvar.set(route_path)
    try:
        yield
    finally:
        _route_path_ctxvar.reset(route_path_token)
        _request_id_ctxvar.reset(request_id_token)
