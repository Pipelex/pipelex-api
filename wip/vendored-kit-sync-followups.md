# `scripts/sync_vendored_kit.py` — review follow-ups

**Status:** deferred from the round-1 review of the branch that introduced `make kit-sync` and `make kit-check`. Neither finding was verified; both are recorded as a reviewer wrote them, with the reason they were not fixed then.

## A missing `backends.toml` syncs every backend off, silently

Raised by the official code-review, as a low-severity note, at `read_enabled_switches`. When `.pipelex/inference/backends.toml` does not exist, the function returns no switches, and `render_backends_toml` writes every backend disabled, `pipelex_gateway` and `internal` included. Someone who deletes the file to "reset it" and runs `make kit-sync` gets an image with no language-model backend on, and `make kit-check` passes, because it reads the switches from that same file. The docstring states the behaviour and the file is tracked, so only a deliberate deletion reaches it; the fix would be to refuse, in sync mode, when the file is absent, naming the switches it would otherwise have had to invent.

## A malformed `backends.toml` fails with a raw traceback

Raised by the same reviewer. `tomllib.loads` in `read_enabled_switches` raises `TOMLDecodeError` uncaught, so the maintainer sees a traceback rather than a one-line message. The traceback names the line and column, which is enough for a maintainer tool; a caught error naming the file would only be friendlier.
