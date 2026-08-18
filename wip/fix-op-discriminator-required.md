# Handoff — publish `kind` as `required` on the seven fix-op members

> Raised by Codex (P2) on [PR #54, thread `PRRT_kwDOSWpJg86aOwAg`](https://github.com/Pipelex/pipelex-api/pull/54#discussion_r3807131931) (`release/v0.14.0`). Triaged, verified, deferred — not fixed in that PR, and not fixable in this repo.

**Status:** confirmed as contract hygiene, not as a reachable defect. No code changed.
**Severity:** low. Nothing this server emits or accepts is affected; the party who feels it is a third-party codegen consumer.

## Where the fix happens

**In the `pipelex` repo, not here.** `docs/openapi/pipelex-api.openapi.yaml` is generated from the pinned dependency's pydantic models, so no edit in `pipelex-api` can fix this and survive CI (see "Why this repo cannot fix it" below). The next session is expected to run from a `pipelex` worktree; this brief is filed in `pipelex-api/wip/` only because that is where the symptom is visible.

## Context (cold start)

`pipelex` 0.46.0 reshaped a `suggested_fix`'s `ops[]` from a single open `FixOp` (a `FixOpKind` enum beside a bag of optional fields) into a **union discriminated on `kind`**, one member model per kind. That change reached this API's published contract in PR #53 and is described in the v0.14.0 changelog.

The seven members live in `pipelex/suggested_fix.py` and each declares its discriminator with a default:

```python
kind: Literal[FixOpKind.SET_KEY] = FixOpKind.SET_KEY
```

Pydantic omits a defaulted field from a schema's `required` list, so every member publishes `kind` as optional:

```yaml
DeleteTableOp:
  properties:
    table_path: { type: array, items: { type: string }, minItems: 1 }
    kind: { type: string, const: delete_table, default: delete_table }
  additionalProperties: false
  required:
  - table_path          # <- kind is not here
```

The union itself is correct — `SuggestedFix.ops.items` carries a complete `oneOf` plus a `discriminator` with a full seven-entry `mapping`.

## What is actually wrong

The published schema is **more permissive than the runtime**, in two ways that were verified mechanically against the committed artifact and the pinned models:

| Probe | Schema says | Runtime says |
|---|---|---|
| `{"table_path": ["a"]}` | **invalid** — matches both `EnsureTableOp` and `DeleteTableOp` (byte-identical apart from the `const`), so a strict `oneOf` sees 2 matches | invalid (`union_tag_not_found`) |
| `{"table_path": [], "key": "k"}` | **valid** — matches exactly one member | **invalid** (`union_tag_not_found`) |

The second row is the real mismatch: a document the schema accepts, the code rejects.

There is a matching docs gap. `docs/error-responses.md` (Suggested fixes) promises the reader:

> read `kind` off the wire and you know exactly which other fields the op carries

The artifact does not currently guarantee `kind` is on the wire, even though the server always puts it there.

## What is *not* wrong — do not "fix" these

Two parts of the report do not hold, and re-deriving them later would waste a session:

- **No reachable failure.** The op schemas are **response-only**: the transitive `$ref` closure over every `requestBody` in the artifact never reaches `SuggestedFix`, so the server never validates a client-supplied op. And the server never omits `kind` — `exclude_defaults` appears nowhere in `api/`, `tests/`, or `scripts/`; every dump site uses `model_dump(mode="json", ...)` with at most `exclude_none=True`, and `kind` is never `None`. Real `set_key` / `ensure_table` / `delete_table` payloads each match exactly one member. **This is contract polish, not a bug fix — do not add runtime guards for it.**

- **It is not a spec violation.** Codex quoted *"MUST be defined at this schema and it MUST be in the required property list"* — that sentence is **Swagger 2.0**. OAS **3.1.0**, which this artifact declares, says nothing about `required` for `propertyName`. OAS 3.0.4 / 3.1.1 later added *"SHOULD be required in the payload schema, as the behavior when the property is absent is undefined"*, alongside *"`discriminator` MUST NOT change the validation outcome of the schema"*. So this fails a later SHOULD, and the report's normative framing overstates it.

- **The "previous `FixOp` required `kind`" comparison is misleading.** It did (`required: [kind, table_path]` as of v0.13.0), but that was one flat open shape with no discriminator, and `kind` was required only because it carried no default. The union is a strict improvement that happens to under-declare one property; nothing was deliberately given up.

## Why this repo cannot fix it

- The artifact carries `# GENERATED FILE — do not edit by hand`.
- `scripts/export_openapi.py --check` compares the committed file **byte-for-byte** against a fresh `fastapi_app.openapi()`, and it runs in `make check` and in CI (`.github/workflows/lint-check.yml`). A hand-edit fails the build immediately.
- The models are in `pipelex/suggested_fix.py`, behind an exact pin (`pipelex==0.46.4` at the time of writing).

A second post-generation transform in `api/openapi_schema.py` would technically survive the drift gate, but **it was considered and rejected.** That module's existing seam is justified by FastAPI *structurally* being unable to express the error media type ("offers no per-response override"). That justification does not transfer here: pydantic can express this upstream in one line, and a component-schema rewriter sitting next to a media-type re-keyer would paper over an upstream modeling choice in a downstream artifact.

## The change (upstream, in `pipelex`)

Add `json_schema_serialization_defaults_required=True` to `FixOpBase.model_config` in `pipelex/suggested_fix.py` (today: `ConfigDict(frozen=True, extra="forbid")`). This puts `kind` into `required` on all seven members with **zero runtime change and zero call-site change** — `kind` is the only defaulted field on those models, so the config change is precisely scoped and nothing else gains a `required` entry. Verified against a faithful reproduction of the model shape behind a FastAPI `response_model`; because these models are response-only here, only the serialization schema is generated, so there is no `-Input`/`-Output` schema split.

**Scoping caveat — apply it to `FixOpBase` only, not `SuggestedFix`.** `SuggestedFix.source: str | None = None` is dumped under `exclude_none=True`, so marking it required in the serialization schema would create a *new* schema-vs-wire mismatch, trading one for another.

Rejected alternative: dropping the field defaults (`kind: Literal[FixOpKind.SET_KEY]` with no assignment). It reaches the same schema outcome but forces every construction site in pipelex's fix planner and its tests to pass `kind=` explicitly — real churn for a schema-only gain.

## Follow-through here, once upstream ships

1. Move the `pipelex` pin in `pyproject.toml`, then `make li`.
2. `make openapi-export` — the seven members should each gain `kind` in `required`, and nothing else should move.
3. Strengthen `tests/unit/test_openapi_contract.py` → `test_suggested_fix_surface_is_published` (currently around line 161) with an assertion that `kind` is in every member's `required` list. That test already pins the discriminator `propertyName` and the full `mapping`, so it is the natural place to lock this in and keep it from regressing on a later pipelex bump.
4. `make check` (the drift gate) and `make agent-test`.
