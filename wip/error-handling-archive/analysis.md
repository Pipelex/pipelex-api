# Analysis — What We're Facing

This document is the case for the work. It walks through one concrete failure, generalizes to what the API loses on every error, and lays out the senior-engineer take that motivates the design.

## The trigger: `EnvVarNotFoundError` with no useful log

Running `make run` locally and hitting `POST /api/v1/pipeline/start` from Postman with default config produces:

```
ERROR    🧠: pipeline_start failed: EnvVarNotFoundError                                              errors.py:79
INFO:     127.0.0.1:54750 - "POST /api/v1/pipeline/start HTTP/1.1" 500 Internal Server Error
```

What actually happened: `_completion_signature` at `api/routes/pipelex/pipeline.py:51` calls `get_required_env("COMPLETION_CALLBACK_SECRET")`. Pipelex raises `EnvVarNotFoundError("Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set")` — a perfectly informative message that names the missing variable. Then `raise_internal_error` at `api/errors.py:79` does this:

```python
log.error(f"{context}: {type(exc).__name__}", include_exception=True)
raise HTTPException(
    status_code=500,
    detail={
        "error_type": type(exc).__name__,
        "message": "Internal server error",
    },
) from exc
```

The log line interpolates only the class name. The actual message — the part naming the env var — is discarded. The HTTP response body throws it away too. The full traceback may make it through `include_exception=True` depending on the log handler configuration, but the visible single-line ERROR is unactionable, and the response is opaque. The information was present in the exception object the whole time; the API just deleted it on the way out.

That is the surface bug. The deeper issue follows.

## The deeper issue: pipelex built a contract this API ignores

The new pipelex error model is explicitly designed for a downstream HTTP adapter. From `pipelex/docs/under-the-hood/error-model.md` under *Interfaces → API*:

> A downstream FastAPI exception handler calls `ErrorReport.http_status` and is a trivial adapter — it must not redefine the mapping.

Pipelex hands us:

- `PipelexError.to_error_report()` — returns a frozen `ErrorReport` with `error_type`, `message`, `error_category`, `error_domain`, `retryable`, `user_action`, `model`, `provider`, `provider_metadata`.
- `ErrorReport.http_status` — already returns 422 / 429 / 500 with the provider-429 passthrough so we can emit `Retry-After`.
- `error_domain_to_http_status(error_domain)` — the pure domain-to-status table, exported for downstream consumers.
- `recover_error_report(exc)` — walks the `__cause__` chain on a Temporal failure and rebuilds the `ErrorReport` from `ApplicationError.details`.
- A guarantee that classification survives every wrapper layer via cause-chain enrichment in `_enrich_error_report_from_cause`.

`pipelex-api` consumes **none of it.** Every route does `except ENDPOINT_HANDLED_EXCEPTIONS as exc: raise_internal_error(...)` which uniformly maps to a 500 with only the class name. The whole `error_category` / `error_domain` / `user_action` / `provider_metadata` / `retryable` apparatus is invisible to clients and to operators.

Concretely, what we lose at every error today:

| What pipelex already classifies | What the API does with it today |
|---|---|
| `EnvVarNotFoundError` message naming the missing variable | Replaced with `"Internal server error"` |
| `error_domain = CONFIG` vs `INPUT` vs `RUNTIME` | Ignored — everything is 500 |
| `error_category = TRANSIENT` / `CAPACITY` / `CONTENT` / ... | Lost — clients cannot decide whether to retry |
| `provider_metadata.retry_after_seconds` on a 429 | No `Retry-After` header emitted |
| `user_action = CHECK_BILLING` / `CHECK_CREDENTIALS` / `CHANGE_INPUT` | Lost — caller gets no guidance |
| `model` / `provider` attribution | Lost — operator cannot tell which backend is failing |
| Temporal-wrapped failure on async `/pipeline/start` | Never unwrapped; classification stays buried in `ApplicationError.details` |

The `EnvVarNotFoundError` we hit is the gentlest example. The worst case is a quota-exhausted Anthropic call on `/pipeline/execute`: pipelex correctly classifies it as `CAPACITY`, non-retryable, with `UserAction(CHECK_BILLING)`. The client sees an HTTP 500 saying `"Internal server error"` and retries it. Three more 500s. The operator log shows `pipeline_execute failed: LLMCompletionError` four times with no provider, no model, no category — when the actual fix is "top up the OpenAI account."

## What a senior Python engineer would say

The right shape is obvious once we accept the contract pipelex already defined:

- **One FastAPI exception handler at app level** registered for `PipelexError`. It calls `exc.to_error_report()`, returns `report.to_dict()`, uses `report.http_status` for the status code, and sets `Retry-After` from `provider_metadata.retry_after_seconds` when present. That is the whole adapter.
- **A second `Exception` handler** at app level for genuinely-unknown failures — sanitized 500, log with traceback. This is one of the two legitimate places the project rules allow `except Exception`.
- **Strip the per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS`** blocks. They are duplicative once a global handler exists, AND they are lossy (they collapse classification to a class name). The route should just call into pipelex and let exceptions propagate.
- **The `ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` tuples become unnecessary.** Their existence is a smell: `ValueError`, `TypeError`, `RuntimeError` in those tuples suggests pipelex (or the api boundary) is not wrapping everything in `PipelexError` subclasses. Worth a one-pass audit — if anything legitimately escapes as a bare `ValueError`, either pipelex should wrap it, or the api endpoint that triggers it (e.g. a Pydantic coercion path) should catch that one specific type and convert to a 422. The current "catch the union of plausible exception types" is the wrong abstraction.
- **Keep the existing `raise_validation_error` / `raise_bad_request` / `raise_payload_too_large` helpers** in `api/errors.py`. Those are API-owned concerns (body parsing, callback-URL SSRF, payload size) and do not go through `ErrorReport`.

In short: the work is mostly subtraction.

## What a senior systems engineer would say

The systems concerns layer on top of the Python cleanup:

- **Observability is the real prize.** Logs today say `pipeline_start failed: EnvVarNotFoundError` — unactionable. With `ErrorReport` we get structured fields we can index and alert on: `error_category`, `error_domain`, `provider`, `model`, `status_code`, `request_id`, plus `pipeline_run_id` and `pipe_code` from the wrapping layers. That unlocks per-provider error-rate dashboards, an alert on `error_category == CAPACITY` (someone's quota is bleeding), separate handling for `CONFIGURATION` (page operator immediately) vs `TRANSIENT` (expected noise).
- **`ProviderErrorMetadata.body` is already excluded.** Pipelex thought about this — the raw provider response body (which can carry account ids, billing details, credential fragments) is `exclude=True` in the Pydantic model. That removes the standard "don't leak provider response bodies" objection to richer error logging. We can log the metadata in full.
- **Retryability is a first-class API concern.** Today this API gives clients no way to distinguish "retry me" from "you broke it" from "I broke it." A Temporal worker, an agent, a frontend — they all need that signal. With `report.error_category` and `report.retryable` it is free. With `provider_metadata.status_code == 429` and `Retry-After`, even better — and pipelex's `http_status` property already encodes the special case.
- **RFC 7807 (`application/problem+json`) is worth considering.** Standard error response shape, maps cleanly onto `ErrorReport`. The current `{"detail": {"error_type", "message"}}` shape is a homegrown convention. While the API surface is small is the right time. Not load-bearing, but a fork in the road — see [track-response-schema.md](track-response-schema.md) for the framed decision.
- **Request correlation.** Independent of pipelex — every request should generate or accept an `X-Request-ID`, every error response and log line should include it. Lets us trace a single client call across API logs, pipelex layer logs, Temporal worker logs.
- **The Temporal boundary is a trap.** `/pipeline/start` returns immediately, but the actual failure surfaces later via webhook completion or via a client polling for status. The pipelex docs are explicit: `recover_error_report(temporal_failure)` walks the `__cause__` chain on `ApplicationError.details` and reconstructs the full classification. Whichever code path delivers the eventual failure to the caller (webhook payload, status endpoint, completion callback) needs to use it. Otherwise the cross-boundary work pipelex did is wasted. See [track-temporal-recovery.md](track-temporal-recovery.md).
- **Info-disclosure default.** The current `"Internal server error"` stance is over-conservative for a self-hosted open-source server whose operators are the same people debugging. The right default is: log the full `ErrorReport`, respond with the same structured payload (sans `body`, which is already excluded). If there is ever a multi-tenant hosted offering that needs further redaction, add an explicit `ERROR_DISCLOSURE=strict` mode rather than redacting by default. This is one of the two open questions — see [track-response-schema.md](track-response-schema.md#open-question-2--info-disclosure-default).

## What kind of work this becomes

Four discrete pieces of work, none huge, with rough ordering:

1. **The exception-handler adapter** ([track-exception-handlers.md](track-exception-handlers.md)). Register a `PipelexError` handler on the FastAPI app that calls `to_error_report()`, maps `.http_status`, sets `Retry-After`, returns the structured payload. Also handles the Temporal-wrapped case via `recover_error_report()`. Plus an `Exception` catch-all for unknowns. Load-bearing — once it exists, everything else gets simpler.
2. **Route cleanup** (folded into the exception-handlers track). Strip `try / except ENDPOINT_HANDLED_EXCEPTIONS` from every route. Delete the tuples. Keep the API-owned validation helpers. Audit whether anything escapes as `ValueError` / `TypeError` / `RuntimeError` that pipelex should be wrapping — and either fix pipelex or catch the specific case in the route.
3. **Response schema decision** ([track-response-schema.md](track-response-schema.md)). Either enrich the existing `{"detail": {...}}` shape with the new fields, or adopt RFC 7807. This affects API consumers, so it is a one-way door.
4. **Structured logging + request correlation** ([track-observability.md](track-observability.md)). Independent of the above. Add a request-id middleware, restructure log lines to use structured fields rather than f-strings. Operators get the dashboards / alerting payoff.

There is also a smaller cross-cutting item: documenting the API error contract in the published API docs so consumers know `error_category` / `retryable` / `Retry-After` are stable fields they can build against.

## The bottom line

Pipelex shipped this as an explicit contract and named pipelex-api as the canonical consumer. Not implementing it is leaving a substantial amount of already-paid-for engineering on the floor. The design that follows claims that contract end-to-end.
