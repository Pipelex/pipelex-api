# Track: Observability

## What this track is

The other consumer of every `ErrorReport`. The exception handler ([track-exception-handlers.md](track-exception-handlers.md)) produces one `ErrorReport`; the response track ([track-response-schema.md](track-response-schema.md)) defines what flows to the client; this track defines what flows to the operator log. The two destinations share a single source — the same object emitted in both shapes — so they cannot drift.

This track also covers request correlation, which is an API-layer concern independent of pipelex but inseparable from "produce useful error logs."

## Design

### One `ErrorReport`, two consumers

The picture from [architecture.md](architecture.md):

```
PipelexError raised → caught by global handler → exc.to_error_report() → ErrorReport
                                                                          │
                                                                          ├── to_dict() → HTTP response body
                                                                          └── structured log fields → operator log
```

The log entry is emitted from the same place the response is built, off the same `ErrorReport` object. The log carries strictly more than the response (no strict-mode redaction applies to logs; `provider_metadata.body` is excluded but everything else flows through).

### What every error log line carries

| Field | Source | Why |
|---|---|---|
| `level` | `error` for unhandled / `warning` for `INPUT`-domain caller errors | Operator alerting differentiates "your code is broken" from "your caller sent bad input" |
| `event` | Fixed value, e.g. `"api_error"` | Stable key for log search / dashboards |
| `request_id` | API-injected | Correlation across API, pipelex, Temporal worker logs |
| `pipeline_run_id` | Available on pipeline routes; injected when known | Correlation with the Temporal workflow log |
| `pipe_code` | From the request | Per-pipe error rate dashboards |
| `route` | The matched FastAPI route path (`/api/v1/pipeline/start`) | Per-endpoint error rate |
| `user_id` | From `request.state.user` (see `_get_user_id` in `api/routes/pipelex/pipeline.py:38`) | Per-user error rate; abuse / misconfig detection |
| `error_type` | `ErrorReport.error_type` | Stable identifier for client-side branching; here also a log facet |
| `error_category` | `ErrorReport.error_category` | Per-category alerts (e.g. spike of `CAPACITY`) |
| `error_domain` | `ErrorReport.error_domain` | Routing of alerts (operator vs caller) |
| `retryable` | `ErrorReport.retryable` | Lets dashboards filter "expected transient" from "needs attention" |
| `provider`, `model` | `ErrorReport.{provider,model}` | Per-backend error rate |
| `provider_status_code` | `ErrorReport.provider_metadata.status_code` | HTTP-status histogram by provider |
| `provider_request_id` | `ErrorReport.provider_metadata.request_id` | Lets us look up the failing call in the provider's own logs |
| `traceback` | Captured by the logger when the level is `error` | The thing operators actually need when triaging |

For the fallback `Exception` handler (the catch-all for non-`PipelexError`), the same envelope is used with `error_type = "InternalServerError"`, `error_domain = "runtime"`, `error_category = "unknown"`, and a full traceback. The shape stays consistent across both handler paths.

### Structured logging, not f-strings

Today the API logs with f-strings:

```python
log.error(f"{context}: {type(exc).__name__}", include_exception=True)
```

This produces a single line of human text. Greppable, but not indexable by Datadog / Grafana / Loki / cloudwatch-insights without parsing.

Design: emit the entry as a structured record (key-value pairs that the configured log handler can render to whatever sink). The pipelex `log` object accepts a `title` arg and renders structured payloads via Rich; for production deployments the API runs behind a JSON-line log handler that emits each field separately. The local-dev rendering stays human-readable.

This is independent of the error-handling work but inseparable from "errors finally carry useful fields" — if the fields are not in structured form, half the value is lost.

### Request correlation

Every inbound request is given an `X-Request-ID` if the client did not supply one. The id is:

- Stored on `request.state.request_id` (alongside `request.state.user`).
- Echoed back on every response (including success responses) via an `X-Request-ID` header.
- Included on every log line emitted during the request via a contextvar or a logger filter.
- Included in the error response body (see [track-response-schema.md](track-response-schema.md)).
- Forwarded to downstream calls — at minimum, to the Temporal workflow as workflow metadata, so logs from the worker side carry the same correlation id.

A small middleware in `api/middleware.py` (sibling of the existing `request_body_size_middleware`) handles the inbound parsing, the storage on `request.state`, and the response-header echo. Logger configuration handles the per-line injection.

The header name is the de facto standard `X-Request-ID`. Format is a ULID or UUIDv7 — sortable, dense, URL-safe.

### What dashboards and alerts the structured logging enables

Once each field above is indexed, the operator gets:

- **Per-category error rate**, broken down by `error_category`. Spike of `CAPACITY` → someone's quota is about to fail. Spike of `CONFIGURATION` → page operator. Spike of `TRANSIENT` → check provider status.
- **Per-provider, per-model error rate**, broken down by `provider` × `model`. Lets us spot "Anthropic is degrading" before users do.
- **Per-route, per-user error rate**. Lets us spot a single misconfigured client from a population.
- **HTTP-status histogram by provider** via `provider_status_code`. Distinguishes provider 429s from provider 5xx from provider auth failures.
- **End-to-end correlation** by `request_id`. A failed API request can be traced through pipelex's internal logs, the Temporal workflow log, and the provider's own logs (via `provider_request_id`) — without parsing free-form strings.

None of this is built as part of the error-handling work; the structured fields are the foundation that makes it possible. Specific dashboards and alert rules are downstream.

### What the success path needs from this track

A request id middleware that runs on every request, not only on errors. A logger configuration that injects the request id into every log line. Both are independent of the exception handlers — the failure path consumes them, but the success path benefits too.

### Logging the webhook delivery

When the completion webhook ([track-temporal-recovery.md](track-temporal-recovery.md)) is posted, the delivery emits its own structured log line carrying:

- `event = "webhook_delivery"` (or `"webhook_failure"` if non-2xx)
- `request_id` of the *original* request (the one that called `/pipeline/start`)
- `pipeline_run_id`
- `callback_url` and the response status
- `error_type`, `error_category`, etc. when the workflow failed — same fields, same shape

This means a single `request_id` ties together: the original API request, every pipelex internal log during execution, the Temporal worker logs, and the webhook delivery line. End-to-end traceability with one search query.

### What the structured log does NOT carry

- `provider_metadata.body` — already excluded by pipelex. Never logged.
- The full HTTP request body — too large, can carry sensitive payloads. Only field-level extracts (pipe code, output name) are logged.
- Stack frames for `INPUT`-domain caller errors — usually noise. The handler logs them at `warning` without a traceback. Operators who want to investigate can correlate via `request_id`.
- Secret material from auth headers, env vars, or config — these never enter the `ErrorReport` because pipelex does not surface them, and the API does not synthesize them into log lines.

## Out of scope

- The specific log sink (stdout JSON for container runtimes, datadog-agent forwarder, etc.) is deployment configuration, not design.
- Dashboards and alerting rules. The fields are the design; the specific Grafana panels are not.
- Tracing (OpenTelemetry) — request correlation is a strict subset of distributed tracing. If the system grows tracing later, the `request_id` becomes part of the trace context.

## Related tracks

- [architecture.md](architecture.md) — the `ErrorReport` is the source for both consumers.
- [track-exception-handlers.md](track-exception-handlers.md) — the producer side.
- [track-response-schema.md](track-response-schema.md) — the other consumer; the strict-mode redaction (`ERROR_DISCLOSURE=strict`) never affects logs.
- [track-temporal-recovery.md](track-temporal-recovery.md) — the same correlation fields tie the webhook delivery back to the original request.
