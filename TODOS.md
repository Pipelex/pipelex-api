# Implementation Plan — Error Handling

This document tracks the error-handling rework for `pipelex-api`. The full design lives under `wip/error-handling/`. **The design is settled** — refer to it for rationale; do not relitigate decisions here.

---

## Status

- **This PR delivers Phases 0–3** — the synchronous error path. Every API error response now emits RFC 7807 `application/problem+json` with the same field set across pipelex domain errors, validation errors (4xx), auth (401/403), payload limits (413), and the catch-all 500.
- **This PR ends at Phase 3.** Phase 4 (Temporal webhook recovery) and Phase 5 (documentation + archive) are deferred to follow-up PRs — their full plans are retained at the bottom of this document for the next session.
- **Branch:** `feature/Adapt-to-pipelex-update`.
- **Phase 3 review** (13 questions surfaced by a multi-agent review at Checkpoint C) **is fully resolved.** Each got a verdict (fix / document-as-intended / file upstream) and the code/tests landed across commits `e683338` → `2a78409`. See the "Phase 3 review resolutions" section below for the one-paragraph summary; the per-question detail lives in those commit messages and in `wip/error-handling/pipelex-changes.md` Stage 7 (for the upstream items filed).

---

## What this PR delivers

**The shape of the change.** Per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` error-shaping is gone. Routes call into pipelex and let exceptions propagate. Four global handlers in `api/exception_handlers.py` render the response:

1. `ApiError` — API-authored 4xx / 5xx raised via the helpers (`raise_validation_error`, `raise_bad_request`, `raise_forbidden`, `raise_unauthenticated`, `raise_payload_too_large`, `raise_internal_server_error`).
2. `PipelexError` — domain errors raised by the pipelex library; rendered via `report.to_problem_document(...)`.
3. `TemporalError` — bare `temporalio.TemporalError` (i.e. *not* a `PipelexError` subclass); synthesized as a transport-transient `ErrorReport`.
4. `Exception` — sanitized 500 catch-all (real class + traceback → log only, never the body).

**Plus:** `RequestValidationError` (FastAPI's automatic body-validation 422) is registered as its own handler so the wire shape matches an explicit `raise_validation_error`.

**On the wire.** `application/problem+json` with RFC 7807 standard fields (`type`, `title`, `status`, `detail`, `instance`) plus extension members (`error_type`, `error_domain`, `retryable`, `request_id`, and — when populated by pipelex — `error_category`, `user_action`, `provider_metadata`, `model`, `provider`). The legacy `{"detail": {"error_type", "message"}}` envelope is gone from the entire surface. Every response (success and error) carries `X-Request-ID`.

**Disclosure modes.** `ERROR_DISCLOSURE=verbose` (default) renders pipelex `ErrorReport`s in full. `ERROR_DISCLOSURE=strict` calls `ErrorReport.to_dict(disclosure_mode=STRICT)`, which redacts the `detail` of non-caller-facing errors upstream (provenance-based, keyed on `caller_facing_message`). Logs are always verbose; `INPUT`-domain errors are always verbose on the wire too.

**Observability.** Every error response emits one structured log line (`event=api_error` for API-authored, `event=pipelex_error` for domain errors, `event=unexpected_error` for the catch-all). Fields: `request_id`, `route`, `error_type`, `error_domain`, `retryable`, `status`, `user_id` (when authenticated), plus pipelex-provided classification fields. INPUT-domain logs at `warning`; everything else at `error` with traceback.

**Original bug fixed.** A `POST /api/v1/pipeline/start` against a deployment missing `COMPLETION_CALLBACK_SECRET` used to return an opaque 500; it now returns RFC 7807 500 whose `detail` names the env var (verbose) or is sanitized to `"Server configuration error"` (strict), and the structured log carries the env var name on every disclosure mode.

---

## File map (for cold starts)

**New API modules:**

- `api/exception_handlers.py` — the four global handlers + `register_exception_handlers(app, *, disclosure_mode)`. Import-side-effect-free (Q13 in the Phase 3 review made this so).
- `api/problem_document.py` — pure builder. `build_problem_document(report, ...)` delegates to `report.to_problem_document(...)`; `build_problem_document_from_api_error(...)` is the API-authored variant. `PROBLEM_JSON_MEDIA_TYPE` constant.
- `api/disclosure.py` — `resolve_disclosure_mode()` reads `ERROR_DISCLOSURE` at startup, fails fast on a bad value.
- `api/error_uri.py` — `error_type_uri(...)` / `error_type_title(...)` for API-authored errors (which have no `ErrorReport`).
- `api/logging_context.py` — `request_id` / `route_path` contextvars set by the middleware.
- `api/middleware.py` — added `RequestIdMiddleware` (pure-ASGI, wraps the FastAPI app — outermost layer so `ServerErrorMiddleware`'s fallback 500 also carries `X-Request-ID`).

**Modified API modules:**

- `api/errors.py` — full rewrite. Added the `ApiError` exception and the `raise_*` helpers; deleted `ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` / `raise_internal_error`.
- `api/main.py` — slimmed from 478 lines to 94. Construction, middleware, router registration, and `register_exception_handlers(app, disclosure_mode=ERROR_DISCLOSURE_MODE)`.
- `api/security.py` — all 7 `HTTPException` sites migrated to `raise_unauthenticated` / `raise_internal_server_error`. `HTTPBearer(auto_error=False)` so the missing-header case also goes through the helpers (Phase 3 review Q3).
- `api/routes/pipelex/{pipeline,validate,build/*,agent/*}.py`, `api/routes/{uploader,storage,version}.py` — per-route `try/except` blocks stripped; inline `HTTPException` sites migrated to the helpers.

**New tests:**

- `tests/unit/test_error_responses.py` — Phase 3 regression suite (T1 413, T2 storage-403, T5 `X-Request-ID` parametrized across every error status).
- `tests/unit/test_problem_document.py`, `test_exception_handlers.py`, `test_request_id_middleware.py`, `test_logging_context.py`, `test_disclosure.py`, `test_error_uri.py`.

**Pipelex companion items consumed** (all landed via PRs #931 / #933 against `pipelex==0.29.1`):

- #1 `PipelexError.title()` / `ErrorReport.title`
- #2 `PipelexError.type_uri()` / `ErrorReport.type_uri`
- #3 First-class `request_id` on `JobMetadata`
- #4 `DisclosureMode` + `ErrorReport.to_dict(disclosure_mode=)`
- #5 `DeliveryExecutor.execute(error_report=...)` — Phase 4a dependency, **landed** (consumed in a follow-up PR, not this one)
- #6 `ErrorReport.to_problem_document(...)`
- #7 Per-class `type` URI doc pages under `docs.pipelex.com/latest/errors/<kebab>/`

Tracking table lives at `wip/error-handling/pipelex-changes.md`. Items #10–#15 (Stage 7) are filed upstream against pipelex / kajson; none block the API.

---

## Settled decisions (do not relitigate)

- **Envelope:** RFC 7807 `application/problem+json` with pipelex `ErrorReport` fields as extension members.
- **Disclosure default:** verbose; opt-in to sanitization via `ERROR_DISCLOSURE=strict`. INPUT-domain always verbose. Logs always verbose. `provider_metadata.body` excluded by pipelex.
- **`type` URI namespace:** `https://docs.pipelex.com/latest/errors/<kebab-case-error-type>/` (owned upstream by `URLs.error_docs_base`; resolves to a real per-class doc page).
- **Webhook payload shape (Phase 4 territory):** the webhook carries the raw `ErrorReport` dict under `error`, *not* an RFC 7807 envelope. Rationale: the receiver may not be HTTP (queue, log shipper, batch processor). Sync HTTP renders to RFC 7807 because HTTP wants it; webhook stays structurally faithful to the source.

---

## Phase 0 — Foundations (✅ landed)

**Scope:** `ERROR_DISCLOSURE` env plumbing, request-id middleware, logging contextvar, RFC 7807 type/title derivation module, unit tests for each.

**What landed:**

- `api/logging_context.py`, `api/disclosure.py`, `api/error_uri.py` created.
- `api/middleware.py` — added `RequestIdMiddleware` (pure-ASGI, wraps the FastAPI app — confirmed catch-all 500 carries `X-Request-ID`).
- `api/main.py` — resolves `ERROR_DISCLOSURE_MODE` at startup; production fail-fast preserved.
- Unit tests in `tests/unit/test_logging_context.py`, `test_disclosure.py`, `test_error_uri.py`, `test_request_id_middleware.py`.

**Reconciliation notes preserved:**

- `RequestIdMiddleware` wraps the app, not `add_middleware` — so the catch-all 500 from Starlette's `ServerErrorMiddleware` still gets the header and the contextvars are bound when the handler runs.
- `error_uri.py` titles are sentence-case (e.g. `"Validation error"`) via pipelex's `pascal_case_to_sentence`, but unlike `PipelexError.title()` it does *not* strip the trailing `Error` — API `ErrorType` like `ValidationError` keeps the suffix.
- `pyright` resolves the editable `pipelex` only with `--pythonpath` set (the `make pyright` target does this) or with `VIRTUAL_ENV` set. Always verify types via `make c`.

---

## Phase 1 — RFC 7807 problem-document builder (✅ landed)

**Scope:** pure module that turns an `ErrorReport` (and optional context) into a problem+json dict. No FastAPI imports.

**What landed:**

- `api/problem_document.py` created. `build_problem_document(report, *, instance, request_id, disclosure_mode)` delegates to `report.to_problem_document(...)` (pipelex item #6). `build_problem_document_from_api_error(error_type, message, status, *, instance, request_id, error_domain=ErrorDomain.INPUT, retryable=False)` is the API-authored variant — needed because API-authored errors have no `ErrorReport`. Includes `retryable: False` unconditionally (Phase 3 review Q7).
- `disclosure_mode` parameter is typed `DisclosureMode` (pipelex's StrEnum), not `Literal["verbose","strict"]` — caller already holds the enum.

**Reconciliation notes preserved:**

- `EnvVarNotFoundError` is **domain-less** in this pipelex version (a `ToolError`; neither parent sets `error_domain`). The RFC 7807 doc has no `error_domain` member; HTTP status is still 500 via `error_domain_to_http_status(None) → 500`. A genuine `error_domain="config"` is exercised by `PipelexConfigError`. Filed upstream as item #10 in `pipelex-changes.md`.
- `ErrorReport.title` / `type_uri` are required fields in `pipelex==0.29.1` — the "fallback when None" path on `build_problem_document` is unreachable. `error_uri.py` is the genuine source of these fields for the API-authored variant only.

---

## Phase 2 — Global exception handlers (✅ landed)

**Scope:** register app-level handlers that consume the Phase 1 builder and emit structured logs. Routes are not modified — that's Phase 3.

**What landed:**

- Three handlers initially (plus the `ApiError` handler added in Phase 3) — all defined in what is now `api/exception_handlers.py` (moved out of `api/main.py` by Phase 3 review Q13). `register_exception_handlers(app, *, disclosure_mode)` wires them.
- `PipelexError` → `report.to_problem_document(...)` → RFC 7807. `Retry-After` header when `provider_metadata.retry_after_seconds` is set. Structured log: `warning` for `INPUT`, `error` + traceback otherwise.
- `TemporalError` (bare, not `PipelexError` subclass) → API-authored `ErrorReport` (`TemporalTransportError`, `runtime` / `transient` / retryable) → same render + log path. Note: `WorkflowExecutionError` IS a `PipelexError` subclass and is caught by the more-specific `PipelexError` handler (T4 test covers dispatch).
- `Exception` fallback → sanitized 500. Real class name + traceback → log only, never the body. Includes `error_category="unknown"` (the catch-all *is* "could not classify").
- Tests in `tests/unit/test_exception_handlers.py` cover dispatch (T4), handler-of-handlers safety (T3), `X-Request-ID` propagation, `Retry-After` emission, strict-mode redaction.

**Reconciliation notes preserved:**

- The pipelex `log` object renders a single message string. `_emit_error_log` flattens the field map to a greppable `key=value` run — **followup:** swap to native structured emission once a JSON log sink lands.
- `disclosure_mode` is bound at registration time via `register_exception_handlers(app, *, disclosure_mode=...)` and captured in two thin closures (no module-level mutable state, no `PLW0603` trip).
- Body-derived observability fields (`pipe_code`, `pipeline_run_id`) are not yet on `request.state` — open follow-up. The `user_id` slice landed via Phase 3 review Q9; the mechanism (set on `request.state`, read in the handler via `_user_id_of`) is in place for the others when a future change wires them.

---

## Phase 3 — Route cleanup, 4xx helper migration, inline-`HTTPException` migration (✅ landed)

**Scope:** the subtraction phase. Strip per-route `try/except` error-shaping; migrate every direct `HTTPException` to RFC 7807 via the new helpers.

**What landed:**

- **Stripped** per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` across `pipeline.py`, `validate.py`, `build/{inputs,output,runner}.py`, `agent/{concept,pipe_spec,models}.py`, `uploader.py`, `storage.py`. `try/finally` for library teardown is kept (`build/*.py`) — sanctioned cleanup pattern.
- **Migrated** every inline `HTTPException` site to the helpers — across `security.py` (7 sites: 5×401, 2×500), `storage.py` (401/400/403/500), `uploader.py` (401/400/413), `version.py` (2×500), and `middleware.py` `_too_large_response()` (413, built inline because middleware must return a response, not raise).
- **`api/errors.py`** — full rewrite. Deleted `ENDPOINT_HANDLED_EXCEPTIONS`, `STORAGE_HANDLED_EXCEPTIONS`, `raise_internal_error`. Added the `ApiError` exception + the six `raise_*` helpers + `handle_api_error` in `api/exception_handlers.py` (a `HTTPException` default handler cannot emit a flat problem+json document — see Phase 3 reconciliation note 1).
- **`build_problem_document_from_api_error` gained an `error_domain` parameter** — a 500 server-config fault is `CONFIG`, not `INPUT`. Default stays `INPUT` for 4xx.
- **`_parse_request`** in `pipeline.py` now catches `PipelineRequestError` / `ValidationError` from `PipelineRequest.from_body` and maps to 422 (fixed Checkpoint B reconciliation #1 — those used to escape as a sanitized 500).
- **Regression tests** in `tests/unit/test_error_responses.py` — T1 (413 RFC 7807), T2 (storage 403 RFC 7807), T5 (`X-Request-ID` parametrized across 401/400/403/413/422/500), validation→`input`-domain, 500→`config`-domain, `WWW-Authenticate` challenge preserved, inbound-id echo.
- **Old-shape audit:** every test asserting `{"detail": {...}}` updated to the flat RFC 7807 shape across `test_pipeline_routes.py`, `test_build_and_agent_routes.py`, `test_storage.py`, `test_uploader.py`, `test_security_verifiers.py`, `test_simple_routes.py`.

**Reconciliation notes preserved:**

1. **Why `ApiError` exists.** FastAPI's default `HTTPException` handler wraps the body as `{"detail": <whatever>}` — it cannot emit a flat RFC 7807 document or set `application/problem+json`. The helpers raise `ApiError` (carrying a pre-built problem document + status + headers) and `handle_api_error` renders it. Mirrors the existing 3-handler architecture; `register_exception_handlers` wires four handlers in total (plus the `RequestValidationError` one added in the Q2 resolution).
2. **A1's enumeration was incomplete.** Review amendment A1 named only `middleware.py` + `storage.py`; the catch-site audit found three more files (`uploader.py`, more sites in `storage.py`, `version.py`) — all migrated for uniform contract.
3. **`PipelineRequestError` is API-side, no upstream item.** It's an `mthds`-package error (not `pipelex`); the API boundary is the right place to classify it.
4. **e2e tests live in `tests/unit/`, not a new `tests/e2e/`.** The entire existing suite is TestClient integration tests with the Pipelex-setup autouse fixture in `tests/unit/conftest.py`. The regression suite is the same kind of test and lives there.

---

## Phase 3 review resolutions

A multi-agent code review at Checkpoint C surfaced 13 questions (Q1–Q13). All resolved across commits `e683338` → `2a78409` (chronological order in `git log --oneline`). Per-question detail lives in those commit messages and in the upstream items filed in `wip/error-handling/pipelex-changes.md` Stage 7.

Material changes from those resolutions, in addition to what's already named above:

- **Q2:** `RequestValidationError` (FastAPI's automatic body-validation) registered as its own handler so the wire shape matches an explicit `raise_validation_error`. Renders via `build_problem_document_from_api_error` → `error_type="ValidationError"`, `error_domain="input"`, `status=422`, `application/problem+json`.
- **Q3:** `HTTPBearer(auto_error=False)` — missing/empty/non-Bearer `Authorization` headers now route through `raise_unauthenticated(...)` instead of FastAPI's default handler. Closes the last 401 path that was still on the legacy `{"detail": "Not authenticated"}` shape.
- **Q4:** `/build/pipe-spec` catches `(ValidationError, ValueError)` for the unknown-`pipe_type` documented error. `/build/concept` left as-is; the `AttributeError`/`TypeError` leak from `parse_concept_spec` is filed upstream as item #11 (catching those types at the route would mask programming bugs).
- **Q6:** `build/runner.py` library teardown race closed — moved `open_library()` + `set_current_library()` into the `try` block with `library_id: str | None = None` initialized above and `None`-guarded teardown. Brings it into line with `inputs.py` / `output.py`.
- **Q7:** `retryable: False` added to every API-authored problem document. Pipelex's `_STRICT_KEPT_FIELDS` treats `retryable` as a stable identifier preserved through STRICT redaction — confirms it's a load-bearing classification field. `error_category` deliberately NOT added — it's inference-domain (`InferenceErrorCategory`), not generic enough to populate for API-authored errors.
- **Q8:** Every API-authored response (4xx + 5xx, raised via `raise_*` or via `RequestValidationError`) now emits one structured `event=api_error` log line. INPUT-domain → `warning` (no traceback); other domains → `error` with traceback.
- **Q9:** `user_id` rides every error log when an authenticated user is present (`request.state.user.user_id`, set by `_set_request_user`). The Checkpoint B observability gap is partially discharged (`pipe_code` / `pipeline_run_id` remain open follow-ups; same mechanism applies once the pipeline route stores them on `request.state` post-`_parse_request`).
- **Q10:** `_decode_body`'s catch tuple widened to `(UnicodeDecodeError, ValueError, KajsonDecoderError, KeyError, AttributeError, TypeError)` covering crafted-payload escapes from `kajson.loads(...)`. The `try` scope is one line, so the Q4 rule against broadly catching `AttributeError`/`TypeError` doesn't apply. Upstream wrap-the-bare-three filed as `pipelex-changes.md` #15 (target repo: kajson, not pipelex). Security concern (kajson imports arbitrary modules from caller-controlled body) tracked separately at `wip/security/kajson-untrusted-deserialization.md`. Known follow-up: `RecursionError` from deeply-nested arrays is reachable; not folded in — out-of-scope, one-line catch widening or stream-pre-pass once the contract question lands.
- **Q11:** `/validate` migrated off the legacy `{success, mthds_contents, message}` failure envelope. 422 (`ValidateBundleError`) now propagates to the global `PipelexError` handler; 400 (no `main_pipe`) becomes a 422 via `raise_validation_error(...)`. Success path (200 `ValidateResponse`) untouched. **Breaking change for first-party consumers:** `pipelex-app/src/actions/mthds-validator.ts` and `mthds-js/src/runners/api-runner.ts` + `types.ts` read the old envelope shape and need cross-repo updates. Fold into the Phase 5 changelog.
- **Q12:** Old status-only 422 / 401 tests across `test_security_verifiers.py`, `test_storage.py`, `test_uploader.py`, `test_pipeline_routes.py` tightened to assert `content-type` + `error_type` + (for 401) `WWW-Authenticate`. Status-only assertions deliberately left in `test_request_id_middleware.py`, `test_error_responses.py::test_inbound_request_id_is_echoed_into_error_body`, and `test_exception_handlers.py` — those test middleware/handler behavior, not the wire shape.
- **Q13:** Global exception handlers + helpers + `register_exception_handlers` moved out of `api/main.py` into `api/exception_handlers.py`. Import-side-effect-free (no env reads, no app construction). Production fail-fast on a bad `ERROR_DISCLOSURE` is preserved at `api/main.py`'s single import. Disclosure mode is bound at registration time, not as module global. Decouples test collection from production-app startup.

Upstream items filed during the review (in `pipelex-changes.md` Stage 7, none blocking this PR): #10 (`EnvVarNotFoundError` → `error_domain=CONFIG`), #11 (`parse_concept_spec` shape validation), #12 (`LocalStorageProvider` wrap `OSError`), #13 (`S3StorageProvider` widen `BotoCoreError`), #14 (`ErrorDomain.is_input` @property helpers), #15 (kajson wrap crafted-marker exceptions).

---

## Verification

- `make fui` clean; `make c` clean (ruff format + lint, pyright 0 errors, mypy success); `make tp` green; `make gha-tests` green.
- Manual `make run` checks: original-bug class verified end-to-end — `/pipeline/start` with a `PipelexError` raised inside `pipeline_run_setup` produces RFC 7807 500 through the global handler, `X-Request-ID` echoed, `detail` carries the env var name in verbose mode, structured log with `error_type`, `error_domain`, `request_id`, `user_id`. Validated also for `/pipeline/execute` invalid pipe code (PipelexError → RFC 7807 500), 422 paths, 401 with `WWW-Authenticate: Bearer`, 413 oversized body, storage 403 ownership mismatch.

---

## What is explicitly NOT in this PR

- **Phase 4 — Temporal webhook recovery.** Async failures still go through pipelex's existing webhook path. Item #5 (the upstream dependency, `DeliveryExecutor.execute(error_report=...)`) is **landed** in `pipelex==0.29.1` (PRs #931, #933) — the API-side audit consuming it is deferred to a follow-up PR. Full plan retained below.
- **Phase 5 — Documentation + archive.** Public API error-contract page, `CHANGELOG.md` entry, archive of `wip/error-handling/`. Deferred to a follow-up PR.
- **Webhook signing scope expansion** (`X-Completion-Signature` covering full payload). Independent track — `_for_api/wip/security/webhook-signing.md` is the authoritative plan; was item #9 in `pipelex-changes.md` Stage 6.
- **`GET /api/v1/pipeline/{run_id}` polling endpoint.** Designed in `track-temporal-recovery.md` for shape consistency but out of scope. Depends on `pipelex-changes.md` Stage 5 item #8 (`query_pipeline_state(...)`).
- **OpenTelemetry / distributed tracing.** The `X-Request-ID` plumbing is a strict subset and can grow into trace context later.
- **JSON log sink.** The structured-log strings (`event=api_error key=value ...`) are greppable today; swap to native structured emission once a JSON sink lands. Plan-sanctioned followup from Checkpoint B reconciliation #2.
- **`pipe_code` / `pipeline_run_id` body-derived log fields.** Mechanism is in place (`request.state` + `_*_of` getters in the handler) — wiring deferred. Open piece of Checkpoint B reconciliation #4.
- **Kajson untrusted-deserialization design pass.** Separate track at `wip/security/kajson-untrusted-deserialization.md`. Realistic attack surface bounded today; needs `pipelex-app` and `pipelex-api-deploy` in the conversation.
- **`RecursionError` from deeply-nested JSON.** Follow-up to Q10; one-line catch widening or json-stream-validator pre-pass once the contract question lands.

---

## Design reference

- `wip/error-handling/README.md` — index, settled decisions, reading order.
- `wip/error-handling/architecture.md` — layer model, contract, types/pure functions consumed from pipelex.
- `wip/error-handling/track-exception-handlers.md` — central handler design.
- `wip/error-handling/track-response-schema.md` — RFC 7807 envelope, field mapping, strict mode.
- `wip/error-handling/track-temporal-recovery.md` — async path + webhook payload shape (Phase 4 reference).
- `wip/error-handling/track-observability.md` — structured logging fields, request correlation.
- `wip/error-handling/pipelex-changes.md` — cross-repo tracking (pipelex/kajson companion items).
- `wip/security/kajson-untrusted-deserialization.md` — separate-track design (out of scope for this PR).

---

# Deferred work (for the next session)

The two sections below preserve the original phase plans verbatim so a follow-up session can pick them up cleanly. They are NOT part of this PR.

## Phase 4 — Temporal webhook recovery (deferred)

Async failures must surface to callers with the same RFC 7807 shape as the synchronous path. The recovery primitive is `pipelex.temporal.tprl.temporal_error.recover_error_report`.

### Phase 4a — Verify pipelex item #5 is landed

Consumes item **#5** from `wip/error-handling/pipelex-changes.md` (`DeliveryExecutor.execute(error_report=...)`). The tracking table marks it **✅ Landed** in PRs #931 / #933.

- [ ] Spec-check on the pinned version: `DeliveryExecutor.execute(...)` accepts `error_report: ErrorReport | None = None`; `_notify_webhook` includes `error: <error_report.to_dict()>` in the payload on `FAILED` status when `error_report` is provided; recovery responsibility stays with the caller (worker/observer), not `DeliveryExecutor` itself.
- [ ] Update the pipelex version pin in `pyproject.toml` if needed.

### Phase 4b — API-side audit

- [ ] Confirm the workflow-completion observer location: `pipelex/pipe_run/delivery_executor.py:_notify_webhook` (verified during plan review on 2026-05-20). The API supplies `callback_urls` via `WebhookTarget`; pipelex composes and posts.
- [ ] **Render decision — pinned: option 2 (raw `ErrorReport` dict in webhook).** Reason: the `ErrorReport` dict IS the structured data — RFC 7807 is a presentational layer over it, and the webhook receiver may or may not want the envelope (a non-HTTP consumer like a queue, a log shipper, or a batch processor probably doesn't). The sync HTTP response renders to RFC 7807 because HTTP wants it; the webhook payload stays structurally faithful to the source. Document this asymmetry clearly in `track-temporal-recovery.md`. Future escape hatch: pipelex item #6 (`ErrorReport.to_problem_document(...)`) is available if a future use case demands "RFC 7807 everywhere."
- [ ] On `status = FAILED` with a recovered `ErrorReport`: confirm the webhook payload includes `error: <error_report.to_dict()>`. Field-by-field parity with the sync RFC 7807 extension members.
- [ ] On `status = FAILED`, confirm the webhook payload always includes a structured `error`. `recover_error_report` is total — synthesizes an `UnrecoverableWorkflowFailureError` report when nothing better can be recovered. No `error_report is None` branch to author API-side.
- [ ] Wire structured logging on the webhook delivery (existing log line at `delivery_executor.py:261`):
    - [ ] `event = "webhook_delivery"` on 2xx, `event = "webhook_failure"` on non-2xx.
    - [ ] Include `request_id` of the original `/pipeline/start` call. Consumed from the first-class field added by pipelex item #3 (workflow input carries `request_id: str | None`). Populate at `make_temporal_pipe_run(...)` dispatch time in `api/routes/pipelex/pipeline.py:96-107`, reading the request-id contextvar.
    - [ ] Include `pipeline_run_id`, `callback_url`, response status, and (on failure) the recovered error classification fields.
- [ ] Tests in `tests/unit/test_webhook_recovery.py`:
    - [ ] `WorkflowExecutionError` carrying a packed `ErrorReport` dict → recovery yields the same classification.
    - [ ] `WorkflowExecutionError` with a malformed / absent details dict → `recover_error_report` still yields a structured report (`UnrecoverableWorkflowFailureError`), and the webhook `error` carries it.
    - [ ] **T6 — cross-path consistency (REGRESSION):** same source `ErrorReport` → both the sync handler and the webhook composer produce identical body content for the error fields, modulo `instance`, `request_id`, and timestamps.
    - [ ] Strict-mode disclosure: the webhook always carries the raw `ErrorReport` regardless of `ERROR_DISCLOSURE` mode (the receiver chooses what to render).
- [ ] e2e or integration test if feasible against a test Temporal instance. If a real Temporal instance is not available in this repo's test infra, document the gap and rely on the unit tests.
- [ ] `make fui && make c && make tp` clean.

## Phase 5 — Documentation and archival (deferred)

Code work is done; documentation makes the contract usable for clients and tidies the design directory.

- [ ] Public API error-contract page under `docs/`:
    - [ ] One page describing the error response shape (RFC 7807 fields + pipelex extension members).
    - [ ] A table of the stable `error_category` values and their retry implications.
    - [ ] A table of `user_action.kind` values.
    - [ ] A reference to the `type` URI namespace and a list of currently-published classes.
- [ ] `type` URI doc pages: pipelex already generates per-class error doc pages under `https://docs.pipelex.com/latest/errors/<kebab>/` (item #7). Confirm the pipelex error doc pages cover the classes the API surfaces; decide whether the API publishes its own schema overview page or just links to the pipelex docs.
- [ ] `CHANGELOG.md` entry:
    - [ ] Breaking change: error response shape moved from `{"detail": {...}}` to RFC 7807 `application/problem+json` across every API endpoint — pipelex domain errors, validation, auth (401/403), payload limits (413), catch-all 500.
    - [ ] New fields available to clients (`error_type`, `error_category`, `error_domain`, `retryable`, `user_action`, `provider_metadata`, `request_id`, plus RFC 7807 standard fields).
    - [ ] `ERROR_DISCLOSURE` env var and its default.
    - [ ] `WWW-Authenticate: Bearer` still appears on 401 responses; only the body shape changed.
    - [ ] `/validate` failure shape changed (Q11 resolution) — `pipelex-app` and `mthds-js` need cross-repo updates.
    - [ ] **Known limitation:** the webhook completion payload carries the raw `ErrorReport` dict under `error`, not an RFC 7807 envelope. See `track-temporal-recovery.md` for rationale.
- [ ] Update `CLAUDE.md` if any conventions changed (e.g. the rule about per-route error handling).
- [ ] Archive the design directory:
    - [ ] Rename `wip/error-handling/` to `wip/error-handling-archive/` (or move under a top-level `archive/`).
    - [ ] Update the README inside the archived directory to mark it historical-reference-only.
    - [ ] `wip/error-handling/pipelex-changes.md` stays live as the cross-repo tracker. Decide whether it moves to `docs/pipelex-companion-changes.md` once all items are landed.
- [ ] `make fui && make c && make tp` clean.
