# Implementation Plan — Error Handling

This document tracks the error-handling rework for `pipelex-api`. The original design body has been archived to `wip/error-handling-archive/` now that the work has shipped; the cross-repo tracker for pipelex-side companion items has been moved one level up to `wip/pipelex-changes.md` (kept live). **The design is settled** — refer to the archived docs for rationale; do not relitigate decisions here.

---

## Status

- **This PR delivers Phases 0–3** — the synchronous error path. Every API error response now emits RFC 7807 `application/problem+json` with the same field set across pipelex domain errors, validation errors (4xx), auth (401/403), payload limits (413), and the catch-all 500.
- **Phase A0 (adapt to post-#931/#933 pipelex) is now ALSO on this branch** — see the section near the bottom for the commit shape and what landed. Phase 4 partially folded in (T6 cross-path regression test only; structured-logging item deferred upstream).
- **Phase 5 (docs + CHANGELOG + CLAUDE.md) is now ALSO on this branch** — see the "Phase 5 — Documentation (✅ landed)" section near the bottom. Archive cleanup already shipped in Phase A0.
- **Phase A1 (sync to pipelex `feature/API-readiness-2`) is now ALSO on this branch.** Pipelex merged `dev` into the readiness branch (picking up releases v0.30.0 / v0.30.1 / v0.30.2) and added `request_id` threading through `DeliveryExecutor` on top. Nothing on the API consumption surface changed; the work was a pin bump + revalidation. See "Phase A1 — Sync to `feature/API-readiness-2`" near the bottom.
- **This PR ends at Phase A1.** Next track is webhook signing — its plan is split out to `wip/webhook-signing-cross-repo.md` (workspace-level) and `_for_api/wip/security/webhook-signing.md` (authoritative pipelex-side plan). Cross-repo, lockstep, Louis-gated.
- **Branch:** `feature/Adapt-to-pipelex-update-3`. Pipelex side: `feature/API-readiness-2` (carries upstream `dev`).
- **Phase 3 review** (13 questions surfaced by a multi-agent review at Checkpoint C) **is fully resolved.** Each got a verdict (fix / document-as-intended / file upstream) and the code/tests landed across commits `e683338` → `2a78409`. See the "Phase 3 review resolutions" section below for the one-paragraph summary; the per-question detail lives in those commit messages and in `wip/pipelex-changes.md` Stage 7 (for the upstream items filed).

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

Tracking table lives at `wip/pipelex-changes.md`. Items #10–#15 (Stage 7) are filed upstream against pipelex / kajson; none block the API.

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

A multi-agent code review at Checkpoint C surfaced 13 questions (Q1–Q13). All resolved across commits `e683338` → `2a78409` (chronological order in `git log --oneline`). Per-question detail lives in those commit messages and in the upstream items filed in `wip/pipelex-changes.md` Stage 7.

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

- **Phase 4 — Temporal webhook recovery.** Folded into Phase A0 as the T6 cross-path regression test. Sender-side `event=webhook_delivery` / `event=webhook_failure` log enrichment is upstream-pipelex work — tracked in "Upstream-pipelex follow-ups" below.
- **Webhook signing scope expansion** (`X-Completion-Signature` covering full payload). Independent track — `_for_api/wip/security/webhook-signing.md` is the authoritative plan; was item #9 in `pipelex-changes.md` Stage 6.
- **`GET /api/v1/pipeline/{run_id}` polling endpoint.** Designed in `track-temporal-recovery.md` for shape consistency but out of scope. Depends on `pipelex-changes.md` Stage 5 item #8 (`query_pipeline_state(...)`).
- **OpenTelemetry / distributed tracing.** The `X-Request-ID` plumbing is a strict subset and can grow into trace context later.
- **JSON log sink.** The structured-log strings (`event=api_error key=value ...`) are greppable today; swap to native structured emission once a JSON sink lands. Plan-sanctioned followup from Checkpoint B reconciliation #2.
- **`pipe_code` / `pipeline_run_id` body-derived log fields.** Mechanism is in place (`request.state` + `_*_of` getters in the handler) — wiring deferred. Open piece of Checkpoint B reconciliation #4.
- **Kajson untrusted-deserialization design pass.** Separate track at `wip/security/kajson-untrusted-deserialization.md`. Realistic attack surface bounded today; needs `pipelex-app` and `pipelex-api-deploy` in the conversation.
- **`RecursionError` from deeply-nested JSON.** Follow-up to Q10; one-line catch widening or json-stream-validator pre-pass once the contract question lands. **Discuss:** decide whether to fold `RecursionError` into `_decode_body`'s catch tuple (one-token change, makes the 1000+ deep-nested-array case land as a 422 instead of a sanitized 500) or close it upstream with a stream-pre-pass. Either lands; the question is where the validator lives. Re-surface during Phase 5 cleanup.

---

## Design reference

- `wip/error-handling-archive/README.md` — index, settled decisions, reading order.
- `wip/error-handling-archive/architecture.md` — layer model, contract, types/pure functions consumed from pipelex.
- `wip/error-handling-archive/track-exception-handlers.md` — central handler design.
- `wip/error-handling-archive/track-response-schema.md` — RFC 7807 envelope, field mapping, strict mode.
- `wip/error-handling-archive/track-temporal-recovery.md` — async path + webhook payload shape (Phase 4 reference).
- `wip/error-handling-archive/track-observability.md` — structured logging fields, request correlation.
- `wip/pipelex-changes.md` — cross-repo tracking (pipelex/kajson companion items).
- `wip/security/kajson-untrusted-deserialization.md` — separate-track design (out of scope for this PR).

---

---

## Phase A0 — Adapt to post-#931/#933 pipelex (✅ landed on `feature/Adapt-to-pipelex-update-2`)

Reacts to the hardening tail of pipelex `feature/post-pr933-followups` (the body of work that originally shipped as PRs #931 / #933 plus the follow-ups landing on top of it). Surface-area changes: error-class import paths, STRICT disclosure keying provenance, native end-to-end `request_id`, acronym-casing in error titles.

**Pipelex source pin — temporary git-rev (was: editable path).** `pyproject.toml` `[tool.uv.sources]` originally declared `pipelex = { path = "../_for_api", editable = true }` so `make install` resolved to whatever was checked out on `_for_api/`. That breaks CI (the GHA runner only checks out `pipelex-api/`, not the sibling `pipelex` repo), so commit `5be3c06` flipped it to `pipelex = { git = "https://github.com/Pipelex/pipelex.git", rev = "<sha>" }`, pinned to the HEAD of `feature/post-pr933-followups` at that moment. Phase A1 bumped the pin again to track `feature/API-readiness-2` (current rev `0be0e332`). **This is still a temporary stopgap** — the intended end-state is to bump the PyPI floor (`pipelex>=<next-release>` in `dependencies`) once the pipelex side and the API side are both ready to ship together. Bump the `rev` to pick up newer pipelex commits in the meantime. Owner: Louis decides when to flip back to a PyPI pin (cross-repo release coordination).

### What landed

- **Phase B — Phase 6 import-path moves.** `EnvVarNotFoundError` import in `tests/unit/test_exception_handlers.py` + `tests/unit/test_problem_document.py` updated from `pipelex.system.environment` → `pipelex.system.exceptions`. No production code touched the moved import. No `MthdsDecodeError` references in the codebase (pipelex-api never used it).

- **Phase C — `WebhookTarget` reserved-key collisions: no-op.** The single `WebhookTarget(...)` call site in `api/routes/pipelex/pipeline.py` already passed only `url` + `headers` — no reserved keys (`pipeline_run_id` / `status` / `result_url` / `error`) anywhere in static payloads.

- **Phase D — STRICT-disclosure provenance audit: no-op.** `api/problem_document.py:build_problem_document` is a thin wrapper that delegates wholesale to `report.to_problem_document(disclosure_mode=...)`. Pipelex's provenance-gated keying (Decision D1 upstream — `_authors_caller_facing_message` ClassVar replaces `error_domain == INPUT` reasoning for STRICT message passthrough) flows through untouched. The two `error_domain == ErrorDomain.INPUT` sites in `api/exception_handlers.py` (`:121`, `:170`) are **log-level switches** (warning for caller mistakes vs error+traceback for server failures), not wire-disclosure switches. They remain correct. The existing `test_strict_disclosure_redacts_config_preserves_input` empirically confirms the provenance gating works end-to-end through the delegation.

- **Phase E — native `request_id` at dispatch.** `POST /pipeline/start` now reads the request-scoped `request_id` contextvar (`api.logging_context.get_request_id()`) and passes it as `request_id=` to `ApiRunner.start_pipeline`, which forwards it to `pipeline_run_setup(...)`. Pipelex then populates `JobMetadata.request_id`, and every worker-side `WorkflowLog` record emitted by `WfPipeRun` / `WfPipeRouter.run` carries `request_id` in `extra`. The legacy `webhook.payload["request_id"]` piggyback was never used in pipelex-api (and `WebhookTarget.payload`'s new validator would now reject it as a reserved key anyway). New regression test: `test_pipeline_routes.py::test_start_propagates_request_id_to_runner` wires `RequestIdMiddleware` into the test client and asserts the kwarg lands.

- **Phase F — Temporal webhook recovery (partial fold-in).** Upstream verification confirmed: `DeliveryExecutor.execute(error_report=...)` + `_notify_webhook` payload-injection of `error: <error_report.to_dict(VERBOSE)>` on FAILED + `recover_error_report` totality (synthesizes `UnrecoverableWorkflowFailureError` when nothing else recovers). All live in pipelex; the API supplies `WebhookTarget`s and otherwise does not touch the webhook payload. The API-side contribution is `tests/unit/test_webhook_recovery.py` — the **T6 cross-path consistency regression** the deferred plan called for: given the same `ErrorReport`, the classification fields surface identically via the sync RFC 7807 path and the webhook `error` field. Pins the asymmetry of the envelope members (`type` / `status` / `detail` / `instance` / `request_id` are sync-only) and the VERBOSE-always-for-webhook rule. **Structured-logging item NOT folded in — it modifies pipelex (`delivery_executor.py:270`), not the API. Tracked as a separate pipelex PR.**

- **Bonus — acronym-casing in error titles.** Upstream commit `07af2e2c` ("preserve acronym casing in auto-derived error titles") flipped `pascal_case_to_sentence("InvalidJSON")` from `"Invalid json"` to `"Invalid JSON"`. The matching assertion in `tests/unit/test_error_uri.py::test_error_type_title` updated; the comment now describes the (corrected) behavior.

### Verification at commit time

- `make fui && make c && make tp` clean. Test count: 188 → 193 (5 new tests across the request_id propagation regression and the four T6 cross-path tests).
- Pyright + mypy: 0 errors. Ruff: clean.

### Out of scope (recorded, not in this branch)

- **Structured `event=webhook_delivery` / `event=webhook_failure` logging** at `pipelex/pipe_run/delivery_executor.py:270`. Belongs upstream in pipelex; cross-repo work should land as a pipelex PR.
- **Webhook signing.** Separate cross-repo track, governed by `_for_api/wip/security/webhook-signing.md`. Three-step rollout (receiver-side dual-format first → worker-side body-signing → drop legacy), waiting on Louis.
- **Upstream `pipelex-changes.md` Stage 7 items #10–#15** (`EnvVarNotFoundError` → `CONFIG` domain, `parse_concept_spec` shape validation, `LocalStorageProvider` `OSError` wrap, `S3StorageProvider` `BotoCoreError` widening, `ErrorDomain.is_input` helper, kajson crafted-marker exceptions). None landed on `feature/post-pr933-followups`; still open upstream.
---

## Phase 5 — Documentation (✅ landed)

Docs match reality. The breaking-change RFC 7807 shape is now disclosed in CHANGELOG, documented as a public contract page, and reflected in `CLAUDE.md` so future route work follows the new pattern.

### What landed

- **[`docs/error-responses.md`](docs/error-responses.md)** — new public docs page covering the RFC 7807 envelope, standard members + the pipelex extension set, the `error_domain → HTTP status` mapping (`input → 422`, `config`/`runtime` → 500, plus 401/403/413/429 carve-outs), the `type` URI namespace (`https://docs.pipelex.com/latest/errors/<kebab>/`), disclosure modes (verbose vs strict, provenance-gated on `_authors_caller_facing_message`), request correlation (`X-Request-ID` → `JobMetadata.request_id` → worker logs), and worked examples (422 input, 500 config, 401 auth). Cross-links to `docs/pipe-run.md` for the async webhook payload note.
- **`docs/pipe-validate.md`** — "Error Responses" section rewritten: the legacy `HTTP 200 + success: false` envelope is gone; failures are now `HTTP 422` RFC 7807. The success-path body (200 `ValidateResponse` with `success: true`, `mthds_contents`, `pipelex_bundle_blueprint`, `graph_spec`, `pipe_structures`) is unchanged.
- **`docs/pipe-run.md`** — the two `{"detail": {error_type, message}}` references on `/execute` and `/start` now point at `error-responses.md`.
- **`mkdocs.yml`** — `Error Responses: error-responses.md` added to the API nav between `Pipe Validate` and `Contribute`.
- **`CHANGELOG.md`** — new `### Breaking Changes` block at the top of `[Unreleased]` discloses the RFC 7807 migration, the `/validate` envelope removal, and the `X-Request-ID` echo. A `### Added` block records the `ERROR_DISCLOSURE` env var and the new docs page.
- **`CLAUDE.md`** — "Error Responses" section (lines 154-171) rewritten: the legacy `HTTPException` + `{"detail": {error_type, message}}` pattern is gone. The section now teaches: routes let `PipelexError` propagate to the global handler; API-authored 4xx/5xx use the `raise_*` helpers in `api/errors.py` (which raise `ApiError`, never `HTTPException`); auth helpers set `WWW-Authenticate: Bearer` automatically; the global handlers emit structured `event=api_error` / `event=pipelex_error` / `event=unexpected_error` logs.

### Cross-repo `/validate` consumer updates (companion PRs)

The `/validate` failure envelope change (Q11) breaks consumers that read the old `{success: false, mthds_contents, message}` shape on a 200 response. Companion PRs:

- **`pipelex-app`** — `src/actions/mthds-validator.ts` had dead legacy-envelope code at the 200-success branch; cleanup PR removes it. The non-200 path was already RFC 7807-aware (top-level `detail` extraction).
- **`mthds-js`** — `src/runners/api-runner.ts` swallowed the RFC 7807 body into an opaque error string on non-2xx; companion PR detects `application/problem+json`, parses `title` / `detail`, and throws an `Error` whose message reads `<title>: <detail>`. Downstream CLI / agent callers (which already display `error.message`) benefit automatically without code changes.

### Verification

- `make fui && make c && make tp && make gha-tests` clean.
- `mkdocs build --strict` clean (the new page + nav entry must not break the site build).
- Local: `make run`, hit `/api/v1/validate` with a malformed bundle → confirm the 422 body matches the example in `docs/error-responses.md`.

---

## Phase A1 — Sync to pipelex `feature/API-readiness-2` (✅ landed on `feature/Adapt-to-pipelex-update-3`)

Pipelex consolidated work onto `feature/API-readiness-2`: merged `origin/dev` (carrying releases v0.30.0 / v0.30.1 / v0.30.2 — agent CLI silencing, doctor command hardening, validation/log discipline) and added four commits on top — none of them touching the pipelex surface this API consumes.

**What landed on the API side.** A pin bump and a clean revalidation. `pyproject.toml` `[tool.uv.sources]` flipped `rev` to `0be0e3327e242c9639f71a9b2a904ff3ed95b9cd` (HEAD of `feature/API-readiness-2`). No production code changed. `make c` and `make tp` are green: 205 tests pass (was 193 at the end of Phase A0; the +12 are the new tests that already landed during Phase A0, not new work here).

**What landed upstream that's relevant to us.**

- `9092b81d` — `fix(validate)`: scoped down `current-library` restore to the bundle-load failure path. No-op for the API (our `/build/*` routes' `try/finally` teardown is unchanged on our side).
- `ceb018b5` + `07f9cce9` — `DeliveryExecutor` now threads `request_id` through both the workflow path and `PipeRun`'s direct-mode dispatcher. This is the **plumbing** that the deferred "structured `event=webhook_delivery` / `event=webhook_failure`" item needed. The structured event-name emission itself is still WIP upstream (see `_for_api/wip/console-targets-and-agent-cli-stdout.md` for the kick-off doc). Our existing T6 cross-path test (`tests/unit/test_webhook_recovery.py`) is unaffected — it pins error rendering consistency, not `request_id` presence in the webhook payload.
- `0be0e332` — docs only; kicks off the upstream structured-logging refactor.

**Dev merge content (recorded for cold-start readers).** The merge brought 0.30.0 / 0.30.1 / 0.30.2 release work: doctor command preserves partial health report on bootstrap config errors; agent CLI silences logging via `app_callback` for full subcommand coverage; stdout discipline + `tomlkit` migration. Tests live entirely under `pipelex/cli/agent_cli/` — does not touch any module we import.

### Verification at commit time

- `make install && make c && make tp` clean.
- 205 tests pass. Pyright + mypy: 0 errors. Ruff: clean.

### Out of scope (recorded, not in this branch)

- Same set as Phase A0. The `DeliveryExecutor` `request_id` plumbing landing upstream does **not** discharge the structured-log follow-up — it just unblocks it. The event-name emission still belongs in a pipelex PR.

---

# Deferred / next-track work

## Upstream-pipelex follow-ups

Items that belong in `pipelex/` rather than `pipelex-api/`, surfaced during this PR's audits. Track here so they don't get lost; land via separate pipelex PRs.

- **Structured `event=webhook_delivery` / `event=webhook_failure` logging** at `pipelex/pipe_run/delivery_executor.py:270`. The receiver-side consistency T6 test landed in Phase A0. The plumbing landed upstream in Phase A1 (`ceb018b5` / `07f9cce9` thread `request_id` through `DeliveryExecutor`). The remaining work is the actual structured event-name emission — kick-off doc lives at `_for_api/wip/console-targets-and-agent-cli-stdout.md`. Surface to the next pipelex session.
- **Stage 7 items #10–#15** in `wip/pipelex-changes.md` (`EnvVarNotFoundError` → `CONFIG` domain, `parse_concept_spec` shape validation, `LocalStorageProvider` `OSError` wrap, `S3StorageProvider` `BotoCoreError` widening, `ErrorDomain.is_input` helper, kajson crafted-marker exceptions). None landed on `feature/post-pr933-followups`; still open upstream.

## Next track — webhook signing

Cross-repo, lockstep, Louis-gated. Three-step rollout (receiver-side dual-format first → worker-side body-signing → drop legacy). Plans:

- **Workspace coordinator** — `wip/webhook-signing-cross-repo.md` (workspace root, cold-start safe).
- **Authoritative pipelex-side plan** — `_for_api/wip/security/webhook-signing.md`.

Not part of this PR.

