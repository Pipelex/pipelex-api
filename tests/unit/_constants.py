"""Shared constants for unit tests.

Each test class assembles a tiny FastAPI app with a couple of throwaway
routes used purely to exercise the auth dependencies through `TestClient`.
Centralising the path strings here means the route names never drift out
of sync between the `add_api_route` call and the `client.get(...)` call.
"""

from enum import StrEnum


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

# An invalid `main_pipe` deterministically fails blueprint validation, producing a categorized
# BLUEPRINT_VALIDATION error that carries the blueprint's `source` — the cheapest way to exercise
# the structured `validation_errors` 422 and its `source` threading. (Mirrors the pipelex
# integration fixture in `tests/integration/pipelex/pipeline/test_validate_bundle_source_threading.py`.)
INVALID_MAIN_PIPE_MTHDS = """\
domain = "broken"
description = "Invalid main_pipe"
main_pipe = "Not A Valid Pipe Code!"

[concept.Customer]
description = "A customer"
"""

# Multi-file batches mirroring pipelex's additive-multi-file-library E2E fixtures
# (`tests/e2e/pipelex/pipes/additive_multi_file_library/` in the pipelex repo) — the same
# scenarios the protocol-alignment baseline snapshots were captured from. Copied, not read
# from the pipelex checkout: the suite must stay self-contained once the editable pin is
# replaced by the PyPI pin (whose wheel ships no tests).

# signature_only/: concepts + a PipeSignature header referenced by a sibling controller —
# valid only in lenient mode (`allow_signatures=True`), reports the pending signature.
SIGNATURE_ONLY_BATCH: list[str] = [
    """\
domain      = "research"
description = "Research method domain"

[concept]
KeyFinding = "A key finding extracted from a source document"
""",
    """\
domain      = "research"
description = "Research method headers"

[pipe.find_key_findings]
description = "Find the key findings in a document (contract only)."
inputs      = { doc = "Text" }
output      = "KeyFinding"

[pipe.research_brief]
type        = "PipeSequence"
description = "Produce a research brief from a document."
inputs      = { doc = "Text" }
output      = "KeyFinding"
steps       = [{ pipe = "find_key_findings", result = "findings" }]
""",
]

# header_and_definition/: the same header plus a concrete definition satisfying it —
# valid in strict mode, nothing pending.
HEADER_AND_DEFINITION_BATCH: list[str] = [
    """\
domain      = "research"
description = "Research method domain"

[concept]
KeyFinding = "A key finding extracted from a source document"
""",
    """\
domain      = "research"
description = "Research method headers"

[pipe.find_key_findings]
description = "Find the key findings in a document (contract only)."
inputs      = { doc = "Text" }
output      = "KeyFinding"
""",
    """\
domain      = "research"
description = "Research method definitions"

[pipe.find_key_findings]
type        = "PipeLLM"
description = "Find the key findings in a document."
inputs      = { doc = "native.Text" }
output      = "research.KeyFinding"
model       = "$quick-reasoning"
prompt      = "List the key findings in $doc."
""",
]

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
description = "Signature placeholder for the summary step."
inputs = { doc = "ApiDoc" }
output = "ApiSummary"
"""
