# Error Handling — Design

This directory captures the design for how `pipelex-api` consumes the `pipelex` error model. It is **design only** — there is no implementation plan yet. Tracks describe the intended shape of the system; open questions are called out where a design decision needs sign-off before we move to planning.

## Why this exists

Pipelex shipped a complete error contract — `ErrorReport`, `error_category`, `error_domain`, `provider_metadata`, cause-chain enrichment, Temporal boundary recovery — and explicitly names "a downstream FastAPI exception handler" as the canonical consumer (see [the pipelex error-model docs](../../../pipelex/docs/under-the-hood/error-model.md), section *Interfaces → API*). This API currently consumes none of it: every route does `except ENDPOINT_HANDLED_EXCEPTIONS as exc: raise_internal_error(...)` (see `api/errors.py:71-86`) and collapses every failure to an HTTP 500 with only the exception class name. The work below is to claim that contract.

For the full motivation — what the API throws away today, what pipelex hands us, and the senior-engineer take — see [analysis.md](analysis.md).

## Status at a glance

| Track | Status | Doc |
|---|---|---|
| Architecture & contract | Designed | [architecture.md](architecture.md) |
| Exception handlers | Designed (load-bearing) | [track-exception-handlers.md](track-exception-handlers.md) |
| Response schema | Designed (decisions settled) | [track-response-schema.md](track-response-schema.md) |
| Temporal recovery | Designed | [track-temporal-recovery.md](track-temporal-recovery.md) |
| Observability | Designed | [track-observability.md](track-observability.md) |

## Settled design decisions

The two one-way doors that needed sign-off are settled. They are documented inside [track-response-schema.md](track-response-schema.md) with the rationale and the conditions under which they would be revisited.

1. **Response envelope — RFC 7807 `application/problem+json`.** HTTP-standard error shape, `type` URI per error class, pipelex `ErrorReport` fields ride as RFC 7807 extension members. See [track-response-schema.md — Decision 1](track-response-schema.md#decision-1--response-envelope-rfc-7807).
2. **Info-disclosure default — verbose, with `ERROR_DISCLOSURE=strict` opt-in.** Self-hosted default returns the full `ErrorReport`; hosted multi-tenant deployments set `ERROR_DISCLOSURE=strict` to sanitize `CONFIG`/`RUNTIME` messages. `INPUT`-domain errors and logs are always verbose. `provider_metadata.body` is always excluded (pipelex already does that). See [track-response-schema.md — Decision 2](track-response-schema.md#decision-2--info-disclosure-default-verbose).

Everything else is recommended-with-rationale and can be iterated on the docs.

## Suggested read order

1. [analysis.md](analysis.md) — what we're facing and why.
2. [architecture.md](architecture.md) — the layer model, the contract with pipelex, the key types and pure functions the design hinges on.
3. [track-exception-handlers.md](track-exception-handlers.md) — the central piece. Everything else builds on this.
4. [track-response-schema.md](track-response-schema.md) — what clients see on the wire. Both settled decisions and their rationale live here.
5. [track-temporal-recovery.md](track-temporal-recovery.md) — async `/pipeline/start` and the completion-callback surface; how a Temporal-side failure reaches the caller with classification intact.
6. [track-observability.md](track-observability.md) — structured logging, request correlation, what operators get.

## Cross-cutting principles

These are inherited from the pipelex error model and shape every track:

- **`PipelexError` is the single root.** Every domain error inherits from it. One global handler covers the whole tree; the per-route `except` blocks go away.
- **Classification is set at the source, never lost.** Pipelex classifies at the worker layer (Layer 1); every wrapper layer above preserves classification via `to_error_report()` cause-chain enrichment. The API is just another wrapper layer — it must preserve classification, not re-derive it.
- **Don't redefine the mapping.** `error_domain_to_http_status()` and `ErrorReport.http_status` are pure functions exported by pipelex. The API consumes them; it does not maintain a parallel table or a `if isinstance(...)` ladder.
- **Log fully, respond consistently.** `ProviderErrorMetadata.body` is already `exclude=True` in pipelex — it never leaves the process. Everything else is structured, classified, and safe to log.
- **No `except Exception` outside the two allowed places.** App-root catch-all in the global error path; everywhere else the catches are specific. The current `ENDPOINT_HANDLED_EXCEPTIONS` tuple is a code smell and goes away.

## Conventions used in track docs

Each `track-*.md` opens with **what this track is** (the concern in one paragraph) and a **design** section laying out the intended shape, with file-path references against `pipelex` and `pipelex-api`. **Open questions** are called out explicitly with options, tradeoffs, and a recommendation. **Related tracks** cross-references close every doc. Design docs describe the intended state, not the steps to get there — implementation planning is a separate exercise.
