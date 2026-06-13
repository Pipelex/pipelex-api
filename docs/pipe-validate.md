# Pipe Validate

Validate MTHDS content by parsing, loading, and dry-running pipes without executing them.

**Endpoint:** `POST /v1/validate`

**Request Body:**

```json
{
  "mthds_contents": ["domain = \"my_domain\"\ndescription = \"My domain\"\nmain_pipe = \"my_pipe\"\n\n[concept.MyResult]\ndescription = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\ninputs = { text = \"native.Text\" }\noutput = \"MyResult\""]
}
```

**Request Fields:**

- `mthds_contents` (list[str], required): MTHDS contents to validate (always an array, even for a single file)
- `allow_signatures` (boolean, optional, default `false`): when true, the validation sweep tolerates unimplemented `PipeSignature` declarations instead of rejecting the bundle — the lenient mode used during top-down builds

**Response:**

The success envelope is the canonical Pipelex validation report — the exact same artifact shapes `PipelexMTHDSProtocol.validate` returns when the runtime is used locally — plus this server's wire-only extras (`mthds_contents` echo, `success`, `message`).

```json
{
  "bundle_blueprint": {
    "domain": "my_domain",
    "description": "My domain",
    "main_pipe": "my_pipe",
    "concept": { ... },
    "pipe": { ... }
  },
  "pipe_io_contracts": {
    "my_domain.my_pipe": {
      "inputs": {
        "text": {
          "concept_code": "native.Text",
          "json_schema": { ... }
        }
      },
      "output": {
        "concept_code": "MyResult",
        "multiplicity": "single"
      }
    }
  },
  "graph_spec": { ... },
  "validated_pipes": [
    { "pipe_ref": "my_domain.my_pipe", "status": "SUCCESS" }
  ],
  "pending_signatures": [],
  "is_runnable": true,
  "mthds_contents": ["..."],
  "success": true,
  "message": "MTHDS content validated successfully"
}
```

**Response Fields (canonical report):**

- `bundle_blueprint` (object): the batch's primary blueprint — the first file declaring `main_pipe`, else the first file
- `pipe_io_contracts` (object): per-pipe input/output contracts, keyed by the namespaced `pipe_ref` (`domain.code`); each entry carries the JSON Schema of every declared input and the output's concept + multiplicity (`single` | `variable`)
- `graph_spec` (object | null): best-effort execution graph of the declared `main_pipe`, dry-run against the validated library; `null` when the batch declares no `main_pipe` or the graph dry-run degrades
- `validated_pipes` (list): per-pipe sweep outcomes — `{pipe_ref, status}` entries with status `SUCCESS` | `FAILURE` | `SKIPPED`
- `pending_signatures` (list[str]): namespaced refs of pipes still declared as `PipeSignature` in the assembled library — what remains to implement
- `is_runnable` (boolean): `pending_signatures` is empty — whether the validated library is complete enough to run

**Response Fields (wire extras, this server only):**

- `mthds_contents` (list[str]): echo of the validated request contents
- `success` (boolean): always `true` on 200 (failures are 422 problem documents)
- `message` (string): status message

**What This Endpoint Does:**

The route is a thin wrapper over the runtime's protocol `validate`: parse → load → dry-run-sweep every pipe → build the per-pipe IO contracts → best-effort graph of the `main_pipe` → assemble the canonical report. A bundle that declares no `main_pipe` validates normally and simply carries `graph_spec: null` — there is no main-pipe precondition.

**Execution Backends:**

The endpoint behaves identically on both deployment backends; only where the work runs differs:

- **Direct (Temporal disabled):** the whole job runs in-process in the API server, one library load.
- **Temporal enabled:** the API dispatches the whole job — validation sweep, graph dry-run, and the worker-side artifacts (`pipe_io_contracts`, `pending_signatures`) — to a worker as **one** in-process activity (`wf_dry_validate` → `act_dry_validate`) and awaits the result in a single round-trip. The API side only parses the blueprints and assembles the same report; it never loads a library. Validation failures cross the boundary as structured error reports carrying the same `error_type=ValidateBundleError` / `error_domain=input` identity, so the 422 problem document is byte-for-byte the same contract as the direct path.

The graph remains best-effort on both backends: a bundle that validates but whose graph dry-run fails still returns 200 with `graph_spec: null`.

**Error Responses:**

If the bundle is invalid, the endpoint returns **HTTP 422** with an [RFC 7807 problem document](error-responses.md):

```json
{
  "type": "https://docs.pipelex.com/latest/errors/validate-bundle-error/",
  "title": "Validate bundle",
  "status": 422,
  "detail": "TOML syntax error at line 1, column 6: Expected '=' after a key in a key/value pair",
  "instance": "/v1/validate",
  "error_type": "ValidateBundleError",
  "error_domain": "input",
  "retryable": false,
  "request_id": "9f2c1ab3-…"
}
```

The HTTP status code is the source of truth — a 200 always means success, and the success body never carries a `success: false` failure mode. See [Error Responses](error-responses.md) for the full envelope and disclosure modes.
