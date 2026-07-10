# Resolve & Codegen

Resolve a library closure into its **normalized library crate**, and project that crate into typed artifacts (zod schemas, pydantic models, runtime structures) — over HTTP, with the same engine and the same trust chain as the local `pipelex resolve` / `pipelex codegen` commands.

Both endpoints speak the [`POST /v1/validate`](pipe-validate.md) verdict discipline: a **produced verdict is always a `200`** discriminated on `is_valid`; the invalid arm carries the structured `validation_errors[]` from pipelex's one shared builder. Non-2xx is reserved for *no verdict could be produced*: request-shape errors (an unknown projection `kind`/`target`, a malformed closure selector) are `422` RFC 7807 `application/problem+json`, auth is `401`/`403`, server faults are `5xx`.

## Selecting the closure

Both endpoints accept the same closure selector — exactly one of:

- `files` (list, content-passing): inline MTHDS bundles, each `{ "content": "...", "source": "optional/logical/path.mthds" }`. `source` threads onto diagnostics and the crate's `source_map`.
- `method_ref` (string): a reference to an installed/published method resolving to a library plus its exported entry pipe. The envelope accepts it today, but the server answers **`501`** (`MethodRefNotSupported`) until method-registry resolution lands.

Providing neither or both is a request-shape `422`.

## Resolve

**Endpoint:** `POST /v1/resolve`

Resolution is a first-class language operation alongside validation: the closure is merged, every ref fully qualified, refinement flattened, natives materialized, and the crate's canonical `fingerprint` computed. Resolution is **static** — it runs no dry-run sweep (runnability is `/validate`'s vocabulary), and the crate is emitted only from a library that loaded and validated.

**Request Body:**

```json
{
  "files": [
    { "content": "...bundle one...", "source": "main.mthds" },
    { "content": "...bundle two...", "source": "steps.mthds" }
  ]
}
```

**Response (valid verdict):**

```json
{
  "is_valid": true,
  "crate": {
    "mthds_version": "…",
    "concepts": { "…": {} },
    "pipes": { "…": {} },
    "domains": { "…": {} },
    "source_map": { "…": "main.mthds" },
    "fingerprint": "…"
  },
  "message": "MTHDS library resolved successfully"
}
```

`crate` is the canonical JSON encoding of the normalized crate — the same bytes `pipelex resolve --format json` prints, so fingerprints computed from either surface agree.

**Response (invalid verdict):** `200` with `is_valid: false` and `validation_errors[]` (no crate exists).

## Codegen

**Endpoint:** `POST /v1/codegen`

Resolves the closure exactly like `/resolve`, then projects the crate through the two explicit axes:

- `kind` (string, required): what to project. Served: `types` (the crate's concept set as typed models). Input templates are deliberately **not** a kind here — they ride [`POST /v1/build/inputs`](pipe-builder.md), the same projection already surfaced per pipe.
- `target` (string, required): for whom. `ts-zod` (zod schemas + inferred types) and `python-pydantic` (self-contained pydantic models) are MTHDS-protocol type projections; `python-structures` (runtime `StructuredContent` classes) is a Pipelex extension.
- `pipe_ref` (string, optional): pipe selector for future per-pipe kinds — not accepted for `types` (request-shape `422`).

An unknown `kind` or `target` is a request-shape `422` problem+json, never a `200` with an error body.

**Request Body:**

```json
{
  "files": [{ "content": "...bundle..." }],
  "kind": "types",
  "target": "ts-zod"
}
```

**Response (valid verdict):**

```json
{
  "is_valid": true,
  "kind": "types",
  "target": "ts-zod",
  "crate_fingerprint": "…",
  "engine_version": "…",
  "artifacts": [
    { "path": "types.ts", "content": "// >>> pipelex-codegen-stamp >>>\n…" }
  ],
  "lock": "# codegen.lock — generated artifact set (Pipelex codegen). Do not edit by hand.\n…",
  "lock_filename": "codegen.lock",
  "message": "Codegen artifacts generated successfully"
}
```

### The trust chain over HTTP

Every artifact ships **stamped** (source-crate fingerprint, engine version, projection, content hash) and the response carries the matching `codegen.lock`. A client that writes each `artifacts[]` entry and the `lock` **verbatim** reproduces a local `pipelex codegen types` run byte-for-byte — so the offline `pipelex codegen check` passes on the written tree exactly as it would on locally generated files.

There is deliberately **no** server-side check route: the drift check is pure hashing over local files, offline by design.
