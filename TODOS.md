# Implementation Plan — Error Handling

This is the implementation plan for the error-handling design captured in `wip/error-handling/`. Work proceeds in phases. Each phase is a coherent unit of work. Four checkpoints mark mandatory stop points where the agent gathers state into this document before any next-session work begins.

**The design is settled.** Do not relitigate. If something needs to change, update `wip/error-handling/*.md` first, then come back here.

---

## Status (update at every checkpoint)

- **Current phase:** not started
- **Last checkpoint reached:** none
- **Next checkpoint:** Checkpoint A — end of Phase 1
- **Branch:** `feature/Adapt-to-pipelex-update` (current branch; may move to a dedicated branch when starting Phase 0)

## How to use this document

- Tick checkboxes as items land. Do not pre-tick.
- At every CHECKPOINT block, **stop**. Run the verification list. Update the Status block above. Add a short note under that checkpoint's "What landed" section. Only then is the work considered complete for that phase.
- A new session that picks this up should be able to read **only** this file plus `wip/error-handling/README.md` and know exactly where things stand and what to do next.
- File-path references in this plan use `pipelex-api/` paths (e.g. `api/errors.py`). Pipelex library references use `pipelex/...` (e.g. `pipelex.base_exceptions`).
- Always run `make fui && make c` after code changes. Always run `make tp` before committing. Per the project CLAUDE.md.

## Design reference (read once, refer back as needed)

- `wip/error-handling/README.md` — index, settled decisions, reading order.
- `wip/error-handling/architecture.md` — layer model, contract, types and pure functions consumed from pipelex.
- `wip/error-handling/track-exception-handlers.md` — the central handler design.
- `wip/error-handling/track-response-schema.md` — RFC 7807 envelope, field mapping, strict mode.
- `wip/error-handling/track-temporal-recovery.md` — async path and webhook payload shape.
- `wip/error-handling/track-observability.md` — structured logging fields, request correlation.

## Settled decisions (do not relitigate)

- Response envelope: RFC 7807 `application/problem+json` with pipelex `ErrorReport` fields as extension members.
- Info disclosure default: verbose; opt-in to sanitization via `ERROR_DISCLOSURE=strict` env var.
- `INPUT`-domain errors are always verbose; logs are always verbose; `provider_metadata.body` is excluded by pipelex.
- `type` URI namespace: `https://pipelex.dev/errors/<kebab-case-error-type>`.

## Glossary of file locations (for cold starts)

| File | Role |
|---|---|
| `api/main.py` | FastAPI app init, middleware, router registration. New exception handlers will register here. |
| `api/middleware.py` | Existing `request_body_size_middleware`. New request-id middleware lands here. |
| `api/errors.py` | Today: catch tuples + `raise_internal_error` + API-owned 4xx helpers. After: 4xx helpers only. |
| `api/error_types.py` | Static `ErrorType` enum. Unchanged. |
| `api/routes/pipelex/pipeline.py` | `/pipeline/execute`, `/pipeline/start`. Has the per-route try/except blocks. |
| `api/routes/pipelex/{validate, build/*, agent/*}.py` | Other routes with per-route try/except blocks. |
| `api/routes/{uploader,storage}.py` | Storage routes using `STORAGE_HANDLED_EXCEPTIONS`. |
| `tests/unit/`, `tests/e2e/` | Test homes. pytest-mock, `@pytest.mark.asyncio(loop_scope="class")`. |
| `pipelex/base_exceptions.py` (read-only dep) | `PipelexError`, `ErrorReport`, `ErrorDomain`, `error_domain_to_http_status`, `recover_error_report`. |
| `pipelex/cogt/exceptions.py` (read-only dep) | `CogtError`, `InferenceErrorCategory`. |
| `pipelex/cogt/inference/error_classification.py` (read-only dep) | `ProviderErrorMetadata`, `UserAction`, `UserActionKind`. |

---

## Phase 0 — Foundations

Small, focused, sets up everything else. Order within the phase is free; the only invariant is that nothing in Phase 1+ assumes a Phase 0 piece is missing.

- [ ] Add `ERROR_DISCLOSURE` env var plumbing. Two valid values: `verbose` (default) and `strict`. Read once at app startup (or via a lightweight settings module if one exists; otherwise inline in the handler). Reject unknown values with a startup-time error.
- [ ] Add a `pipelex.dev/errors/` namespace constant in a small module (proposed: `api/error_uri.py`). Single function `error_type_uri(error_type: str) -> str` that returns the kebab-cased URI. Also returns a small `error_type_title(error_type: str) -> str` that produces a stable human title from the class name (e.g. `EnvVarNotFoundError` → `"Environment variable not set"`). For unknown classes, fall back to a humanized split of the camel case.
- [ ] Add a request-id middleware in `api/middleware.py` (sibling of `request_body_size_middleware`):
    - [ ] Reads `X-Request-ID` from the inbound request if present; otherwise generates a ULID.
    - [ ] Validates inbound id (length cap, character set) and replaces with a fresh id on invalid input — never trust inbound ids blindly.
    - [ ] Stores on `request.state.request_id`.
    - [ ] Echoes `X-Request-ID` on every response (success and error).
- [ ] Add a logging contextvar (proposed: `api/logging_context.py`) that exposes `request_id` to loggers for structured-log injection. A small adapter that any log site can call (`get_request_id()` returns `str | None`).
- [ ] Register the request-id middleware in `api/main.py`. Confirm ordering relative to CORS and body-size middleware — request-id should run first so all downstream code (and exception handlers) see the id.
- [ ] Unit tests:
    - [ ] `tests/unit/test_error_uri.py` — exhaustively cover known error types and the unknown-class fallback.
    - [ ] `tests/unit/test_request_id_middleware.py` — generates id when absent; echoes when present; rejects malformed; sets `request.state.request_id`; sets `X-Request-ID` response header.
- [ ] `make fui && make c && make tp` clean.

---

## Phase 1 — RFC 7807 problem-document builder

A pure module that turns an `ErrorReport` (and optional context) into a problem+json dict. No FastAPI imports. No I/O. All logic for field mapping and strict-mode redaction lives here so it can be unit-tested in isolation.

Proposed location: `api/problem_document.py`.

- [ ] Define the public API:
    - [ ] `build_problem_document(report: ErrorReport, *, instance: str | None, request_id: str | None, disclosure_mode: Literal["verbose", "strict"]) -> dict[str, Any]`
    - [ ] `build_problem_document_from_api_error(error_type: ErrorType, message: str, status: int, instance: str | None, request_id: str | None) -> dict[str, Any]` — used by `raise_validation_error` etc. Always treated as `INPUT` domain.
- [ ] Implement the RFC 7807 standard fields per the field-mapping table in `wip/error-handling/track-response-schema.md`:
    - [ ] `type` from `error_type_uri(report.error_type)`.
    - [ ] `title` from `error_type_title(report.error_type)`.
    - [ ] `status` from `report.http_status`.
    - [ ] `detail` from `report.message` (subject to strict-mode redaction).
    - [ ] `instance` from the caller-supplied route path.
- [ ] Implement extension members (drop `None`-valued fields per `ErrorReport.to_dict()` semantics):
    - [ ] `error_type`, `error_category`, `error_domain`, `retryable`, `user_action`, `model`, `provider`, `provider_metadata`.
    - [ ] `request_id` from the caller-supplied id (distinct from `provider_metadata.request_id`).
- [ ] Implement strict-mode redaction per `wip/error-handling/track-response-schema.md` — *The strict mode, in detail* section:
    - [ ] `INPUT` domain: unchanged (always verbose).
    - [ ] `CONFIG` and `RUNTIME` domain: `detail` replaced with the fixed string; `user_action`, `provider`, `model`, `provider_metadata` dropped; `error_category`, `retryable`, `error_type`, `error_domain`, `request_id`, RFC 7807 standard fields retained.
- [ ] Unit tests in `tests/unit/test_problem_document.py`. One `TestClass` per project rules; one test method per scenario:
    - [ ] Builds a problem document from a synthetic `EnvVarNotFoundError` (`CONFIG` domain).
    - [ ] Builds a problem document from a synthetic `LLMCompletionError` with `provider_metadata` carrying `retry_after_seconds` (verify all extension members present).
    - [ ] Strict mode redacts `CONFIG`-domain `detail` and drops the right fields.
    - [ ] Strict mode preserves `INPUT`-domain detail.
    - [ ] API-error variant produces a valid problem document with `error_domain = "input"`.
    - [ ] `None`-valued fields are dropped, not emitted as `null`.
    - [ ] Use `parametrize` for the redaction matrix (domain × mode → which fields survive).
- [ ] `make fui && make c && make tp` clean.

---

## CHECKPOINT A — End of Phase 1

**Stop. Do this in order.**

1. **Verify.** Run `make fui && make c && make tp`. All green. Read the diff (`git diff main...HEAD`) — does it touch only Phase 0 + Phase 1 files? No collateral changes?
2. **Manual sanity check.** Write a 10-line throwaway script that imports `build_problem_document`, feeds it a synthetic `EnvVarNotFoundError`, prints the dict. Confirm the RFC 7807 standard fields are correct and the `detail` carries the env var name in verbose mode.
3. **Update the Status block at the top of this doc.** Set current phase to "Phase 1 complete, ready for Phase 2." Set last checkpoint to "A."
4. **Append a "What landed" note under this checkpoint heading below.** Include: files created (paths only), public API surface added, anything unexpected encountered, anything deferred to a later phase.
5. **Commit.** Conventional message: `error-handling: phase 0+1 — request id, problem document builder`.

### What landed (fill in at Checkpoint A)

_To be filled in by the agent reaching this checkpoint._

### Cold-start prompt template for Phase 2

> Pick up the error-handling implementation. Read `TODOS.md` (the status block and the "What landed" note under Checkpoint A) and `wip/error-handling/track-exception-handlers.md`. The pure problem-document builder is in `api/problem_document.py`. Phase 2 is to register global FastAPI exception handlers in `api/main.py` that consume the builder and emit structured logs. Follow the Phase 2 checklist in `TODOS.md`.

---

## Phase 2 — Global exception handlers

Wire three app-level handlers in `api/main.py`. Each handler consumes the Phase 1 builder and emits a structured log entry. Routes are **not** modified in this phase — that's Phase 3. This separation keeps the diff reviewable.

- [ ] In `api/main.py`, register handlers via `app.add_exception_handler(...)` or the decorator form. Pick one form and use it consistently.
- [ ] Handler for `pipelex.base_exceptions.PipelexError`:
    - [ ] Calls `exc.to_error_report()`.
    - [ ] Calls `build_problem_document(...)` with `instance = request.url.path`, `request_id = request.state.request_id`, `disclosure_mode` from env.
    - [ ] Builds a `JSONResponse` with `media_type="application/problem+json"`, `status_code = report.http_status`.
    - [ ] Emits `Retry-After: <int(ceil(retry_after_seconds))>` when `report.provider_metadata` exists and has `retry_after_seconds`.
    - [ ] Emits the `X-Request-ID` header (the middleware already does it, but the handler must not strip it).
    - [ ] Emits a structured log entry. Level is `error` by default and `warning` when `report.error_domain == ErrorDomain.INPUT`. See observability fields list in `wip/error-handling/track-observability.md`.
- [ ] Handler for `temporalio.exceptions.TemporalError`:
    - [ ] Authors a minimal `ErrorReport`-shaped dict directly (do not invoke `to_error_report`, since `TemporalError` is not a `PipelexError`). Or: build the problem document by hand with `error_type = "TemporalTransportError"`, `error_domain = "runtime"`, `error_category = "transient"`, `retryable = true`, `status = 500`.
    - [ ] Same response shape, headers, and structured log as the `PipelexError` handler.
    - [ ] **Open question for this phase:** does pipelex's `WorkflowExecutionError` (a `PipelexError`) take precedence here? It does — the `PipelexError` handler catches it first because it's more specific. Document the answer in the inline comment.
- [ ] Handler for `Exception` (fallback catch-all):
    - [ ] Logs at `error` level with the full traceback. Include `error_type = type(exc).__name__` in the log structured fields, but **not** in the response body.
    - [ ] Returns a fixed sanitized problem document: `type = "https://pipelex.dev/errors/internal-server-error"`, `title = "Internal server error"`, `status = 500`, `detail = "An unexpected error occurred. The request id is included for support."`, `error_type = "InternalServerError"`, `error_domain = "runtime"`, `retryable = false`, `request_id`. This handler is one of the two legitimate `except Exception` locations per project rules; add a one-line comment justifying it.
- [ ] Wire the log emission. Determine whether the existing `pipelex` `log` object renders structured fields in JSON when configured for a JSON sink, and confirm fields are extractable. If the existing logger does not support structured fields natively, accept a degraded "key=value in the message" rendering for now and file a followup. Do not block this phase on perfect structured logging.
- [ ] Integration tests in `tests/unit/test_exception_handlers.py` against a small test FastAPI app (or against the real app via `TestClient`):
    - [ ] `PipelexError` subclass produces a problem+json response with correct fields.
    - [ ] `EnvVarNotFoundError` → 500, `error_domain = "config"`, `detail` carries the env var name (verbose mode).
    - [ ] `ERROR_DISCLOSURE=strict` redacts `detail` for `CONFIG` errors, preserves `INPUT`.
    - [ ] Simulated `LLMCompletionError` with `provider_metadata.retry_after_seconds = 12.0` → 429, `Retry-After: 12` header.
    - [ ] `Exception` subclass triggers the fallback handler → 500 with `error_type = "InternalServerError"`, response body does NOT include the original exception class name.
    - [ ] `TemporalError` triggers the dedicated handler with the expected synthetic classification.
    - [ ] `X-Request-ID` is present on every response.
    - [ ] Inbound `X-Request-ID` is echoed (proves the middleware integration).
- [ ] `make fui && make c && make tp` clean.

---

## CHECKPOINT B — End of Phase 2

**Stop. Do this in order.**

1. **Verify.** `make fui && make c && make tp` green. Read the diff. Phase 2 should touch `api/main.py`, `tests/unit/test_exception_handlers.py`, and nothing else. The per-route try/except blocks should still be in place — Phase 2 deliberately overlaps with them.
2. **Manual sanity check.** Run `make run` and hit `POST /api/v1/pipeline/start` from Postman (no env vars set, reproducing the original failure). Verify:
    - Response is `application/problem+json`.
    - HTTP status is 500.
    - Body `detail` field contains the actual env var name (verbose mode).
    - Response has `X-Request-ID` header.
    - Server log line contains the env var name and structured fields (`error_type`, `error_domain`, `request_id`).
3. **The fact that both old per-route handler and new global handler exist simultaneously is a temporary state.** Verify: which one is taking precedence? Likely the per-route one (it catches before the exception escapes). The new global handlers only fire for failures the per-route catch lets through. **This is expected** — Phase 3 deletes the per-route catches. Do not "fix" by hacking the order; just verify and proceed.
4. **Update Status block.** Set last checkpoint to "B." Note current phase.
5. **Append a "What landed" note** under this checkpoint heading.
6. **Commit.** Conventional message: `error-handling: phase 2 — global exception handlers`.

### What landed (fill in at Checkpoint B)

_To be filled in by the agent reaching this checkpoint._

### Cold-start prompt template for Phase 3

> Pick up the error-handling implementation. Read `TODOS.md` (the status block, "What landed" under Checkpoint B) and `wip/error-handling/track-exception-handlers.md` — specifically the "Route cleanup" section. Phase 3 is to strip the per-route try/except blocks across `api/routes/`, delete the `ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` tuples and `raise_internal_error` from `api/errors.py`, and migrate the API-owned 4xx helpers to emit RFC 7807. Follow the Phase 3 checklist in `TODOS.md`.

---

## Phase 3 — Route cleanup and 4xx helper migration

The subtraction phase. Risky because it touches every route file; the global handler must be in place and working (Checkpoint B verified) before this phase runs.

- [ ] Strip per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` blocks in:
    - [ ] `api/routes/pipelex/pipeline.py` (both `/execute` and `/start`)
    - [ ] `api/routes/pipelex/validate.py`
    - [ ] `api/routes/pipelex/build/inputs.py`
    - [ ] `api/routes/pipelex/build/output.py`
    - [ ] `api/routes/pipelex/build/runner.py`
    - [ ] `api/routes/pipelex/agent/concept.py`
    - [ ] `api/routes/pipelex/agent/pipe_spec.py`
    - [ ] `api/routes/pipelex/agent/models.py`
- [ ] Strip per-route `try / except STORAGE_HANDLED_EXCEPTIONS` blocks in:
    - [ ] `api/routes/uploader.py`
    - [ ] `api/routes/storage.py`
    - [ ] Decision per design: trust pipelex's wrapping; if `OSError`/`BotoCoreError`/`ClientError` ever leak, the `Exception` fallback catches them and the leak is a pipelex bug. Do **not** keep a narrow catch defensively.
- [ ] Audit `ValueError` / `TypeError` / `RuntimeError` catch sites for each route. For each occurrence:
    - [ ] If pipelex should wrap it but doesn't → note it in `wip/error-handling/upstream-followups.md` (create this file if it doesn't exist). File an upstream issue if appropriate.
    - [ ] If the API boundary itself raises it (e.g. Pydantic coercion at the route) → catch the **specific** exception (not the union) and call `raise_validation_error(...)`.
    - [ ] Otherwise → let it fall through to the `Exception` fallback. Document the call site in `upstream-followups.md`.
- [ ] In `api/errors.py`:
    - [ ] Delete `ENDPOINT_HANDLED_EXCEPTIONS` and `STORAGE_HANDLED_EXCEPTIONS`.
    - [ ] Delete `raise_internal_error`.
    - [ ] Update `raise_validation_error`, `raise_bad_request`, `raise_payload_too_large` to emit RFC 7807 problem documents via `build_problem_document_from_api_error`. Three signature changes:
        - [ ] Each helper now needs the `Request` (to get `request.state.request_id` and `request.url.path`). Pass it explicitly or read from a context — pick whichever is cleaner.
        - [ ] Status code stays the same (422 / 400 / 413).
        - [ ] Body shape changes from `{"detail": {"error_type", "message"}}` to RFC 7807.
- [ ] Update every call site of `raise_validation_error` / `raise_bad_request` / `raise_payload_too_large` to pass the `Request` (or the equivalent context).
- [ ] Update existing tests that assert the old `{"detail": {...}}` shape to assert the new RFC 7807 shape. Likely candidates: every e2e test that exercises 4xx paths.
- [ ] Update the module docstring at the top of `api/errors.py` to describe the new world (no more catch tuples, helpers emit problem documents).
- [ ] e2e coverage in `tests/e2e/`:
    - [ ] `/pipeline/start` with no `COMPLETION_CALLBACK_SECRET` → 500 problem+json, `error_domain = "config"`, `detail` names the env var. (The original bug, verified end-to-end.)
    - [ ] `/pipeline/execute` with an invalid pipe code → expected error from pipelex propagates correctly.
    - [ ] `raise_validation_error` path returns RFC 7807 with `error_domain = "input"`.
    - [ ] At least one storage route happy path still works (no regression from removing `STORAGE_HANDLED_EXCEPTIONS`).
- [ ] `make fui && make c && make tp` clean.
- [ ] Run `make gha-tests` to confirm the no-inference CI tier still passes.

---

## CHECKPOINT C — End of Phase 3

**Stop. Do this in order.**

1. **Verify.** `make fui && make c && make tp` green. `make gha-tests` green.
2. **Confirm the diff is purely subtraction + helper migration.** Read `git diff main...HEAD` for Phase 3 changes. There should be many `-` lines (per-route try/except removal), a handful of `+` lines in `api/errors.py` (helper signatures), and updated call sites. No new logic.
3. **Manual end-to-end check.** Run `make run`. From Postman:
    - `POST /api/v1/pipeline/start` without `COMPLETION_CALLBACK_SECRET` → response is RFC 7807 problem+json, 500, `detail` names the env var. **This is the original bug — fixed.**
    - A deliberately bad request body to a 422 path → response is RFC 7807 problem+json, 422.
    - The new path through the global handler is the only one in play; the old `raise_internal_error` no longer exists.
4. **Check `wip/error-handling/upstream-followups.md`.** If any catch sites were noted as "pipelex should wrap this," confirm the upstream issues / notes are filed. Move forward only when there's no half-finished investigation lurking.
5. **Update Status block.** Last checkpoint = "C." Current phase = "Phase 3 complete, sync path complete, ready for Phase 4."
6. **Append a "What landed" note** under this checkpoint heading. Include: which routes were cleaned up, anything surprising in the catch-site audit, list of upstream pipelex follow-ups raised.
7. **Commit.** Conventional message: `error-handling: phase 3 — route cleanup, 4xx helpers emit problem documents`.

### What landed (fill in at Checkpoint C)

_To be filled in by the agent reaching this checkpoint._

### Cold-start prompt template for Phase 4

> Pick up the error-handling implementation. Read `TODOS.md` (the status block, "What landed" under Checkpoint C) and `wip/error-handling/track-temporal-recovery.md`. The synchronous error path is complete; Phase 4 wires `recover_error_report` into the async / webhook-completion path so workflow failures reach callers with the same RFC 7807 shape. Start by mapping where the workflow-completion observer lives (pipelex's `WorkflowExecutor` vs an API-side background worker). Follow the Phase 4 checklist in `TODOS.md`.

---

## Phase 4 — Temporal webhook recovery

Async failures must surface to callers with the same RFC 7807 shape as the synchronous path. The recovery primitive is `pipelex.base_exceptions.recover_error_report`.

- [ ] **First task: locate the workflow-completion observer.** Open question from the design: is it pipelex's `WorkflowExecutor` (upstream code) or an API-side background worker (this repo)? Read `api/routes/pipelex/pipeline.py` (the `_completion_signature` flow), the `make_temporal_pipe_run` call, and pipelex's `WorkflowExecutor` to find out. Document the answer in this checkbox before proceeding.
- [ ] If the observer is in pipelex:
    - [ ] Confirm pipelex already calls `recover_error_report` and posts the webhook. If yes, this phase is mostly a payload-shape audit — verify the webhook body matches the synchronous RFC 7807 problem document.
    - [ ] If pipelex needs to be updated, file the upstream change and pause this phase until it lands.
- [ ] If the observer is in this repo:
    - [ ] Find / build the workflow-completion observer (likely a background task or async polling).
    - [ ] On failed-state completion: call `recover_error_report(exc)` → `ErrorReport | None`.
    - [ ] Build the webhook payload `{pipeline_run_id, status: "failed", error: <problem document>}` using `build_problem_document(...)`.
    - [ ] On `None` recovery: emit the explicit unrecoverable problem document per `wip/error-handling/track-temporal-recovery.md` — never silent.
    - [ ] POST to each `callback_url` with `X-Completion-Signature` and a stable `Content-Type: application/json` (or `application/problem+json` for the failure case — pick one and document).
- [ ] Wire structured logging on the webhook delivery itself:
    - [ ] `event = "webhook_delivery"` on success, `"webhook_failure"` on non-2xx.
    - [ ] `request_id` of the original `/pipeline/start` call (this needs to be persisted into the workflow input or fetched by `pipeline_run_id`).
    - [ ] `pipeline_run_id`, `callback_url`, response status, and (on failure) the recovered error classification fields.
- [ ] Tests in `tests/unit/test_webhook_recovery.py`:
    - [ ] Simulated `WorkflowExecutionError` carrying a packed `ErrorReport` dict → recovery yields the same classification.
    - [ ] Simulated `WorkflowExecutionError` with a malformed details dict → `recover_error_report` returns `None`, the unrecoverable payload is emitted.
    - [ ] Webhook payload shape matches the synchronous response shape (RFC 7807 problem document inside the `error` field).
    - [ ] Strict-mode disclosure applies to the webhook payload too.
- [ ] e2e or integration test (if feasible): trigger a real workflow failure on a test Temporal instance, observe the webhook payload. If a real Temporal instance is not available in this repo's test infra, document the gap and rely on the unit tests.
- [ ] `make fui && make c && make tp` clean.

---

## CHECKPOINT D — End of Phase 4

**Stop. Do this in order.**

1. **Verify.** `make fui && make c && make tp` green. New unit tests for webhook recovery pass.
2. **Manual cross-path consistency check.** Confirm: the JSON in a webhook `error` field for failure X is byte-identical (modulo `instance`, `request_id`, and timing) to the JSON returned by `/pipeline/execute` for the same failure X. If they differ, identify why and fix.
3. **Update Status block.** Last checkpoint = "D." Current phase = "Phase 4 complete; async path consistent with sync. Ready for documentation."
4. **Append a "What landed" note** under this checkpoint heading.
5. **Commit.** Conventional message: `error-handling: phase 4 — temporal webhook recovery`.

### What landed (fill in at Checkpoint D)

_To be filled in by the agent reaching this checkpoint._

### Cold-start prompt template for Phase 5

> Pick up the error-handling implementation. Read `TODOS.md` (the status block, "What landed" under Checkpoint D). The code work is done; Phase 5 is documentation: publishing the RFC 7807 type URI doc pages, writing the public API error contract, updating the changelog, and archiving the design directory. Follow the Phase 5 checklist in `TODOS.md`.

---

## Phase 5 — Documentation and archival

Code work is done. Documentation makes the contract usable for clients and makes the design directory tidy.

- [ ] Public API error-contract page under `docs/`:
    - [ ] One page describing the error response shape (RFC 7807 fields + pipelex extension members).
    - [ ] A table of the stable `error_category` values and their retry implications.
    - [ ] A table of `user_action.kind` values.
    - [ ] A reference to the `type` URI namespace and a list of currently-published classes.
- [ ] `type` URI doc pages — minimum viable:
    - [ ] Decision: ship a single generic page at `https://pipelex.dev/errors/` describing the schema, **or** stub one page per error class with redirect to the generic page until per-class pages are written. **Recommended:** generic page only at this stage; per-class pages can grow incrementally. Document the decision.
- [ ] `CHANGELOG.md` entry capturing:
    - [ ] The breaking change: error response shape moved from `{"detail": {...}}` to RFC 7807 `application/problem+json`.
    - [ ] The new fields available to clients.
    - [ ] The `ERROR_DISCLOSURE` env var and its default.
- [ ] Update `CLAUDE.md` if any conventions changed (e.g. the rule about per-route error handling).
- [ ] Archive the design directory:
    - [ ] Rename `wip/error-handling/` to `wip/error-handling-archive/` (or move under a top-level `archive/`, depending on the repo's convention).
    - [ ] Update the README.md inside the archived directory to mark it as historical-reference-only.
    - [ ] If `wip/error-handling/upstream-followups.md` exists, decide whether to keep it as a live tracker (rename to `docs/upstream-followups.md` if so).
- [ ] `make fui && make c && make tp` clean.
- [ ] Final commit. Conventional message: `error-handling: phase 5 — documentation, changelog, archive design`.

---

## When everything is done

- [ ] All checkboxes ticked.
- [ ] Status block above shows "Complete."
- [ ] PR opened against `main` with a summary linking back to the archived design directory.
- [ ] This `TODOS.md` file is itself archived or deleted as part of the final commit.

## What is explicitly NOT in scope

For the agent's reference, so it doesn't get pulled into adjacent work mid-phase:

- The webhook signing scheme (`X-Completion-Signature` covers only the `pipeline_run_id` today). Flagged in `wip/error-handling/track-temporal-recovery.md` for a separate iteration.
- A synchronous status-polling endpoint `GET /api/v1/pipeline/{run_id}`. Designed in `track-temporal-recovery.md` for shape consistency but out of scope to implement here.
- OpenTelemetry / distributed tracing. The `X-Request-ID` plumbing is a strict subset and can grow into trace context later.
- Dashboards and alert rules. The structured-log fields are the foundation; the operator can build whatever they want on top.
- Auth-error refactors. Authentication errors in `api/security.py` use standard FastAPI 401/403 and stay as-is.
