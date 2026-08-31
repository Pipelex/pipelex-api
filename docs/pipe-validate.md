# Pipe Validate

Validate MTHDS content by parsing, loading, and dry-running pipes without executing them.

**Endpoint:** `POST /v1/validate`

`/validate` is a **diagnostic endpoint**: any verdict the validator can produce — valid, invalid, or valid-but-not-runnable — rides an HTTP **200**, discriminated in the body on `is_valid`. "Invalid, here are the problems" is the *successful product* of the call, not a transport failure. Non-2xx is reserved for the cases where **no verdict could be produced** (a malformed request body, an `mthds_sources` length mismatch, a `method_ref` that could not be resolved, auth, or a server fault).

**Request Body:**

```json
{
  "mthds_contents": ["domain = \"my_domain\"\ndescription = \"My domain\"\nmain_pipe = \"my_pipe\"\n\n[concept.MyResult]\ndescription = \"A result\"\n\n[pipe.my_pipe]\ntype = \"PipeLLM\"\ndescription = \"Process input\"\ninputs = { text = \"native.Text\" }\noutput = \"MyResult\""]
}
```

**Request Fields:**

- `mthds_contents` (list[str], optional): MTHDS contents to validate (always an array, even for a single file). Exactly one of `mthds_contents` / `method_ref` must be provided
- `method_ref` (string, optional): **Pipelex-API extension.** Validate a published method by address — `github.com/<owner>/<repo>` plus an optional package selector and `@<tag>` — resolved by this server through the same fetch path as a [`method_ref` run](pipe-run.md#running-a-method-by-address-method_ref): the repository is fetched at the tag, the package located by manifest identity, and its `.mthds` files validated with their **real relative paths** feeding the per-file sources (so `validation_errors[].source` names the owning file). Exactly one of `mthds_contents` / `method_ref`; `mthds_sources` cannot accompany it
- `allow_signatures` (boolean, optional, default `false`): controls only the **sweep mechanics** for `PipeSignature` placeholders — whether signature pipes are mock-run during the dry-run sweep and therefore listed in `validated_pipes`. It does **not** change the verdict: an unimplemented signature is never a rejection, it is a *runnability fact* reported via `pending_signatures` + `is_runnable` in both modes
- `mthds_sources` (list[str] | null, optional): per-file sources, parallel to `mthds_contents` — see [Sourcing submitted files](#sourcing-submitted-files). When present it must match `mthds_contents` in length (a mismatch is a 422 request error)
- `render` (list[str], optional, default `[]`): opt-in *rendered text* to attach to the verdict — see [Opt-in extras](#opt-in-extras-render-and-views)
- `views` (list[str], optional, default `[]`): opt-in *structured views* to attach to the verdict — see [Opt-in extras](#opt-in-extras-render-and-views)
- `orchestration_mode` (string | null, optional): per-request backend override for the validation dispatch — see [Where validation runs](#where-validation-runs). Honored only when the deployment allows it; a forbidden override is a 403

**Response (the verdict union):**

The 200 body is one of two arms, discriminated on the mandatory `is_valid` field. A consumer pattern-matches `is_valid` to learn the verdict — it never inspects a status code or catches an exception body.

**Valid arm (`is_valid: true`)** — the canonical Pipelex validation report (the exact same artifact shapes `PipelexMTHDSProtocol.validate` returns when the runtime is used locally) plus this server's wire-only extras (`mthds_contents` echo, `message`, `default_pipe_ref`):

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
  "message": "MTHDS content validated successfully",
  "default_pipe_ref": "my_domain.my_pipe"
}
```

**Response Fields (canonical report — valid arm):**

- `is_valid` (`true`): the discriminant of the valid arm — always `true` on this report
- `bundle_blueprint` (object): the batch's primary blueprint — the first file declaring `main_pipe`, else the first file
- `pipe_io_contracts` (object): per-pipe input/output contracts, keyed by the namespaced `pipe_ref` (`domain.code`); each entry carries the JSON Schema of every declared input and the output's concept + multiplicity (`single` | `variable`)
- `graph_spec` (object | null): best-effort execution graph of the declared `main_pipe`, dry-run against the validated library; `null` when the batch declares no `main_pipe` or the graph dry-run degrades
- `validated_pipes` (list): per-pipe sweep outcomes — `{pipe_ref, status}` entries with status `SUCCESS` | `FAILURE` | `SKIPPED`
- `pending_signatures` (list[str]): namespaced refs of pipes still declared as signatures (contract-only pipes — `inputs`/`output` with no `type` and no implementation) in the assembled library — what remains to implement
- `is_runnable` (boolean): `pending_signatures` is empty — whether the validated library is complete enough to run

**Response Fields (wire extras, valid arm only, this server only):**

- `mthds_contents` (list[str]): echo of the validated request contents
- `message` (string): status message
- `default_pipe_ref` (string | null): the qualified `domain.pipe_code` a caller gets by **omitting** the pipe selector — the pipe a selector-less run of this same request would execute. See [The effective entry pipe](#the-effective-entry-pipe) below

**Response Fields (opt-in, present only when requested):**

- `input_form` (object, valid arm only, requires `views: ["input_form"]`): per-pipe input-form descriptors, keyed by the same namespaced `pipe_ref` as `pipe_io_contracts`. Each entry carries an ordered `fields` list describing what a caller must collect to run that pipe — a field's `kind` (drawn from a closed vocabulary), its `name`, whether it is `required`, whether an empty value should `gate` the run, and the per-kind detail (`choices`, `item_count`, `default_value`, nested `concept_ref`, …). It is a **projection of facts the verdict already states**, derived from what the bundle authored rather than from the emitted JSON Schema, so a client can render a run form from the verdict alone
- `rendered_markdown` (string, both arms, requires `render: ["markdown"]`): a server-rendered Markdown view of the verdict, produced by the same renderers the local CLI uses

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
- `validation_errors` (list): the structured per-error diagnostics a client maps to per-line problems — built by pipelex's one shared builder, so they are byte-for-byte the same items the agent CLI emits. Each item carries a `category` (the closed set `blueprint_validation` | `pipe_factory` | `pipe_validation` | `dry_run`), a `message`, and the locators the runtime can attribute (`error_type`, `pipe_code` / `concept_code` / `domain_code`, `field_path` / `field_name`, and `source`). Absent locators are omitted, not null. An item may also carry a **`suggested_fix`** — a structured, deterministic repair (`fix_code`, `description`, `safety: safe|unsafe`, and the `ops[]` of semantic TOML patch operations to apply). It is present only when the fix planner derived one from the typed error data; a client that ignores it behaves exactly as before. See [Error Responses → Structured validation errors](error-responses.md#structured-validation-errors) and [→ Suggested fixes](error-responses.md#suggested-fixes) for every field. The array is **never empty on an invalid verdict** (the structured-info invariant is total): a dry-run residual failure becomes one `dry_run` item carrying the message (graph-level, so usually no `source`), and a parse-level failure with no attributable locator (a raw TOML-syntax error, an empty blueprint, an elaborator failure) becomes one `blueprint_validation` residual item carrying the message (no `source`)
- `pending_signatures` (list[str]): best-effort outstanding signatures (empty on the invalid arm, since no library was assembled)
- `is_runnable` (`false`): an invalid bundle is never runnable
- `message` (string): the human-readable verdict summary (the caller-facing pipelex error message)

**What This Endpoint Does:**

The route wraps the runtime's protocol `validate`: parse → load → dry-run-sweep every pipe → build the per-pipe IO contracts → best-effort graph of the `main_pipe` → assemble the canonical report. The runner returns the verdict as a value — the canonical report on the valid arm, or a structured `ErrorReport` (a bundle the caller can fix) on the invalid arm — and the route maps the invalid verdict to the 200 invalid arm by matching the returned value, never by catching a transport error. A bundle that declares no `main_pipe` validates normally and simply carries `graph_spec: null` — there is no main-pipe precondition.

**The effective entry pipe:**

`default_pipe_ref` states which pipe this request's closure would run if the caller named none. It applies the run routes' own precedence, minus the request selector `/validate` does not have:

1. a `method_ref`'s fetched package manifest (`METHODS.toml`) `main_pipe` — the package author's declared entry pipe, qualified against the closure;
2. otherwise, and when the manifest declares none, the closure's primary blueprint's `main_pipe` (the first file declaring one), qualified by its domain.

It is `null` when no entry pipe is determined — no blueprint declares `main_pipe`, or a manifest names a pipe the closure does not declare, or declares in several domains. In those last two cases a selector-less run by that address would fail to resolve the manifest's pipe too, so the field says nothing rather than naming the closure's pipe, which no such run would execute.

The field exists because the canonical report is **manifest-blind**: `bundle_blueprint` is the batch's primary blueprint, so for a package whose `METHODS.toml` entry differs from — or exists without — a bundle-level `main_pipe`, a consumer deriving the entry pipe from `bundle_blueprint.main_pipe` alone gets the wrong pipe, or none. Reading `default_pipe_ref` is how a client projects an entry signature that matches what [`POST /v1/execute`](pipe-run.md#running-a-method-by-address-method_ref) and the [`/v1/build/*` projections](pipe-builder.md) actually default to.

It states the **run** default, which is looser than the `/build/*` routes' rule on one point: a closure whose domains each declare a `main_pipe` cannot be defaulted on `/build/*` (a `422`), but `/execute` and `/start` run its first declaring blueprint happily — so this field names that pipe rather than reporting `null`.

The field rides the valid arm only. The invalid arm assembles no library, so there is no entry pipe to name and the field is absent, like the other structural artifacts.

**Opt-in extras (`render` and `views`):**

The verdict body is lean by default: a request that sends neither list gets exactly the structured contract described above, byte-identical to a request that omits both fields. This matters because the highest-frequency callers of `/validate` — editor hooks, CI gates, agent loops — read a handful of fields and discard the rest, and should never pay for bytes they throw away.

Two independent opt-in axes attach more:

- **`render`** produces *rendered text*, attached under a mechanical `rendered_<format>` key. The supported token is `markdown`, which attaches `rendered_markdown` on **both** 200 arms — failure text is exactly what a human-facing surface wants.
- **`views`** attaches a *structured* artifact under a **same-named** top-level field. The supported token is `input_form`, which attaches `input_form` on the **valid arm only**: the descriptor derives from a library that was never assembled when load/parse/wiring failed, so it follows `bundle_blueprint`, `pipe_io_contracts` and `graph_spec` into absence on the invalid arm.

Both lists share the same mechanics, and both are deliberately typed as plain `list[str]` rather than closed enums at the request boundary:

- Each token is resolved **independently** against the server's supported set. A known token is honored; an unknown or unsupported one is **silently dropped — never a 422**. A stale token from an older client must not fail the call, and one bad token in a list does not poison the good ones.
- The lists are treated as **sets**: order-insensitive and deduped.
- They are **independent of each other**. A request may carry both, and each resolves its own tokens against its own supported set. A token is unknown to the other list — sending `render: ["input_form"]` attaches nothing at all.
- Neither ever appears on a no-verdict response. No verdict, no view.

Neither axis is part of the verdict contract: a machine consumer branches on the structured fields, and an extra is a presentation or a projection layered on top. That is what keeps adding a token, or changing what one renders, a non-breaking change.

**Sourcing submitted files:**

The submit path carries bundle text, not file paths, so by default the runtime cannot tell the client which file an error belongs to — `source` comes back `null`. Send `mthds_sources` parallel to `mthds_contents` to fix this: each source is the logical identity of that content (e.g. the file's path relative to the submitted directory), and the runtime threads it onto the corresponding `blueprint.source`. The source then rides back on both arms — `bundle_blueprint.source` on the valid arm, and `validation_errors[].source` on the invalid arm — so a multi-file editor client can map a cross-file diagnostic to the file that owns it. Omit `mthds_sources` (or send `null`) and behavior is exactly as before. The list, when present, must be the same length as `mthds_contents`; a mismatch is a request-shape 422 (it is the caller's wiring bug, caught before the validation sweep runs).

**Where validation runs:**

Validation is **`orchestration_mode`-aware**, the same way `/start` is: the runner resolves the effective backend (the deployment default plus the optional per-request `orchestration_mode` override) and dispatches through the bundle-validator registry. Validation is inherently blocking, so there is no delivery axis here — only the backend varies. On the orchestrator-agnostic base — and for `orchestration_mode: direct` — the whole job runs **in-process in one library load on the API side**. On an orchestrator flavor whose mode is selected (e.g. `temporal`), the whole job is **dispatched to a worker** instead, and the API side assembles the same canonical report from the worker's result without loading a library. Either way the verdict is byte-identical: the backend changes, the contract does not. A per-request override the deployment forbids is refused with a 403.

> **Resource note for deployment.** When validation runs in-process (the agnostic base, or `direct` mode), the API server loads the method library to validate, so a deployment that receives large or frequent in-process `/validate` traffic should be sized for that load (memory + CPU for library assembly and the graph dry-run). On a distributed-execution flavor that dispatches validation to a worker, the library work happens worker-side; size the workers accordingly.

The graph is best-effort: a bundle that validates but whose graph dry-run fails still returns 200 on the valid arm with `graph_spec: null`.

**No-verdict (non-2xx) responses:**

Only conditions where the endpoint could not produce a verdict are non-2xx, rendered as [RFC 7807 problem documents](error-responses.md):

- **422** — a malformed request body, an `mthds_sources` / `mthds_contents` length mismatch, or both/neither of `mthds_contents` / `method_ref` (request-shape errors caught before the runtime).
- **401 / 403** — unauthenticated / forbidden (including a per-request `orchestration_mode` override the deployment does not allow).
- **`method_ref` resolution failures** — a selector that could not be resolved produced no verdict, so it is never rendered as `is_valid: false`: a malformed reference or a failed fetch is a **422**, no matching package in the repository a **404**, and the custom-Python policy (the sandbox gate, the structures refusal) a **403** — the same statuses and `error_type`s as on the run routes (see the [error table](pipe-run.md#running-a-method-by-address-method_ref)).
- **5xx** — a server fault (including a host-wiring programmer error, surfaced as `PipelexUnexpectedError`).

Read it as one rule: a non-2xx on `/validate` always means "the endpoint could not produce a verdict," never "your bundle is bad."
