# Error Responses

Every error returned by the API uses the RFC 7807 `application/problem+json` envelope. The same shape applies across pipelex domain errors, validation errors, auth failures, payload-size limits, and the catch-all 500.

## Envelope

A failure response looks like this:

```json
{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle error",
  "status": 422,
  "detail": "Bundle does not declare a main_pipe, which is required for validation.",
  "instance": "/api/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

`Content-Type` is `application/problem+json` and `X-Request-ID` is always echoed in the response headers.

## Fields

**Standard RFC 7807 members** — `type`, `title`, `status`, `detail`, `instance`.

**Extension members** — all optional except `error_type`, `error_domain`, `retryable`:

- `error_type` — stable class name of the originating error (`ValidateBundleError`, `EnvVarNotFoundError`, `WorkflowExecutionError`, …). The same identifier the `type` URI is derived from.
- `error_domain` — one of `input`, `config`, `runtime`. See [Status codes](#status-codes).
- `retryable` — whether retrying the same request can plausibly succeed. `false` for 4xx; `true` only when the originating error explicitly classifies itself as transient (e.g. inference provider rate limits).
- `request_id` — server-correlated identifier for the request. Echoed from inbound `X-Request-ID` if provided, otherwise generated. Use this when reporting issues.
- `error_category` — finer classification when the originating error provides one (currently used by inference errors — see [`InferenceErrorCategory`](https://docs.pipelex.com/latest/errors/) upstream).
- `user_action` — structured suggestion of what the caller should do next, when the error can author one.
- `model`, `provider`, `provider_metadata` — populated when the failure originated in an upstream inference call. **Stripped under STRICT disclosure** (see [Disclosure modes](#disclosure-modes)).

## Status codes

The HTTP status follows pipelex's `error_domain_to_http_status`:

- `error_domain = "input"` → **422** — the caller can fix it (bad `.mthds`, malformed arguments, validation failure).
- `error_domain = "config"` → **500** — the deployment needs a fix (missing env var, bad TOML override).
- `error_domain = "runtime"` → **500** — failure during execution (worker crash, upstream service failure).
- Unclassified / unknown → **500**.

A few specific statuses bypass the domain mapping:

- **401** — missing/invalid bearer token. `WWW-Authenticate: Bearer` is set.
- **403** — authenticated but not authorized (storage-ownership mismatch).
- **413** — request body exceeds the configured size limit.
- **429** — set by `Retry-After` when an upstream provider rate-limits and the originating error carries `provider_metadata.retry_after_seconds`.

The HTTP status is the source of truth for success vs failure — there is no `success: true/false` field anywhere in the envelope.

## Disclosure modes

The `ERROR_DISCLOSURE` env var controls how much of the originating error makes it onto the wire:

- `verbose` (default) — renders the full `ErrorReport`. Use in dev, staging, and any deployment where the caller is trusted.
- `strict` — redacts `detail` and provider fields for errors that do not author caller-facing messages. Specifically:
    - `detail` is preserved only for error classes flagged as authoring caller-facing messages (today: `PipelexInterpreterError`, `ValidateBundleError`). Everything else has `detail` replaced with a generic title-derived string.
    - `model`, `provider`, `provider_metadata` are always stripped — they have no business on a caller-facing surface.
    - The redaction is keyed on the **provenance of the message** (`_authors_caller_facing_message` ClassVar), not on `error_domain`. A `RuntimeError` raised `from` an `INPUT`-domain cause does not leak the wrapper's internal message.

Server logs are always verbose regardless of `ERROR_DISCLOSURE` — the operator sees the full picture, the caller sees what the error chose to disclose.

## The `type` URI

`type` is a stable URI pointing at the per-class documentation page upstream:

```
https://docs.pipelex.com/latest/errors/<kebab-class-name>/
```

Every `PipelexError` subclass resolves to a live page (the trailing slash is canonical — pipelex emits it to match the MkDocs `use_directory_urls: true` form). The page describes the class, the typical cause, and how to recover.

API-authored errors (`ValidationError`, `BadRequest`, `Unauthenticated`, etc.) follow the same convention with their own slugs.

## Request correlation

Every response carries `X-Request-ID`. The middleware respects an inbound `X-Request-ID` header if present, otherwise generates a UUID. The same id rides through to the Temporal worker via `JobMetadata.request_id` and is bound onto every `WorkflowLog` / `ActivityLog` record produced during the run — so a single id correlates the inbound HTTP call, the API-side log line, and every worker-side log line for the run.

When opening an issue, include the `request_id` from the response (or response headers) and the timestamp.

## Examples

### 422 — input validation failure

```http
POST /api/v1/validate
{"mthds_contents": ["domain = \"x\"\ndescription = \"x\"\n"]}
```

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json
X-Request-ID: 9f2c1ab3-…

{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle error",
  "status": 422,
  "detail": "Bundle does not declare a main_pipe, which is required for validation.",
  "instance": "/api/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

### 500 — deployment configuration fault

```http
HTTP/1.1 500 Internal Server Error
Content-Type: application/problem+json
X-Request-ID: 9f2c1ab3-…

{
  "type": "https://docs.pipelex.com/latest/errors/env-var-not-found-error/",
  "title": "Env var not found error",
  "status": 500,
  "detail": "Missing required environment variable: COMPLETION_CALLBACK_SECRET",
  "instance": "/api/v1/pipeline/start",
  "error_type": "EnvVarNotFoundError",
  "error_domain": "config",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

Under `ERROR_DISCLOSURE=strict` the same failure has `detail` redacted to `"Server configuration error"` — the env var name lives in the server log only.

### 401 — missing bearer token

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/problem+json
WWW-Authenticate: Bearer
X-Request-ID: 9f2c1ab3-…

{
  "type": "https://docs.pipelex.com/latest/errors/unauthenticated/",
  "title": "Unauthenticated",
  "status": 401,
  "detail": "Missing bearer token.",
  "instance": "/api/v1/pipeline/execute",
  "error_type": "Unauthenticated",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

## Async callbacks (webhook payload)

For [async pipeline runs](pipe-run.md) registering a `callback_url`, the failure payload delivered to the caller's webhook **does not** use this envelope. The webhook body carries the raw `ErrorReport` dict under an `error` key, alongside `pipeline_run_id`, `status`, and the run metadata — a non-HTTP receiver (queue, log shipper) does not necessarily want an RFC 7807 wrapper.

The classification fields (`error_type`, `error_domain`, `retryable`, etc.) surface identically on both paths; only the envelope members (`type`, `status`, `detail`, `instance`, `request_id`) are sync-only. See [Pipe Run → Async Completion Callbacks](pipe-run.md) for the full webhook contract.
