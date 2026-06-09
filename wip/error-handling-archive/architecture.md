# Architecture — Layer Model & Contract

This document is the reference for *how* `pipelex-api` sits on top of the pipelex error model. Every track doc cross-references back here.

## The layer model, extended to the API

The pipelex error model is laid out in layers (`pipelex/docs/under-the-hood/error-model.md`, section *The Layer Model*). The pipelex side defines six layers (0 to 5). When `pipelex-api` is the consumer, the picture extends one layer further: the API itself is the consumer of `ErrorReport`, alongside (and analogous to) the human CLI and the agent CLI.

| Layer | Role | What it does with errors |
|-------|------|--------------------------|
| **A — Client** | Postman, agents, frontends, internal services | Sees an HTTP response carrying `ErrorReport` fields and decides whether to retry / report / route |
| **6 — API adapter** *(this repo)* | FastAPI handlers in `pipelex-api` | Catches `PipelexError` once at app level; maps to status via `ErrorReport.http_status`; emits structured response + structured log |
| **5 — Pipelex entry points** | `PipelexRunner`, CLI entry, library callers | Raises `PipelexError` subclasses with full classification |
| **4–1 — Pipelex internals** | Factories, routers, operators, workers | Classify (at L1) and wrap with context (L2–L4); never lose classification |
| **0 — Third-party SDKs** | OpenAI, Anthropic, Google, Temporal, boto3 | Raise raw, untyped provider exceptions |

The single invariant: **`pipelex-api` is a wrapper layer like any other** — it must preserve classification, not re-derive it. Concretely, this means:

- The API does not maintain its own enum of error categories or its own domain-to-HTTP-status table; it consumes `ErrorReport.http_status` and `error_domain_to_http_status()` directly.
- The API does not pattern-match on exception class names to decide a status code; the class is opaque and `error_domain` is authoritative.
- The API does not re-classify provider SDK exceptions; pipelex's workers (Layer 1) already did that, and the classification survives wrappers via `_enrich_error_report_from_cause`.

## The contract — what we consume from pipelex

Every type and function below is exported by pipelex today. The API depends on this surface and on nothing else.

### Types

| Symbol | Source | Role |
|---|---|---|
| `PipelexError` | `pipelex.base_exceptions` | Single root of the exception hierarchy. The only exception type the global handler catches by name. |
| `ErrorReport` | `pipelex.base_exceptions` | Frozen serialization schema (`extra="forbid"`, Pydantic dataclass). What the API returns. |
| `ErrorDomain` | `pipelex.base_exceptions` | `INPUT` / `CONFIG` / `RUNTIME`. Drives HTTP status. |
| `InferenceErrorCategory` | `pipelex.cogt.exceptions` | `TRANSIENT` / `CAPACITY` / `CONTENT` / `CONFIGURATION` / `AMBIGUOUS` / `UNKNOWN`. Drives retry decisions. |
| `UserAction`, `UserActionKind` | `pipelex.cogt.inference.error_classification` | Typed advice — `kind` + free-form `detail`. |
| `ProviderErrorMetadata` | `pipelex.cogt.inference.error_classification` | SDK metadata — status code, request id, `retry_after_seconds`, provider error code. `body` is excluded from serialization. |

### Methods and pure functions

| Symbol | Role |
|---|---|
| `PipelexError.to_error_report()` | The entry point. Returns an `ErrorReport` enriched from the `__cause__` chain. |
| `ErrorReport.to_dict()` | `None`-free dict — what we put on the wire. |
| `ErrorReport.from_dict()` | Strict inverse — raises `ValidationError` on a malformed dict. |
| `ErrorReport.http_status` | 422 / 429 / 500 with the provider-429 passthrough. |
| `error_domain_to_http_status(error_domain)` | The pure domain-to-status table. `ErrorReport.http_status` layers the 429 special-case on top. |
| `recover_error_report(exc)` | Walks `__cause__` for a Temporal `ApplicationError`, returns `ErrorReport | None`. |

### What "cause-chain enrichment" means for us

Pipelex's `to_error_report()` overrides walk the `__cause__` chain and pull `error_category` / `error_domain` / `retryable` / `user_action` / `model` / `provider` / `provider_metadata` from the deepest classified cause. A wrapper exception keeps its own `error_type` and `message` but inherits classification it did not set itself.

The API exploits this directly. We do not need to walk the chain ourselves — we catch the outermost `PipelexError`, call `to_error_report()`, and pipelex assembles the full picture. The exception we catch in `/pipeline/execute` may be a `PipelineExecutionError` whose ultimate cause is an `LLMCompletionError` raised four layers deep; the resulting `ErrorReport` carries the LLM classification regardless.

## Where each piece of the contract lives in pipelex

For grounding when reading the design docs:

| Pipelex file | Purpose |
|---|---|
| `pipelex/base_exceptions.py` | `PipelexError`, `ErrorReport`, `ErrorDomain`, `error_domain_to_http_status()`, `recover_error_report()` |
| `pipelex/cogt/exceptions.py` | `CogtError`, `InferenceErrorCategory` |
| `pipelex/cogt/inference/error_classification.py` | `ProviderErrorMetadata`, `UserAction`, `UserActionKind` |
| `pipelex/temporal/tprl/temporal_error.py` | `TemporalError`, `from_message_exception()`, `recover_error_report()` (the Temporal-aware overload) |
| `pipelex/temporal/tprl/workflow_caller.py` | `WorkflowExecutionError` |

## The API surface affected

For grounding when reading the track docs, here is what changes in `pipelex-api`:

| File | Role today | Role after |
|---|---|---|
| `api/main.py` | App init, middleware, router registration | Same + global exception handlers registered on the app |
| `api/errors.py` | Per-route catch tuples + `raise_internal_error` + API-owned validation helpers | API-owned validation helpers only; tuples and `raise_internal_error` removed |
| `api/error_types.py` | Static enum of API-owned error_type strings | Same — unchanged |
| `api/routes/pipelex/pipeline.py` and siblings | `try / except ENDPOINT_HANDLED_EXCEPTIONS / raise_internal_error` blocks per route | Plain calls into pipelex; the global handler takes care of `PipelexError`s |

## The response and the log — one source, two consumers

A single `ErrorReport` produced inside the global handler feeds two destinations:

```
PipelexError raised → caught by global handler → exc.to_error_report() → ErrorReport
                                                                          │
                                                                          ├── to_dict() → HTTP response body
                                                                          └── structured log fields → operator log
```

`ProviderErrorMetadata.body` is `exclude=True` and never appears in either destination. Everything else does, in both destinations, identically. There is no "log it but hide it from the user" tier in the default disclosure mode — the open question for a stricter hosted-mode default is captured in [track-response-schema.md](track-response-schema.md#open-question-2--info-disclosure-default).

## The Temporal half of the picture

For async `/pipeline/start`, the picture extends past the initial response. The eventual failure surfaces via one of:

- A completion-callback webhook (POST with the failed `PipelexPipelineExecuteResponse` or an error payload).
- A future status-polling endpoint (not yet implemented; a known gap).
- A client-side Temporal handle (out of scope for `pipelex-api`).

The error model still applies, but the recovery point is different: the API is no longer catching a live exception, it is reading a Temporal `WorkflowExecutionError` (or its underlying `ApplicationError`) and reconstructing the `ErrorReport` via `recover_error_report()`. The output shape (response body / webhook payload) must be the same as the synchronous path so callers do not have to special-case it. See [track-temporal-recovery.md](track-temporal-recovery.md).

## Cross-references

- [track-exception-handlers.md](track-exception-handlers.md) — the central handler that turns this contract into FastAPI behavior.
- [track-response-schema.md](track-response-schema.md) — what the response shape on the wire looks like, with the two open questions.
- [track-temporal-recovery.md](track-temporal-recovery.md) — the asynchronous half of the contract.
- [track-observability.md](track-observability.md) — the log side of the one-source-two-consumers picture.
