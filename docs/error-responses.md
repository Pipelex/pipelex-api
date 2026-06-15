# Error Responses

Every error returned by the API uses the RFC 7807 `application/problem+json` envelope. The same shape applies across pipelex domain errors, validation errors, auth failures, payload-size limits, and the catch-all 500.

## Envelope

A failure response looks like this:

```json
{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle",
  "status": 422,
  "detail": "TOML syntax error at line 1, column 6: Expected '=' after a key in a key/value pair",
  "instance": "/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

`Content-Type` is `application/problem+json` and `X-Request-ID` is always echoed in the response headers.

## Fields

**Standard RFC 7807 members** — `type`, `title`, `status`, `detail`, `instance`.

**Extension members** — only `error_type` is always present; the others appear when the originating error populates them:

- `error_type` — stable class name of the originating error (`ValidateBundleError`, `EnvVarNotFoundError`, `WorkflowExecutionError`, …). The same identifier the `type` URI is derived from.
- `error_domain` — one of `input`, `config`, `runtime`. See [Status codes](#status-codes). Absent for domain-less pipelex errors (some pipelex tool errors, e.g. `EnvVarNotFoundError`, do not classify a domain — the HTTP status still defaults to **500**).
- `retryable` — whether retrying the same request can plausibly succeed. Always emitted for API-authored 4xx/5xx (always `false`). For pipelex-originated errors, present only when the source error populates it — `true` only when the originating error explicitly classifies itself as transient (e.g. inference provider rate limits); absent on pipelex errors that don't set it (e.g. `EnvVarNotFoundError`, `PipelexConfigError`).
- `request_id` — server-correlated identifier for the request. Echoed from inbound `X-Request-ID` if provided, otherwise generated. Use this when reporting issues.
- `error_category` — finer classification when the originating error provides one (currently used by inference errors — see [`InferenceErrorCategory`](https://docs.pipelex.com/latest/errors/) upstream).
- `user_action` — structured suggestion of what the caller should do next, when the error can author one.
- `model`, `provider`, `provider_metadata` — populated when the failure originated in an upstream inference call. **Stripped under STRICT disclosure** (see [Disclosure modes](#disclosure-modes)).
- `validation_errors` — structured per-error list on a `ValidateBundleError` 422. See [Structured validation errors](#structured-validation-errors).

## Structured validation errors

When `/validate` rejects a bundle, the `ValidateBundleError` 422 carries a `validation_errors` array alongside the single human-readable `detail` — the per-error diagnostics an editor maps to per-line problems. Each item is one categorized validation failure:

| Field | Meaning |
|---|---|
| `category` | The failure family — one of `blueprint_validation`, `pipe_factory`, `pipe_validation`. |
| `message` | Human-readable description of this specific error. |
| `error_type` | Finer error subtype within the category, when the source error provides one. |
| `source` | The owning file of the error — present on `pipe_validation` and `blueprint_validation` items that the runtime could attribute to a file. On the in-memory submit path it is the matching `mthds_names[i]` (see [Naming submitted files](pipe-validate.md)); `null` when the caller sent no names. Absent for `pipe_factory` errors, and absent on any blueprint error the runtime could not attribute (those are summarized in `detail` only — see the note below). |
| `pipe_code`, `concept_code`, `domain_code` | The pipe / concept / domain the error is about, when applicable. |
| `field_path`, `field_name` | The offending field within the bundle, when the error localizes to one. |
| `variable_names`, `missing_concept_code`, `declared_concepts` | Extra context for specific failure shapes (undefined variables, an unresolved concept reference, the set of concepts that were declared). |

Items carry only the fields that apply to their category — absent fields are omitted, not null. `validation_errors` is **retained under STRICT disclosure** (it describes the caller's own submitted bundle, not server internals). It is present only on `ValidateBundleError`; other error types omit it.

Not every failure becomes a structured item. A blueprint-validation failure the runtime cannot attribute to a known pipe/concept/field is reported in the human-readable `detail` only and does not produce a `validation_errors` entry — so `validation_errors` is a best-effort per-error breakdown, and `detail` remains the authoritative summary. Treat the array as possibly shorter than the set of problems implied by `detail`.

## Status codes

The HTTP status follows pipelex's `error_domain_to_http_status`:

- `error_domain = "input"` → **422** — the caller can fix it (bad `.mthds`, malformed arguments, validation failure).
- `error_domain = "config"` → **500** — the deployment needs a fix (missing env var, bad TOML override).
- `error_domain = "runtime"` → **500** — failure during execution (worker crash, upstream service failure).
- Unclassified / unknown → **500**.

A few specific statuses bypass the domain mapping:

- **401** — missing/invalid bearer token. `WWW-Authenticate: Bearer` is set.
- **403** — authenticated but not authorized (storage-ownership mismatch).
- **409** — `error_type = "PipelineManagerAlreadyExistsError"`: the submitted `pipeline_run_id` is already registered for a run that is still in flight on this server. Completed and failed runs free their id, so this only fires for genuinely concurrent duplicates — resubmit after the in-flight run finishes, or pick a fresh id.
- **413** — request body exceeds the configured size limit.
- **429** — set by `Retry-After` when an upstream provider rate-limits and the originating error carries `provider_metadata.retry_after_seconds`.
- **501** — `error_type = "AsyncExecutionNotEnabledError"`: this deployment does not provide async pipeline execution (`/pipeline/start`). Permanent under the current deployment — do not retry.

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
POST /v1/validate
{
  "mthds_contents": ["domain = \"broken\"\nmain_pipe = \"Not A Valid Pipe Code!\"\n"],
  "mthds_names": ["broken.mthds"]
}
```

```http
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/problem+json
X-Request-ID: 9f2c1ab3-…

{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle",
  "status": 422,
  "detail": "Validation error(s):\n\nValue errors: 'main_pipe': Value error, Invalid main pipe syntax 'Not A Valid Pipe Code!'. Must be in snake_case.",
  "instance": "/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "user_action": {
    "kind": "change_input",
    "detail": "Check the validation_errors array for specific issues"
  },
  "request_id": "9f2c1ab3-…",
  "validation_errors": [
    {
      "category": "blueprint_validation",
      "message": "Value error, Invalid main pipe syntax 'Not A Valid Pipe Code!'. Must be in snake_case.",
      "error_type": "invalid_pipe_code_syntax",
      "domain_code": "broken",
      "source": "broken.mthds"
    }
  ]
}
```

The single `detail` is the human summary; `validation_errors` is the machine-readable list a client maps to per-line diagnostics. `source` echoes the `mthds_names` entry the caller paired with that content — see [Structured validation errors](#structured-validation-errors) and [Naming submitted files](pipe-validate.md).

### 500 — deployment configuration fault

```http
HTTP/1.1 500 Internal Server Error
Content-Type: application/problem+json
X-Request-ID: 9f2c1ab3-…

{
  "type": "https://docs.pipelex.com/latest/errors/env-var-not-found-error/",
  "title": "Environment variable not set",
  "status": 500,
  "detail": "Missing required environment variable: COMPLETION_CALLBACK_SECRET",
  "instance": "/v1/start",
  "error_type": "EnvVarNotFoundError",
  "request_id": "9f2c1ab3-…"
}
```

`EnvVarNotFoundError` is a domain-less pipelex tool error — neither `error_domain` nor `retryable` is populated on the report, so both extension members are absent on the wire. The HTTP status is still **500** (the deployment, not the caller, has to fix it), but the classification fields only ride along when the originating error sets them. See [Fields](#fields).

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
  "instance": "/v1/execute",
  "error_type": "Unauthenticated",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

## Async callbacks (webhook payload)

For [async pipeline runs](pipe-run.md) registering a `callback_url`, the failure payload delivered to the caller's webhook **does not** use this envelope. The webhook body carries the raw `ErrorReport` dict under an `error` key, alongside `pipeline_run_id` (the protocol field) plus the runtime's legacy `pipeline_run_id` / `status` keys — a non-HTTP receiver (queue, log shipper) does not necessarily want an RFC 7807 wrapper.

The classification fields (`error_type`, `error_domain`, `retryable`, etc.) surface identically on both paths; only the envelope members (`type`, `status`, `detail`, `instance`, `request_id`) are sync-only. See [Pipe Run → Async Completion Callbacks](pipe-run.md) for the full webhook contract.
