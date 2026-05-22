# Implementation Plan — Error Handling

This is the implementation plan for the error-handling design captured in `wip/error-handling/`. Work proceeds in phases. Each phase is a coherent unit of work. Four checkpoints mark mandatory stop points where the agent gathers state into this document before any next-session work begins.

**The design is settled.** Do not relitigate. If something needs to change, update `wip/error-handling/*.md` first, then come back here.

---

## Status (update at every checkpoint)

- **Current phase:** Phase 1 complete, ready for Phase 2
- **Last checkpoint reached:** A
- **Next checkpoint:** Checkpoint B — end of Phase 2
- **Branch:** `feature/Adapt-to-pipelex-update` (error-handling work continues on this branch — prior `feat(error-handling)` commits already live here)

## Dependency status — pipelex companion work (reconciled 2026-05-22)

The pipelex-side companion items (`wip/error-handling/pipelex-changes.md` #1–#7) **have all landed** — via pipelex PR #931 and the `feature/API-readiness-2` follow-ups (PR #933). The dev dependency now points at that worktree: `pipelex = { path = "../_for_api", editable = true }`, published pin `pipelex==0.29.1`. This plan was drafted against the *proposed* surface; the real surface differs. Corrections that change the work below — read these before starting any phase:

- **No `ErrorReport.to_strict_dict()`.** Disclosure is `ErrorReport.to_dict(disclosure_mode=DisclosureMode.STRICT)` (`DisclosureMode` is a StrEnum, `VERBOSE` / `STRICT`). Every `to_strict_dict()` reference below means that call.
- **`ErrorReport.to_problem_document(*, instance, request_id, disclosure_mode)` already exists** (pipelex item #6). The Phase 1 builder should delegate to it instead of re-mapping RFC 7807 fields by hand — Phase 1 shrinks to a thin wrapper plus the API-error variant.
- **`type` URI namespace is `https://docs.pipelex.com/latest/errors/<kebab-class-name>/`** (trailing slash) — owned upstream by `URLs.error_docs_base`, resolves to a real per-class doc page. Not `https://pipelex.dev/errors/`.
- **`ErrorReport.title` / `type_uri` are required, always-populated fields.** The Phase 0 `error_uri.py` "fallback when `None`" is never exercised against this pipelex version — keep it only as a thin defensive backstop, or drop it.
- **`recover_error_report` is total** — it always returns an `ErrorReport`, synthesizing an `UnrecoverableWorkflowFailureError` report when none can be recovered. It never returns `None`. Phase 4 must not branch on `recover_error_report(...) is None`, and the pipelex webhook always carries a structured `error` on `FAILED`.
- **STRICT redaction is provenance-based** — keyed on a `caller_facing_message` flag, not on `error_domain == INPUT`. The API does not re-implement it (it just calls `to_dict(disclosure_mode=STRICT)`), but the "INPUT verbose / CONFIG-RUNTIME redacted" mental model below is approximate; the precise rule lives upstream.
- **New pipelex constraint for Phase 4:** `WebhookTarget.payload` now rejects the reserved keys `pipeline_run_id` / `status` / `result_url` / `error` at construction time. The API must not place those keys in a webhook's static payload.

Before starting Phase 0, do a short reconciliation pass over the inline references below (the `to_strict_dict()` mentions, the URI namespace, the Phase 4 `None` branches) so implementation is coded against the real surface.

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
- `type` URI namespace: `https://docs.pipelex.com/latest/errors/<kebab-case-error-type>/` (trailing slash; owned upstream by `URLs.error_docs_base`; resolves to a real per-class doc page).

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

- [x] Add `ERROR_DISCLOSURE` env var plumbing. Two valid values: `verbose` (default) and `strict`. Read once at app startup (or via a lightweight settings module if one exists; otherwise inline in the handler). Reject unknown values with a startup-time error. *(Landed: `api/disclosure.py` — `resolve_disclosure_mode()` returns a `DisclosureMode`, raises `InvalidErrorDisclosureError` on a bad value; `api/main.py` calls it at module/startup into `ERROR_DISCLOSURE_MODE`.)*
- [x] **(Defensive only — see the Dependency status block: `report.type_uri` / `report.title` are required, always-populated fields, so this fallback module is not needed for correctness. Keep it as a thin backstop or drop the item.)** Add a `docs.pipelex.com/latest/errors/` fallback namespace constant in a small module (proposed: `api/error_uri.py`). The module consumes `report.type_uri` and `report.title` directly from `ErrorReport` (set by pipelex items **#1** `PipelexError.title()` and **#2** `PipelexError.type_uri()`). Fallback behavior if those fields are ever absent:
    - `error_type_uri(error_type: str) -> str` — returns kebab-cased URI under `https://docs.pipelex.com/latest/errors/`. Used when `report.type_uri is None`.
    - `error_type_title(error_type: str) -> str` — returns a deterministic humanized split of the camel case (`EnvVarNotFoundError` → `"Env Var Not Found Error"`). Used when `report.title is None`.
    - Rationale: the fallback exists so the API works against pipelex versions that haven't yet adopted items #1 / #2 on every class. Once full upstream coverage lands, the fallback is rarely exercised but stays as a backstop. **No curated title map API-side** (review amendment A4): curation belongs upstream on the class.
    - *Reconciliation (landed): kept `api/error_uri.py` — not as a pipelex-`None` backstop (those fields are required) but as the genuine source of `type`/`title` for **API-authored** errors in Phase 1's `build_problem_document_from_api_error`, which have no `ErrorReport`. It reuses pipelex's own transforms (`URLs.error_docs_base`, `pascal_case_to_kebab`, `pascal_case_to_sentence`) so output is sentence-case — `"Env var not found error"`, consistent with `PipelexError.title()` — not the Title-Case the example sketched.*
- [x] Add a request-id middleware in `api/middleware.py` (sibling of `request_body_size_middleware`): *(Landed as `RequestIdMiddleware` — a pure-ASGI middleware, not `BaseHTTPMiddleware`, so its contextvars reach the outermost `ServerErrorMiddleware` where the catch-all 500 handler runs.)*
    - [x] Reads `X-Request-ID` from the inbound request if present; otherwise generates a ULID.
    - [x] Validates inbound id (length cap, character set) and replaces with a fresh id on invalid input — never trust inbound ids blindly.
    - [x] Stores on `request.state.request_id`.
    - [x] Echoes `X-Request-ID` on every response (success and error).
- [x] Add a logging contextvar (proposed: `api/logging_context.py`) that exposes both `request_id` AND `route_path` (the matched FastAPI route) to loggers AND to the 4xx helpers in Phase 3. Two getters: `get_request_id() -> str | None` and `get_route_path() -> str | None`. The middleware sets both at request entry; the contextvars are read from the global exception handlers, the 4xx helpers, and structured-log sites. **Review amendment Q1:** this avoids threading `Request` through helper signatures in Phase 3 — helpers stay parameter-clean, call-site diff stays tiny. *(Reconciliation: `route_path` holds the request URL path, set by the middleware at entry. The literal "matched FastAPI route template" is only known after routing — unavailable to a middleware. For this API every route path is static, so URL path == route template; both `instance` and the log `route` field are well served.)*
- [x] Register the request-id middleware in `api/main.py`. Confirm ordering relative to CORS and body-size middleware — request-id should run first so all downstream code (and exception handlers) see the id. *(Registered last → outermost → runs first.)*
- [x] Unit tests:
    - [x] `tests/unit/test_error_uri.py` — `error_type_uri` kebab-casing and `error_type_title` humanized split for known + unknown class names.
    - [x] `tests/unit/test_logging_context.py` — `get_request_id()` and `get_route_path()` return `None` outside a request; return the set values inside.
    - [x] `tests/unit/test_request_id_middleware.py` — generates ULID when absent; echoes when present; rejects malformed; sets `request.state.request_id`; sets the contextvars (`request_id` + `route_path`); sets `X-Request-ID` response header.
    - [x] *(Added) `tests/unit/test_disclosure.py` — `ERROR_DISCLOSURE` defaults to verbose, resolves valid values case-insensitively, rejects unknown values.*
- [x] `make fui && make c && make tp` clean.

---

## Phase 1 — RFC 7807 problem-document builder

A pure module that turns an `ErrorReport` (and optional context) into a problem+json dict. No FastAPI imports. No I/O. All logic for field mapping lives here so it can be unit-tested in isolation. Strict-mode redaction is delegated to pipelex item **#4** (`ErrorReport.to_dict(disclosure_mode=DisclosureMode.STRICT)`); the API only chooses the disclosure mode.

**Reconciliation note (2026-05-22):** pipelex item **#6** (`ErrorReport.to_problem_document(*, instance, request_id, disclosure_mode)`) has landed — it already produces the RFC 7807 dict. The builder below should **delegate to `report.to_problem_document(...)`** rather than re-mapping every field; what stays API-side is the `disclosure_mode` selection from env and the `build_problem_document_from_api_error` variant. Treat the field-by-field checklist below as the spec for *what `to_problem_document` produces* (use it to verify the upstream output), not as a re-implementation list.

Proposed location: `api/problem_document.py`.

- [x] Define the public API: *(Landed in `api/problem_document.py`.)*
    - [x] `build_problem_document(report: ErrorReport, *, instance: str | None, request_id: str | None, disclosure_mode: Literal["verbose", "strict"]) -> dict[str, Any]` *(Reconciliation: `disclosure_mode` is typed `DisclosureMode` — the pipelex enum — not `Literal["verbose","strict"]`. The caller already holds a `DisclosureMode`; passing the enum straight through avoids a needless string round-trip.)*
    - [x] `build_problem_document_from_api_error(error_type: ErrorType, message: str, status: int, instance: str | None, request_id: str | None) -> dict[str, Any]` — used by `raise_validation_error` etc. Always treated as `INPUT` domain. *(`instance` / `request_id` are keyword-only.)*
- [x] Source the extension-member fields from the report. Switch on `disclosure_mode`:
    - [x] `verbose` → `report.to_dict(disclosure_mode=DisclosureMode.VERBOSE)` (the default).
    - [x] `strict` → `report.to_dict(disclosure_mode=DisclosureMode.STRICT)` (pipelex item **#4**). Provenance-based redaction lives upstream — the API does not re-implement the rules. *(Done by delegating to `to_problem_document` — `disclosure_mode` is passed straight through.)*
- [x] Implement the RFC 7807 standard fields per the field-mapping table in `wip/error-handling/track-response-schema.md`: *(For `build_problem_document` these are produced by `report.to_problem_document(...)` upstream — verified by the unit tests. Listed below as the spec of what that produces; `build_problem_document_from_api_error` builds them by hand.)*
    - [x] `type` from `report.type_uri` (pipelex item #2), falling back to `error_type_uri(report.error_type)` when `None`. *(`type_uri` is a required field — the `None` fallback never fires; `error_type_uri` is instead the primary source for the API-error variant.)*
    - [x] `title` from `report.title` (pipelex item #1), falling back to `error_type_title(report.error_type)` when `None`. *(Same: `title` is required; `error_type_title` is the API-error variant's source.)*
    - [x] `status` from `report.http_status`.
    - [x] `detail` from the `message` field of the source dict (`to_dict(disclosure_mode=...)` — strict-mode replacement is already applied upstream).
    - [x] `instance` from the caller-supplied route path.
- [x] Layer extension members on top (drop `None`-valued fields per `ErrorReport.to_dict()` semantics):
    - [x] `error_type`, `error_category`, `error_domain`, `retryable`, `user_action`, `model`, `provider`, `provider_metadata` — all sourced from the disclosure-aware dict.
    - [x] `request_id` from the caller-supplied id (distinct from `provider_metadata.request_id`).
- [x] Unit tests in `tests/unit/test_problem_document.py`. One `TestClass` per project rules; one test method per scenario:
    - [x] Builds a problem document from a synthetic `EnvVarNotFoundError` (`CONFIG` domain). *(Reconciliation: `EnvVarNotFoundError` is domain-less in this pipelex version — still HTTP 500, but no `error_domain` member. A separate `PipelexConfigError` case covers a genuine `error_domain = "config"`.)*
    - [x] Builds a problem document from a synthetic `LLMCompletionError` with `provider_metadata` carrying `retry_after_seconds` (verify all extension members present). *(Built as a synthetic `ErrorReport` directly — the builder consumes an `ErrorReport`, not an exception.)*
    - [x] **Dispatch:** the builder forwards `disclosure_mode` to the pipelex call unchanged. Use pytest-mock to assert the right `disclosure_mode` is passed. The redaction rules themselves are pipelex's responsibility (item #4 unit tests cover them upstream); the API only proves it forwards the right mode.
    - [x] ~~**Fallback:** when `report.title is None`...~~ *Dropped — `ErrorReport.title` / `type_uri` are required, never-`None` fields, so this branch is unreachable. `error_type_uri` / `error_type_title` are exercised via the API-error variant test and `tests/unit/test_error_uri.py`.*
    - [x] API-error variant produces a valid problem document with `error_domain = "input"`.
    - [x] `None`-valued fields are dropped, not emitted as `null`.
- [x] `make fui && make c && make tp` clean.

---

## CHECKPOINT A — End of Phase 1

**Stop. Do this in order.**

1. **Verify.** Run `make fui && make c && make tp`. All green. Read the diff (`git diff main...HEAD`) — does it touch only Phase 0 + Phase 1 files? No collateral changes?
2. **Manual sanity check.** Write a 10-line throwaway script that imports `build_problem_document`, feeds it a synthetic `EnvVarNotFoundError`, prints the dict. Confirm the RFC 7807 standard fields are correct and the `detail` carries the env var name in verbose mode.
3. **Update the Status block at the top of this doc.** Set current phase to "Phase 1 complete, ready for Phase 2." Set last checkpoint to "A."
4. **Append a "What landed" note under this checkpoint heading below.** Include: files created (paths only), public API surface added, anything unexpected encountered, anything deferred to a later phase.
5. **Commit.** Conventional message: `error-handling: phase 0+1 — request id, problem document builder`.

### What landed (fill in at Checkpoint A)

**Files created:**

- `api/logging_context.py` — request-id / route-path contextvars.
- `api/disclosure.py` — `ERROR_DISCLOSURE` resolution.
- `api/error_uri.py` — RFC 7807 `type` / `title` derivation.
- `api/problem_document.py` — the RFC 7807 builder.
- `tests/unit/test_logging_context.py`, `test_disclosure.py`, `test_error_uri.py`, `test_request_id_middleware.py`, `test_problem_document.py`.

**Files modified:**

- `api/middleware.py` — added `RequestIdMiddleware` (pure-ASGI), `generate_request_id()`, `REQUEST_ID_HEADER`, inbound-id validation. The existing `request_body_size_middleware` is untouched.
- `api/main.py` — registered `RequestIdMiddleware` (last → outermost); resolves `ERROR_DISCLOSURE_MODE` at startup.

**Public API surface added:**

- `api.logging_context`: `get_request_id() -> str | None`, `get_route_path() -> str | None`, `bound_request_context(*, request_id, route_path)` context manager.
- `api.disclosure`: `resolve_disclosure_mode() -> DisclosureMode`, `InvalidErrorDisclosureError`, `ERROR_DISCLOSURE_ENV_VAR`.
- `api.error_uri`: `error_type_uri(error_type: str) -> str`, `error_type_title(error_type: str) -> str`.
- `api.middleware`: `RequestIdMiddleware`, `generate_request_id() -> str`, `REQUEST_ID_HEADER`.
- `api.main`: `ERROR_DISCLOSURE_MODE: DisclosureMode` module constant (startup-resolved; **Phase 2 consumes it** — registered handlers in `api/main.py` read it directly).
- `api.problem_document`: `build_problem_document(report, *, instance, request_id, disclosure_mode)`, `build_problem_document_from_api_error(error_type, message, status, *, instance, request_id)`.

**Verification:** `make fui` clean; `make c` clean (ruff format + lint, pyright 0 errors, mypy success); `make tp` — 124 passed. Sanity check confirmed: a synthetic `EnvVarNotFoundError` builds an RFC 7807 doc with `type`/`title`/`status:500`/`detail` correct, `detail` carrying the env var name in verbose mode.

**Reconciliation findings — read before Phase 2/3:**

1. **`EnvVarNotFoundError` is domain-less, NOT `CONFIG`.** It is a `ToolError`; neither `ToolError` nor `EnvVarNotFoundError` sets `error_domain`, so its `ErrorReport` has `error_domain = None`. The problem document therefore has **no `error_domain` member** — but `http_status` is still **500** (`error_domain_to_http_status(None) → 500`) and `detail` still names the env var. **Phase 2's test (checklist line: "`EnvVarNotFoundError` → 500, `error_domain = "config"`") and Phase 3's e2e ("`error_domain = "config"`, `detail` names the env var") are wrong against this pipelex version on the `error_domain` part only.** Phase 2/3 should: assert `error_domain` is *absent* for `EnvVarNotFoundError`, and/or use a genuine CONFIG-domain error (`PipelexConfigError` works — verified) to exercise `error_domain = "config"`. A missing env var arguably *should* be `ErrorDomain.CONFIG` upstream — a candidate new item for `wip/error-handling/pipelex-changes.md` (left for the Phase 3 catch-site audit to file).
2. **`RequestIdMiddleware` is pure-ASGI and wraps the whole FastAPI app.** `api/main.py` does `app = RequestIdMiddleware(fastapi_app)`, **not** `add_middleware`. `add_middleware` would nest it *inside* Starlette's `ServerErrorMiddleware` (always the outermost layer); wrapping the app instead puts it genuinely outermost. So the `request_id` / `route_path` contextvars are bound, and `X-Request-ID` is echoed, on **every** response — including the catch-all 500 that `ServerErrorMiddleware` emits. (The first Phase 0 cut used `add_middleware`; the Checkpoint A code review caught that the catch-all 500 then lost both the header and the contextvars — now fixed.)
3. **No Phase 2 workaround needed for `X-Request-ID`.** Because of the wrap in note 2, the `Exception` fallback handler (Phase 2) does **not** need to set `X-Request-ID` itself — the middleware's `send` wrapper covers every response, the catch-all 500 included. The handler still reads `request.state.request_id` for the response *body* and structured logs.
4. **`route_path` contextvar holds the request URL path**, set at middleware entry — the literal "matched FastAPI route template" is unknowable to a middleware (routing happens downstream). Every route path in this API is static, so URL path == route template; both the RFC 7807 `instance` and the observability `route` field are well served.
5. **`error_uri.py` kept but reframed.** Not the pipelex-`None` backstop the plan sketched — `ErrorReport.title` / `type_uri` are required, never-`None` fields. It is the genuine source of `type` / `title` for the API-authored-error variant (`build_problem_document_from_api_error`), which has no `ErrorReport`. Titles are sentence-case (`"Validation error"`) via pipelex's `pascal_case_to_sentence` transform — but, unlike `PipelexError.title()`, *without* stripping a trailing `Error` (an API `ErrorType` like `ValidationError` must keep the suffix). Not the Title-Case the plan example showed.
6. **`build_problem_document`'s `disclosure_mode` is typed `DisclosureMode`** (the pipelex enum), not `Literal["verbose","strict"]` — the caller already holds a `DisclosureMode`. The Phase 1 "Fallback (title is None)" test was dropped as unreachable.
7. **Tooling note:** pyright resolves the editable `pipelex` correctly only when given `--pythonpath` (the `make pyright` target does this) or with `VIRTUAL_ENV` set. A bare `pyright` invocation resolves a stale published `pipelex` and reports false "unknown symbol" errors — always verify types via `make c`.

**Deferred to later phases (as planned):** Phase 2 global exception handlers, Phase 3 route cleanup / 4xx-helper migration / auth migration. `ERROR_DISCLOSURE_MODE` is resolved but not yet consumed — Phase 2 wires it in. The per-route `try/except` blocks are untouched (Phase 3).

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
    - [ ] Returns a fixed sanitized problem document: `type = "https://docs.pipelex.com/latest/errors/internal-server-error/"`, `title = "Internal server error"`, `status = 500`, `detail = "An unexpected error occurred. The request id is included for support."`, `error_type = "InternalServerError"`, `error_domain = "runtime"`, `retryable = false`, `request_id`. This handler is one of the two legitimate `except Exception` locations per project rules; add a one-line comment justifying it.
- [ ] Wire the log emission. Determine whether the existing `pipelex` `log` object renders structured fields in JSON when configured for a JSON sink, and confirm fields are extractable. If the existing logger does not support structured fields natively, accept a degraded "key=value in the message" rendering for now and file a followup. Do not block this phase on perfect structured logging.
- [ ] Integration tests in `tests/unit/test_exception_handlers.py` against a small test FastAPI app (or against the real app via `TestClient`):
    - [ ] `PipelexError` subclass produces a problem+json response with correct fields.
    - [ ] `EnvVarNotFoundError` → 500, `error_domain = "config"`, `detail` carries the env var name (verbose mode).
    - [ ] `ERROR_DISCLOSURE=strict` redacts `detail` for `CONFIG` errors, preserves `INPUT`.
    - [ ] Simulated `LLMCompletionError` with `provider_metadata.retry_after_seconds = 12.0` → 429, `Retry-After: 12` header.
    - [ ] `Exception` subclass triggers the fallback handler → 500 with `error_type = "InternalServerError"`, response body does NOT include the original exception class name.
    - [ ] **TemporalError dispatch (T4):** `WorkflowExecutionError` (a `PipelexError` subclass) goes through the `PipelexError` handler, NOT the `TemporalError` handler. A bare `temporalio.exceptions.RPCError` (or another non-`PipelexError` `TemporalError` subclass) goes through the dedicated `TemporalError` handler with the synthetic transport-transient classification.
    - [ ] **Handler-of-handlers (T3):** monkey-patch a `PipelexError` subclass so `to_error_report()` itself raises; confirm the `Exception` fallback catches it and returns a sanitized 500 — not FastAPI's default bodyless 500. Without this test, a corrupt `to_error_report` silently breaks every error response.
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

## Phase 3 — Route cleanup, 4xx helper migration, inline-HTTPException migration

The subtraction phase. Risky because it touches every route file; the global handler must be in place and working (Checkpoint B verified) before this phase runs.

**Scope note (review amendment A1 + A2):** This phase also migrates direct `HTTPException` sites that today bypass the 4xx helpers and emit the old `{"detail": {...}}` shape — `api/middleware.py` `_too_large_response()`, `api/routes/storage.py:157-163` (403 ownership), `api/routes/storage.py:173-179` (500 presign), and the seven `HTTPException` sites in `api/security.py` (5×401, 2×500). The design originally punted on auth migration as a separate track; per CLAUDE.md's "no backward compatibility" rule, we migrate it here instead so clients see one error shape everywhere. RFC 7807 supports the `WWW-Authenticate: Bearer` header — moving the body to `application/problem+json` does not break the auth challenge.

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
    - [ ] If pipelex should wrap it but doesn't → append to `wip/error-handling/pipelex-changes.md` (already seeded with the 9 companion items; just add new ones as discovered). File an upstream pipelex issue if appropriate.
    - [ ] If the API boundary itself raises it (e.g. Pydantic coercion at the route) → catch the **specific** exception (not the union) and call `raise_validation_error(...)`.
    - [ ] Otherwise → let it fall through to the `Exception` fallback. Document the call site in `pipelex-changes.md`.
- [ ] In `api/errors.py`:
    - [ ] Delete `ENDPOINT_HANDLED_EXCEPTIONS` and `STORAGE_HANDLED_EXCEPTIONS`.
    - [ ] Delete `raise_internal_error`.
    - [ ] Update `raise_validation_error`, `raise_bad_request`, `raise_payload_too_large` to emit RFC 7807 problem documents via `build_problem_document_from_api_error`.
        - [ ] Helpers read `request_id` and `route_path` from the Phase 0 contextvars (`api/logging_context.py`) — **no signature change** (review amendment Q1). Call sites are unchanged.
        - [ ] Status code stays the same (422 / 400 / 413).
        - [ ] Body shape changes from `{"detail": {"error_type", "message"}}` to RFC 7807 problem document.
        - [ ] Response `Content-Type` is `application/problem+json`.
    - [ ] Add new helpers (review amendments A1 + A2):
        - [ ] `raise_forbidden(message: str, error_type: ErrorType = ErrorType.FORBIDDEN) -> NoReturn` — 403 RFC 7807. Used by `api/routes/storage.py` ownership-mismatch.
        - [ ] `raise_internal_server_error(message: str, error_type: ErrorType) -> NoReturn` — 500 RFC 7807 for API-owned 500s (NOT for pipelex domain errors, which go through the global handler). Used by `api/routes/storage.py` presign-failure and `api/security.py` SERVER_MISCONFIGURED cases.
        - [ ] `raise_unauthenticated(message: str, error_type: ErrorType = ErrorType.UNAUTHENTICATED) -> NoReturn` — 401 RFC 7807 with `WWW-Authenticate: Bearer` header. Used by all `api/security.py` 401 sites. (RFC 7807 fully supports the challenge header — moving the body to `application/problem+json` does not break OAuth/JWT clients that parse the `WWW-Authenticate` header.)

### A1 — migrate inline `HTTPException` sites

- [ ] `api/middleware.py` `_too_large_response()` (lines 13-22): build an RFC 7807 problem document inline (the middleware owns the response without going through a route, so it cannot use the 4xx helpers directly — they'd need a route context that the middleware doesn't have on the early-reject path). Set `Content-Type: application/problem+json`. Set the `X-Request-ID` header on the response if the contextvar is populated (the request-id middleware should run first; see Phase 0 middleware ordering checkbox).
- [ ] `api/routes/storage.py:157-163` (403 ownership mismatch): replace direct `HTTPException(...)` with `raise_forbidden(...)`.
- [ ] `api/routes/storage.py:173-179` (500 presign-failure): replace direct `HTTPException(...)` with `raise_internal_server_error(...)`. (Note: pipelex's storage layer should be the canonical author of `StorageError`-style failures; the API should NOT raise a pipelex error here. The 500 is API-authored — the storage backend returned a non-presigned URL, which is an API-layer configuration check, not a pipelex domain error.)
- [ ] `api/security.py` — migrate all 7 `HTTPException` sites (review amendment A2):
    - [ ] Lines 93-96 (`verify_jwt`, 500 missing `JWT_SECRET_KEY`) → `raise_internal_server_error(message, error_type=ErrorType.SERVER_MISCONFIGURED)`.
    - [ ] Lines 118-122 (`verify_jwt`, 401 missing `user_id` claim) → `raise_unauthenticated(message, error_type=ErrorType.INVALID_TOKEN)`.
    - [ ] Lines 125-129 (`verify_jwt`, 401 `user_id` not a UUID) → `raise_unauthenticated(message, error_type=ErrorType.INVALID_TOKEN)`.
    - [ ] Lines 136-140 (`verify_jwt`, 401 expired token) → `raise_unauthenticated(message, error_type=ErrorType.TOKEN_EXPIRED)`.
    - [ ] Lines 143-147 (`verify_jwt`, 401 invalid token) → `raise_unauthenticated(message, error_type=ErrorType.INVALID_TOKEN)`.
    - [ ] Lines 159-162 (`verify_api_key`, 500 missing `API_KEY`) → `raise_internal_server_error(message, error_type=ErrorType.SERVER_MISCONFIGURED)`.
    - [ ] Lines 166-170 (`verify_api_key`, 401 key mismatch) → `raise_unauthenticated(message, error_type=ErrorType.INVALID_TOKEN)`.
    - [ ] Confirm `WWW-Authenticate: Bearer` header is emitted on every 401 response (the helper adds it; verify in tests).
- [ ] Update existing tests on these paths to assert the new RFC 7807 shape (covered by the T1/T2/T5 regression checkboxes in the e2e section below, and explicitly enumerated in the old-shape audit checkbox).
- [ ] Update existing tests that assert the old `{"detail": {...}}` shape to assert the new RFC 7807 shape. Likely candidates: every e2e test that exercises 4xx paths.
- [ ] Update the module docstring at the top of `api/errors.py` to describe the new world (no more catch tuples, helpers emit problem documents).
- [ ] e2e coverage in `tests/e2e/`:
    - [ ] `/pipeline/start` with no `COMPLETION_CALLBACK_SECRET` → 500 problem+json, `error_domain = "config"`, `detail` names the env var. (The original bug, verified end-to-end.)
    - [ ] `/pipeline/execute` with an invalid pipe code → expected error from pipelex propagates correctly.
    - [ ] `raise_validation_error` path returns RFC 7807 with `error_domain = "input"`.
    - [ ] At least one storage route happy path still works (no regression from removing `STORAGE_HANDLED_EXCEPTIONS`).
    - [ ] **REGRESSION T1**: a request body > `MAX_REQUEST_BODY_BYTES` returns 413 `application/problem+json` (not the old `{"detail": {...}}` shape). Header `X-Request-ID` present.
    - [ ] **REGRESSION T2**: storage 403 ownership-mismatch returns RFC 7807. Header `X-Request-ID` present.
    - [ ] **REGRESSION T5**: every error-emitting route includes `X-Request-ID` on the response. Parametrize across 422 / 400 / 403 / 413 / 500.
- [ ] **REGRESSION — old-shape audit**: `grep -rn '"detail"' tests/` and enumerate every test asserting `{"detail": {"error_type", "message"}}`. Update each to assert the new RFC 7807 shape. The list of updated files goes into the "What landed" note at Checkpoint C, so the audit's completeness is visible at review time. (Skill rule: regressions are mandatory tests — no AskUserQuestion gate.)
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
4. **Check `wip/error-handling/pipelex-changes.md`.** Confirm: (a) any new pipelex gaps discovered during the catch-site audit have been added to the tracking table, and (b) Stage 1 items (1, 2, 3) and Stage 2 item (4) are at or near merge — the API has just consumed all of them in Phases 0-1. If any are still in flight, note it here so Phase 4 entry knows the dependency status.
5. **Update Status block.** Last checkpoint = "C." Current phase = "Phase 3 complete, sync path complete, ready for Phase 4."
6. **Append a "What landed" note** under this checkpoint heading. Include: which routes were cleaned up, anything surprising in the catch-site audit, and the current status of `wip/error-handling/pipelex-changes.md` items (which Stage 1-2 items landed, which Stage 3+ items are in flight).
7. **Commit.** Conventional message: `error-handling: phase 3 — route cleanup, 4xx helpers emit problem documents`.

### What landed (fill in at Checkpoint C)

_To be filled in by the agent reaching this checkpoint._

### Cold-start prompt template for Phase 4

> Pick up the error-handling implementation. Read `TODOS.md` (the status block, "What landed" under Checkpoint C) and `wip/error-handling/track-temporal-recovery.md`. The synchronous error path is complete; Phase 4 wires `recover_error_report` into the async / webhook-completion path so workflow failures reach callers with the same RFC 7807 shape. Start by mapping where the workflow-completion observer lives (pipelex's `WorkflowExecutor` vs an API-side background worker). Follow the Phase 4 checklist in `TODOS.md`.

---

## Phase 4 — Temporal webhook recovery

Async failures must surface to callers with the same RFC 7807 shape as the synchronous path. The recovery primitive is `pipelex.temporal.tprl.temporal_error.recover_error_report`.

**Scope note (review amendment A3) — Phase split:** During plan review the workflow-completion observer was located at `pipelex/pipe_run/delivery_executor.py:238 _notify_webhook`. Today it composes the webhook payload as `{pipeline_run_id, status, result_url}` with **no `error` field**, and `recover_error_report(...)` is **not called** by `delivery_executor`. The pipelex change to fix this is item **#5** in `wip/error-handling/pipelex-changes.md` — landing in parallel with this work, not as a separate timeline. Phase 4 is split into **4a (verify pipelex item #5 landed)** and **4b (API-side audit)**.

### Phase 4a — Verify pipelex item #5 is landed

This phase consumes item **#5** from `wip/error-handling/pipelex-changes.md` (`DeliveryExecutor.execute(error_report=...)`), which is implemented in parallel with the API work. See `pipelex-changes.md` for the full specification.

- [ ] Confirm pipelex item #5 has landed. Spec-check: `DeliveryExecutor.execute(...)` accepts `error_report: ErrorReport | None = None`; `_notify_webhook` includes `error: <error_report.to_dict()>` in the payload on `FAILED` status when `error_report` is provided; recovery responsibility stays with the caller (worker/observer), not `DeliveryExecutor` itself.
- [ ] Update pipelex version pin in `pyproject.toml` to the commit / version that lands item #5. Re-pin `mthds` if its pipelex constraint moves.
- [ ] Update `wip/error-handling/pipelex-changes.md` tracking table — set item #5 Status to "Landed" with the PR link / commit hash.
- [ ] Stage 1 items (#1 `PipelexError.title()`, #2 `PipelexError.type_uri()`, #3 first-class `request_id` on `JobMetadata`) and Stage 2 item (#4 `DisclosureMode` + `to_dict(disclosure_mode=)`) are already landed — see the Dependency status block at the top. Confirm the pinned pipelex version carries them.

### Phase 4b — API-side audit (gated on 4a)

- [ ] Confirm the workflow-completion observer location: `pipelex/pipe_run/delivery_executor.py:_notify_webhook` (verified during plan review on 2026-05-20). The API supplies `callback_urls` via `WebhookTarget`; pipelex composes and posts.
- [ ] **Render decision — pinned: option 2 (raw `ErrorReport` dict in webhook).** Reason: the `ErrorReport` dict IS the structured data — RFC 7807 is a presentational layer over it, and the webhook receiver may or may not want the envelope (a non-HTTP consumer like a queue, a log shipper, or a batch processor probably doesn't). The sync HTTP response renders to RFC 7807 because HTTP wants it; the webhook payload stays structurally faithful to the source. Document this asymmetry clearly in `wip/error-handling/track-temporal-recovery.md`. **Future escape hatch:** pipelex item **#6** (`ErrorReport.to_problem_document(...)`) has landed (`pipelex-changes.md` #6) — if a future use case demands "RFC 7807 everywhere," `_notify_webhook` can call that method to render upstream without re-architecting.
- [ ] On `status = FAILED` with a recovered `ErrorReport`: confirm the webhook payload includes `error: <error_report.to_dict()>`. Field-by-field parity with the sync RFC 7807 extension members (because the sync response builds them from the same `to_dict()`).
- [ ] On `status = FAILED`, confirm the webhook payload always includes a structured `error`. `recover_error_report` is total — the pipelex worker always recovers and passes an `ErrorReport`, synthesizing an `UnrecoverableWorkflowFailureError` report (`error_domain="runtime"`, `retryable=false`) when nothing better can be recovered. There is no `error_report is None` / unrecoverable branch to author API-side; the never-silent guarantee holds by construction upstream. (Original plan text — pre-reconciliation — assumed `recover_error_report` could return `None`; it cannot.)
- [ ] Wire structured logging on the webhook delivery (existing log line at `pipelex/pipe_run/delivery_executor.py:261` — `Webhook delivery completed: ...`):
    - [ ] Extend to emit `event = "webhook_delivery"` on 2xx, `event = "webhook_failure"` on non-2xx (already raised as `WebhookDeliveryError` at lines 262-267).
    - [ ] Include `request_id` of the original `/pipeline/start` call. Consumed from the first-class field added by pipelex item **#3** (workflow input carries `request_id: str | None`). The API populates it at `make_temporal_pipe_run(...)` dispatch time in `api/routes/pipelex/pipeline.py:96-107`, reading it from the request-id contextvar set by the Phase 0 middleware. **No webhook.payload piggyback needed** once item #3 lands.
    - [ ] Include `pipeline_run_id`, `callback_url`, response status, and (on failure) the recovered error classification fields.
- [ ] Tests in `tests/unit/test_webhook_recovery.py`:
    - [ ] Simulated `WorkflowExecutionError` carrying a packed `ErrorReport` dict → recovery yields the same classification.
    - [ ] Simulated `WorkflowExecutionError` with a malformed / absent details dict → `recover_error_report` still yields a structured report (`UnrecoverableWorkflowFailureError`), and the webhook `error` carries it.
    - [ ] **T6 — cross-path consistency (REGRESSION):** same source `ErrorReport` → both the sync handler and the webhook composer produce identical body content for the error fields, modulo `instance`, `request_id`, and timestamps. Automates the manual check from Checkpoint D step 2. (Skill rule: regression test, mandatory.)
    - [ ] Strict-mode disclosure: the chosen behavior is pinned here. If option 2 (consumer-side rendering) is selected, the test confirms the webhook always carries the raw `ErrorReport` regardless of `ERROR_DISCLOSURE` mode (the receiver chooses what to render).
- [ ] e2e or integration test (if feasible): trigger a real workflow failure on a test Temporal instance, observe the webhook payload. If a real Temporal instance is not available in this repo's test infra, document the gap and rely on the unit tests.
- [ ] `make fui && make c && make tp` clean.

---

## CHECKPOINT D — End of Phase 4

**Stop. Do this in order.**

1. **Verify.** Both 4a (upstream pipelex change merged + version pinned in this repo) and 4b (API-side audit + tests) are complete. `make fui && make c && make tp` green. New unit tests for webhook recovery pass.
2. **Manual cross-path consistency check.** Confirm: the structured error content in a webhook `error` field for failure X matches (modulo `instance`, `request_id`, and timing) the structured fields in the RFC 7807 problem document returned by `/pipeline/execute` for the same failure X. The T6 regression test automates this; the manual check is the smell-test backstop. If they differ, identify why and fix before signing off.
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
    - [ ] Decision: pipelex already generates per-class error doc pages under `https://docs.pipelex.com/latest/errors/<kebab>/` (item #7). So `type` URIs already resolve — this checkbox reduces to: confirm the pipelex error doc pages cover the classes the API surfaces, and decide whether the API publishes its own schema overview page or just links to the pipelex docs. Document the decision.
- [ ] `CHANGELOG.md` entry capturing:
    - [ ] The breaking change: error response shape moved from `{"detail": {...}}` to RFC 7807 `application/problem+json` across **every** API endpoint — pipelex domain errors, validation errors, auth errors (401/403), payload-size limits (413), and the catch-all 500.
    - [ ] The new fields available to clients (`error_type`, `error_category`, `error_domain`, `retryable`, `user_action`, `provider_metadata`, `request_id`, plus the RFC 7807 standard fields).
    - [ ] The `ERROR_DISCLOSURE` env var and its default.
    - [ ] `WWW-Authenticate: Bearer` still appears on 401 responses; only the body shape changed.
    - [ ] **Known limitation:** the webhook completion payload carries the raw `ErrorReport` dict under `error`, not an RFC 7807 problem document. Sync HTTP responses render to RFC 7807 because HTTP wants it; the webhook stays structurally faithful to the source so non-HTTP receivers (queues, log shippers, batch processors) don't have to unwrap an envelope. See `wip/error-handling/track-temporal-recovery.md` for rationale.
- [ ] Update `CLAUDE.md` if any conventions changed (e.g. the rule about per-route error handling).
- [ ] Archive the design directory:
    - [ ] Rename `wip/error-handling/` to `wip/error-handling-archive/` (or move under a top-level `archive/`, depending on the repo's convention).
    - [ ] Update the README.md inside the archived directory to mark it as historical-reference-only.
    - [ ] `wip/error-handling/pipelex-changes.md` is the live cross-repo tracking doc — keep it active. Decide where it lives long-term: stays under `wip/` if pipelex-side work is still in flight at the time of archive, or moves to `docs/pipelex-companion-changes.md` if all 9 items are landed.
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
- ~~Auth-error refactors.~~ **Now in scope** (review amendment A2): Phase 3 migrates the 7 `HTTPException` sites in `api/security.py` to RFC 7807. `WWW-Authenticate: Bearer` is preserved on 401 responses; only the body changes.
- Default `Retry-After` header for internal-capacity errors (where `provider_metadata.retry_after_seconds` is absent). *(Review amendment Q2: out of scope; revisit if observed to matter in practice. Cooperating proxies can already act on the provider-emitted header.)*
- Webhook signature scope expansion (`X-Completion-Signature` covering the full payload). *(Captured as item #9 in `wip/error-handling/pipelex-changes.md` — independent track, lands on its own timeline rather than gating this PR.)*

Note: items previously listed here as "upstream pipelex work for later" — `PipelexError.title` / `type_uri`, disclosure-mode redaction (`to_dict(disclosure_mode=)`), first-class `request_id` — have **landed** in pipelex via `wip/error-handling/pipelex-changes.md` items #1–#7. See the Dependency status block at the top.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | AMENDED | 7 findings + 6 test gaps surfaced; 7 amendments applied to plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**Amendments applied to the plan (2026-05-20):**
- **A1 (MEDIUM-HIGH) — applied.** Phase 3 now explicitly migrates the three direct `HTTPException` sites: `api/middleware.py` `_too_large_response()`, `api/routes/storage.py:157-163` (403 ownership), `api/routes/storage.py:173-179` (500 presign). New helpers `raise_forbidden` and `raise_internal_server_error` added to `api/errors.py`.
- **A2 (MEDIUM) — applied** *(updated after first round: user override of design carve-out, in line with CLAUDE.md's "no backward compatibility" rule)*. All 7 `HTTPException` sites in `api/security.py` migrate to RFC 7807 in Phase 3 via two new helpers `raise_unauthenticated` (401 + `WWW-Authenticate: Bearer`) and `raise_internal_server_error` (already added for A1). Clients see one error shape across every endpoint.
- **A3 (HIGH) — applied.** Phase 4 split into 4a (verify pipelex item #5 from `wip/error-handling/pipelex-changes.md` is landed — `DeliveryExecutor.execute(error_report=...)`) and 4b (API-side audit). The pipelex change is being done in parallel with the API work, not deferred. Render decision pinned: webhook carries raw `ErrorReport` dict (consumer renders RFC 7807 if needed); sync HTTP response renders to RFC 7807 because HTTP wants it. Rationale documented in Phase 4b. Future escape hatch via pipelex item #6 (`ErrorReport.to_problem_document()`) lands later in `pipelex-changes.md`.
- **A4 (LOW-MEDIUM) — applied + extended.** Phase 0 drops the curated `error_type_title` map; the API now reads `report.title` and `report.type_uri` directly from the upstream `ErrorReport` (pipelex items #1 and #2 in `pipelex-changes.md`), with humanize-from-classname as a fallback. Curation moves to the class definition upstream, where it belongs.
- **Q1 (MEDIUM) — applied.** Phase 0 contextvar now carries `request_id` AND `route_path`; Phase 3 4xx helpers read from the contextvar instead of taking `Request`. Helper signatures and call sites unchanged.
- **T1, T2, T5 (CRITICAL regression tests) — applied.** Phase 3 e2e now explicitly tests 413 RFC 7807 shape, storage 403 ownership RFC 7807 shape, and `X-Request-ID` parametrized across 422/400/403/413/500. Old-shape audit checkbox enumerates the test files to update.
- **T3 (handler-of-handlers), T4 (TemporalError dispatch) — applied.** Phase 2 tests now cover both `WorkflowExecutionError` vs bare `RPCError` dispatch and the monkey-patched `to_error_report()` failure path.
- **T6 (cross-path consistency regression) — applied.** Phase 4b automates the Checkpoint D manual consistency check as a test.

**UNRESOLVED:** None. The plan is now coherent for Phase 0 start.

**VERDICT:** CLEARED FOR IMPLEMENTATION — amendments applied. Pipelex companion work is captured in `wip/error-handling/pipelex-changes.md` (9 items across 6 stages, being done in parallel rather than deferred). The dependency graph between the two plans:

- **API Phase 0** consumes pipelex items **#1, #2, #3** (Stage 1 of `pipelex-changes.md`). Land first.
- **API Phase 1** consumes pipelex item **#4** (Stage 2). Land before API Phase 1.
- **API Phase 4a** consumes pipelex item **#5** (Stage 3). Land before API Phase 4.
- API Phases 2-3, 4b, 5 have no hard pipelex dependencies — they can run in parallel with the remaining pipelex stages.

If pipelex stages 1-3 progress on schedule, API Phase 0 can start as soon as items #1, #2, #3 are merged. If they slip, API Phase 0 can still proceed by humanizing class names locally (the original A4 fallback) and consuming the upstream class attributes once they land — small migration cost, no hard block.

**Audit trail — original findings (for future review consistency):**
- A1: `api/middleware.py:13-22`, `api/routes/storage.py:157-163`, `:173-179` bypass the 4xx helpers.
- A3: `pipelex/pipe_run/delivery_executor.py:238 _notify_webhook` composes `{pipeline_run_id, status, result_url}` with no `error` field; `recover_error_report` is not called.
- Q1: `_decode_body` and `_validate_extras` in `api/routes/pipelex/pipeline.py` would otherwise need a `Request` parameter; contextvar approach keeps them pure.
