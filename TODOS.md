# Plan: unify the codegen/build underpinnings (without merging the routes)

Status: **Phase 1 complete** (uncommitted, working trees of `pipelex/`, `pipelex-api/`, and the workspace spec). Phases 2–5 not started. This plan replaces the previous TODOS.md. It spans `pipelex-api/` (main), `pipelex/` (engine hygiene), the workspace spec (`Pipelex/docs/specs/pipelex-codegen.md`), and three JS consumers. No backward compatibility is owed anywhere — breaking wire changes are fine, noted in changelogs.

## Background — read this first on a cold start

The question that triggered this plan: *should `POST /v1/codegen` and `POST /v1/build/inputs` be united into one route?* The answer is **no — but they must share more of their underpinnings than they do today.** The full reasoning:

**The dividing line is tracked-artifact vs editable scaffold, not codegen vs build.** The engine draws it explicitly in `pipelex/pipelex/codegen/emission.py` (module docstring): "Input templates (`codegen inputs`) are deliberately *not* stamped or locked: they are user-editable scaffolds, not tracked generated code." `/codegen`'s whole contract is the stamp/lock/offline-check trust chain (`CodegenValidReport.lock` is required; the docstring promises byte-identical materialization and a passing offline `codegen check`). An `inputs` kind cannot honor that, and the axes don't align either: `target` (ts-zod / python-pydantic / python-structures) is meaningless for inputs, while inputs' own axes (json/toml format, `--explicit`) are meaningless for types. Merging would fork the request and response contracts per kind — unification in name only.

**The durable route-membership rule** (this is the principle every change below serves): *a projection that rides the stamp/lock trust chain lives on `/codegen` (per-pipe kinds select via the already-reserved `pipe_ref` field); an editable scaffold lives on `/build`.* Future per-pipe kinds from the spec (`docs`, `tools`, `tests`) will be stamped generated code → they land on `/codegen`. Inputs stays on `/build` for a reason that won't age.

**Drift discovered while answering the question** (these are the things to fix):

1. **Stated rationale is incidental, not principled.** `CodegenRouteKind`'s docstring (`api/routes/pipelex/codegen.py`) justifies the split as "mirroring the agent CLI's deliberate absence of a `codegen inputs` mirror" — which wobbles the moment you notice the *bare* CLI **does** have `pipelex codegen inputs` (`pipelex/pipelex/cli/commands/codegen/inputs_cmd.py`). Replace with the trust-chain rule.
2. **Engine docstring contradiction.** `CodegenKind`'s docstring (`pipelex/pipelex/codegen/emitters/target.py`) claims "Only the kinds that emit tracked artifacts today are listed: `types` … and `inputs`", directly contradicting `emission.py`'s "inputs deliberately not stamped or locked". `emission.py` is authoritative — the CLI writes inputs via plain `save_text_to_path`, bypassing the stamping layer entirely.
3. **Request-envelope drift.** `/resolve` and `/codegen` ride `MthdsFilesRequest` (`files[]` with per-file `source` labels XOR `method_ref`, 501 until the method registry lands) — `api/schemas/models.py`. The `/build/*` routes still ride the older `MthdsContentsRequest` (bare `mthds_contents` strings + `allow_signatures`). Consequences: build diagnostics can't carry a `source`, and when `method_ref` resolution lands, `/build/*` needs a second breaking migration.
4. **Verdict-vocabulary divergence.** The bare CLI's `pipelex codegen inputs` is **static** (crate loader, no dry-run), while `/build/inputs` and `/build/output` **dry-run** the requested pipe via `validate_bundle(dry_run_pipe_codes=[...])`. Same projection, two verdict semantics. The engine treats template rendering as a static read of declared inputs; runnability is `/validate`'s vocabulary. `/build/runner` is different: it genuinely needs the dry-run (a pipe recorded SKIPPED by the sweep → honest 422, per spec).

**Key plumbing fact enabling the fix:** `resolve_requested_crate()` in `api/routes/pipelex/crate_ops.py` inherits the engine's loaded-on-success contract — on success the library is loaded + current so a route can read live pipes from it (its docstring literally names "input templates" as the use case), and the route owns teardown via `teardown_current_library()`. So `/build/inputs` and `/build/output` can become thin per-pipe reads over the *same* core `/codegen` uses: shared closure selector, shared static resolution, shared `CrateInvalidReport` invalid arm — two presentations of one engine. That is the real unification.

**Consumers of `/build/inputs` today** (all post `{mthds_contents, pipe_code}`):

- `mthds-js/src/runners/api/client.ts` — `buildInputs()` (posts to `build/inputs`)
- `pipelex-sdk-js/src/client.ts` — `buildInputs()` (same shape)
- `pipelex-app/src/actions/build-inputs.ts` — `buildInputsTemplate()` server action → `src/components/deploy/deploy-dialog.tsx` (+ its `__tests__`), `src/lib/deploy-snippets.ts`
- `pipelex-api/.claude/skills/postman-bundle/` — request templates for the build routes
- `n8n-nodes-pipelex/` — checked: does **not** call `build/inputs`

**Reference implementations to mirror:** `pipelex/pipelex/cli/commands/codegen/inputs_cmd.py` (per-pipe selection over a loaded crate: qualified `--pipe` ref, defaults to the closure's `main_pipe`) and `api/routes/pipelex/codegen.py` (the `resolve_requested_crate` → work → `teardown_current_library()` in `finally` pattern).

**Spec anchor:** `Pipelex/docs/specs/pipelex-codegen.md` — sections "Two axes", the HTTP request-envelope paragraph, and the paragraph describing the `/v1/build/*` migration onto the verdict discipline. Spec edits ride the same phase as the code they describe; run `make check-spec-links` in `conformance/` after spec edits.

## Decisions

All decisions are taken (conversation of 2026-07-13). None remain pending.

- **Do not merge** `/codegen` and `/build/inputs`. Adopt the trust-chain rule as the stated route-membership criterion.
- **Migrate `/build/*` onto the `MthdsFilesRequest` selector** (`files[]` XOR `method_ref`), breaking the wire shape.
- **Make `/build/inputs` and `/build/output` static** (ride `resolve_requested_crate`, drop the dry-run). `/build/runner` keeps its dry-run (SKIPPED detection is load-bearing).
- `allow_signatures` disappears from `/build/inputs` and `/build/output` (it only parameterized the dry-run sweep). `/build/runner` keeps it.
- **D1 — pipe selector:** `pipe_ref` — qualified `domain.pipe_code`, **optional**, defaulting to the closure's declared `main_pipe` (422 when omitted and the closure declares none). Replaces `pipe_code` on all three build routes. Matches `pipelex codegen inputs --pipe` and the field `/codegen` reserves. Mirror `inputs_cmd.py` for the default-resolution semantics.
- **D2 — `CodegenKind.INPUTS`:** **remove it** from the engine enum; the enum is the stamped-kind vocabulary and nothing can ever stamp an inputs file. The Phase-1 grep is a verification step, not a decision gate — if it turns up a live reference (stamp parsing, telemetry), surface it before proceeding rather than silently keeping the member.
- **D3 — CLI parity on `/build/inputs`: FULL parity** (user chose this over the light-JSON-only recommendation). The request gains `format` (`json` | `toml`, default `json` — reuse `InputsTemplateFormat` from `pipelex.core.pipes.inputs.input_renderer`) and `explicit` (bool, default false — the ceremonial `{concept, content}` envelope). Render via the same `render_inputs` / `render_inputs_toml` the CLI uses, and mirror the CLI's semantics exactly, including which `format`×`explicit` combos it allows or rejects. **Response-shape consequence:** TOML cannot ride as a parsed dict — the valid arm must carry the template as a parsed `dict` for `format=json` but a raw string for `format=toml`. Design this deliberately in Phase 2 (e.g. echoed `format` + a single content field whose JSON type follows it, or two mutually exclusive fields with a model validator) — do not silently stringify the JSON case, since the dict shape is what the deploy dialog and SDKs consume.
- **D4 — sequencing:** Phase 1 ships as **its own PR** (non-breaking hygiene lands immediately); the breaking migration (Phases 2–4) follows as a coherent breaking change.

## Phase 1 — rationale & engine hygiene (non-breaking, shippable as its own PR per D4) — ✅ DONE

- [x] `pipelex/`: grep usages of `CodegenKind.INPUTS`, then **remove it** per D2. **Outcome: it was dead.** Every construction site in the repo (stamp writer, emission, both CLIs, agent CLI, `build runner`, all tests) passes `CodegenKind.TYPES`; nothing anywhere referenced `.INPUTS`. Removed with no call-site changes.
- [x] `pipelex/`: fix the `CodegenKind` docstring in `pipelex/codegen/emitters/target.py` — now states the trust-chain rule ("a kind is a member exactly when its artifacts ride the stamp/lock/offline-check trust chain"), names input templates as the standing counter-example, and points at `emission.py`. The module docstring's `kind`-axis parenthetical no longer names `inputs` as an enum value.
- [x] `pipelex/`: spec adjusted — new **"Tracked artifact vs editable scaffold"** paragraph in `Pipelex/docs/specs/pipelex-codegen.md` → "Two axes". `make check-spec-links` in `conformance/`: **OK** (no headings renamed, so all `> Verified by:` links hold).
- [x] `pipelex-api/`: `CodegenRouteKind` docstring in `api/routes/pipelex/codegen.py` rewritten to the trust-chain rule; `docs/codegen.md` `kind` bullet rewritten to match.
- [x] Changelogs — see the **deviation** recorded at the checkpoint below: no standalone `pipelex/` entry (the enum is unreleased); `pipelex-api/`'s existing `[Unreleased]` `/v1/codegen` bullet reworded to carry the trust-chain rationale. The docstring **is** wire-visible → `make openapi-export` run, artifact committed (diff is the `CodegenRouteKind.description` only; the enum's served values were already just `types`).
- [x] `make agent-check` + `make agent-test` in both repos — all green. Also `make openapi-check` in `pipelex-api`: artifact up to date.

**CHECKPOINT 1** — reached 2026-07-13. Phase 1 is self-contained and non-breaking; Phase 2 can start in a fresh session.

**D2 outcome (settled, no surprise).** `CodegenKind.INPUTS` was a dead member. Beyond the grep: `pipelex/codegen/` **does not exist at the latest tag (`v0.38.0`)** — the whole codegen engine is unreleased — so its removal cannot break a released surface, and no stamp with an `inputs` projection line has ever been written by any shipped version (`stamp.py::_kind_from_value` will now simply fail to parse a hand-forged one, which is correct). The trust-chain rule is confirmed *in code*, not just in prose: `pipelex/cli/commands/codegen/inputs_cmd.py` writes templates with a plain `save_text_to_path`, never touching `apply_stamp` or the lock.

**Deviation from the plan (deliberate): no standalone `pipelex/` changelog entry.** The plan called for one ("docstring/enum hygiene"), but `CodegenKind` ships for the *first* time in the same `[Unreleased]` block — a "removed `CodegenKind.INPUTS`" bullet would document the removal of something that same release introduces, i.e. pure churn for the reader. Verified instead that the existing `[Unreleased]` text is already accurate and already states the rule ("Input templates (`codegen inputs`) are deliberately not stamped or locked — they are user-editable scaffolds, not tracked generated code"). `pipelex-api` is the same case (the `/v1/codegen` route is also unreleased), so its `[Unreleased]` bullet was **reworded in place** rather than given a new entry.

**Extra fix, same drift, not in the original box list.** Drift #1 (the incidental "mirroring the agent CLI" rationale) was *also* present in the spec's "Route envelopes" section, i.e. in the most authoritative document. Fixed there too — it now defers to the trust-chain rule. Left deliberately alone: the spec's *agent-CLI* sentence ("there is deliberately no `pipelex-agent codegen inputs` — the existing `pipelex-agent inputs` group already surfaces that projection"). That statement is about the agent CLI's own surface and is true on its own terms; the drift was only ever in *borrowing* it as the route-membership reason.

**Files touched in Phase 1:**

- `pipelex-api/`: `api/routes/pipelex/codegen.py`, `docs/codegen.md`, `CHANGELOG.md`, `docs/openapi/pipelex-api.openapi.yaml`, `TODOS.md` — committed on `chore/codegen-trust-chain-rationale`, PR opened against `feature/Codegen`.
- `pipelex/`: `pipelex/codegen/emitters/target.py` (module docstring, `CodegenKind` docstring, `INPUTS` member removed). **Still uncommitted** — the repo is on branch `dev`, so branch before committing.
- workspace: `docs/specs/pipelex-codegen.md` ("Two axes" + "Route envelopes"). **Still uncommitted.**

**Carried into Phase 2 (found while reading, saves a re-read).** `inputs_cmd.py::_default_main_pipe_ref` is the reference semantics for D1's optional `pipe_ref`: it collects `f"{domain_code}.{domain.main_pipe}"` over every domain in the crate that declares one, then errors on **zero** candidates *and* on **more than one** (ambiguous closure). The route must mirror both arms — the plan's D1 only names the zero case ("422 when omitted and the closure declares none"); **the ambiguous case needs a 422 too.** Pipe lookup itself is `get_required_pipe(pipe_code=<qualified ref>)`, raising `PipeLibraryError` on a bad ref (→ 422, a request-shape error, not an invalid-crate verdict).

## Phase 2 — server: `/build/inputs` + `/build/output` → files[] envelope + static core

- [ ] New request models: subclass `MthdsFilesRequest` adding `pipe_ref` (qualified, optional → `main_pipe`, per D1). Drop `allow_signatures`. Keep `MAX_PIPE_CODE_LEN`-style bounds on the ref. Note: `MthdsContentsRequest` stays — `/validate` still uses it (protocol-owned envelope, out of scope here).
- [ ] `/build/inputs` request additionally gains `format` (`InputsTemplateFormat`, default json) and `explicit` (bool, default false) per D3 — mirror the CLI's allowed combos exactly.
- [ ] Design the `/build/inputs` valid arm for D3's dict-or-string consequence (parsed `dict` for json, raw string for toml; echoed `format` + `explicit`); keep `/build/output`'s `output: dict` shape unchanged (it already has its own `ConceptRepresentationFormat` field).
- [ ] Rewrite `api/routes/pipelex/build/inputs.py`: `resolve_requested_crate(request_data)` → catch `ValidateBundleError` → `invalid_crate_report_response` (unchanged invalid arm); on success read the pipe from the loaded library (mirror `inputs_cmd.py` for qualified-ref lookup + `main_pipe` default), render via `render_inputs` / `render_inputs_toml`, tear down with `teardown_current_library()` in `finally`. The route also inherits the `method_ref` → 501 behavior — declare `PROBLEM_501_METHOD_REF` on the decorator like `/codegen` does.
- [ ] Same rewrite for `api/routes/pipelex/build/output.py` (via `render_output`).
- [ ] Valid arms: echo the pipe selector as requested + as resolved (so a defaulted `main_pipe` is visible to the caller).
- [ ] Update unit tests (`tests/unit/`) and any e2e touching these routes; verify `tests/unit/test_openapi_contract.py` still pins the `x-mthds-protocol` set unchanged (these routes were never tagged).
- [ ] Docs: `docs/codegen.md`, `docs/pipe-builder.md`, `docs/index.md` where the build envelopes are described; state the static-verdict semantics (structural invalidity only — runnability is `/validate`'s vocabulary; a valid build verdict is not a promise the pipe runs).
- [ ] Spec: update the `/v1/build/*` paragraph in `Pipelex/docs/specs/pipelex-codegen.md` (new envelope, static verdicts for inputs/output, `allow_signatures` removal); update the conformance skeletons it names; `make check-spec-links` in `conformance/`.
- [ ] `make openapi-export` + commit artifact; CHANGELOG entry (breaking: build route request/response shapes).
- [ ] `make agent-check` + `make agent-test`.

## Phase 3 — server: `/build/runner` → files[] envelope (keeps its dry-run)

- [ ] Read `api/routes/pipelex/build/runner.py` fully before touching it — it already straddles both worlds (dry-run via `validate_bundle` **and** a crate-based stamped `structures/` projection built inline with `normalize_crate` + `emit_types`). Migration opportunity: after the envelope switch it may be able to reuse `resolve_requested_crate`/`crate_ops` for the crate half instead of its inline duplication — assess, don't force.
- [ ] New request model: subclass `MthdsFilesRequest` + pipe selector (per D1) + keep `allow_signatures` (the sweep needs it).
- [ ] Keep the SKIPPED → 422 no-verdict carve-out and the stamped structures projection exactly as spec'd.
- [ ] Tests, docs, spec paragraph, `make openapi-export`, CHANGELOG, `make agent-check` + `make agent-test` — same drill as Phase 2.

**CHECKPOINT 2** — server surface is fully migrated and the OpenAPI artifact regenerated; consumers are now broken against a locally-run server. Update this file with the final wire shapes (paste the new request/response JSON of each route) so Phase 4 can run without re-reading the server code.

## Phase 4 — consumers

- [ ] `mthds-js`: update `BuildInputsRequest` type + `buildInputs()` in `src/runners/api/client.ts` (and the build/output/runner siblings) to the new envelope; update its tests.
- [ ] `pipelex-sdk-js`: same in `src/client.ts`; remember the one-way dep `@pipelex/sdk → mthds` — if the request types live in `mthds/protocol` types, fix them there first. Check whether these are protocol-typed or SDK-local (build routes are Pipelex extensions, so they should be SDK-local — flag if not).
- [ ] `pipelex-app`: `src/actions/build-inputs.ts` (new payload; pipe selector per D1 — the deploy dialog currently holds bare pipe codes), `src/components/deploy/deploy-dialog.tsx` + `__tests__`, `src/lib/deploy-snippets.ts` (the snippets it renders for users must show the new wire shape too).
- [ ] `pipelex-api/.claude/skills/postman-bundle/`: update the build-route request templates; run `/update-postman` if the live collection carries the old shapes.
- [ ] Each repo: its own test suite + changelog.

**CHECKPOINT 3** — end-to-end: run the API locally (`make run`), drive the webapp deploy dialog against it, and exercise `buildInputs` from both JS clients (their test suites or a scratch script). Record results here.

## Phase 5 — final sweep

- [ ] Re-read this file top to bottom; every unchecked box is either done-and-checked or explicitly moved to follow-ups with a reason.
- [ ] `pipelex-api`: `make openapi-check` clean; full `make agent-check` + `make agent-test` in every touched repo.
- [ ] Verify spec/conformance sync one last time: `make check-spec-links` in `conformance/`.

## Out of scope / follow-ups (deliberately not in this plan)

- **`/validate`'s envelope** stays on `MthdsContentsRequest`: it is an MTHDS Protocol route, so moving it to `files[]`-with-sources (which would benefit its diagnostics most of all) is a protocol-level change owned by the `mthds/` spec — raise separately.
- **Hosted deploy dance** (bump `API_VERSION` in `pipelex-api-hosted/`, `api_image_tag` in `pipelex-api-infra/`) happens at release time, not in this plan.
- **Future `/codegen` kinds** (`docs`, `tools`, `tests`): when they arrive they take `pipe_ref` on `/codegen` per the trust-chain rule; the reserved-field validator in `codegen.py` already documents how an arm gets added.
