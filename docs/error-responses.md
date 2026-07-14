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
- `validation_errors` — structured per-error list carried by a `ValidateBundleError`. On the run routes (`/execute`, `/start`) it rides this 422 problem document; on the diagnostic routes (`/validate`, `/resolve`, `/codegen`, `/build/{inputs,output,runner}`) it rides the **200 `is_valid: false`** body instead (see [Pipe Validate](pipe-validate.md)). See [Structured validation errors](#structured-validation-errors).

## Structured validation errors

When a bundle fails validation, the `ValidateBundleError` carries a `validation_errors` array — the per-error diagnostics an editor maps to per-line problems. Where it surfaces depends on the endpoint: on the diagnostic routes (`/validate`, `/resolve`, `/codegen`, `/build/{inputs,output,runner}`) it rides the **200 `is_valid: false`** body (the diagnostic-endpoint contract — see [Pipe Validate](pipe-validate.md)); on the run routes (`/execute`, `/start`) — where an invalid bundle means the run cannot proceed — it rides the **422** problem document alongside the single human-readable `detail`. Built by pipelex's one shared builder, the items are identical wherever they appear (and to the agent CLI's). Each item is one categorized validation failure:

| Field | Meaning |
|---|---|
| `category` | The failure family — one of `blueprint_validation`, `pipe_factory`, `pipe_validation`, `dry_run`. |
| `message` | Human-readable description of this specific error. |
| `error_type` | Finer error subtype within the category, when the source error provides one. |
| `source` | The owning file of the error — present on `pipe_validation` and `blueprint_validation` items that the runtime could attribute to a file. On the in-memory submit path it is the matching `mthds_sources[i]` (see [Sourcing submitted files](pipe-validate.md)); `null` when the caller sent no sources. Absent for `pipe_factory` and `dry_run` errors (the latter is graph-level), and absent on the parse-level `blueprint_validation` residual (a raw TOML-syntax error, an empty blueprint, or an elaborator failure), which carries the failure message but no file attribution — see the note below. |
| `pipe_code`, `concept_code`, `domain_code` | The pipe / concept / domain the error is about, when applicable. |
| `field_path`, `field_name` | The offending field within the bundle, when the error localizes to one. |
| `variable_names`, `missing_concept_code`, `missing_pipe_code`, `declared_concepts` | Extra context for specific failure shapes (undefined variables, an unresolved concept or pipe reference, the set of concepts that were declared). |
| `suggested_fix` | A structured, deterministic fix for this error — present only when the fix planner derived one. See [Suggested fixes](#suggested-fixes). |

Items carry only the fields that apply to their category — absent fields are omitted, not null. `validation_errors` is **retained under STRICT disclosure** (it describes the caller's own submitted bundle, not server internals). It is present only on `ValidateBundleError`; other error types omit it.

Every invalid verdict carries a **non-empty** `validation_errors` array — the structured-info invariant is total. A parse-level failure the runtime cannot attribute to a known pipe/concept/field — a raw TOML-syntax error, an empty blueprint, or an elaborator failure — still becomes one `blueprint_validation` residual item carrying the failure message (no `source`, no `error_type` at this layer), so the array is never empty on an invalid verdict. The richer, locator-bearing items appear only when the runtime could attribute the failure; the human-readable summary (the `detail` on a run-route 422, the `message` on a diagnostic-route 200 invalid verdict) stays available alongside, but a consumer can always read at least one structured item.

## Suggested fixes

A validation error item may carry a `suggested_fix`: a deterministic repair the runtime's fix planner derived from the *typed* error data — never by parsing a message string. It is optional and additive. An item the planner has no rule for simply omits the field, and a client that ignores `suggested_fix` entirely behaves exactly as before.

```json
{
  "category": "pipe_validation",
  "message": "Pipe 'summarize_and_translate' declares output 'Text' but its last step produces 'Translation'",
  "error_type": "inadequate_output_concept",
  "pipe_code": "summarize_and_translate",
  "source": "translate.mthds",
  "suggested_fix": {
    "fix_code": "match-sequence-output",
    "description": "Set output of pipe 'summarize_and_translate' to 'Translation' to match its last step",
    "safety": "safe",
    "source": "translate.mthds",
    "ops": [
      {
        "kind": "set_key",
        "table_path": ["pipe", "summarize_and_translate"],
        "key": "output",
        "value": "Translation"
      }
    ]
  }
}
```

**Fields:**

- `fix_code` — the kebab-case rule id that produced the fix (`match-sequence-output`, `sync-controller-inputs`, `strip-native-concept-redecl`, `strip-namespace`, …). Stable; use it to allow-list or suppress rules.
- `description` — human-readable summary of what the fix does.
- `safety` — `safe` or `unsafe`. Only apply an `unsafe` fix behind an explicit opt-in: it resolves an ambiguity the runtime could not resolve on the caller's behalf.
- `source` — the file the ops target, when known. **An applier must only apply ops to the file they target** — in a multi-file library the ops are meaningless against any other file.
- `ops` — the semantic TOML patch operations, in order.

**Ops** are addressed by `table_path` (the containing table, e.g. `["pipe", "my_seq"]` — the same addressing convention as the items' `field_path`). Each op has a `kind`:

| `kind` | Effect | Uses |
|---|---|---|
| `set_key` | Set (or add) a key in the table | `key`, `value` |
| `ensure_table` | Create the table when it is absent | — |
| `delete_key` | Remove a key from the table | `key` |
| `delete_table` | Remove the table | — |
| `rename_table_key` | Rename a key in place | `key`, `new_key` |

`value` is a TOML scalar (string, integer, float, boolean) or a flat scalar mapping, which a fix that must create a whole table at once — a missing `inputs` mapping, say — writes as an inline table.

**The ops are the machine contract; any rendered diff is presentation.** Apply them with a style-preserving TOML editor rather than reconstructing the file from a diff: that is what keeps the caller's formatting, comments, and key order intact.

## Suggested fixes

A validation error item may carry a `suggested_fix`: a deterministic repair the runtime's fix planner derived from the *typed* error data — never by parsing a message string. It is optional and additive. An item the planner has no rule for simply omits the field, and a client that ignores `suggested_fix` entirely behaves exactly as before.

```json
{
  "category": "pipe_validation",
  "message": "Pipe 'summarize_and_translate' declares output 'Text' but its last step produces 'Translation'",
  "error_type": "inadequate_output_concept",
  "pipe_code": "summarize_and_translate",
  "source": "translate.mthds",
  "suggested_fix": {
    "fix_code": "match-sequence-output",
    "description": "Set output of pipe 'summarize_and_translate' to 'Translation' to match its last step",
    "safety": "safe",
    "source": "translate.mthds",
    "ops": [
      {
        "kind": "set_key",
        "table_path": ["pipe", "summarize_and_translate"],
        "key": "output",
        "value": "Translation"
      }
    ]
  }
}
```

**Fields:**

- `fix_code` — the kebab-case rule id that produced the fix (`match-sequence-output`, `sync-controller-inputs`, `strip-native-concept-redecl`, `strip-namespace`, …). Stable; use it to allow-list or suppress rules.
- `description` — human-readable summary of what the fix does.
- `safety` — `safe` or `unsafe`. Only apply an `unsafe` fix behind an explicit opt-in: it resolves an ambiguity the runtime could not resolve on the caller's behalf.
- `source` — the file the ops target, when known. **An applier must only apply ops to the file they target** — in a multi-file library the ops are meaningless against any other file.
- `ops` — the semantic TOML patch operations, in order.

**Ops** are addressed by `table_path` (the containing table, e.g. `["pipe", "my_seq"]` — the same addressing convention as the items' `field_path`). Each op has a `kind`:

| `kind` | Effect | Uses |
|---|---|---|
| `set_key` | Set (or add) a key in the table | `key`, `value` |
| `ensure_table` | Create the table when it is absent | — |
| `delete_key` | Remove a key from the table | `key` |
| `delete_table` | Remove the table | — |
| `rename_table_key` | Rename a key in place | `key`, `new_key` |

`value` is a TOML scalar (string, integer, float, boolean) or a flat scalar mapping, which a fix that must create a whole table at once — a missing `inputs` mapping, say — writes as an inline table.

**The ops are the machine contract; any rendered diff is presentation.** Apply them with a style-preserving TOML editor rather than reconstructing the file from a diff: that is what keeps the caller's formatting, comments, and key order intact.

## Status codes

The HTTP status follows pipelex's `error_domain_to_http_status`:

- `error_domain = "input"` → **422** — the caller can fix it (bad `.mthds`, malformed arguments, validation failure).
- `error_domain = "config"` → **500** — the deployment needs a fix (missing env var, bad TOML override).
- `error_domain = "runtime"` → **500** — failure during execution (worker crash, upstream service failure).
- Unclassified / unknown → **500**.

A few specific statuses bypass the domain mapping:

- **400** — a well-formed request this deployment cannot serve. `error_type = "StartRequiresAsyncOrchestration"`: `POST /v1/start` is fire-and-forget by nature, and this deployment's orchestrator is blocking-only (the in-process `direct` default), so it refuses honestly rather than blocking and acking — use `POST /v1/execute` instead.
- **401** — missing/invalid bearer token. `WWW-Authenticate: Bearer` is set. Only reachable when the deployment enables auth (`AUTH_MODE=api_key` or `AUTH_MODE=jwt`).
- **403** — authenticated but not authorized. Two cases: a storage-ownership mismatch, and `error_type = "OrchestrationModeOverrideForbidden"` — the request asked for an `orchestration_mode` this deployment does not allow overriding per request (`allow_request_orchestration_mode_override = false`).
- **409** — `error_type = "PipelineManagerAlreadyExistsError"`: the submitted `pipeline_run_id` is already registered for a run that is still in flight on this server. Completed and failed runs free their id, so this only fires for genuinely concurrent duplicates — resubmit after the in-flight run finishes, or pick a fresh id. Only `POST /v1/start` accepts a client-supplied `pipeline_run_id`, so only `/start` can produce it.
- **413** — request body exceeds the configured size limit (`MAX_REQUEST_BODY_MIB`, 100 MiB by default).
- **429** — an upstream inference provider rate-limited the run. `Retry-After` is set when the originating error carries `provider_metadata.retry_after_seconds`. Only `POST /v1/execute` runs inference, so only `/execute` can produce it.
- **501** — a request shape the published contract accepts but this server cannot serve. `error_type = "AsyncExecutionNotEnabledError"`: this deployment does not provide async pipeline execution (`POST /v1/start`). `error_type = "MethodRefNotSupported"`: `POST /v1/resolve` and `POST /v1/codegen` accept a `method_ref` closure selector, but no server-side method registry resolves it yet — submit inline `files[]` instead. Both are permanent under the current deployment — do not retry.

The HTTP status is the source of truth for success vs failure — there is no `success: true/false` field anywhere in the envelope.

Which statuses a given route can actually produce is documented per operation in the [committed OpenAPI artifact](openapi/pipelex-api.openapi.yaml), each as an `application/problem+json` `ProblemDocument`.

**What is *not* an error status:** an invalid `.mthds` bundle. On the diagnostic routes — `/validate`, `/resolve`, `/codegen`, and `/build/{inputs,output,runner}` — a bundle that fails validation is the *successful product* of the call, so it rides a **200** discriminated on `is_valid: false`, carrying the same `validation_errors[]` described above. Non-2xx on those routes is reserved for *no verdict could be produced*. See [Pipe Validate](pipe-validate.md).

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

Every response carries `X-Request-ID`. The middleware respects an inbound `X-Request-ID` header if present, otherwise generates a UUID. The same id rides through onto `JobMetadata.request_id`, so it correlates the inbound HTTP call with the API-side log line — and, on a distributed-execution flavor, with every orchestrator worker-side log record produced during the run.

When opening an issue, include the `request_id` from the response (or response headers) and the timestamp.

## Examples

### 422 — input validation failure

```http
POST /v1/execute
{
  "mthds_contents": ["domain = \"broken\"\nmain_pipe = \"Not A Valid Pipe Code!\"\n"]
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
  "instance": "/v1/execute",
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
      "domain_code": "broken"
    }
  ]
}
```

The single `detail` is the human summary; `validation_errors` is the machine-readable list a client maps to per-line diagnostics. Only the run routes answer an invalid bundle with this 422 — on the diagnostic routes the same bundle is a **200 `is_valid: false`** verdict, where per-file `source` attribution is also available — see [Structured validation errors](#structured-validation-errors) and [Sourcing submitted files](pipe-validate.md).

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
