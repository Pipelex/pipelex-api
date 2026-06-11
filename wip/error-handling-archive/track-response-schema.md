# Track: Response Schema

## What this track is

This track defines what an error response body looks like on the wire. Two design decisions are settled: the envelope is RFC 7807 `application/problem+json`, and the self-hosted default is verbose disclosure with an `ERROR_DISCLOSURE=strict` opt-in. The rationale for each, along with the conditions that would prompt a revisit, is documented below.

The track also documents the response headers, the relationship to the existing 4xx helpers (`raise_validation_error` and friends), and how a webhook completion payload mirrors the synchronous response.

## Design

### What goes in every error response, regardless of envelope

These fields are non-negotiable: they come straight from `ErrorReport.to_dict()` minus the parts that pipelex already excludes.

| Field | Source | Notes |
|---|---|---|
| `error_type` | `ErrorReport.error_type` | The pipelex exception class name. Stable identifier for client-side branching. |
| `message` | `ErrorReport.message` | Human-readable. Includes details like the missing env var name. |
| `error_category` | `ErrorReport.error_category` | Present for inference errors. `"transient"` / `"capacity"` / `"content"` / `"configuration"` / `"ambiguous"` / `"unknown"`. |
| `error_domain` | `ErrorReport.error_domain` | `"input"` / `"config"` / `"runtime"`. Mirrors HTTP status semantics. |
| `retryable` | `ErrorReport.retryable` | Bool. Derived from `error_category.is_retryable` for inference errors. |
| `user_action` | `ErrorReport.user_action` | Object `{kind, detail}`. Free-form `detail`, discrete `kind` (`CHECK_BILLING`, `CHANGE_INPUT`, …). |
| `model` | `ErrorReport.model` | Model handle, when attributable. |
| `provider` | `ErrorReport.provider` | Backend name, when attributable. |
| `provider_metadata` | `ErrorReport.provider_metadata` | SDK metadata — status code, request id, retry_after, provider error code. `body` is already `exclude=True`. |
| `request_id` | API-injected | The API's own request correlation id (X-Request-ID) — distinct from `provider_metadata.request_id`, which is the SDK's. |

`None`-valued fields are dropped by `to_dict()`. The API-side `request_id` is added on top of whatever pipelex emits.

### Response headers

The handler always emits:

- `Content-Type: application/json` (or `application/problem+json` if RFC 7807 is adopted).
- `X-Request-ID: <id>` — echoed back so clients can correlate.
- `Retry-After: <seconds>` — when `provider_metadata.retry_after_seconds` is present. Integer-ceiling of the float. The HTTP value is independent of the body's classification; both are emitted so cooperating proxies (CloudFront, nginx) can act on the header alone.

### Decision 1 — response envelope: RFC 7807

The envelope is RFC 7807 `application/problem+json` ([RFC 7807](https://datatracker.ietf.org/doc/html/rfc7807), updated by [RFC 9457](https://datatracker.ietf.org/doc/html/rfc9457)). The `ErrorReport` fields ride as RFC 7807 extension members alongside the standard `type` / `title` / `status` / `detail` / `instance`.

**The shape.**

```json
{
  "type": "https://pipelex.dev/errors/env-var-not-found",
  "title": "Environment variable not set",
  "status": 500,
  "detail": "Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set",
  "instance": "/api/v1/pipeline/start",
  "error_type": "EnvVarNotFoundError",
  "error_domain": "config",
  "retryable": false,
  "request_id": "req_01J7Q..."
}
```

```json
{
  "type": "https://pipelex.dev/errors/llm-completion",
  "title": "LLM provider rate limit",
  "status": 429,
  "detail": "OpenAI returned 429 (rate_limit_exceeded)",
  "instance": "/api/v1/pipeline/execute",
  "error_type": "LLMCompletionError",
  "error_category": "transient",
  "error_domain": "runtime",
  "retryable": true,
  "user_action": {"kind": "WAIT_AND_RETRY", "detail": "Retry after 12s."},
  "provider": "openai",
  "model": "gpt-4o-mini",
  "provider_metadata": {
    "provider": "openai",
    "sdk_exception_type": "RateLimitError",
    "status_code": 429,
    "retry_after_seconds": 12.0,
    "request_id": "req_abc..."
  },
  "request_id": "req_01J7Q..."
}
```

**Field mapping from `ErrorReport` to RFC 7807.**

| RFC 7807 field | Source | Notes |
|---|---|---|
| `type` | `pipelex.dev/errors/<kebab-case error_type>` | Dereferenceable doc anchor per error class. Stable namespace under our control. |
| `title` | A short human label derived from `error_type` (or hand-curated per class) | Stable across instances of the same error. |
| `status` | `ErrorReport.http_status` | Same number as the HTTP response. Redundant on purpose — RFC 7807 mandates it in the body. |
| `detail` | `ErrorReport.message` | The variable, per-instance human-readable explanation. |
| `instance` | The matched route path | Per [RFC 9457 §3.1.5](https://datatracker.ietf.org/doc/html/rfc9457#section-3.1.5), a URI reference identifying the specific occurrence. |
| `error_type`, `error_category`, `error_domain`, `retryable`, `user_action`, `model`, `provider`, `provider_metadata`, `request_id` | Extension members from `ErrorReport.to_dict()` | RFC 7807 §3.2 explicitly allows extension members. |

**The `type` URI namespace.** We commit to `https://pipelex.dev/errors/<kebab-case>` as a stable, dereferenceable namespace. The page at each URI is the authoritative documentation for that error class. This is a small ongoing maintenance commitment that pairs naturally with the future docs site for the error model. Until the docs pages exist, the URI is still stable as an identifier — clients can pattern-match on it without dereferencing.

**Why RFC 7807.** Standard wins on tooling (OpenAPI codegens recognize it), recognizability (clients written against any RFC 7807-conformant API can pattern-match the shape), and content-type disambiguation (`application/problem+json` tells generic intermediaries "this is an error payload"). Pipelex's `ErrorReport` fields slot into extension members without contortion.

**Conditions that would prompt a revisit.** If a downstream consumer (frontend, partner) is found to be locked into the current `{"detail": {...}}` shape and the migration cost is non-trivial, we revisit. Today, no such consumer is known.

### Decision 2 — info-disclosure default: verbose

The self-hosted default is verbose: every field from `ErrorReport.to_dict()` flows to the response body (which already excludes `provider_metadata.body` via pipelex). The `EnvVarNotFoundError` example says exactly "Environment variable 'COMPLETION_CALLBACK_SECRET' is required but not set" in the response.

An env var `ERROR_DISCLOSURE=strict` flips to sanitized mode for `CONFIG` and `RUNTIME` domain errors. `INPUT`-domain errors are always verbose (the caller can fix them). The server-side log is always verbose, regardless of mode — strict mode affects only the response body.

**Why verbose by default.** The intended deployment posture for `pipelex-api` is "behind an API gateway, or in a trusted environment." For a self-hosted open-source server, the operator and the debugging developer are usually the same person — they should see the actual problem in Postman without tailing the log. Pipelex already excludes the genuinely sensitive piece (`provider_metadata.body`), so the worst-case leak (provider response bodies carrying account ids) is off the table. Hosted multi-tenant deployments set `ERROR_DISCLOSURE=strict` to sanitize the rest.

**Conditions that would prompt a revisit.** If `pipelex-api` becomes a default-exposed component of a future product (e.g. shipped inside a desktop app whose ports are publicly reachable), the right answer becomes strict-by-default. The two-mode design supports either default; only the default value changes.

### The strict mode, in detail

The design provides both modes; the default value of the `ERROR_DISCLOSURE` env var is `verbose`, and operators can flip it to `strict`. In strict mode:

- `INPUT`-domain errors are unchanged: `detail`, `error_type`, `user_action`, etc. all flow through. The caller can fix the input.
- `CONFIG`-domain errors: `detail` is replaced with `"Server misconfiguration — contact the operator."`. `error_type`, `error_domain`, `retryable`, `request_id`, and the RFC 7807 standard fields (`type`, `title`, `status`, `instance`) remain so the operator can find the entry in the logs by `request_id`. `user_action` is dropped (it usually names the misconfigured component).
- `RUNTIME`-domain errors: same treatment as `CONFIG`. `error_category` and `retryable` remain so the client can decide whether to retry.
- `provider`, `model`, `provider_metadata` are dropped in strict mode for `CONFIG`/`RUNTIME` (they can identify the operator's backend wiring).

The log on the server side is always verbose, regardless of disclosure mode — strict mode affects only the response body.

### Webhook completion payload

Async `/pipeline/start` delivers completion via a webhook (configured per-call via `callback_urls`). When the pipeline fails, the webhook payload must carry the same RFC 7807 `error` shape as a synchronous error response — callers cannot be asked to read a different schema depending on which entry point they used.

Design: the webhook payload is a fixed-shape envelope `{pipeline_run_id, status, result | error}`. `error` is the same RFC 7807 problem document the synchronous handler would have returned for an equivalent failure — same disclosure mode, same fields. See [track-temporal-recovery.md](track-temporal-recovery.md) for the recovery flow that produces it.

### The 4xx-helper integration

The existing `raise_validation_error` / `raise_bad_request` / `raise_payload_too_large` helpers do not produce `ErrorReport`s — they produce API-authored validation errors. They need to emit the same RFC 7807 problem document as `PipelexError` responses, so a client only learns one shape.

Design: the helpers build a minimal RFC 7807 problem document (with `error_domain = "input"`, the static `error_type` from `ErrorType` enum mapped to the `type` URI and `title`, the caller-provided `message` as `detail`, and the request id) and flow through the same response-construction path as the global handler. The helpers are not deleted — they remain the way routes raise an API-owned 4xx.

## Cross-references

The design choices above ripple to:

- [track-exception-handlers.md](track-exception-handlers.md) — the handler's response-builder produces RFC 7807 problem documents from `ErrorReport`s; the field-mapping logic lives here.
- [track-temporal-recovery.md](track-temporal-recovery.md) — webhook payload shape mirrors the synchronous response (RFC 7807 problem document, same disclosure mode, same fields).
- [track-observability.md](track-observability.md) — the log always carries the full `ErrorReport` plus request correlation, regardless of disclosure mode.

## Related tracks

- [architecture.md](architecture.md) — the `ErrorReport` contract this track exposes on the wire.
- [track-exception-handlers.md](track-exception-handlers.md) — what produces the response.
- [track-temporal-recovery.md](track-temporal-recovery.md) — the async equivalent (webhook payload).
- [track-observability.md](track-observability.md) — the log side, unaffected by envelope choice.
