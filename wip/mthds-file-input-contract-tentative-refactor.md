# MTHDS File Input Contract In Pipelex API

## Summary

Implement `mthds_files: list[MthdsFile]` as the Pipelex API HTTP request contract in this repo, with `MthdsFile = {content: str, uri: str | null}`. Inside `pipelex-api`, translate that paired shape to the currently installed Pipelex runtime's legacy arguments:

- `mthds_contents = [file.content, ...]`
- `mthds_sources = [file.uri, ...]` for validation/build diagnostics
- `bundle_uris = [file.uri, ...]` for execute/start duplicate-load identity where URIs are present

This can pass checks in `pipelex-api` without changing Pipelex, but it is still an adapter. A Pipelex runtime change is needed if we want the internal/runtime contract itself to stop being parallel arrays.

## Key Changes

- Add `MthdsFile` and a shared `MthdsFilesRequest` base model in `api/schemas/models.py`.
  - `mthds_files` is required for validate/build routes, min 1, max `MAX_MTHDS_FILES_PER_REQUEST`.
  - Reuse the existing per-file byte limit on `content`.
  - `uri` is optional and is not fetched by the API.
  - Use helper properties/methods on the request model: `mthds_contents`, `mthds_sources`, and `bundle_uris`.

- Replace public request bodies on `/v1/validate`, `/v1/build/inputs`, `/v1/build/output`, and `/v1/build/runner`.
  - These routes accept `mthds_files`, not `mthds_contents`/`mthds_sources`.
  - `/validate` passes `mthds_sources` derived from `uri` into `ApiRunner.validate_verdict`.
  - `/build/inputs` and `/build/output` pass derived `mthds_sources` into `validate_bundle`.
  - `/build/runner` passes each file's `uri` as `mthds_source` when constructing blueprints.

- Update `/v1/execute` and `/v1/start` raw-body parsing.
  - `RunRequest` should carry `mthds_files` instead of `mthds_contents`.
  - `_parse_request` extracts contents and URIs from the paired objects.
  - Instantiate `ApiRunner(user_id=..., bundle_uris=derived_bundle_uris)` so the runtime's current `pipeline_run_setup(..., bundle_uris=...)` behavior receives the submitted identities.
  - Continue passing only `mthds_contents` into the runtime until Pipelex is migrated.

- Update OpenAPI/docs/tests to make `mthds_files` the documented API shape.
  - Replace request examples in README and docs.
  - Regenerate `docs/openapi/pipelex-api.openapi.yaml`.
  - Update tests that post `mthds_contents` to post `mthds_files`.

## Pipelex Runtime Note

`pipelex-api` can implement the public contract here, but it cannot honestly complete Wave 2 alone. Pipelex still exposes `mthds_contents`, `mthds_sources`, and `bundle_uris` in `PipelexMTHDSProtocol`, `validate_bundle`, validator plugins, and run setup.

A Pipelex change is needed if we want:

- internal Python calls to use `mthds_files` rather than API-side translation;
- a typed `uri: str | None` per file without squeezing it through `list[str] | None`;
- Temporal or future validators/orchestrators to receive the paired object directly;
- SDK/runtime parity with the API contract instead of only HTTP compatibility.

The minimal Pipelex follow-up would introduce shared `MthdsFile`, update validate/run signatures to accept `mthds_files`, and keep legacy `mthds_contents` adapters temporarily.

## Test Plan

- Add request-model tests:
  - valid `mthds_files` with and without `uri`;
  - empty `mthds_files` rejected;
  - missing `content` rejected;
  - oversized `content` rejected;
  - too many files rejected.

- Update route tests:
  - `/validate` valid and invalid bundles work with `mthds_files`;
  - validation errors and valid blueprint source use `uri`;
  - build routes work with `mthds_files`;
  - `/execute` and `/start` dispatch still receive the same MTHDS content.

- Add dispatch/adapter assertions:
  - validator stub receives `mthds_contents` and `mthds_sources` derived from `mthds_files`;
  - execute/start instantiate or use runner with `bundle_uris` derived from `uri`.

- Run:
  - targeted unit tests for validate/build/execute/start;
  - `make openapi-check` after regenerating OpenAPI;
  - `make check` or the repo's normal CI check target.

## Assumptions

- `mthds_files` is the new documented Pipelex API contract; legacy `mthds_contents`/`mthds_sources` are not kept as public compatibility aliases in this repo.
- Future MCP tools will use parameter name `files`, but no MCP tool surface is implemented in this repo as part of this change.
- `uri` may be adapted to both `mthds_source` and `bundle_uri`; the API still treats submitted `content` as authoritative and does not dereference `uri`.
