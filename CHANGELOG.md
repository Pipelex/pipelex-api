# Changelog

## [Unreleased]

### Added

- `/release` Claude Code skill (`.claude/skills/release/`) automating the release workflow: version bump in `pyproject.toml`, changelog finalization, `make check`, `make li`, release branch creation, commit, push, and PR.

## [v0.1.1] - 2026-05-07

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
