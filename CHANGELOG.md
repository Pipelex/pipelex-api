# Changelog

## [v0.2.0] - 2026-06-08

### Breaking Changes

- **Every error response is now [RFC 7807 `application/problem+json`](https://github.com/Pipelex/pipelex-api/blob/main/docs/error-responses.md).** Replaces the legacy `{"detail": {"error_type", "message"}}` envelope across pipelex domain errors, validation (422), auth (401/403), payload limits (413), and the catch-all 500. Standard members on the wire: `type` / `title` / `status` / `detail` / `instance`. Extension members: `error_type`, `error_domain`, `retryable`, `request_id`, and — when populated by pipelex — `error_category`, `user_action`, `provider_metadata`, `model`, `provider`. Content-Type is `application/problem+json`. Clients reading the legacy `data.detail.message` must read RFC 7807 `detail` (top-level string) instead.
- **`/validate` failure envelope removed.** A failing validation no longer returns `HTTP 200` with `{success: false, mthds_contents, message}`; it now returns `HTTP 422` (`ValidateBundleError`) with the RFC 7807 envelope. The former 400 "no `main_pipe`" path is also 422 now. **Success path (200 `ValidateResponse`) is unchanged** — same `mthds_contents`, `pipelex_bundle_blueprint`, `graph_spec`, `pipe_structures`, `success: true`, `message` fields. Cross-repo consumers in `pipelex-app` and `mthds-js` updated in companion PRs.
- **`X-Request-ID` is now echoed on every response** (success and error). Inbound `X-Request-ID` is respected; otherwise the server generates a UUID. The same id rides through `JobMetadata.request_id` to every Temporal worker log record.

### Added

- **`ERROR_DISCLOSURE` env var.** `verbose` (default) renders the full `ErrorReport`; `strict` redacts `detail` for non-caller-facing errors and always strips `model` / `provider` / `provider_metadata`. Provenance-gated via pipelex's `_authors_caller_facing_message` ClassVar — `error_domain` no longer drives redaction. Server logs stay verbose regardless of disclosure mode.
- **[`docs/error-responses.md`](https://github.com/Pipelex/pipelex-api/blob/main/docs/error-responses.md)** — public API error-contract page describing the envelope, status-code mapping (`input`→422, `config`/`runtime`→500), the `type` URI namespace, disclosure modes, request correlation, and worked examples. Linked from `docs/pipe-run.md` and `docs/pipe-validate.md`.

### Changed

- **Adapt to post-#931/#933 pipelex surface.**
  - Phase 6 module relocation: `EnvVarNotFoundError` is now imported from `pipelex.system.exceptions` (was `pipelex.system.environment`). Tests updated; no production code touched the moved import.
  - Acronym-casing fix: pipelex's `pascal_case_to_sentence` now preserves trailing acronym casing (`InvalidJSON` → `Invalid JSON`); the `test_error_uri.py::test_error_type_title` assertion updated.
  - **Native `request_id` wiring at dispatch.** `POST /pipeline/start` now reads the request-scoped `request_id` contextvar and passes it as `request_id=` to `pipeline_run_setup(...)`, so it lands on `JobMetadata.request_id` and rides every worker-side `WorkflowLog` record. No more `webhook.payload["request_id"]` piggyback needed (and `WebhookTarget.payload` would now reject it as a reserved key anyway).
  - **Cross-path consistency regression (T6).** New `tests/unit/test_webhook_recovery.py` pins the invariant: given the same source `ErrorReport`, the classification fields surface identically via the sync HTTP RFC 7807 response and via the webhook `error` payload (composed upstream by `DeliveryExecutor._notify_webhook`).
  - **STRICT-disclosure audit (no code change).** Confirmed `api/problem_document.py` delegates wholesale to `report.to_problem_document(disclosure_mode=...)`, so pipelex's provenance-gated keying flip (Decision D1) flows through untouched. The two `error_domain == INPUT` sites in `api/exception_handlers.py` are log-level switches, not wire-disclosure switches, and remain correct.

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
