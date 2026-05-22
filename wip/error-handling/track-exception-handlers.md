# Track: Exception Handlers

## What this track is

The load-bearing piece of the design: a single FastAPI exception handler at app level catches every `PipelexError`, calls `to_error_report()`, returns the structured payload with the correct status code, and emits the corresponding structured log. A second app-level handler catches the residual `Exception` for genuinely-unknown failures. Once these are in place, the per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` blocks become unnecessary and are removed.

This track owns the centerpiece of the design. Everything else — the response schema, the Temporal-side recovery, the observability story — composes onto these two handlers.

## Design

### The `PipelexError` handler

The handler registered for `PipelexError` is the canonical translation from "a pipelex domain error reached the API boundary" to "an HTTP response leaves the server with classification intact." It is the only place in `pipelex-api` that knows about `ErrorReport`.

Behavior, in order:

1. Call `exc.to_error_report()` to obtain the enriched `ErrorReport`. This includes cause-chain enrichment — the API does not walk `__cause__` itself.
2. Read `report.http_status` for the status code. This already encodes the provider-429 passthrough; the API does not maintain a parallel mapping.
3. Build the response body as an RFC 7807 problem document derived from `report.to_dict()` (which drops `None` fields). The field mapping is documented in [track-response-schema.md](track-response-schema.md#decision-1--response-envelope-rfc-7807). When `ERROR_DISCLOSURE=strict`, the strict-mode redaction (see [track-response-schema.md](track-response-schema.md#the-strict-mode-in-detail)) is applied before serialization.
4. If `report.provider_metadata` is present and carries `retry_after_seconds`, emit a `Retry-After` header (integer seconds, ceiling). This is the one HTTP-protocol nicety that does not fall out of the body alone.
5. Emit a structured log entry using the same `ErrorReport` plus the request correlation fields. The log and the response are derived from one object — see [track-observability.md](track-observability.md) for the log side.

The handler does not branch on exception type, on `error_category`, or on `error_domain`. All three are encoded in the `ErrorReport` it just produced.

### The fallback `Exception` handler

A second app-level handler catches `Exception` for failures that are not `PipelexError`. This is one of the two legitimate places `except Exception` is permitted by the project Python standards (the other is the root of CLI commands).

Behavior:

1. Log with full traceback. The log line should include exception class and request correlation fields.
2. Return an HTTP 500 with a sanitized body — a fixed `error_type = "InternalServerError"`, a generic message, and the request id. No exception class name, no `str(exc)`, no traceback in the response body.

The two handlers, taken together, cover the universe: pipelex domain errors get structured handling; everything else gets sanitized 500 with full server-side context.

### Handler registration

Both handlers register on the FastAPI app at startup, alongside the existing CORS middleware and router registration in `api/main.py`. FastAPI's exception-handler API (`app.add_exception_handler(...)` or the `@app.exception_handler(...)` decorator) is the standard mechanism.

Registration order matters: FastAPI resolves handlers most-specific-first, but for clarity the design registers `PipelexError` explicitly and `Exception` as the catch-all.

### What about `TemporalError`?

`temporalio.exceptions.TemporalError` is the base class for raw temporalio errors. It does NOT inherit from `PipelexError`. Two cases:

- **Failures from inside an activity** that pipelex wraps. These are already handled by pipelex's `@convert_pipelex_errors` decorator, which transforms them into `TemporalError` instances carrying the packed `ErrorReport` in `ApplicationError.details`. By the time they reach the API at the submitter side, they reach the API as a `WorkflowExecutionError` (a `PipelexError`) — the `PipelexError` handler handles them. See [track-temporal-recovery.md](track-temporal-recovery.md).
- **Failures of the temporalio transport itself** — Temporal worker unreachable, connection error to the cluster, RPC errors during workflow dispatch. These can surface as a bare `TemporalError` (e.g. an `RPCError`) before pipelex's wrapping kicks in. They are genuine infrastructure failures.

The design decision: register a small third handler for `TemporalError` that classifies these as `INPUT`-domain-impossible / `RUNTIME`-domain `error_type = "TemporalTransportError"` with `error_category = TRANSIENT`, retryable. This is the API doing the classification because pipelex deliberately does not cover bare transport-level Temporal errors. The handler builds an `ErrorReport` by hand (carrying our authored classification) and then funnels through the same response-and-log path as the `PipelexError` handler.

Alternative: let `TemporalError` fall through to the `Exception` catch-all. Rejected because it loses the retryable signal — a bare RPC error to a Temporal worker is the prototypical retryable transient failure.

### What about `ValidationError` and the API-owned validation helpers?

Pydantic `ValidationError` is raised when request bodies fail to validate. Routes that take JSON-derived structured input (`agent/concept.py`, `agent/pipe_spec.py`, the callback-URLs extras in `pipeline.py`) handle it explicitly with a 422 response via the existing `raise_validation_error(...)` helper. This stays unchanged — it is API-owned, not pipelex-owned.

The `raise_validation_error` / `raise_bad_request` / `raise_payload_too_large` helpers in `api/errors.py` remain as the canonical way for routes to emit 4xx responses for API-owned concerns. They do not go through `ErrorReport` because the underlying cause is the API's own request validation, not pipelex's classification.

What changes in `api/errors.py`:

| Symbol | Status after |
|---|---|
| `raise_validation_error` | Kept |
| `raise_bad_request` | Kept |
| `raise_payload_too_large` | Kept |
| `raise_internal_error` | **Removed** |
| `ENDPOINT_HANDLED_EXCEPTIONS` | **Removed** |
| `STORAGE_HANDLED_EXCEPTIONS` | **Removed** |

### Route cleanup

Every route in `api/routes/pipelex/` and `api/routes/storage.py`, `api/routes/uploader.py` currently ends with:

```python
except ENDPOINT_HANDLED_EXCEPTIONS as exc:
    raise_internal_error(exc, context="...")
```

These `try / except` blocks come out. Routes call into pipelex and let exceptions propagate — the global handler takes care of `PipelexError`, the fallback handles anything else.

A specific case to audit: routes that today catch `ValueError` / `TypeError` / `RuntimeError` rely on the assumption that pipelex (or the API boundary) can leak these. Three possibilities:

1. **Pipelex should wrap it.** If pipelex internals raise a bare `ValueError` we can attribute to its domain, that is a pipelex bug — file it upstream. The route doesn't catch it.
2. **The API boundary should catch it.** If a Pydantic coercion at the route entry can raise a bare `ValueError`, that's an API-owned 422 — catch the specific case at the route and use `raise_validation_error`.
3. **Genuinely unknown.** Falls through to the `Exception` handler. Returns 500. The log + traceback are what the operator needs to investigate.

The audit is a small piece of work, not part of the design. The design states: the right outcome is no per-route `try / except (PipelexError, TemporalError, ValueError, TypeError, RuntimeError)` blocks.

### Storage routes (uploader, storage resolver)

`api/routes/uploader.py` and `api/routes/storage.py` currently catch `STORAGE_HANDLED_EXCEPTIONS = (PipelexError, OSError, BotoCoreError, ClientError)`. The first three become moot once the global `PipelexError` handler exists (pipelex's storage providers wrap backend errors into `StorageError → ToolError → PipelexError`).

`OSError`, `BotoCoreError`, `ClientError` are defensive: they should be wrapped by pipelex but are listed in case of leakage. Two options:

1. **Trust pipelex's wrapping.** Remove the tuple. If a bare `BotoCoreError` ever leaks, the `Exception` fallback catches it; we file a pipelex bug.
2. **Keep a narrow `except (OSError, BotoCoreError, ClientError)` at the route.** Translates them to a 500 with `error_type = "StorageBackendError"` and `error_category = TRANSIENT` (since most boto retries are transient).

Recommendation: option 1. Option 2 duplicates classification work that pipelex's storage layer should own. If we observe leakage in production we revisit, but designing for the leak is over-engineering.

### Auth errors

Auth errors emitted from `api/security.py` (today via `HTTPException`) are not within the pipelex error model — they are FastAPI's standard 401/403 with `WWW-Authenticate: Bearer` headers. They are out of scope for this track. The global handlers do not change them.

## The shape of the change, in one picture

Before (per-route, lossy):

```
Route handler
  └── try:
        await runner.execute_pipeline(...)
      except (PipelexError, TemporalError, ValueError, TypeError, RuntimeError) as exc:
        log.error(f"... {type(exc).__name__}", include_exception=True)
        raise HTTPException(500, detail={"error_type": type(exc).__name__, "message": "Internal server error"}) from exc
```

After (one handler, lossless):

```
Route handler
  └── await runner.execute_pipeline(...)         # exceptions propagate

App-level handlers
  ├── PipelexError       → exc.to_error_report() → response (status, body, Retry-After) + structured log
  ├── TemporalError      → API-authored ErrorReport (transport-transient) → same pipeline
  └── Exception          → sanitized 500 + log with traceback
```

## Open questions

### `TemporalError` classification — blanket `retryable=true` and HTTP 500 (raised in the Phase 2 code review, 2026-05-22)

The `TemporalError` handler authors one fixed classification for every bare `temporalio.exceptions.TemporalError`: `error_type = "TemporalTransportError"`, `error_category = transient`, `retryable = true`, `error_domain = RUNTIME` → HTTP 500. The Phase 2 code review surfaced two mismatches in that blanket rule:

1. **Not every `TemporalError` is retryable.** `WorkflowAlreadyStartedError` is a `temporalio.exceptions.TemporalError`, but retrying a duplicate-workflow start fails identically every time — it is a caller (`INPUT`) mistake (the API accepts a caller-supplied `pipeline_run_id`), not a transient transport failure. Labelling it `retryable = true` / `transient` misclassifies a caller error as a retry-me runtime error. Fixing it means either branching the handler on the concrete `TemporalError` subclass, or having pipelex wrap `WorkflowAlreadyStartedError` into a classified `PipelexError` upstream so it never reaches this handler as a bare error.

2. **HTTP 500 contradicts `retryable = true`.** `error_domain = RUNTIME` maps to HTTP 500, but a 500 tells a status-respecting HTTP client "server bug, do not retry" while the body says "retry me." A transport failure against an unreachable dependency is semantically a 503. `error_domain_to_http_status` (upstream) only ever returns 422/500 — surfacing a 503 would require either a new mapping upstream or an API-side status override in this handler.

**Decision needed:** (a) keep the blanket 500 / transient / retryable rule as the pragmatic default and accept the two mismatches, or (b) branch the `TemporalError` handler on subclass and/or pin a 503 for genuine transport-unavailability. Option (b) touches the upstream `error_domain → HTTP status` mapping and is cross-repo. The Phase 2 implementation ships option (a) until this is decided.

The two settled design decisions ([RFC 7807 envelope](track-response-schema.md#decision-1--response-envelope-rfc-7807) and [verbose disclosure default](track-response-schema.md#decision-2--info-disclosure-default-verbose)) affect the body the handler returns but not the handler's shape.

## Related tracks

- [architecture.md](architecture.md) — the contract this track implements.
- [track-response-schema.md](track-response-schema.md) — the shape of the body this handler returns.
- [track-temporal-recovery.md](track-temporal-recovery.md) — how `PipelexError`s coming back from a Temporal worker are recovered into the same handler pipeline.
- [track-observability.md](track-observability.md) — the log side, derived from the same `ErrorReport` the response uses.
