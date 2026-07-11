# Endpoint documentation overhaul — implementation plan

Goal: bring the endpoint documentation (committed OpenAPI artifact, route docstrings, docs site) to high quality and fully up to date, including the new surface coming from the pipelex codegen branch (`suggested_fix` on validation errors).

## Cold-start context

- **Repo:** `pipelex-api` (open-source FastAPI runner), branch `feature/Codegen`.
- **Pipelex pin:** `pyproject.toml` declares `pipelex==0.38.0` but `[tool.uv.sources]` overrides it to an **editable local worktree**: `pipelex = { path = "../_codegen", editable = true }`. That worktree is the pipelex repo on branch `feature/Devex-codegen`. This is intentional dev wiring for the codegen feature; the committed OpenAPI artifact must be regenerated against it so the artifact reflects what this branch's server actually serves. (Before release, the pin moves to a released pipelex — that's the release process, out of scope here.)
- **What the codegen pipelex adds to the API surface:** `ValidationErrorItem` (in `../_codegen/pipelex/base_exceptions.py`) gained an optional `suggested_fix: SuggestedFix` field. `SuggestedFix` / `FixOp` / `FixOpKind` / `FixSafety` live in `../_codegen/pipelex/suggested_fix.py` — structured, deterministic fixes (semantic TOML patch ops: `set_key`, `ensure_table`, `delete_key`, `delete_table`, `rename_table_key`; `safety: safe|unsafe`; `fix_code` kebab-case rule id; `ops[]` addressed by `table_path`). These flow into every place `validation_errors[]` appears: the `/validate` (and `/build/*`, `/resolve`, `/codegen`) invalid-verdict 200 bodies and the RFC 7807 problem documents.
- **Key commands:** `make openapi-export` (regenerate `docs/openapi/pipelex-api.openapi.yaml`), `make openapi-check` (drift gate), `make agent-check` (lint+types, silent on success), `make agent-test` (unit tests, silent on success).
- **Key files:** routes under `api/routes/` (see `CLAUDE.md` project structure); global error handlers `api/exception_handlers.py`; problem-document builder `api/problem_document.py`; API-authored error helpers `api/errors.py`; wire-contract doc `docs/error-responses.md`; docs-site endpoint catalog `docs/index.md`; committed contract `docs/openapi/pipelex-api.openapi.yaml`.

## Audit findings driving this plan (2026-07-11)

1. **Stale artifact:** `make openapi-check` fails — regenerating adds `FixOp`/`FixOpKind`/`FixSafety` component schemas (from the editable pipelex) missing from the committed YAML.
2. **Error contract misdocumented:** every error is actually rendered as RFC 7807 `application/problem+json` (global handlers — including FastAPI `RequestValidationError`), but the OpenAPI publishes FastAPI's default `HTTPValidationError` (`application/json`) 422 on nearly every route. Only `/v1/lint` and `/v1/format` opted into the correct shape via `PROBLEM_422_RESPONSE` (`api/routes/pipelex/tools.py:15`), and even that one is an untyped `additionalProperties: true` object.
3. **`/v1/execute` and `/v1/start` document no failure responses at all** (raw-`Request` body parsing means FastAPI doesn't even auto-add a 422). No route documents 401/403/409/413/429/500/501 despite `docs/error-responses.md` specifying exactly when each occurs.
4. **`docs/index.md` endpoint catalog is missing `/v1/resolve` and `/v1/codegen`** (this branch's own additions — `docs/codegen.md` exists but nothing on the index links it). `GET /` isn't mentioned anywhere.
5. **`/health` and `GET /` have no docstrings** → auto-generated stubs in the artifact ("Get Health", `Response Get Health Health Get`).
6. **Internal jargon in the published artifact:** `/v1/build/runner` description says "riding the codegen types projection (D9)" — internal plan-phase code (`api/routes/pipelex/build/runner.py:102`).
7. **Docs gaps vs the new pipelex:** `docs/error-responses.md` "Structured validation errors" table lacks `suggested_fix` (and pre-existing gap: `missing_pipe_code`). `suggested_fix` appears nowhere in `docs/`.
8. **`docs/index.md` misstates the auth requirement on `/v1/upload` + `/v1/resolve-storage-url`.** It says they "require `AUTH_MODE=api_key` or `AUTH_MODE=jwt`". Actually both handlers require an **established user identity** (`request.state.user`, checked at `api/routes/uploader.py:82` / `api/routes/storage.py:139`): (a) `AUTH_MODE=api_key` does NOT work — `verify_api_key` never sets an identity (shared key, by design per its docstring), so these routes 401 even with a valid key; (b) `AUTH_MODE=none` + `TRUST_FORWARDED_IDENTITY_HEADERS=true` + proxy-forwarded `X-User-Id` DOES work (the hosted configuration). **Remedy decided (2026-07-11): the uploader pair is a temporary feature — do not invest in documenting it. Leave existing docstrings and docs prose exactly as they are (including the "NON-CONTRACT" paragraphs); add nothing new (no per-route error responses, no temporary-framing). Sole exception: the one factually wrong `AUTH_MODE=api_key` sentence in `docs/index.md` gets a minimal one-sentence correction.**

---

## Phase 1 — Typed problem-document contract in OpenAPI ✅ DONE

The core fix: make the published error responses match the RFC 7807 reality, with real schemas.

- [x] Created `api/openapi_responses.py`: the documentation-only `ProblemDocument` Pydantic model + the shared response dicts. Pipelex exports **no** reusable problem-document model (only `ErrorReport`, whose `to_problem_document()` returns a plain `dict`), so the model is hand-written here — but its `validation_errors` field reuses pipelex's own `ValidationErrorItem`, and `user_action` / `provider_metadata` reuse `UserAction` / `ProviderErrorMetadata`, so the published schema cannot drift from the wire.
- [x] Shared response dicts: `PROBLEM_400_START_REQUIRES_ASYNC`, `PROBLEM_401` (documents `WWW-Authenticate`), `PROBLEM_403_ORCHESTRATION_MODE`, `PROBLEM_409_DUPLICATE_RUN`, `PROBLEM_413`, `PROBLEM_422`, `PROBLEM_429` (documents `Retry-After`), `PROBLEM_500`, `PROBLEM_501_ASYNC_NOT_ENABLED`, `PROBLEM_501_METHOD_REF`.
- [x] Deleted the local `PROBLEM_422_RESPONSE` in `api/routes/pipelex/tools.py`; `/lint` + `/format` now inherit the shared typed 422.
- [x] Applied per route. `HTTPValidationError` is gone from the artifact entirely (not even a component).
- [x] Unit coverage: `tests/unit/test_openapi_contract.py`.
- [x] `make agent-check` && `make agent-test` && `make openapi-check` all green.

**Decisions taken in Phase 1:**

1. **Router-level `responses=` works.** `APIRouter(responses=COMMON_PROBLEM_RESPONSES)` on the composite router in `api/routes/__init__.py` merges into every composed operation (FastAPI folds the including router's `responses` into each route's own, route-level entries winning on a status collision). Declaring a `422` there **does** suppress FastAPI's auto-`HTTPValidationError` (the guard is `fastapi/openapi/utils.py:456`). Shared set: **401, 413, 422, 500** — 413 is included because the body-size middleware wraps every route, not just the ones with a declared body.
2. **The media type needed an app-class override.** FastAPI renders a `responses` entry's `model` under the *route's* response-class media type (`application/json`) and offers **no** per-response override (`route_response_media_type`, `fastapi/openapi/utils.py:436`). Hand-writing `content: {application/problem+json: ...}` would have meant hand-writing a `$ref` too and forfeiting component registration for `ProblemDocument`. So: **`api/openapi_schema.py`** holds `PipelexFastAPI(FastAPI)`, which overrides `openapi()` to re-key every 4xx/5xx response onto `application/problem+json` after generation (FastAPI's documented "Extending OpenAPI" seam). It lives in its own **import-side-effect-free** module — same rationale `api/exception_handlers.py` documents — so tests can build a production-faithful app without `api.main`'s startup chain. `api/main.py` now instantiates `PipelexFastAPI` instead of `FastAPI`. (A monkeypatched `app.openapi = fn` was rejected: mypy's `method-assign`, and it would have needed a `type: ignore`.)
3. **`responses=` and `openapi_extra` compose** on `/execute` + `/start` — confirmed in the regenerated artifact. They touch different members of the operation object.
4. **`/execute` gets NO 409.** Answering the plan's open question: unlike `/start`, `/execute` accepts no client-supplied `pipeline_run_id` (pipelex's base `execute` doesn't take the arg — it generates the id per call), so a caller cannot collide with an in-flight run. `/execute` gets 403 + 429 only; it is also the *only* route that runs inference, hence the only one that can be rate-limited upstream.
5. **`/start` gets a 400 the plan didn't list.** The code raises `StartRequiresAsyncOrchestration` (400) when the resolved orchestrator is blocking-only — the reachable failure on the `direct` base. The 501 `AsyncExecutionNotEnabledError` is still documented (it *is* raised, by the `pipelex-temporal` plugin). Final set for `/start`: **400, 403, 409, 501**. Note `docs/error-responses.md` currently documents only the 501 and names a stale `/pipeline/start` path — fixed in Phase 3.
6. **Uploader pair untouched**, per finding 8: they inherit the router-level 401/413/422/500 and get no per-route documentation.

**Final status matrix (verified in the artifact):**

| Route | Documented failures |
|---|---|
| every auth-wrapped `/v1` route | 401, 413, 422, 500 |
| `POST /v1/execute` | + 403, 429 |
| `POST /v1/start` | + 400, 403, 409, 501 |
| `POST /v1/validate` | + 403 |
| `POST /v1/resolve`, `POST /v1/codegen` | + 501 |
| `GET /v1/version` (public, outside the composite router) | 500 only — never 401 |
| `GET /health`, `GET /` | none (cannot fail) |

## Phase 2 — Route docstring / description polish ✅ DONE

- [x] `api/routes/health.py`: `HealthResponse` model + summary + docstring (liveness probe, no auth, touches no dependency).
- [x] `api/main.py` `GET /`: `ServiceIdentity` model + summary + docstring, tagged `health`.
- [x] `api/routes/pipelex/build/runner.py`: dropped "(D9)".
- [x] Swept the published surfaces. **Field descriptions and model docstrings land in the artifact too, not just route docstrings** — so the fixes were: `/start` route docstring ("protocol D11" → prose), `build/inputs` + `build/output` route docstrings ("the D6 loaded-on-success contract" → "the loaded-on-success contract"), and the two `rendered_markdown` **Field descriptions** in `validate.py` ("(D-D)"). Internal comments, private-helper docstrings (`_decode_body`'s `wip/` reference), the non-schema `RenderFormat` enum docstring, and `api_config.py` all stay as-is — none of them are published.
- [x] Regenerated; `make openapi-check` green.

### CHECKPOINT 1 — contract landed ✅

Committed: artifact + code + tests, all gates green. Remaining phases are docs-site prose only.

## Phase 3 — Docs site: catalog completeness + new pipelex surface ✅ DONE

- [x] `docs/index.md`: added the **Resolve & Codegen** section, placed after Pipe Validate (matches both the `mkdocs.yml` nav order and the natural validate → resolve → codegen reading order; the rest of the index's section order was left alone rather than churned to match the nav exactly).
- [x] `docs/index.md`: `GET /` is cataloged, one line under Health & Version, as recommended.
- [x] `docs/index.md` "Uploader": the one wrong sentence replaced. It now says the routes need an authenticated **user identity**, that `AUTH_MODE=api_key` does not establish one (shared key, not per-caller), and names the two configurations that do work (`AUTH_MODE=jwt`, or a trusted proxy + `TRUST_FORWARDED_IDENTITY_HEADERS=true`). Nothing else in that section touched.
- [x] `docs/error-responses.md`: `suggested_fix` and `missing_pipe_code` rows added to the "Structured validation errors" table, plus a full **Suggested fixes** section — a realistic payload (`match-sequence-output`), the field semantics, the op-kind table, and the "ops are the machine contract; any rendered diff is presentation" rule.
- [x] `docs/pipe-validate.md`: `suggested_fix` surfaced where the item shape is described, cross-linked both ways.
- [x] `docs/error-responses.md` "Status codes": sanity-passed against the artifact. Fixed the stale `/pipeline/start` path, and added what was missing entirely — the **400** (`StartRequiresAsyncOrchestration`), the `orchestration_mode` **403** (it only mentioned the storage-ownership case), and the `method_ref` **501**. Added a closing note that an invalid bundle is a 200, not an error status.
- [x] `docs/codegen.md` + `docs/pipe-builder.md`: currency pass. Envelopes still match the regenerated schemas; added the `suggested_fix` pointer to both, and the missing `method_ref` 501 to codegen.md's status list.

**Drift found in Phase 3 that the plan had not spotted:** `/v1/resolve` and `/v1/codegen` carry `x-mthds-protocol: true` (they are protocol *capabilities* — resolution and type-projection), so the protocol surface is **seven** operations, not five. The "five protocol routes" framing was stale in three places, all now fixed: the FastAPI app `description` (published verbatim in the artifact), `docs/index.md`'s three-layer-contract bullet, and this repo's `CLAUDE.md`. `CLAUDE.md`'s project-structure block was also missing `resolve.py` / `codegen.py` / `crate_ops.py` / `tools.py`, and now documents the two new `api/openapi_*.py` modules plus how to document a new failure status.

## Phase 4 — Changelog + verification ✅ DONE

- [x] `CHANGELOG.md` [Unreleased]: added the `suggested_fix` surface (Added), and under Changed: the RFC 7807 error contract in the artifact, the resolve/codegen protocol-route correction, and the docs fixes. Nothing here is breaking — it is documentation of behavior the server already had.
- [x] Full gate: `make agent-check` && `make agent-test` && `make openapi-check` — all green.
- [ ] **Optional, not done:** refresh the Postman collection. Deliberately skipped: the artifact's *request* surface did not change (only error responses and descriptions did), and a Postman collection carries requests, not error responses. Run `/update-postman` if you want the descriptions refreshed anyway.
- [x] Cross-repo sanity: **no `docs/specs/` or `conformance/` edit needed**, as predicted. The protocol spec's error-presentation section (`docs/specs/pipelex-mthds-protocol.md`, "Validation status codes" table) already specifies exactly what this work published — 422 request-shape / 401 / 403 / 5xx as `problem+json`, with a produced verdict on a 200. This change documented existing behavior; it did not alter the contract.

### CHECKPOINT 2 — done ✅

All phases complete. Two commits on `feature/Codegen`:

- `a155ad1` — the wire contract: `api/openapi_responses.py`, `api/openapi_schema.py`, router-level + per-route `responses=`, typed `/health` + `GET /`, D-code sweep, `tests/unit/test_openapi_contract.py`, regenerated artifact.
- (this one) — docs-site prose, changelog, `CLAUDE.md`, and the resolve/codegen protocol-surface correction (which touched `api/main.py`'s description, hence a second artifact regeneration).

If the pipelex editable pin (`[tool.uv.sources]` → `../_codegen`) moves before release, re-run `make openapi-export` and re-commit the artifact.
