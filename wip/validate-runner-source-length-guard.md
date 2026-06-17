# Handoff — guard `mthds_sources` length in `ApiRunner.validate()` (not just the route)

> Raised by greptile on PR #20 (`feature/validation-errors-source`). Triaged, confirmed, deferred — not fixed in that PR.

## Context (cold start)

`/v1/validate` accepts an optional `mthds_sources` list, parallel to `mthds_contents`, that threads a per-file source onto each bundle's `blueprint.source` so diagnostics name the owning file. A length mismatch between the two is a caller error.

Today that mismatch is caught only at the HTTP boundary, by the Pydantic `model_validator` on `ValidateRequest` (`api/routes/pipelex/validate.py`):

```python
@model_validator(mode="after")
def _sources_match_contents(self) -> Self:
    if self.mthds_sources is not None and len(self.mthds_sources) != len(self.mthds_contents):
        msg = "mthds_sources, when provided, must be a per-item source list matching mthds_contents in length"
        raise ValueError(msg)   # → 422
    return self
```

But `ApiRunner.validate()` (`api/routes/pipelex/pipeline.py`) is a **public MTHDS-protocol override**, callable directly (tests, other Python callers, future runtime paths) without going through `ValidateRequest`. A direct caller with mismatched lengths gets a messy, backend-dependent failure instead of a clean request error — verified against the pinned pipelex source:

- **Temporal disabled** (in-process): `validate_bundles_in_process` → `validate_bundle` raises `PipelexUnexpectedError` (`pipelex/pipeline/validate_bundle.py`). Pipelex deliberately treats a `mthds_sources` mismatch as a *host wiring bug* (→ 500, redacted under STRICT), because in pipelex's framing `mthds_sources` is host-internal, never end-caller input.
- **Temporal enabled**: `DryValidateArg` does not validate the relationship, so `dispatch_dry_validate` runs the worker first; the mismatch surfaces only afterward — at the worker's own `validate_bundle` guard and/or the API-side `zip(mthds_contents, content_sources, strict=True)` at `pipeline.py:214` — i.e. after backend work has already started.

Either way: no clean, pre-work, caller-shaped error for a direct caller.

## The change

Add an early guard at the very top of `ApiRunner.validate()`, before the `get_config().temporal.is_enabled` branch, mirroring the `extra` guard `ApiRunner.start` already uses (`pipeline.py:102`):

```python
if mthds_sources is not None and len(mthds_sources) != len(mthds_contents):
    msg = "mthds_sources, when provided, must be a per-item source list matching mthds_contents in length"
    raise PipelineRequestError(msg)
```

`PipelineRequestError` (`mthds.protocol.exceptions`) is already imported at `pipeline.py:12` and is the idiomatic "request to the runner is malformed" type — the same one `start` raises for caller-shape misuse. Placing it first means both backends short-circuit before any dispatch or library work, and direct callers get one clean, typed error regardless of backend.

## Semantics to preserve

- **Keep the route-level Pydantic 422.** It still fires first for HTTP callers and yields the correct caller-facing 422 RFC 7807 (the runner guard never triggers on the HTTP path). This is defense-in-depth, not a replacement — pinned by the existing `test_length_mismatch_is_a_request_error`.
- **Don't touch pipelex's stance.** Pipelex intentionally classifies a `mthds_sources` mismatch as a host wiring bug (500). The API guard sits *above* pipelex and reflects the API's framing — here `mthds_sources` IS a caller-supplied request field — so it fires first and pipelex's 500 path is never reached from this method.
- The guard is purely additive: `mthds_sources=None` (the common case) is untouched, and a matched-length call behaves exactly as before.

## Tests

Async test added to `TestValidateErrors` in `tests/unit/test_validate_errors.py` (one TestClass per module — extend the existing one; mark the single async method `@pytest.mark.asyncio`):

```python
@pytest.mark.asyncio
async def test_direct_runner_rejects_mismatched_sources(self):
    with pytest.raises(PipelineRequestError):
        await ApiRunner().validate(mthds_contents=[VALID_MTHDS], mthds_sources=["a.mthds", "b.mthds"])
```

Hermetic: the guard fires before any `get_config()` / library / dispatch work, so the test needs no Temporal or inference setup (the unit conftest already forces Temporal disabled). Before the change this raises `PipelexUnexpectedError`; after, `PipelineRequestError`.
