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
VALID_MTHDS = """\
domain = "smoke"
main_pipe = "echo"

[pipe.echo]
type = "PipeLLM"
description = "Echo"
inputs = { text = "Text" }
output = "Text"
prompt = "@text"
"""

# A valid single-pipe bundle that declares NO main_pipe — validates fine (D2: no main-pipe
# precondition on /validate) and simply yields no graph.
NO_MAIN_PIPE_MTHDS = """\
domain = "nomain"

[pipe.echo]
type = "PipeLLM"
description = "Echo"
inputs = { text = "Text" }
output = "Text"
prompt = "@text"
"""

# A bundle whose PipeSequence references an unimplemented PipeSignature step. It loads and wires
# cleanly, so the only thing that rejects it in strict mode is the signature pre-pass — isolating
# the `allow_signatures` behavior from any other validation failure.
SIGNATURE_MTHDS = """\
domain = "sig_api"
main_pipe = "caller_seq"

[concept]
ApiDoc = "A document used in API signature tests."
ApiSummary = "A summary used in API signature tests."

[pipe.caller_seq]
type = "PipeSequence"
description = "Caller sequence referencing a signature step."
inputs = { doc = "ApiDoc" }
output = "ApiSummary"
steps = [ { pipe = "summary_sig", result = "summary" } ]

[pipe.summary_sig]
type = "PipeSignature"
description = "Signature placeholder for the summary step."
inputs = { doc = "ApiDoc" }
output = "ApiSummary"
"""
