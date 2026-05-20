"""Shared constants for unit tests.

Each test class assembles a tiny FastAPI app with a couple of throwaway
routes used purely to exercise the auth dependencies through `TestClient`.
Centralising the path strings here means the route names never drift out
of sync between the `add_api_route` call and the `client.get(...)` call.
"""

from pipelex.types import StrEnum


class RoutePath(StrEnum):
    """Route paths registered by the test helper apps.

    Named `RoutePath` (not `TestRoute`) so pytest's `Test*` class-collection
    scanner doesn't try to collect this StrEnum.
    """

    WHOAMI = "/whoami"
    PING = "/ping"
