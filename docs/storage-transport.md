# Storage transport (`/upload`, `/resolve-storage-url`)

Two routes back Pipelex asset storage: `POST /v1/upload` (write) and `POST /v1/resolve-storage-url` (read). They are **NOT part of the published Pipelex API contract** — neither the MTHDS Protocol nor the build extensions. They are a deployment convenience, and the route docstrings say so. Do not build new integrations directly on them.

## What they do

- **`POST /v1/upload`** — accepts a base64 JSON body `{filename, data, content_type}`, stores it via the configured storage provider under a per-user key (`{user_id}/assets/{uuid}.{ext}`), and returns `{uri, filename}` where `uri` is a `pipelex-storage://` reference. Authentication is required (an `anonymous` request is rejected). Size limits are enforced in two layers, with different statuses. The base64 `data` field carries a Pydantic `max_length` of `MAX_UPLOAD_BASE64_CHARS` (≈ 4/3 × `MAX_UPLOAD_MIB`, so ~66.7 MiB of base64 at the default 50 MiB cap), so an oversized upload is normally rejected at request validation with `422`, before the body is decoded; the route's post-decode check returns `413` only for the sliver of payloads whose base64 fits under `max_length` yet decodes above `MAX_UPLOAD_MIB`. Ahead of both, a request body over `MAX_REQUEST_BODY_MIB` (default 100 MiB) is rejected with `413` by the body-size middleware.
- **`POST /v1/resolve-storage-url`** — resolves a `pipelex-storage://` URI to a short-lived presigned URL. The ownership invariant: the URI's first path segment must equal the requester's `user_id`. Returns `{url, expires_at, content_type?}`.

## They are the current transport behind the SDK preparation contract

The stable, caller-facing contract for turning local assets into run-ready inputs lives in the SDKs — `@pipelex/sdk`'s `uploadFile` / `prepareInputs` and `pipelex-sdk`'s `upload_file` / `prepare_inputs` (see those repos' `docs/input-preparation.md`). Callers depend on the SDK operations, the `uri` result field, and the `pipelex-storage://` scheme. These two HTTP routes are the transport those SDKs currently ride; they are deliberately **below** the public abstraction, which is exactly why they can stay non-contract while the SDK surface is stable.

## Future ownership

Hosted storage upload is slated to move to `pipelex-platform` as part of the storage redesign, **together with** its paired resolution route — one storage domain with one authorization model (resolution's ownership check is coupled to the key scheme upload writes). When that move happens, `pipelex-api-infra` routes the public path to the new owner while preserving the public path and wire shape, so released SDK versions keep working. Whether the open-source `pipelex-api` route is removed at that point is a separate decision, made after hosted ownership is proven — not folded into the move.
