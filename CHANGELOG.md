# Changelog

## [v0.2.1] - 2026-06-17

### Changed — `user_id` is opaque again (path-safe, not UUID-shaped)

The runner no longer requires `user_id` to be a bare UUID. `user_id` is the owner segment of every storage key (`<user_id>/...`), and the runner is a generic execution engine: identity is the deployment's concern (enforced upstream by the gateway/auth layer that injects `X-User-Id`, or by the JWT issuer), not the runner's. The previous UUID-shape check wrongly rejected any non-UUID id — including hosted deployments that use prefixed ids like `user_<uuid>`, which were **silently downgraded to anonymous** and wrote results under `anonymous/...`.

- `USER_ID_UUID_REGEX` is replaced by `is_safe_user_id(value)` in `api/security.py`: the only constraint is **path-safety** — a single segment with no `/`, `\`, NUL/control chars, and not `.`/`..`. Any other opaque id (`user_<uuid>`, `google#abc`, an email, a bare uuid) is accepted as-is.
- Applies to all three sites: the JWT `user_id` claim, the forwarded `X-User-Id` header (`TRUST_FORWARDED_IDENTITY_HEADERS=true`), and `pipelex-storage://` URI parsing (`/resolve-storage-url`).
- This reverses the prior "must be a UUID" constraint. Path-traversal protection is unchanged (it never depended on the UUID shape).

### Fixed — close the `anonymous`-sentinel and malformed-forwarded-id gaps

The opaque-id change above made `anonymous` a path-safe value and left the forwarded-id path failing open. Both are now closed in `api/security.py`:

- **The reserved `anonymous` sentinel can no longer be claimed by an authenticated token.** A JWT with `user_id: "anonymous"` previously passed the path-safety check and bound that exact value — landing the caller's runs in the shared `anonymous/...` namespace while storage/upload routes still treated them as unauthenticated. `verify_jwt` now rejects it with `401 InvalidToken`. The value is centralized as `ANONYMOUS_USER_ID`.
- **A malformed forwarded `X-User-Id` now fails closed.** When `TRUST_FORWARDED_IDENTITY_HEADERS=true` and the proxy forwards a non-empty but path-unsafe id, `no_auth` previously logged and silently downgraded to anonymous; it now rejects the request with `400 BadRequest`. The absent-header and explicit-`anonymous` cases still stay anonymous (the proxy's deliberate "this request is anonymous" signal).
- **`is_safe_user_id` now rejects DEL (`\x7f`).** The unsafe-character class covered C0 controls (`\x00-\x1f`) but let DEL through, contradicting the "no control characters" invariant. DEL is now rejected across all three sites (JWT claim, forwarded header, storage-URI parsing).

## [v0.2.0] - 2026-06-12

### Changed — extension args are this server's own (callback_urls)

- The MTHDS Protocol no longer defines `callback_urls` (or any completion channel) — it is now formally THIS server's extension. The `/start` OpenAPI schema is published from the server's own `PipelexApiStartRequest` model (protocol `StartRequest` + the documented `callback_urls` extension) instead of relying on the protocol model to advertise it. `ApiRunner.start` drops the dead `method_id` compatibility param (the hosted platform handles `method_id` itself and never forwards it) and gains the protocol's generic `extra` slot. SDK clients pass server-specific args via `extra` — e.g. `client.start(..., extra={"callback_urls": [...]})`.

### Breaking Changes — MTHDS Protocol alignment (master plan 05, Phase C1)

This server is now the reference implementation of the **[MTHDS Protocol](https://mthds.ai)** (contract nesting: MTHDS Protocol ⊂ Pipelex API ⊂ Pipelex hosted API). Clients on the new SDKs (`mthds` Python/JS protocol releases) require a pipelex-api image carrying these changes — **the minimum image version for the `/v1` surface is this release**; an older image 404s on every `/v1/*` call.

- **Base path: `/api/v1` → `/v1`, no aliases.** The API router now mounts at `/v1` (SDKs compose `{MTHDS_API_URL}/v1/{endpoint}`). Zero `/api/v1` routes remain.
- **Run routes renamed:** `POST /api/v1/pipeline/execute` → `POST /v1/execute`, `POST /api/v1/pipeline/start` → `POST /v1/start`. `/start` now answers **202 Accepted** (protocol `StartAck`) instead of 200.
- **Wire fields renamed (D1):** request extra `pipeline_run_id` → `pipeline_run_id` (client-supplied run ids on `/start` are still accepted — the protocol allows it and `StartAck.pipeline_run_id` is authoritative); responses serialize `pipeline_run_id` / `state` instead of `pipeline_run_id` / `pipeline_state`. The pipelex runtime internals keep `pipeline_run_id` — only the wire renames.
- **`GET /version` replaces `GET /pipelex_version` + `GET /api_version` (both deleted, no alias).** Returns the protocol `VersionInfo`: `{protocol_version, implementation: "pipelex-api", implementation_version, runtime_version}`. PUBLIC — excluded from auth exactly like `/health` (it's the handshake clients use before they have credentials).
- **Completion-callback payload now carries `pipeline_run_id`.** The webhook POSTed to `callback_urls` carries the protocol `pipeline_run_id` field alongside the runtime's existing `pipeline_run_id`/`status` keys; the `X-Completion-Signature` is `HMAC-SHA256(secret, pipeline_run_id)` (unchanged scheme, renamed input). The `status` → `state` key rename lives in the pipelex runtime's delivery executor and ships with a later pipelex release — receivers should read `pipeline_run_id` + `status` for now.
- **Pipelex pinned to 0.33.0** (`PipelexMTHDSProtocol` — the renamed `PipelexRunner` — with protocol methods `execute`/`start`/`validate`/`models`/`version`; `PipelexRunResult`/`PipelexStartAck` response models).

### Added — MTHDS Protocol alignment

- **Committed OpenAPI artifact + drift gate.** `docs/openapi/pipelex-api.openapi.yaml` is the layer-2 contract, exported from the live app via `make openapi-export` and drift-checked in CI via `make openapi-check` (wired into the lint workflow and `make check`). The five protocol routes are tagged `x-mthds-protocol: true`; `/upload` and `/resolve-storage-url` are documented as NON-CONTRACT in their descriptions (kept in the schema for self-hosters' interactive docs).
- **Protocol conformance suite.** `tests/unit/test_protocol_conformance.py` gates CI on: the five protocol paths under `/v1` (and zero legacy paths), the `RunRequest` anyOf rule (422 on empty body), the public `/version` handshake + shape, client-supplied `pipeline_run_id` acceptance, and the completion-callback E2E — a local in-test HTTP receiver verifies delivery, the `X-Completion-Signature` HMAC, and the payload's `pipeline_run_id`/status fields through the real `DeliveryExecutor` code path (Temporal dispatch faked in-process).

### Changed — `/validate` fast path restored

- **Temporal-enabled `/validate` dispatches one in-process activity again.** The earlier temporary regression (direct in-process `validate_bundle` + `dry_run_pipeline`) is undone now that the pinned pipelex ships `wf_dry_validate` / `act_dry_validate`. On a Temporal-enabled runner, `/validate` runs the whole sweep **+** graph dry-run as ONE `act_dry_validate` activity (a single worker round-trip) instead of dispatching the dry-run pipeline pipe-by-pipe through Temporal (one workflow + activities per pipe) — restoring the fast path first added in PR #12. Direct (Temporal-disabled) mode is unchanged. Same wire contract; the error contract and best-effort-graph semantics are identical across both backends.

### Changed

- **Duplicate `pipeline_run_id` now returns 409 Conflict instead of 500.** `PipelineManagerAlreadyExistsError` — raised when a submission reuses a `pipeline_run_id` that is still registered for an in-flight run — is mapped to 409 via `_ERROR_TYPE_STATUS_OVERRIDES`, so a genuinely concurrent duplicate is a client-visible conflict rather than an opaque internal error. Pairs with the pipelex-side fix that frees a run's registry entry when it completes or fails, making serial resubmission of the same id succeed (previously every resubmission of a used id 500'd until process restart). Documented in [`docs/error-responses.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/error-responses.md).
- **Error-log disposition is now keyed off the final HTTP status, not the error domain.** A 4xx logs at `warning` (no traceback); a 5xx logs at `error` (with traceback). This keeps API-level 4xx overrides — the new 409 conflict, and the provider-429 passthrough — out of error dashboards instead of paging on a normal client conflict. The previous rule (only `INPUT`-domain → `warning`) left domain-less 4xx errors logging at `error`.
- **Temporal-enabled `/validate` now runs as ONE worker round-trip.** When `temporal.is_enabled` is true, the route dispatches the whole job — validation sweep **+** graph dry-run — as the one-step wrapper workflow `wf_dry_validate` (→ the single in-process `act_dry_validate` activity) via pipelex's `dispatch_dry_validate`, instead of running `validate_bundle` API-side and `dry_run_pipeline` as a top-level worker workflow with a tracing-backend round-trip. The worker traces the graph in memory and returns `{status map, graph_spec}` on the activity result; the route re-parses the blueprints and builds `pipe_structures` from a local load-only library acquisition. The wire contract is unchanged on both backends: same 200 `ValidateResponse` envelope, same best-effort `graph_spec` (null when the graph dry-run fails), same RFC 7807 422 carrying `error_type=ValidationError` for the missing-`main_pipe` precondition and `error_type=ValidateBundleError` for validation failures — both with `error_domain=input` (the structured report crosses the activity boundary and the global handler renders it identically). Direct mode (Temporal disabled) is untouched. Requires a pipelex version that ships `act_dry_validate` (newer than v0.32.1).

### Breaking Changes

- **Every error response is now [RFC 7807 `application/problem+json`](https://github.com/Pipelex/pipelex-api/blob/main/docs/error-responses.md).** Replaces the legacy `{"detail": {"error_type", "message"}}` envelope across pipelex domain errors, validation (422), auth (401/403), payload limits (413), and the catch-all 500. Standard members on the wire: `type` / `title` / `status` / `detail` / `instance`. Extension members: `error_type`, `error_domain`, `retryable`, `request_id`, and — when populated by pipelex — `error_category`, `user_action`, `provider_metadata`, `model`, `provider`. Content-Type is `application/problem+json`. Clients reading the legacy `data.detail.message` must read RFC 7807 `detail` (top-level string) instead.
- **`/validate` failure envelope removed.** A failing validation no longer returns `HTTP 200` with `{success: false, mthds_contents, message}`; it now returns `HTTP 422` (`ValidateBundleError`) with the RFC 7807 envelope. The former 400 "no `main_pipe`" path is also 422 now. **Success path (200 `ValidateResponse`) is unchanged** — same `mthds_contents`, `pipelex_bundle_blueprint`, `graph_spec`, `pipe_structures`, `success: true`, `message` fields. Cross-repo consumers in `pipelex-app` and `mthds-js` updated in companion PRs.
- **`X-Request-ID` is now echoed on every response** (success and error). Inbound `X-Request-ID` is respected; otherwise the server generates a UUID. The same id rides through `JobMetadata.request_id` to every Temporal worker log record.

### Added

- **`ERROR_DISCLOSURE` env var.** `verbose` (default) renders the full `ErrorReport`; `strict` redacts `detail` for non-caller-facing errors and always strips `model` / `provider` / `provider_metadata`. Provenance-gated via pipelex's `_authors_caller_facing_message` ClassVar — `error_domain` no longer drives redaction. Server logs stay verbose regardless of disclosure mode.
- **[`docs/error-responses.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/error-responses.md)** — public API error-contract page describing the envelope, status-code mapping (`input`→422, `config`/`runtime`→500), the `type` URI namespace, disclosure modes, request correlation, and worked examples. Linked from `docs/pipe-run.md` and `docs/pipe-validate.md`.
- **`allow_signatures` API flag.** Opt-in boolean on `/validate`, `/build/inputs`, `/build/output`, and `/build/runner`. When `true`, the validation sweep tolerates unimplemented `PipeSignature` placeholders (dry-running them by minting a mock) instead of rejecting the bundle. Defaults to `false` (strict).
- **Postman & `curl` bundle runner.** New `postman-run-bundle` Claude skill and `build_postman_query.py` script that turn a local MTHDS bundle into a Postman request, a `curl` command, or a direct API execution. Resolves the bundle exactly like `pipelex run bundle <path>` and targets `/api/v1/pipeline/execute`, `/start`, and `/api/v1/validate`.
- **Bundle testing Make targets.** `make bundle-run`, `bundle-validate`, `bundle-curl`, `bundle-postman`, and `bundle-dry` exercise a bundle against the API from the CLI.
- **Local Pipelex WIP support.** `make run-wip` / `install-wip-pipelex` run the API against a local, editable `pipelex` working tree without hand-editing `pyproject.toml`.

### Changed

- **Adapt to post-#931/#933 pipelex surface.**
  - Phase 6 module relocation: `EnvVarNotFoundError` is now imported from `pipelex.system.exceptions` (was `pipelex.system.environment`). Tests updated; no production code touched the moved import.
  - Acronym-casing fix: pipelex's `pascal_case_to_sentence` now preserves trailing acronym casing (`InvalidJSON` → `Invalid JSON`); the `test_error_uri.py::test_error_type_title` assertion updated.
  - **Native `request_id` wiring at dispatch.** `POST /pipeline/start` now reads the request-scoped `request_id` contextvar and passes it as `request_id=` to `pipeline_run_setup(...)`, so it lands on `JobMetadata.request_id` and rides every worker-side `WorkflowLog` record. No more `webhook.payload["request_id"]` piggyback needed (and `WebhookTarget.payload` would now reject it as a reserved key anyway).
  - **Cross-path consistency regression (T6).** New `tests/unit/test_webhook_recovery.py` pins the invariant: given the same source `ErrorReport`, the classification fields surface identically via the sync HTTP RFC 7807 response and via the webhook `error` payload (composed upstream by `DeliveryExecutor._notify_webhook`).
  - **STRICT-disclosure audit (no code change).** Confirmed `api/problem_document.py` delegates wholesale to `report.to_problem_document(disclosure_mode=...)`, so pipelex's provenance-gated keying flip (Decision D1) flows through untouched. The two `error_domain == INPUT` sites in `api/exception_handlers.py` are log-level switches, not wire-disclosure switches, and remain correct.
- **Shared request validation.** Consolidated the MTHDS payload validation (the `mthds_contents` bound + per-file size guard) and the new `allow_signatures` flag into a shared `MthdsContentsRequest` Pydantic base model that `/validate` and the build routes subclass, so the validation routes can't drift.
- **`/build/inputs` and `/build/output` reuse the validated library.** Both now read the requested pipe from the library `validate_bundle` already opened and left current, instead of opening and loading a second one — less work and memory per request — and scope the dry-run sweep to the requested pipe.
- **Unit tests run with Temporal disabled.** `tests/unit/conftest.py` forces `temporal_enabled=False`, so the suite executes pipelines (including dry-run validation) in-process and hermetically.
- **`pyproject.toml` tooling config.** Set pyright `venvPath` / `venv` and expanded the `exclude` lists to ignore `node_modules`, hidden files, and `.claude/`.

### Fixed

- **Library resource leak in `/validate`, `/build/inputs`, and `/build/output`.** The library `validate_bundle` opens and leaves current on success was never torn down, orphaning a library in the `LibraryManager` on every successful call. Each route now owns that teardown in a `finally`.
- **`/build/runner` returned 500 on a failed dry-run.** A failed dry-run of a caller-submitted bundle now becomes a 422 `ValidateBundleError` (RFC 7807 problem response), matching `/validate`, `/build/inputs`, and `/build/output`. (The bare `DryRunError` carried no `error_domain`, so the global handler had been rendering it as a server fault.)
- **`/build/runner` generated code for a `SKIPPED` pipe.** When the requested pipe was `SKIPPED` during validation (an unresolved cross-package dependency), the endpoint used to emit runner code for a pipeline that can't actually run; it now rejects the request with 422.
- **Makefile `help` output.** `Makefile_basics.mk` no longer overrides the root `help` target, so the local and deployment help sections all compose.

### Known follow-ups (deferred, not in this set of changes)

- Structured logging on `_notify_webhook` (`event=webhook_delivery` / `event=webhook_failure`) — lives in pipelex upstream at `delivery_executor.py`, not in this repo. Tracked for a separate pipelex PR.

## [v0.1.2] - 2026-05-20

### Changed

- **Trimmed `RequestUser` to `user_id` only.** Dropped the `email`, `sub`, and `auth_method` fields. The runner is a generic execution engine — the only piece of identity it consumes is an opaque user id, which it scopes S3 storage keys under (`<user_id>/...`). Anything else (email, OAuth subject, auth method) is metadata the runner has no use for; handlers that need it look it up by `user_id` against the deployment's own user store.
- **JWT auth now requires a `user_id` claim (UUID).** No fallback to the standard `sub` claim, and the value is now validated as a UUID (`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`) at the auth boundary. Storage URIs require the owner segment to match the same shape; provider-issued `sub` values like `"google#abc"` (or any non-UUID `user_id`) would otherwise let a caller upload to S3 under a key that `/resolve-storage-url` would later refuse to resolve. Deployments using OAuth JWTs must mint their own `user_id` claim (a UUID for that caller) when issuing tokens.
- **Forwarded identity headers reduced to a single `X-User-Id` (must be a UUID).** With `TRUST_FORWARDED_IDENTITY_HEADERS=true`, only `X-User-Id` is honored and its value is validated against the same UUID shape as JWT `user_id`. Non-UUID values are silently ignored and the request stays anonymous. The previous `X-User-Email`, `X-User-Sub`, and `X-Auth-Method` headers are gone — they were metadata the runner never used.
- Bumped Pipelex to v0.28.0. See changelog here: https://docs.pipelex.com/latest/changelog/

### Fixed

- `mkdocs build --strict` no longer fails on relative links inside the `CONTRIBUTING.md` snippet included into `docs/contributing.md`. Those links are authored to resolve from the repo root on GitHub; mkdocs validation for them is downgraded from `warn` to `info`.

### Security

- **Tightened `pipelex-storage://` owner-segment validation.** `parse_storage_uri` previously accepted any 36-character mix of hex and dashes (`^[a-f0-9-]{36}$`), so values like 36 dashes or `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` could pass through. The check now enforces the canonical UUID positional shape `8-4-4-4-12`, matching the validator newly applied at the auth boundary so the two layers cannot drift.

## [v0.1.1] - 2026-05-07

### Added

- `/release` Claude Code skill (`.claude/skills/release/`) automating the release workflow: version bump in `pyproject.toml`, changelog finalization, `make check`, `make li`, release branch creation, commit, push, and PR.

### Changed

- Bumped Pipelex to v0.27.0. See changelog here: https://docs.pipelex.com/latest/changelog/

## [v0.1.0] - 2026-05-04

### Changed

- **The published `pipelex/pipelex-api` Docker image is now generic.** Temporal is **off** by default, no S3 storage is configured, and no DynamoDB tracing is enabled. Anything environment-specific is now expected to come from a user-supplied `.pipelex/pipelex_override.toml` (or `pipelex_<env>.toml`) mounted into `/root/.pipelex/`. See [`docs/configuration.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/configuration.md).
- **Removed deployment-specific env configs from the repo.** `pipelex_dev.toml`, `pipelex_staging.toml`, `pipelex_prod.toml`, and `pipelex_service.toml` previously baked one specific deployment's Temporal cluster, S3 buckets, and DynamoDB table names into the public image. They are gone — bring your own via a mounted `.pipelex/` override (see [`docs/configuration.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/configuration.md)).
- **AWS/ECR deploy targets removed.** `Makefile.deploy.api.mk` now only contains `deploy-docker-hub`. The previous `deploy-api`, `deploy-api-dev`, `deploy-api-staging` targets and their corresponding workflows are gone — they were specific to one deployment's AWS account. CI in this repo now publishes to Docker Hub only on every push to `main`. Self-hosters wire their own deploy from a separate infra-as-code repo.
- **Hardcoded `temporal_enabled=True` removed from `api/main.py`.** Temporal is now controlled entirely by `[temporal] is_enabled` in your config, with no Python-level override.
- **Dockerfile cleans up `/app/.pipelex` after copying it to `/root/.pipelex`** so user-supplied overrides at `/root/.pipelex/*.toml` are not shadowed by a project-level `.pipelex/` inside the image.
- **`.env.example` trimmed** to universally-needed vars only (`PIPELEX_GATEWAY_API_KEY`). Auth, Temporal, and callback-secret vars are now optional / deployment-specific.
- **`COMPLETION_CALLBACK_SECRET` is no longer required at boot.** `api/routes/pipelex/pipeline.py` now reads it lazily inside `_completion_signature()` instead of at module import. The image boots with just `PIPELEX_GATEWAY_API_KEY`; the secret is only required when you actually use `POST /api/v1/pipeline/start` with a `callback_url`. See [`docs/pipe-run.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/pipe-run.md) → "Async Completion Callbacks".

### Added

- [`docs/configuration.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/configuration.md) — explains the env vars this Docker image accepts, the Pipelex config layering inside the container, and how to mount your own `.pipelex/` overrides. The actual Pipelex configuration syntax (storage, tracing, inference, Temporal, …) lives at https://docs.pipelex.com.

### Fixed

- Move `Pipelex.make()` from module top-level into a FastAPI `lifespan` handler in `api/main.py`. Importing `api.main` no longer mutates the global Pipelex singleton, which was causing `make t` to fail under `pytest-xdist` once enough unit tests existed for workers to share processes with the e2e import-smoke test.
- Set a placeholder `COMPLETION_CALLBACK_SECRET` in `tests/conftest.py` so test collection succeeds in CI environments where `.env` isn't present. `api/routes/pipelex/pipeline.py` reads this var at import time, which was breaking `make gha-tests` collection as soon as any test transitively imported `api.routes`.

### Security

- `/upload` now rejects unauthenticated and `anonymous` callers with `401 Unauthenticated`, mirroring `/resolve-storage-url`. Previously, anonymous uploads were silently accepted and stored under an `anonymous/assets/…` prefix.

### Changed

- `/upload` returns `400 InvalidBase64` (instead of `500`) when the request body's `data` field isn't valid base64. Genuine storage failures still surface as `500`.
- `UploadRequest` now uses `ConfigDict(extra="forbid")`, so unknown fields produce `422` instead of being silently ignored.
- **`mthds_content` → `mthds_contents`** — Updated `ApiRunner.start_pipeline`, pipeline routes, and build routes to use `mthds_contents: list[str] | None` instead of singular `mthds_content`. Aligns with the updated `RunnerProtocol` in `mthds>=0.2.0`.
- **`bundle_uri` → `bundle_uris`** — Renamed to `bundle_uris: list[str] | None` in `ApiRunner` to match the updated `PipelexRunner` interface.

## [v0.0.12] - 2026-01-15

### Changed

- Bump `pipelex` to `v0.18.0`, the `Chicago` release: See changelog [here](https://docs.pipelex.com/changelog/)

## [v0.0.11] - 2025-12-01

### Added

- `pyjwt` dependency.

## [v0.0.10] - 2025-12-01

### Changed

- Bump `pipelex` to `v0.17.3`: See `Pipelex` changelog [here](https://docs.pipelex.com/changelog/)

### Added

- JWT authentication support.

## [v0.0.9] - 2025-11-26

### Changed

- Bump `pipelex` to `v0.17.1`: See `Pipelex` changelog [here](https://docs.pipelex.com/changelog/)

## [v0.0.8] - 2025-11-04

### Added

- Documentation for the API.

### Changed

- Reverted the `all_blackboxai` routing profile to the pipelex default one.

## [v0.0.7] - 2025-10-29

### Changed

- Hardcoded routing profile `all_blackboxai` for the pipelex hackathon.

## [v0.0.6] - 2025-10-29

### Changed

- More robust library management.

## [v0.0.5] - 2025-10-27

### Fixed

- Updated `Pipelex` dependency to `v0.14.0`.

## [v0.0.4] - 2025-10-26

### Fixed

- Updated telemetry settings.

## [v0.0.3] - 2025-10-25

### Feature

- You can now run directly a pipelex bundle from the API.

### Added

- `Pipelex.make(IntegrationMode.FASTAPI)` to the API

## [v0.0.2] - 2e25-10-22

### Added

- `docker-compose.yml` file

### Changed

- `README.md` Added more precise instructions for local, docker run, and docker compose.

## [v0.0.1] - 2025-10-22

- Initial commit!
