# Implementation Plan — Error Handling

This is the implementation plan for the error-handling design captured in `wip/error-handling/`. Work proceeds in phases. Each phase is a coherent unit of work. Four checkpoints mark mandatory stop points where the agent gathers state into this document before any next-session work begins.

**The design is settled.** Do not relitigate. If something needs to change, update `wip/error-handling/*.md` first, then come back here.

---

## Status (update at every checkpoint)

- **Current phase:** Phase 3 complete, sync path complete — **but see the review questions below before Phase 4**
- **Last checkpoint reached:** C
- **Next checkpoint:** Checkpoint D — end of Phase 4
- **Before anything else:** triage the "Phase 3 review — open questions" section immediately below.
- **Branch:** `feature/Adapt-to-pipelex-update` (error-handling work continues on this branch — prior `feat(error-handling)` commits already live here)

## ⚠️ Before anything else — Phase 3 review, open questions

A multi-agent code review ran at the end of Phase 3, **while the context window was heavily loaded** — so treat everything here as *leads to verify with fresh eyes*, not as confirmed defects. Some may be real bugs, some may be intended behavior that just needs a comment, some may be wrong. **Do this first, before starting Phase 4:** work through each question, decide one of {fix it / document it as intended / dismiss it as a non-issue}, and record the verdict. Then delete this section (or fold any survivors into a real Phase). All Phase 3 verification (`make c`, `make tp`, `make gha-tests`, manual `make run`) was green — none of these block a running server; they are correctness/contract/observability questions.

Phase 3 commit under review: `7b758f6`. Diff: `git diff HEAD~1` (while it is still `HEAD`).

1. ~~**Did narrowing the `/validate` dry-run catch introduce a regression?**~~ Phase 3 changed the best-effort dry-run block in `api/routes/pipelex/validate.py` from `except ENDPOINT_HANDLED_EXCEPTIONS` (`PipelexError, TemporalError, ValueError, TypeError, RuntimeError`) to `except PipelexError`. Can `dry_run_pipeline` raise a non-`PipelexError` (a `KeyError`/`AttributeError`/`TypeError` — pipelex's `assemble_graph_on_output` reportedly lets those propagate by design)? If yes, a request that used to return 200 (validated bundle, no graph) now 500s. Should this best-effort path catch the wider tuple again?

   **✅ RESOLVED (2026-05-22) — verdict: document as intended. The catch stays `except PipelexError`. No regression to fix; do NOT re-widen.**

   Traced the full `dry_run_pipeline` exception surface against the pinned pipelex (`../_for_api`):
   - A non-`PipelexError` *can* escape `dry_run_pipeline`, but via exactly one path: a **successful** dry run whose graph assembly hits a pipelex programming bug. `PipeRun.run()` calls `assemble_graph_on_output` in a `finally` block; that function catches only `(OSError, JSONDecodeError, ValidationError, PipelexConfigError, MissingDependencyError)` and — per its own docstring — *deliberately lets `KeyError`/`AttributeError`/`TypeError` propagate "so they surface during development."* `runner.execute_pipeline` catches only `PipeRouterError`/`PipelexError`/`ValidationError`, so such a bug escapes unwrapped.
   - Every other call site is clean: `make_pipelex_bundle_blueprint` wraps all failures in `PipelexInterpreterError` (a `PipelexError`); `make_pipe_ref_with_domain` is string concat; `PipelexRunner.__init__` is field assignment; `with_graph_config_overrides` uses `model_copy(update=...)`, which does **not** validate in Pydantic v2 (verified) — no `ValidationError`. `dry_run_pipeline`'s own empty-`mthds_contents` `ValueError` is unreachable from `/validate` (the route model is `min_length=1`). `TemporalError` cannot arise — a dry run uses a plain `PipelexRunner` / `PipeRunMode.DRY` with no Temporal client.
   - Net behavior change from the narrowing: a graph-assembly bug surfacing as a bare `TypeError`/`RuntimeError`/`ValueError` (incl. `pydantic.ValidationError`, which subclasses `ValueError`) used to be swallowed (200, no graph) and now 500s. `KeyError`/`AttributeError` were never in `ENDPOINT_HANDLED_EXCEPTIONS` — they 500'd before *and* after; no change there.

   Why this is correct, not a regression: **(a)** pipelex *designed* `assemble_graph_on_output` to let programming bugs propagate — swallowing them API-side defeats that design; **(b)** the project error-handling rules ("crashing loudly on an unexpected exception is the desired behavior; it hides bugs") forbid the broad `ValueError`/`TypeError`/`RuntimeError` catch the old tuple gave — removing it *was* Phase 3's mandate; **(c)** the old tuple was already incoherent — it caught `TypeError`/`RuntimeError`/`ValueError` graph-assembly bugs but not `KeyError`/`AttributeError`; the narrowing makes *all* graph-assembly programming bugs consistently 500, fixing a half-catch; **(d)** the legitimate best-effort case (`mock_inputs` can't satisfy a pipe) raises a `PipelexError` subclass and is still caught → still 200 with the validated bundle and no graph, so the endpoint contract holds; **(e)** re-widening cannot even reach "never fail on graph problems" without `except Exception` (the realistic bug types are `KeyError`/`AttributeError`), which the project rules ban and which is redundant with the global `Exception` handler.

   Action taken: refined the `except PipelexError` comment in `api/routes/pipelex/validate.py` so the rationale is precise (the old comment's blanket "dry-run failures are PipelexError subclasses" is exactly what triggered this question). No code-behavior change.

2. ~~**Should `RequestValidationError` get an RFC 7807 handler?**~~ FastAPI's automatic request-body validation (`extra="forbid"`, `max_length`/`min_length`, missing/mistyped fields on `/upload`, `/resolve-storage-url`, `/validate`, `/build/*`) raises `RequestValidationError`, which still hits FastAPI's default handler → `{"detail": [...]}` / `application/json`, **not** RFC 7807. Is the Phase 5 changelog claim "RFC 7807 across every endpoint" — and the docstring of `tests/unit/test_error_responses.py` — accurate while this is true? Should `register_exception_handlers` also register a `RequestValidationError` handler?

   **✅ RESOLVED (2026-05-22) — verdict: fix it. Registered a global `RequestValidationError` handler; FastAPI's automatic-validation 422 now emits RFC 7807. Landed in this commit.**

   The premise is correct, verified against the running app. `FastAPI.__init__` registers a built-in `RequestValidationError` handler; `app.add_exception_handler(Exception, ...)` does **not** override it — Starlette resolves handlers most-specific-first, and `RequestValidationError` is more specific than `Exception`. Confirmed empirically before the fix: `POST /api/v1/validate` with `{}`, `{"mthds_contents": []}`, or `{"mthds_contents": "notalist"}` all returned `422` / `application/json` / `{"detail": [{...}]}`.

   The affected surface is broad — every route that declares a Pydantic body model and leans on FastAPI's automatic validation: `/validate`, `/upload`, `/resolve-storage-url`, `/build/{inputs,output,runner}`, `/build/{concept,pipe-spec}`. Every `extra="forbid"` / `min_length` / `max_length` breach, every missing/mistyped field, every `field_validator` raising `ValueError` (`_bound_each_file`, `_bound_spec_size`), and malformed JSON funnel through `RequestValidationError`. The pipeline routes (`/pipeline/{execute,start}`) are **not** affected — `_parse_request` deserializes their bodies explicitly and already maps failures to `raise_validation_error`.

   This was not a Phase 3 regression — `RequestValidationError` always went to FastAPI's default. But it **is** a genuine contract hole: on a single endpoint a client could get RFC 7807 for one validation failure (`raise_bad_request` / `raise_validation_error`) and the FastAPI `{"detail": [...]}` envelope for another (`RequestValidationError`) — incoherent. The Phase 5 changelog commits to "RFC 7807 across **every** API endpoint" and `test_error_responses.py`'s docstring claims "every API error path emits one RFC 7807 shape" — **both were inaccurate while this stood.** The design doc's "What about `ValidationError`" section in `track-exception-handlers.md` only ever addressed the *explicit* `model_validate` catches (`agent/concept.py`'s inner `spec` dict); it never covered FastAPI's *automatic* outer-model validation — an unaddressed design gap, not a settled decision.

   Why fix, not document-as-intended: "RFC 7807 across every endpoint" is the settled design intent — and Phase 3 reconciliation finding #2 set the exact precedent (A1's under-listed file set was completed precisely to honor "every endpoint"). CLAUDE.md's "no backward compatibility" + "Solid over quick" + "flag and fix existing bugs" all point the same way.

   Action taken:
   - `api/main.py`: added `handle_request_validation_error` + `_summarize_request_validation_error`; `register_exception_handlers` now registers `RequestValidationError` (overriding FastAPI's built-in). It renders via `build_problem_document_from_api_error` → `error_type = "ValidationError"`, `error_domain = "input"`, `status = 422`, `application/problem+json` — identical on the wire to an explicit `raise_validation_error`. `detail` is a human-readable summary of the per-field failures (`<loc>: <msg>`, joined). No structured per-field `errors` member is added — that would diverge from `raise_validation_error`, which has none; shape uniformity wins.
   - `wip/error-handling/track-exception-handlers.md`: added a `RequestValidationError` subsection so the design reflects the handler.
   - Tests: added `test_request_validation_error_is_rfc7807` to `tests/unit/test_error_responses.py`; tightened the status-only 422 tests on this surface — across `test_uploader.py`, `test_storage.py`, and `test_build_and_agent_routes.py` — to assert `content-type` + `error_type`. This also discharges the `RequestValidationError`-surface portion of question 12 below.

   `make fui && make c && make tp` clean. With the handler in place, the Phase 5 "RFC 7807 across every endpoint" changelog claim and the `test_error_responses.py` docstring are now accurate.

3. **Should the `HTTPBearer` missing-header rejection be migrated?** `api/security.py` uses `security = HTTPBearer()` (default `auto_error=True`). A missing/malformed `Authorization` header makes `HTTPBearer` itself raise a plain `HTTPException` *before* the migrated `verify_jwt`/`verify_api_key` code runs → old `{"detail": ...}` shape. Does the A2 "one auth-error shape" goal require `HTTPBearer(auto_error=False)` + `raise_unauthenticated`?

4. **Should malformed-spec builder errors be 422 instead of 500?** `parse_pipe_spec` (invalid `pipe_type`) and `parse_concept_spec` (non-dict `structure`) reportedly raise bare `ValueError`/`AttributeError`/`TypeError` — not `ValidationError`, not `PipelexError` — so they escape the `except ValidationError` in `agent/concept.py` / `agent/pipe_spec.py` and become an opaque 500. Caller mistakes → should be 422. Catch specifically at the route, or file as a pipelex-wrapping gap? (The Phase 3 catch-site audit was meant to settle this class — was it missed?)

5. **Do pipelex storage providers actually wrap all backend errors?** Phase 3 deleted `STORAGE_HANDLED_EXCEPTIONS` on the assumption (now a code comment) that storage failures surface as a pipelex `StorageError`. A review claims `LocalStorageProvider._store` leaks raw `OSError` (disk-full, permission) and `S3StorageProvider` leaks non-`ClientError` `BotoCoreError` (read/connect timeouts). Verify against the pinned pipelex. If true, the audit step says to file these in `wip/error-handling/pipelex-changes.md` — should new items be added?

6. **Is `build/runner.py`'s library teardown actually guaranteed?** `build_runner` calls `open_library()` / `set_current_library()` *outside* the `try`, so the `finally` is skipped if `set_current_library` raises — yet the Phase 3 docstring claims "the try/finally guarantees the library is torn down on both paths". `build/inputs.py` and `build/output.py` use the safer `library_id: str | None = None` + open-inside-`try` pattern. Should `runner.py` match them, or is the docstring claim simply wrong and the window negligible?

7. **Should API-authored problem documents carry `error_category` / `retryable`?** `build_problem_document_from_api_error` omits both, but pipelex `ErrorReport` documents *and* the catch-all `handle_unexpected_error` 500 include them — so two API 500s have different field sets. A 422 is meaningfully `retryable: false`. Is the `problem_document.py` docstring claim "shape-identical to a pipelex one on the wire" accurate? Should the API documents add `retryable` (and maybe `error_category`) for a uniform contract?

8. **Should API-authored 500s be logged?** `handle_api_error` emits no log line, and `api/routes/version.py`'s `raise_internal_server_error` sites do not log before raising — so a `/pipelex_version` 500 produces *zero* operator log output. (`security.py` / `storage.py` 500 sites still log first.) Should `handle_api_error` emit a structured `event=api_error` line, at least for 5xx?

9. **Is dropping per-route `user_id` / `key` log context acceptable?** The deleted storage `except` blocks logged `user=… key=…` on upload/storage-backend failures; the global handlers log only `route` + `request_id`. Is losing that correlation acceptable, or should the routes / global handler re-add it? (Checkpoint B reconciliation #4 already noted body-derived log fields as a deferred follow-up — is this the same item?)

10. **Should `_decode_body` catch more than `(UnicodeDecodeError, ValueError)`?** `kajson.loads` reconstructs typed objects from `__class__`/`__module__` markers and can raise `ModuleNotFoundError`/`ImportError`/`AttributeError`/`TypeError` on a crafted body → escapes to a 500 instead of a 422. Widen the catch? Separately: is kajson instantiating arbitrary classes from an untrusted request body a security concern worth its own flag? (Pre-existing — not introduced by Phase 3 — but surfaced by the review.)

11. **Is `/validate`'s legacy result envelope intended?** `/validate` still returns `{"success": false, "mthds_contents": …, "message": …}` for its 422 (`ValidateBundleError`) and 400 (no `main_pipe`) — not RFC 7807. Is this a deliberate "validation-result payload" (in which case add a comment saying so), or an endpoint the migration missed?

12. **Are the 422-path tests strong enough?** Several tests (`test_extra_fields_rejected`, `test_uri_too_long_rejected`, the oversized/empty-input tests, `test_api_key_missing_header_rejected`) assert only the status code, so they pass regardless of body shape and give no coverage of the migration on exactly the surfaces in questions 2 and 3. Tighten them (assert `content-type` + `error_type`) once 2/3 are decided?

    **Partially addressed (2026-05-22, via question 2):** every `RequestValidationError`-surface 422 test was tightened to assert `content-type` + `error_type` when question 2 landed — `test_extra_fields_rejected` (in `test_uploader.py` and `test_storage.py`), `test_uri_too_long_rejected`, `test_empty_mthds_contents_rejected`, and the oversized 422 tests in `test_uploader.py` and `test_build_and_agent_routes.py`. **Still open for this question:** `test_api_key_missing_header_rejected` (gated on question 3) and any remaining status-only auth-path 422 tests not on the `RequestValidationError` path.

13. **Is coupling test collection to `api.main` import acceptable?** Many test modules now do `from api.main import register_exception_handlers`; importing `api.main` runs `resolve_disclosure_mode()` at module load, which raises on a bad `ERROR_DISCLOSURE` env value — crashing collection of all of them at once. Fine as fail-fast, or should `register_exception_handlers` move to a thinner module?

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

- [x] In `api/main.py`, register handlers via `app.add_exception_handler(...)` or the decorator form. Pick one form and use it consistently. *(Landed: `register_exception_handlers(app)` calls `app.add_exception_handler(...)` for all three — one function shared by the production app and the Phase 2 tests.)*
- [x] Handler for `pipelex.base_exceptions.PipelexError`:
    - [x] Calls `exc.to_error_report()`.
    - [x] Calls `build_problem_document(...)` with `instance = request.url.path`, `request_id = request.state.request_id`, `disclosure_mode` from env. *(`request_id` read via `getattr(request.state, "request_id", None)` so a request that never hit the middleware degrades gracefully; `disclosure_mode` from the `api.main.ERROR_DISCLOSURE_MODE` module global, read at call time.)*
    - [x] Builds a `JSONResponse` with `media_type="application/problem+json"`, `status_code = report.http_status`.
    - [x] Emits `Retry-After: <int(ceil(retry_after_seconds))>` when `report.provider_metadata` exists and has `retry_after_seconds`.
    - [x] Emits the `X-Request-ID` header (the middleware already does it, but the handler must not strip it). *(The handler sets no `X-Request-ID`; `RequestIdMiddleware`'s send wrapper adds it to every response — confirmed on the real app for both the success and 500 paths.)*
    - [x] Emits a structured log entry. Level is `error` by default and `warning` when `report.error_domain == ErrorDomain.INPUT`. See observability fields list in `wip/error-handling/track-observability.md`.
- [x] Handler for `temporalio.exceptions.TemporalError`:
    - [x] Authors an `ErrorReport` directly — `error_type = "TemporalTransportError"`, `error_domain = "runtime"`, `error_category = "transient"`, `retryable = true`, `status = 500` — and funnels it through the same `_problem_response` path as the `PipelexError` handler. *(Built a real `ErrorReport`, not a hand-rolled dict, so render + log are shared code.)*
    - [x] Same response shape, headers, and structured log as the `PipelexError` handler.
    - [x] **Open question — resolved:** pipelex's `WorkflowExecutionError` IS a `PipelexError` (via `TemporalFlowError`) and is NOT a `temporalio.TemporalError`, so the more-specific `PipelexError` handler catches it first. Documented in the `handle_pipelex_error` / `handle_temporal_error` docstrings; covered by the T4 test.
- [x] Handler for `Exception` (fallback catch-all):
    - [x] Logs at `error` level with the full traceback. Includes `error_type = type(exc).__name__` in the log structured fields, but **not** in the response body.
    - [x] Returns a fixed sanitized problem document: `type = "https://docs.pipelex.com/latest/errors/internal-server-error/"`, `title = "Internal server error"`, `status = 500`, `detail = "An unexpected error occurred. The request id is included for support."`, `error_type = "InternalServerError"`, `error_domain = "runtime"`, `retryable = false`, `request_id`. One-line comment justifies the `except Exception` equivalent.
- [x] Wire the log emission. *(The pipelex `log` object renders a single message string, not indexed key/value fields — `_emit_error_log` flattens the field map to a greppable `key=value` run. Degraded-by-design, plan-sanctioned; see the followup note under "What landed".)*
- [x] Integration tests in `tests/unit/test_exception_handlers.py` — one `TestClass`, a throwaway app carrying the production handlers + `RequestIdMiddleware` so the tests mirror production:
    - [x] `PipelexError` subclass produces a problem+json response with correct fields.
    - [x] `EnvVarNotFoundError` → 500, `detail` carries the env var name (verbose mode). *(Reconciliation #1: `EnvVarNotFoundError` is domain-less — the test asserts `error_domain` is **absent**, not `"config"`; `PipelexConfigError` covers a genuine `error_domain = "config"`.)*
    - [x] `ERROR_DISCLOSURE=strict` redacts `detail` for a non-caller-facing error, preserves a caller-facing one. *(Reconciliation: STRICT keys on the `caller_facing_message` flag, not `error_domain`; the test pairs a `CONFIG` error — redacted — with an `INPUT` caller-facing error — preserved.)*
    - [x] Simulated `LLMCompletionError` with `provider_metadata.retry_after_seconds = 12.0` → 429, `Retry-After: 12` header.
    - [x] `Exception` subclass triggers the fallback handler → 500 with `error_type = "InternalServerError"`, response body does NOT include the original exception class name.
    - [x] **TemporalError dispatch (T4):** `WorkflowExecutionError` (a `PipelexError` subclass) goes through the `PipelexError` handler, NOT the `TemporalError` handler. A non-`PipelexError` `TemporalError` subclass goes through the dedicated `TemporalError` handler with the synthetic transport-transient classification.
    - [x] **Handler-of-handlers (T3):** a `PipelexError` subclass whose `to_error_report()` itself raises; the `Exception` fallback catches it and returns a sanitized 500 — not FastAPI's default bodyless 500.
    - [x] `X-Request-ID` is present on every response (parametrized across all error routes).
    - [x] Inbound `X-Request-ID` is echoed (proves the middleware integration).
- [x] `make fui && make c && make tp` clean.

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

**Files modified:**

- `api/main.py` — added the three global exception handlers, their helpers (`_request_id_of`, `_retry_after_header`, `_emit_error_log`, `_log_error_report`, `_problem_response`), and `register_exception_handlers`; called `register_exception_handlers(fastapi_app)` after router registration. The existing app init, CORS / body-size middleware, and router registration are unchanged.

**Files created:**

- `tests/unit/test_exception_handlers.py` — integration tests over a throwaway app carrying the production handlers + `RequestIdMiddleware`.

**Public API surface added (`api.main`):**

- `PROBLEM_JSON_MEDIA_TYPE` — the `application/problem+json` content-type constant.
- `handle_pipelex_error`, `handle_temporal_error`, `handle_unexpected_error` — the three handlers (module-level so the tests register them on a test app).
- `register_exception_handlers(app: FastAPI) -> None` — registers all three; called by `api.main` for the production app and by the Phase 2 tests.

**Behavior:**

- `PipelexError` (incl. `WorkflowExecutionError`) → `to_error_report()` → RFC 7807 `application/problem+json`, status from `report.http_status` (provider-429 passthrough included), `Retry-After` header when the provider supplied a hint, structured log (`warning` for `INPUT` domain, `error` + traceback otherwise).
- bare `temporalio.TemporalError` → API-authored `ErrorReport` (`TemporalTransportError`, `runtime` / `transient` / retryable) → same render + log path.
- anything else → sanitized 500 problem document; real class name + traceback go to the log only, never the body.

**Verification:** `make fui` clean; `make c` clean (ruff format + lint, pyright 0 errors, mypy success); `make tp` — 147 passed. Manual real-server check (`uvicorn api.main:app`): `GET /` → 200 with `X-Request-ID`; `POST /api/v1/pipeline/execute` with `{}` → the global fallback fired on the real app — `500 application/problem+json`, sanitized body, `request_id` echoing the `X-Request-ID` header, plus a structured `event=api_error error_type=PipelineRequestError error_category=unknown error_domain=runtime status=500` log line with traceback.

**Checkpoint B step 3 — dual-state confirmed (expected):** the per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` blocks are still in place, so a `PipelexError` raised *inside* a route's try (e.g. `/pipeline/start` missing `COMPLETION_CALLBACK_SECRET`) is still caught per-route and still returns the old `{"detail": {...}}` shape — confirmed on the real app via the malformed-JSON 422 (`raise_validation_error`, old shape). The new global handlers fire only for failures that escape a per-route catch (e.g. `_parse_request` raising before the route's try — the `{}` case above). Phase 3 deletes the per-route catches; nothing was reordered or hacked.

**Reconciliation findings — read before Phase 3:**

1. **`mthds.client.exceptions.PipelineRequestError` escapes as a generic 500.** `_parse_request` → `PipelineRequest.from_body(...)` raises `PipelineRequestError` (an `mthds` package error, NOT a `PipelexError`) on a malformed/empty body. It is genuinely an `INPUT` error but currently funnels to the `Exception` fallback as a sanitized 500. Phase 3's catch-site audit should catch it in `_parse_request` and call `raise_validation_error(...)` (422) — handle it under the Phase 3 `ValueError`/`TypeError`/`RuntimeError` audit step.
2. **Structured logging is degraded by design (followup).** The pipelex `log` object renders a single message string, not indexed key/value fields. `_emit_error_log` flattens the field map to a greppable `key=value` run — the plan explicitly sanctioned this ("accept a degraded rendering, file a followup"). **Followup:** when a JSON log sink lands, swap `_emit_error_log`'s string rendering for native structured emission. No API behavior depends on the rendering.
3. **`disclosure_mode` is a module global, read at call time.** The handlers read `api.main.ERROR_DISCLOSURE_MODE` dynamically (not captured at registration), so a test overrides it with `mocker.patch("api.main.ERROR_DISCLOSURE_MODE", ...)`.
4. **Per-request-body observability fields deferred.** `user_id` / `pipe_code` / `pipeline_run_id` (observability track "when known") need request-body context the global handler does not have. The structured log carries the report-derived + correlation fields (`event`, `request_id`, `route`, `error_type`, `error_category`, `error_domain`, `retryable`, `status`, `provider`, `model`, `provider_status_code`, `provider_request_id`). Enriching with body-derived fields is a natural Phase 3/5 follow-up.

**Deferred to later phases (as planned):** Phase 3 strips the per-route try/except blocks, deletes the catch tuples + `raise_internal_error`, and migrates the 4xx / inline-`HTTPException` sites to RFC 7807. Phase 4 wires the Temporal webhook recovery.

### Cold-start prompt template for Phase 3

> Pick up the error-handling implementation. Read `TODOS.md` (the status block, "What landed" under Checkpoint B) and `wip/error-handling/track-exception-handlers.md` — specifically the "Route cleanup" section. Phase 3 is to strip the per-route try/except blocks across `api/routes/`, delete the `ENDPOINT_HANDLED_EXCEPTIONS` / `STORAGE_HANDLED_EXCEPTIONS` tuples and `raise_internal_error` from `api/errors.py`, and migrate the API-owned 4xx helpers to emit RFC 7807. Follow the Phase 3 checklist in `TODOS.md`.

---

## Phase 3 — Route cleanup, 4xx helper migration, inline-HTTPException migration

The subtraction phase. Risky because it touches every route file; the global handler must be in place and working (Checkpoint B verified) before this phase runs.

**Scope note (review amendment A1 + A2):** This phase also migrates direct `HTTPException` sites that today bypass the 4xx helpers and emit the old `{"detail": {...}}` shape — `api/middleware.py` `_too_large_response()`, `api/routes/storage.py:157-163` (403 ownership), `api/routes/storage.py:173-179` (500 presign), and the seven `HTTPException` sites in `api/security.py` (5×401, 2×500). The design originally punted on auth migration as a separate track; per CLAUDE.md's "no backward compatibility" rule, we migrate it here instead so clients see one error shape everywhere. RFC 7807 supports the `WWW-Authenticate: Bearer` header — moving the body to `application/problem+json` does not break the auth challenge.

- [x] Strip per-route `try / except ENDPOINT_HANDLED_EXCEPTIONS` blocks in:
    - [x] `api/routes/pipelex/pipeline.py` (both `/execute` and `/start`)
    - [x] `api/routes/pipelex/validate.py` *(the dry-run block is a genuine best-effort recover-and-continue, not error shaping — kept, but narrowed `except ENDPOINT_HANDLED_EXCEPTIONS` → `except PipelexError`.)*
    - [x] `api/routes/pipelex/build/inputs.py` *(kept `try/finally` for library teardown — the sanctioned cleanup pattern.)*
    - [x] `api/routes/pipelex/build/output.py` *(kept `try/finally`.)*
    - [x] `api/routes/pipelex/build/runner.py` *(kept `try/finally`.)*
    - [x] `api/routes/pipelex/agent/concept.py` *(kept the `except ValidationError` — API-owned 422 — migrated to `raise_validation_error`.)*
    - [x] `api/routes/pipelex/agent/pipe_spec.py` *(same as concept.py.)*
    - [x] `api/routes/pipelex/agent/models.py`
- [x] Strip per-route `try / except STORAGE_HANDLED_EXCEPTIONS` blocks in:
    - [x] `api/routes/uploader.py`
    - [x] `api/routes/storage.py`
    - [x] Decision per design: trust pipelex's wrapping; if `OSError`/`BotoCoreError`/`ClientError` ever leak, the `Exception` fallback catches them and the leak is a pipelex bug. Do **not** keep a narrow catch defensively.
- [x] Audit `ValueError` / `TypeError` / `RuntimeError` catch sites for each route. For each occurrence:
    - [x] If pipelex should wrap it but doesn't → append to `wip/error-handling/pipelex-changes.md`. *(One new item filed: #10 — `EnvVarNotFoundError` should carry `error_domain = CONFIG`.)*
    - [x] If the API boundary itself raises it (e.g. Pydantic coercion at the route) → catch the **specific** exception (not the union) and call `raise_validation_error(...)`. *(`_parse_request` now catches `PipelineRequestError` / `ValidationError` from `PipelineRequest.from_body`; `models.py` already caught `ValueError` from `ModelCategory(...)` specifically; `concept.py` / `pipe_spec.py` keep their `except ValidationError`.)*
    - [x] Otherwise → let it fall through to the `Exception` fallback. Document the call site in `pipelex-changes.md`. *(No genuinely-unknown leak sites remained after the audit.)*
- [x] In `api/errors.py`:
    - [x] Delete `ENDPOINT_HANDLED_EXCEPTIONS` and `STORAGE_HANDLED_EXCEPTIONS`.
    - [x] Delete `raise_internal_error`.
    - [x] Update `raise_validation_error`, `raise_bad_request`, `raise_payload_too_large` to emit RFC 7807 problem documents via `build_problem_document_from_api_error`.
        - [x] Helpers read `request_id` and `route_path` from the Phase 0 contextvars (`api/logging_context.py`) — **no signature change** (review amendment Q1). Call sites are unchanged.
        - [x] Status code stays the same (422 / 400 / 413).
        - [x] Body shape changes from `{"detail": {"error_type", "message"}}` to RFC 7807 problem document.
        - [x] Response `Content-Type` is `application/problem+json`. *(Carried on a new `ApiError` exception + `handle_api_error` global handler — `HTTPException`'s default handler cannot emit a flat problem document; see the "What landed" note.)*
    - [x] Add new helpers (review amendments A1 + A2):
        - [x] `raise_forbidden(message: str, error_type: ErrorType = ErrorType.FORBIDDEN) -> NoReturn` — 403 RFC 7807. Used by `api/routes/storage.py` ownership-mismatch.
        - [x] `raise_internal_server_error(message: str, error_type: ErrorType) -> NoReturn` — 500 RFC 7807 for API-owned 500s (NOT for pipelex domain errors, which go through the global handler). Used by `api/routes/storage.py` presign-failure, `api/routes/version.py`, and `api/security.py` SERVER_MISCONFIGURED cases.
        - [x] `raise_unauthenticated(message: str, error_type: ErrorType = ErrorType.UNAUTHENTICATED) -> NoReturn` — 401 RFC 7807 with `WWW-Authenticate: Bearer` header. Used by all `api/security.py` 401 sites. (RFC 7807 fully supports the challenge header — moving the body to `application/problem+json` does not break OAuth/JWT clients that parse the `WWW-Authenticate` header.)

### A1 — migrate inline `HTTPException` sites

- [x] `api/middleware.py` `_too_large_response()`: builds an RFC 7807 problem document inline via `build_problem_document_from_api_error` (the middleware must `return` a response, not raise, so it cannot use the `raise_*` helpers). `Content-Type: application/problem+json`. `X-Request-ID` is stamped by `RequestIdMiddleware`'s send wrapper on every response.
- [x] `api/routes/storage.py` (403 ownership mismatch): replaced direct `HTTPException(...)` with `raise_forbidden(...)`.
- [x] `api/routes/storage.py` (500 presign-failure): replaced direct `HTTPException(...)` with `raise_internal_server_error(...)`.
- [x] `api/security.py` — migrated all 7 `HTTPException` sites (review amendment A2):
    - [x] `verify_jwt`, 500 missing `JWT_SECRET_KEY` → `raise_internal_server_error(..., error_type=ErrorType.SERVER_MISCONFIGURED)`.
    - [x] `verify_jwt`, 401 missing `user_id` claim → `raise_unauthenticated(..., error_type=ErrorType.INVALID_TOKEN)`.
    - [x] `verify_jwt`, 401 `user_id` not a UUID → `raise_unauthenticated(..., error_type=ErrorType.INVALID_TOKEN)`.
    - [x] `verify_jwt`, 401 expired token → `raise_unauthenticated(..., error_type=ErrorType.TOKEN_EXPIRED)`.
    - [x] `verify_jwt`, 401 invalid token → `raise_unauthenticated(..., error_type=ErrorType.INVALID_TOKEN)`.
    - [x] `verify_api_key`, 500 missing `API_KEY` → `raise_internal_server_error(..., error_type=ErrorType.SERVER_MISCONFIGURED)`.
    - [x] `verify_api_key`, 401 key mismatch → `raise_unauthenticated(..., error_type=ErrorType.INVALID_TOKEN)`.
    - [x] Confirmed `WWW-Authenticate: Bearer` is emitted on every 401 — asserted in `test_security_verifiers.py`, `test_storage.py`, `test_uploader.py`, `test_error_responses.py`, and verified on the real server.
- [x] **Beyond A1's enumeration:** A1 named only the storage + middleware sites, but the Phase 5 changelog commits to RFC 7807 "across **every** API endpoint". The audit found three more files with inline `HTTPException` on the old shape — `api/routes/uploader.py` (401/400/413), `api/routes/storage.py` (401/400), `api/routes/version.py` (2×500) — all migrated to the helpers so the contract is uniform. See the "What landed" note.
- [x] Updated existing tests on these paths to assert the new RFC 7807 shape.
- [x] Updated every test asserting the old `{"detail": {...}}` shape to assert the new RFC 7807 shape.
- [x] Updated the module docstring at the top of `api/errors.py` (and `api/error_types.py`) to describe the new world.
- [x] e2e / regression coverage (landed in `tests/unit/` as TestClient integration tests, consistent with the existing suite — there is no real-Temporal e2e infra in this repo):
    - [x] `/pipeline/start` original-bug class verified: a `PipelexError` raised inside `pipeline_run_setup` propagates through the cleaned route to the global handler → RFC 7807 500 (confirmed on the real server with `PipeNotFoundError`; `EnvVarNotFoundError` shares that exact handler path and is covered by `test_exception_handlers.py` + `test_problem_document.py`).
    - [x] `/pipeline/execute` with an invalid pipe code → pipelex error propagates correctly (real-server check).
    - [x] `raise_validation_error` path returns RFC 7807 with `error_domain = "input"` (`test_error_responses.py`).
    - [x] Storage route happy paths still work — no regression from removing `STORAGE_HANDLED_EXCEPTIONS` (`test_storage.py`, `test_uploader.py`).
    - [x] **REGRESSION T1**: a request body > `MAX_REQUEST_BODY_BYTES` returns 413 `application/problem+json` with `X-Request-ID` (`test_error_responses.py`).
    - [x] **REGRESSION T2**: storage 403 ownership-mismatch returns RFC 7807 with `X-Request-ID` (`test_error_responses.py`).
    - [x] **REGRESSION T5**: every error-emitting route includes `X-Request-ID` — asserted across the 422/400/403/413/500/401 cases in `test_error_responses.py`.
- [x] **REGRESSION — old-shape audit**: `grep -rn '"detail"' tests/` enumerated and updated. Files updated: `test_pipeline_routes.py`, `test_build_and_agent_routes.py`, `test_storage.py`, `test_uploader.py`, `test_security_verifiers.py`, `test_simple_routes.py`. (`test_problem_document.py` and `test_exception_handlers.py` already used `detail` as the RFC 7807 field — unchanged.)
- [x] `make fui && make c && make tp` clean — 160 passed.
- [x] `make gha-tests` clean — 160 passed.

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

**The shape of the change:** every API error response — pipelex domain errors, API-authored 4xx, auth 401/403, the 413 payload limit, the catch-all 500 — is now a single RFC 7807 `application/problem+json` document. The old `{"detail": {"error_type", "message"}}` envelope is gone from the entire surface. Per-route `try/except` error-shaping is gone; routes call into pipelex and let exceptions propagate to the four global handlers.

**Files modified — `api/`:**

- `api/errors.py` — full rewrite. Deleted `ENDPOINT_HANDLED_EXCEPTIONS`, `STORAGE_HANDLED_EXCEPTIONS`, `raise_internal_error`. Added the `ApiError` exception class and `_raise_api_error`; `raise_validation_error` / `raise_bad_request` / `raise_payload_too_large` now emit RFC 7807; added `raise_forbidden` (403), `raise_unauthenticated` (401 + `WWW-Authenticate: Bearer`), `raise_internal_server_error` (500). Module docstring rewritten.
- `api/problem_document.py` — added the `PROBLEM_JSON_MEDIA_TYPE` constant (moved here from `api/main.py` so middleware can use it without a circular import); added an `error_domain: ErrorDomain | None = ErrorDomain.INPUT` parameter to `build_problem_document_from_api_error` so an API-owned 500 can classify `CONFIG`.
- `api/main.py` — added `handle_api_error` and registered it as a fourth global handler (`register_exception_handlers` now wires `ApiError` → `PipelexError` → `TemporalError` → `Exception`). Imports `PROBLEM_JSON_MEDIA_TYPE` from `api.problem_document`.
- `api/middleware.py` — `_too_large_response()` builds an RFC 7807 document inline.
- `api/security.py` — all 7 `HTTPException` sites migrated to `raise_unauthenticated` / `raise_internal_server_error`; dropped the now-unused `HTTPException` / `status` imports.
- `api/error_types.py` — module docstring updated (no more `{"detail": {...}}`, no `raise_internal_error`).
- `api/routes/pipelex/pipeline.py` — stripped both per-route `try/except` blocks; `_parse_request` now catches `PipelineRequestError` / `ValidationError` from `PipelineRequest.from_body` and maps them to a 422 (fixes Checkpoint B reconciliation #1 — those used to escape as a sanitized 500).
- `api/routes/pipelex/validate.py` — dry-run best-effort catch narrowed `ENDPOINT_HANDLED_EXCEPTIONS` → `PipelexError`.
- `api/routes/pipelex/build/{inputs,output,runner}.py` — stripped the `except`; kept `try/finally` for library teardown.
- `api/routes/pipelex/agent/{concept,pipe_spec}.py` — stripped the `ENDPOINT_HANDLED_EXCEPTIONS` catch; kept `except ValidationError` migrated to `raise_validation_error`.
- `api/routes/pipelex/agent/models.py` — stripped the `try/except` around `list_models`.
- `api/routes/uploader.py`, `api/routes/storage.py` — stripped the `STORAGE_HANDLED_EXCEPTIONS` catches; migrated every inline `HTTPException` (401/400/413/403/500) to the helpers.
- `api/routes/version.py` — both `HTTPException` 500 sites migrated to `raise_internal_server_error`.

**Files modified — `tests/`:** `test_pipeline_routes.py`, `test_build_and_agent_routes.py`, `test_storage.py`, `test_uploader.py`, `test_security_verifiers.py`, `test_simple_routes.py` — old `{"detail": {...}}` assertions updated to the flat RFC 7807 shape; each `_build_client` helper now calls `register_exception_handlers(app)`. `test_problem_document.py` — added a test for the `error_domain` override.

**Files created:** `tests/unit/test_error_responses.py` — Phase 3 regression suite (T1 413, T2 storage-403, T5 `X-Request-ID` on every error, validation→`input`-domain, 500→`config`-domain, `WWW-Authenticate` challenge, inbound-id echo) over a production-faithful app.

**Verification:** `make fui` clean; `make c` clean (ruff format + lint, pyright 0 errors, mypy success); `make tp` — 160 passed; `make gha-tests` — 160 passed. Manual `make run` checks confirmed `application/problem+json` with `X-Request-ID` for a 422 (malformed JSON), a 422 (invalid model category), a 401 (`/upload`, with `WWW-Authenticate: Bearer`), and a 500 — the last via `/pipeline/start` with a bad pipe code, which produced `PipeNotFoundError` → RFC 7807 500 through the global `PipelexError` handler, proving the per-route catch removal works end-to-end.

**Reconciliation findings — read before Phase 4:**

1. **The 4xx/5xx helpers needed a dedicated exception + handler, not bare `HTTPException`.** FastAPI's default `HTTPException` handler wraps the body as `{"detail": <whatever>}` — it cannot emit a flat RFC 7807 document, and it does not set `application/problem+json`. So the helpers raise a new `ApiError` (carrying a pre-built problem document + status + headers) and `api/main.py` registers a fourth handler, `handle_api_error`, that renders it. This mirrors the existing 3-handler architecture and is why `register_exception_handlers` now wires four handlers.
2. **A1's file enumeration was incomplete; the full surface was migrated.** Review amendment A1 named only `api/middleware.py` and `api/routes/storage.py`. The catch-site audit found inline `HTTPException` on the old shape in three more files — `api/routes/uploader.py` (401/400/413), `api/routes/storage.py` (401/400, beyond the 403/500 A1 listed), `api/routes/version.py` (2×500). The Phase 5 changelog commits to RFC 7807 "across **every** API endpoint" and explicitly lists 401/403/413 — so all of them were migrated. This honors the settled design intent; A1 simply under-listed the files.
3. **`build_problem_document_from_api_error` gained an `error_domain` parameter.** A1/A2 added `raise_internal_server_error`, but a 500 server-config fault is not `INPUT` domain (the caller cannot fix a missing `JWT_SECRET_KEY`). The Phase 1 builder hardcoded `INPUT`; Phase 3 parameterized it (`INPUT` default for 4xx, `CONFIG` for the 500 helper). A genuine, necessary extension of Phase 1 code — not relitigation.
4. **`EnvVarNotFoundError` domain gap filed upstream.** The catch-site audit confirmed `EnvVarNotFoundError` is domain-less (Phase 1 reconciliation #1). Filed as item #10 in `wip/error-handling/pipelex-changes.md` — `EnvVarNotFoundError` should carry `error_domain = CONFIG`. Non-blocking; the API renders it correctly today (HTTP 500, `error_domain` member simply absent).
5. **`PipelineRequestError` is resolved API-side, no upstream item.** `mthds.client.exceptions.PipelineRequestError` (an `mthds`-package error, not a `PipelexError`) is raised by `PipelineRequest.from_body` on an empty/malformed body. It is a caller-input error; `_parse_request` now catches it explicitly at the API boundary and maps it to a 422 — the "API boundary should catch it" branch of the audit. No `pipelex`-library change is warranted (it is an `mthds` error, and the API boundary is the right place to classify it).
6. **e2e tests live in `tests/unit/`, not a new `tests/e2e/`.** The plan named `tests/e2e/`, but the entire existing suite is TestClient integration tests under `tests/unit/` with the Pipelex-setup autouse fixture in `tests/unit/conftest.py`. The Phase 3 regression tests are the same kind of test; they landed in `tests/unit/test_error_responses.py` to reuse that fixture and stay consistent. A true real-Temporal e2e harness does not exist in this repo — the original-bug class is verified by the manual `make run` check plus the Phase 1/2 unit coverage of the identical handler path.
7. **`pipelex-changes.md` status:** Stage 1 (#1, #2, #3) and Stage 2 (#4) are landed and consumed by Phases 0–1 — confirmed against the pinned `pipelex==0.29.1` (the `../_for_api` worktree). Item #6 (`to_problem_document`) is landed and consumed by Phase 1. Item #5 (`DeliveryExecutor.execute(error_report=...)`), the Phase 4a dependency, is marked Landed in the tracking table — Phase 4a must spec-check it. New item #10 filed (see finding 4); #8 / #9 / #10 remain open, none blocking Phase 4.

### Cold-start prompt template for Phase 4

> Pick up the error-handling implementation. Read `TODOS.md` — **first the "⚠️ Before anything else — Phase 3 review, open questions" section near the top: triage every question there (fix / document-as-intended / dismiss) before touching Phase 4** — then the status block, "What landed" under Checkpoint C, and `wip/error-handling/track-temporal-recovery.md`. The synchronous error path is complete; Phase 4 wires `recover_error_report` into the async / webhook-completion path so workflow failures reach callers with the same RFC 7807 shape. Start by mapping where the workflow-completion observer lives (pipelex's `WorkflowExecutor` vs an API-side background worker). Follow the Phase 4 checklist in `TODOS.md`.

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
