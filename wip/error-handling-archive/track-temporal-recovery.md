# Track: Temporal Recovery

## What this track is

Async `/pipeline/start` returns immediately and runs the pipeline on a Temporal worker. The eventual failure surfaces later — through a completion-callback webhook, a future status endpoint, or a client polling for state. This track defines how a Temporal-side failure reaches the caller with classification intact. The work is mostly to invoke pipelex's existing `recover_error_report()` at the right boundary, so the same `ErrorReport` apparatus the synchronous path uses also covers the asynchronous one.

The synchronous path (`/pipeline/execute`) is covered by the global handler in [track-exception-handlers.md](track-exception-handlers.md). This track is about everything that happens after `/pipeline/start` returns 202.

## Background — what pipelex already does for us

From `pipelex/docs/under-the-hood/error-model.md`, section *The Temporal Error Bridge*:

- On the activity side, pipelex's `@convert_pipelex_errors` decorator transforms each `PipelexError` into a Temporal `ApplicationError` whose `details` carry `to_error_report().to_dict()`. The activity-side retry decision is derived from `InferenceErrorCategory.is_retryable`.
- On the submitter side, `recover_error_report(exc)` walks the `__cause__` chain of a Temporal failure, locates the dict packed into `ApplicationError.details`, trims unknown keys (for version-skew tolerance), and rebuilds the `ErrorReport`.
- A `WorkflowExecutionError` (a `PipelexError` subclass) wraps the recovered report; its `to_error_report()` override returns the recovered report unchanged.

The contract is: whoever holds the Temporal handle and observes the workflow completing in `FAILED` state must call `recover_error_report()` and surface the result. Pipelex's `WorkflowExecutor` already does this when the pipeline is awaited via `execute_pipeline()`, which is why the synchronous `/pipeline/execute` path Just Works once the global handler exists. The asynchronous path needs explicit wiring.

## Design

### The three async failure surfaces

A pipeline started via `/pipeline/start` can fail in three distinguishable places. Each needs the same recovery treatment but different delivery.

| Surface | When | Delivery |
|---|---|---|
| **Dispatch failure** | At `temporal_pipe_run.start(...)` — the Temporal cluster is unreachable, the worker queue is unknown, the workflow id collides, etc. | Synchronously inside the `/pipeline/start` route. Falls through to the global handlers in [track-exception-handlers.md](track-exception-handlers.md). |
| **Workflow failure** | Pipeline reached a worker and ran, but a pipe failed and the workflow ended `FAILED`. | Asynchronously, via the completion webhook. |
| **Status query failure** | A future status endpoint (not yet implemented; a known gap) returns the final state of a pipeline run. | Synchronously, mirroring the webhook payload. |

This track focuses on the second surface, with passing notes on the first (which is already covered) and the third (which is design-only for now).

### Webhook delivery for workflow failures

The piece of code that observes the Temporal workflow ending in `FAILED` state is the part that posts the completion webhook. Today it lives outside this repo (in pipelex's Temporal-runner / `WorkflowExecutor`), but the design says: wherever it lives, it MUST go through `recover_error_report()` before composing the webhook payload.

Flow:

```
Workflow finishes FAILED
  └── Temporal returns a WorkflowExecutionError to the watching task
        └── recover_error_report(exc) → ErrorReport (or None on a malformed details dict)
              └── Compose webhook payload {pipeline_run_id, status: "failed", error: <ErrorReport>}
                    └── POST to each callback_url with X-Completion-Signature
```

`error` in the webhook payload is the same RFC 7807 problem document the synchronous handler would have returned for an equivalent failure (see [track-response-schema.md — Decision 1](track-response-schema.md#decision-1--response-envelope-rfc-7807)) — same disclosure mode, same field set. A receiver does not have to special-case "this came from a webhook" vs "this came from a synchronous response."

If `recover_error_report` returns `None` (malformed details, version skew that even trimming cannot fix), the payload still carries a usable RFC 7807 `error` object: `{type: "https://pipelex.dev/errors/workflow-failure-unrecoverable", title: "Workflow failure (unrecoverable details)", status: 500, detail: "Workflow failed but the structured error could not be recovered.", error_type: "WorkflowFailureUnrecoverable", error_domain: "runtime", retryable: false, pipeline_run_id, request_id}`. This is the explicit failure-of-the-failure-path case — never silent.

### Webhook signing and integrity

The completion webhook is signed with HMAC-SHA256 (`X-Completion-Signature`, see `_completion_signature` in `api/routes/pipelex/pipeline.py:44-56`). The signature covers the `pipeline_run_id`. The current design signs only the run id, not the payload — which means a tampered `error` field would not invalidate the signature. Independent of the error-handling work, this is a small design concern worth flagging.

Out of scope for this track. Flagged here so a future iteration on signing can be considered in concert with the error payload.

### Where the webhook composer lives

The exact home of the workflow-completion observer is to be decided by [track-exception-handlers.md](track-exception-handlers.md)'s neighboring implementation work, but the candidates are:

1. **Inside pipelex's `WorkflowExecutor`** — generic, runs for any consumer of pipelex's Temporal layer. The API just supplies callback URLs.
2. **Inside a `pipelex-api`-specific background worker** — reads finished workflows, posts webhooks. More flexibility, more code.

If option 1: the design needs no further code on the API side; the API supplies `callback_urls` and pipelex handles the rest. The error-payload shape decision propagates upstream into pipelex, which is fine because pipelex already owns `ErrorReport`.

If option 2: the API-side background worker calls `recover_error_report()` directly. The shape decision lives in this repo.

Recommendation: option 1 if pipelex is willing to take it, option 2 otherwise. The design works either way — the error payload shape is identical.

### Synchronous status endpoint (future)

A `GET /api/v1/pipeline/{run_id}` status endpoint is a natural future addition. It would return:

- For pending runs: `{pipeline_run_id, status: "running", started_at}`.
- For completed runs: `{pipeline_run_id, status: "completed", result: <PipelineResult>}`.
- For failed runs: `{pipeline_run_id, status: "failed", error: <problem+json>}` — using the same RFC 7807 problem document as the webhook and the synchronous response.

This endpoint is out of scope to implement, but the design names it explicitly so the response shape stays consistent.

### Idempotent recovery and version skew

`recover_error_report` already tolerates version skew: unknown keys in the details dict are trimmed before `ErrorReport.from_dict` validation, and a dict that still fails validation yields `None` (not an exception). The API does not add anything here — it consumes `recover_error_report` as-is.

The webhook composer must treat the `None` return as a real case (the "WorkflowFailureUnrecoverable" payload above). It must not call `from_dict` itself and must not work around a `None` by inventing classification fields.

### Retry behavior at the activity layer

`@convert_pipelex_errors` already sets the Temporal activity retry policy based on `InferenceErrorCategory.is_retryable`. The API does not configure this — pipelex owns it. The result the API sees in the webhook is the *final* state after all activity-level retries have been exhausted.

What the API DOES communicate to clients (via `error_category` / `retryable` on the webhook payload) is whether the *client* should retry submitting a new run. This is a different question from whether the activity should have retried internally:

- `retryable = true` on the payload means: re-submitting the same input may succeed (transient network, rate limit, the dropped connection mid-call that came back as `AMBIGUOUS=false` but `TRANSIENT=true`).
- `retryable = false` means: re-submitting will fail the same way (bad input, quota exhausted, content policy).

This distinction is already encoded by `ErrorReport.retryable`; the API just delivers it.

## Out of scope

- Designing the webhook signing scheme (flagged above as a separate concern).
- The status-polling endpoint, beyond confirming its payload shape would mirror the webhook.
- Server-side retry policy (pipelex's activity-level retries, configured by `cogt.transport_max_retries` and the activity RetryPolicy).

## Related tracks

- [architecture.md](architecture.md) — the contract `recover_error_report` belongs to.
- [track-exception-handlers.md](track-exception-handlers.md) — the synchronous half of the same picture.
- [track-response-schema.md](track-response-schema.md) — the envelope the webhook payload shares.
- [track-observability.md](track-observability.md) — the log side; webhook deliveries should be logged with the same correlation fields.
