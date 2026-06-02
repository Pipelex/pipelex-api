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


# A minimal, valid single-pipe bundle used across the build/validate/pipeline route tests.
VALID_MTHDS = (
    'domain = "smoke"\n'
    'main_pipe = "echo"\n'
    "\n"
    "[pipe.echo]\n"
    'type = "PipeLLM"\n'
    'description = "Echo"\n'
    'inputs = { text = "Text" }\n'
    'output = "Text"\n'
    'prompt = "@text"\n'
)

# A bundle whose PipeSequence references an unimplemented PipeSignature step. It loads and wires
# cleanly, so the only thing that rejects it in strict mode is the signature pre-pass — isolating
# the `allow_signatures` behavior from any other validation failure.
SIGNATURE_MTHDS = (
    'domain = "sig_api"\n'
    'main_pipe = "caller_seq"\n\n'
    "[concept]\n"
    'ApiDoc = "A document used in API signature tests."\n'
    'ApiSummary = "A summary used in API signature tests."\n\n'
    "[pipe.caller_seq]\n"
    'type = "PipeSequence"\n'
    'description = "Caller sequence referencing a signature step."\n'
    'inputs = { doc = "ApiDoc" }\n'
    'output = "ApiSummary"\n'
    'steps = [ { pipe = "summary_sig", result = "summary" } ]\n\n'
    "[pipe.summary_sig]\n"
    'type = "PipeSignature"\n'
    'description = "Signature placeholder for the summary step."\n'
    'inputs = { doc = "ApiDoc" }\n'
    'output = "ApiSummary"\n'
)
