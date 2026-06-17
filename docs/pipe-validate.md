# Pipe Validate

Validate MTHDS content by parsing, loading, and dry-running pipes without executing them.

**Endpoint:** `POST /v1/validate`

`/validate` is a **diagnostic endpoint**: any verdict the validator can produce — valid, invalid, or valid-but-not-runnable — rides an HTTP **200**, discriminated in the body on `is_valid`. "Invalid, here are the problems" is the *successful product* of the call, not a transport failure. Non-2xx is reserved for the cases where **no verdict could be produced** (a malformed request body, an `mthds_sources` length mismatch, auth, or a server fault).

**Request Body:**

```json
{
  "mthds_contents": ["domain = \"my_domain\"\ndescription = \"My domain\"\nmain_pipe = \"my_pipe\"\n\n[concept.MyResult]\ndescription = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\ninputs = { text = \"native.Text\" }\noutput = \"MyResult\""]
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to validate (always an array, even for a single file)
- `allow_signatures` (boolean, optional, default `false`): controls only the **sweep mechanics** for `PipeSignature` placeholders — whether signature pipes are mock-run during the dry-run sweep and therefore listed in `validated_pipes`. It does **not** change the verdict: an unimplemented signature is never a rejection, it is a *runnability fact* reported via `pending_signatures` + `is_runnable` in both modes
- `mthds_sources` (list[str] | null, optional): per-file sources, parallel to `mthds_contents` — see [Sourcing submitted files](#sourcing-submitted-files). When present it must match `mthds_contents` in length (a mismatch is a 422 request error)

**Response (the verdict union):**

The 200 body is one of two arms, discriminated on the mandatory `is_valid` field. A consumer pattern-matches `is_valid` to learn the verdict — it never inspects a status code or catches an exception body.

**Valid arm (`is_valid: true`)** — the canonical Pipelex validation report (the exact same artifact shapes `PipelexMTHDSProtocol.validate` returns when the runtime is used locally) plus this server's wire-only extras (`mthds_contents` echo, `message`):

```json
{
  "is_valid": true,
  "bundle_blueprint": {
    "domain": "my_domain",
    "description": "My domain",
    "main_pipe": "my_pipe",
    "concept": { "...": "..." },
    "pipe": { "...": "..." }
  },
  "pipe_io_contracts": {
    "my_domain.my_pipe": {
      "inputs": { "text": { "concept_ref": "native.Text", "json_schema": { "...": "..." } } },
      "output": { "concept_ref": "MyResult", "multiplicity": "single" }
    }
  },
  "graph_spec": { "...": "..." },
  "validated_pipes": [
    { "pipe_ref": "my_domain.my_pipe", "status": "SUCCESS" }
  ],
  "pending_signatures": [],
  "is_runnable": true,
  "mthds_contents": ["..."],
  "message": "MTHDS content validated successfully"
}
```

**Response Fields (canonical report — valid arm):**

- `is_valid` (`true`): the discriminant of the valid arm — always `true` on this report
- `bundle_blueprint` (object): the batch's primary blueprint — the first file declaring `main_pipe`, else the first file
- `pipe_io_contracts` (object): per-pipe input/output contracts, keyed by the namespaced `pipe_ref` (`domain.code`); each entry carries the JSON Schema of every declared input and the output's concept + multiplicity (`single` | `variable`)
- `graph_spec` (object | null): best-effort execution graph of the declared `main_pipe`, dry-run against the validated library; `null` when the batch declares no `main_pipe` or the graph dry-run degrades
- `validated_pipes` (list): per-pipe sweep outcomes — `{pipe_ref, status}` entries with status `SUCCESS` | `FAILURE` | `SKIPPED`
- `pending_signatures` (list[str]): namespaced refs of pipes still declared as `PipeSignature` in the assembled library — what remains to implement
- `is_runnable` (boolean): `pending_signatures` is empty — whether the validated library is complete enough to run

**Response Fields (wire extras, valid arm only, this server only):**

- `mthds_contents` (list[str]): echo of the validated request contents
- `message` (string): status message

**Invalid arm (`is_valid: false`)** — the per-error diagnostics plus the runnability facts; the structural artifacts (`bundle_blueprint`, `pipe_io_contracts`, `graph_spec`, `validated_pipes`) and `mthds_contents` are **absent**, because they do not exist when load/parse/wiring failed:

```json
{
  "is_valid": false,
  "validation_errors": [
    {
      "category": "blueprint_validation",
      "message": "Value error, Invalid main pipe syntax 'Not A Valid Pipe Code!'. Must be in snake_case.",
      "error_type": "invalid_pipe_code_syntax",
      "domain_code": "broken",
      "source": "broken.mthds"
    }
  ],
  "pending_signatures": [],
  "is_runnable": false,
  "message": "Validation error(s): ..."
}
```

**Response Fields (invalid arm):**

- `is_valid` (`false`): the discriminant of the invalid arm
- `validation_errors` (list): the structured per-error diagnostics a client maps to per-line problems — built by pipelex's one shared builder, so they are byte-for-byte the same items the agent CLI emits. Each item carries a `category` (the closed set `blueprint_validation` | `pipe_factory` | `pipe_validation` | `dry_run`), a `message`, and the locators the runtime can attribute (`error_type`, `pipe_code` / `concept_code` / `domain_code`, `field_path` / `field_name`, and `source`). Absent locators are omitted, not null. See [Error Responses → Structured validation errors](error-responses.md#structured-validation-errors) for every field. The array is **never empty on an invalid verdict** (the structured-info invariant is total): a dry-run residual failure becomes one `dry_run` item carrying the message (graph-level, so usually no `source`), and a parse-level failure with no attributable locator (a raw TOML-syntax error, an empty blueprint, an elaborator failure) becomes one `blueprint_validation` residual item carrying the message (no `source`)
- `pending_signatures` (list[str]): best-effort outstanding signatures (empty on the invalid arm, since no library was assembled)
- `is_runnable` (`false`): an invalid bundle is never runnable
- `message` (string): the human-readable verdict summary (the caller-facing pipelex error message)

**What This Endpoint Does:**

The route wraps the runtime's protocol `validate`: parse → load → dry-run-sweep every pipe → build the per-pipe IO contracts → best-effort graph of the `main_pipe` → assemble the canonical report. When the runtime instead raises a `ValidateBundleError` (a bundle the caller can fix), the route converts it to the 200 invalid arm rather than letting it become a transport error. A bundle that declares no `main_pipe` validates normally and simply carries `graph_spec: null` — there is no main-pipe precondition.

**Sourcing submitted files:**

The submit path carries bundle text, not file paths, so by default the runtime cannot tell the client which file an error belongs to — `source` comes back `null`. Send `mthds_sources` parallel to `mthds_contents` to fix this: each source is the logical identity of that content (e.g. the file's path relative to the submitted directory), and the runtime threads it onto the corresponding `blueprint.source`. The source then rides back on both arms — `bundle_blueprint.source` on the valid arm, and `validation_errors[].source` on the invalid arm — so a multi-file editor client can map a cross-file diagnostic to the file that owns it. Omit `mthds_sources` (or send `null`) and behavior is exactly as before. The list, when present, must be the same length as `mthds_contents`; a mismatch is a request-shape 422 (it is the caller's wiring bug, caught before the validation sweep runs).

**Execution Backends:**

The endpoint behaves identically on both deployment backends; only where the work runs differs:

- **Direct (Temporal disabled):** the whole job runs in-process in the API server, one library load.
- **Temporal enabled:** the API dispatches the whole job — validation sweep, graph dry-run, and the worker-side artifacts (`pipe_io_contracts`, `pending_signatures`) — to a worker as **one** in-process activity (`wf_dry_validate` → `act_dry_validate`) and awaits the result in a single round-trip. The API side only parses the blueprints and assembles the same report; it never loads a library. An invalid verdict crosses the boundary as a structured error report (a `WorkflowExecutionError` recovering the original `ValidateBundleError`), which the route detects and renders as the same 200 invalid arm as the direct path. A genuine workflow fault (one that recovers no `ValidateBundleError`) stays a 5xx.

The graph remains best-effort on both backends: a bundle that validates but whose graph dry-run fails still returns 200 on the valid arm with `graph_spec: null`.

**No-verdict (non-2xx) responses:**

Only conditions where the endpoint could not produce a verdict are non-2xx, rendered as [RFC 7807 problem documents](error-responses.md):

- **422** — a malformed request body, or an `mthds_sources` / `mthds_contents` length mismatch (a request-shape error caught before the runtime).
- **401 / 403** — unauthenticated / forbidden.
- **5xx** — a server fault (including a host-wiring programmer error, surfaced as `PipelexUnexpectedError`, and a genuine Temporal workflow fault).

Read it as one rule: a non-2xx on `/validate` always means "the endpoint could not produce a verdict," never "your bundle is bad."
