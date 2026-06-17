# Handoff — surface `pending_signatures` / `is_runnable` on `/validate`

> **Status: ⛔ SUPERSEDED (2026-06-12)** by the cross-repo alignment plan at workspace root: [`../wip/mthds-protocol-surface-alignment.md`](../../wip/mthds-protocol-surface-alignment.md). A full consistency pass found `/validate` (and `/models`) bypass `PipelexMTHDSProtocol` with divergent envelopes; the minimal "add two fields to the existing envelope" patch below would cement that divergence instead of healing it. The context and semantics notes below remain accurate — only the "The change" section is replaced by the plan's Phases 1–2.

## Context (cold start)

The pipelex runtime now computes a **runnability verdict** on bundle validation: `ValidateBundleResult.pending_signatures` — the library-wide list of pipes still declared as `PipeSignature` (unimplemented forward declarations, namespaced `domain.code` refs) — with the convention `is_runnable = not pending_signatures`. It backs the recursive top-down build flow (a method built layer-by-layer with `allow_signatures=True`; design: `../mthds-plugins/wip/recursive/design.md`). The verdict is surfaced on the agent-CLI / builder validate envelopes and on the protocol-level `PipelexMTHDSProtocol.validate` (`PipelexValidationReport` extension fields).

**It does NOT reach this API automatically.** A correction to an earlier assumption ("pipelex-api serializes whatever protocol `validate` returns"): this repo's `/validate` route (`api/routes/pipelex/validate.py`) does not call `PipelexMTHDSProtocol.validate` at all. It builds its own strict pydantic `ValidateResponse` envelope and gets its data from `validate_bundle` (direct mode) or `act_dry_validate` (Temporal mode) — and neither the envelope nor the activity result carries the verdict. So without the changes below, the webapp's Dry Run button (which lands on `/validate`) and any API-driven build flow stay blind to what remains to implement.

## The change (two repos, ordered)

1. **pipelex (prerequisite — must ride the release this repo's pin bumps to):** add `pending_signatures: list[str] = Field(default_factory=list)` to `DryValidateResult` (`pipelex/temporal/tprl_pipe/act_dry_validate.py`) and populate it in `act_dry_validate` from its `validate_result.pending_signatures`. The activity already holds the result in hand. No wire-compat concern — the Temporal integration has never shipped to prod.
2. **pipelex-api:** add to `ValidateResponse` (`api/routes/pipelex/validate.py`):
   - `pending_signatures: list[str] = Field(default_factory=list, description="Qualified refs of pipes still declared as PipeSignature — unimplemented forward declarations")`
   - `is_runnable: bool = Field(default=True, description="True iff pending_signatures is empty")`

   Populate on both backends, mirroring the runtime convention `is_runnable = not pending_signatures`:
   - **Direct path** (`_validate_direct`): from `validate_bundle_result.pending_signatures` — already available.
   - **Temporal path** (`_validate_via_temporal`): from `dry_validate_result.pending_signatures` — available once step 1 ships.

   No request-side change: `MthdsContentsRequest` already carries `allow_signatures`, and both paths already thread it into `validate_bundle`.

## Semantics to preserve (don't re-derive, don't broaden)

- **Lenient-path-only field.** With `allow_signatures=False` (the default), an unsatisfied signature makes `validate_bundle` raise `ValidateBundleError` → the existing RFC 7807 422. The fields only ever carry data on a lenient success. A complete bundle on either mode reports `[]` / `true`.
- **Entries are namespaced `pipe_ref`s** (`domain.code`), sorted, library-wide, cross-package refs excluded — computed by `build_pending_signatures` in `pipelex/pipeline/validate_bundle.py`. Pass the value through verbatim; never recompute it API-side (e.g. from the blueprints' pipe types).
- **The graph stays best-effort and unrelated.** `dry_run_pipeline` failing on a signature-reaching main pipe already degrades to `graph_spec=None` without failing the request — the verdict fields don't change that.
- **`main_pipe` is still required** by this endpoint (API-side precondition). A signature-only bundle whose `main_pipe` is itself a signature is a legitimate lenient-validation subject — worth a test that it returns 200 with the pending list rather than tripping on the graph arm.

## Tests

- Direct mode: lenient request with an unsatisfied signature → 200, `pending_signatures == ["<domain>.<code>"]`, `is_runnable == false`; complete bundle → `[]` / `true`; strict request with a signature → 422 (existing behavior, pin it next to the new fields).
- Temporal mode: same pair through `wf_dry_validate`, pinning that the field crosses the activity boundary.
- Reusable fixtures exist in pipelex: `tests/e2e/pipelex/pipes/additive_multi_file_library/signature_only/` (header + concepts, no definition) and `header_and_definition/` (complete).

## Downstream consumer — pipelex-app (small, optional, after this ships)

`pipelex-app`'s validator action (`src/actions/mthds-validator.ts`) calls `/v1/validate` via the `mthds` TS SDK, which relays the body verbatim — so no SDK change is needed; the new keys simply appear in the JSON. To *use* them, the app extends its local `PipelexValidationReport` interface + `MthdsValidatorResponse` with the two optional fields and surfaces the verdict in the build/method UX ("not yet runnable — N signatures pending", listing the refs). Both fields are optional in the TS types, so the app tolerates older API deployments during rollout. That work is UX-driven and gated on this repo's deploy — it does not need its own handoff doc; point the implementer here.

## Ship sequence

1. pipelex release lands (carrying the recursive-design branch + the `DryValidateResult` field).
2. This repo: bump the `pipelex` pin, make the `ValidateResponse` change, CHANGELOG entry, version bump.
3. Hosted rollout per the platform playbook: `pipelex-api-deploy` (`API_VERSION` / `PIPELEX_API_SOURCE`), then `api_image_tag` in `pipelex-api-infra`. The Temporal path also needs the worker image (`pipelex-worker`) on the same pipelex version so `act_dry_validate` returns the field.
4. pipelex-app picks up the fields whenever the UX work is scheduled.
