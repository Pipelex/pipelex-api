# `scripts/sync_vendored_kit.py` — review follow-ups

**Status:** one improvement deferred from the round-2 review of the branch that introduced `make kit-sync` and `make kit-check`. It was not verified; it is recorded as the reviewer wrote it, with the reason it was not fixed then.

## `scripts/` is outside the type checkers' scope

Raised by cubic, as a P3, at `render_backends_toml`. `pyproject.toml` points pyright at `api` and `tests` and mypy at the same two packages, so neither checks `scripts/sync_vendored_kit.py` or `scripts/export_openapi.py` on `make agent-check`. The sync script was clean under pyright when checked by hand, and its switch logic is now covered by `tests/unit/test_sync_vendored_kit.py`, which loads it from its path. Bringing `scripts/` into both checkers is a repo-wide configuration change, larger than the review's `defects` bar admitted.

## Fixed in the same branch

Round 1 deferred two findings on `read_enabled_switches`, and round 2 fixed both after three reviewers raised the first one again. A missing `backends.toml` used to sync every backend off, `pipelex_gateway` and `internal` included, after which `make kit-check` passed; `make cleanall` reached that state, because its `cleanconfig` step deleted the tracked `.pipelex/` directory. A malformed `backends.toml` failed with a raw traceback. Both modes of the script now stop without writing when the file is missing, is not valid TOML, or holds no `enabled` switch, and `make cleanall` no longer touches `.pipelex/`.
