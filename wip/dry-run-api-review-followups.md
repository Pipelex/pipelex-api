# Dry-run API PR — deferred review follow-ups

Follow-up items from the `xhigh` code review of PR #9 (`feature/Update-dry-run-api` → `dev`). The two load-bearing correctness bugs from that review — `/build/runner` returning 500 instead of 422 for a dry-run failure, and `/validate` leaking `validate_bundle`'s library on success — were fixed directly in the PR. **This file captures items #3–#12, which still need a decision.** Each is written to allow a cold start: read this file plus the cited line, and you have enough to discuss the item with the user without re-deriving the pipelex contracts.

## Shared ground truth (verified against the pinned pipelex `5fc2cbde`)

Trust these; they were read from `.venv/.../pipelex/` source, not inferred:

- `validate_bundle(mthds_contents=..., allow_signatures=..., dry_run_pipe_codes=[...])` opens **one** library, loads **all** blueprints, dry-runs the selected pipes. On **success** it leaves the library **open + current** (the "loaded-on-success" / D6 contract — its `finally` only cleans up `if not success`). On **failure** it tears down its own library and restores/clears the current-library ContextVar.
- `BundleValidator().validate_pipes(pipes, *, library_id, allow_signatures)` returns `dict[str, DryRunOutput]` keyed by namespaced `pipe_ref`. It **never** tears the library down. It raises `SignaturesNotAllowedError` (strict mode, a signature is reachable) or `DryRunError` (≥1 non-`allowed_to_fail` failure). A cross-package unresolved dependency is recorded `DryRunStatus.SKIPPED` (not raised).
- The **signature pre-pass runs over the `pipes` list it is given**. `validate_bundle` pre-filters that list to `dry_run_pipe_codes` via `_pipes_to_dry_run` *before* calling `validate_pipes`, so scoping the dry-run **also scopes the signature check**.
- Error-domain → HTTP status (`pipelex.base_exceptions.error_domain_to_http_status`): `INPUT → 422`, `CONFIG`/`RUNTIME`/unknown/`None → 500`. `DryRunError` sets **no** `error_domain` (→ 500). `SignaturesNotAllowedError` and `ValidateBundleError` are `INPUT` (→ 422).
- `DryRunStatus` = exactly `{SUCCESS, FAILURE, SKIPPED}`. `DryRunOutput` fields: `pipe_code` (bare), `pipe_ref` (namespaced `domain.code`), `status`, `error_message`.
- The current-library state (`set/get/clear_current_library`) is a per-async-task `ContextVar`, so concurrent FastAPI requests are isolated — **not** a race.

The four validation-performing routes and how they validate:
- `/validate` → `validate_bundle(...)` (whole bundle).
- `/build/inputs`, `/build/output` → `validate_bundle(..., dry_run_pipe_codes=[pipe_code])` (scoped to the requested pipe), then reuse the loaded-on-success library to read the pipe.
- `/build/runner` → opens its own library, loads blueprints, then `BundleValidator().validate_pipes(pipes=<ALL loaded>, ...)` directly (no `validate_bundle` wrapper).

---

## #3 — `/build/runner` sweeps the whole bundle; `/build/{inputs,output}` scope to the requested pipe

**Severity:** correctness / behavioral inconsistency (decide intended behavior). **File:** `api/routes/pipelex/build/runner.py:75`.

Runner passes the entire loaded `pipes` list to `validate_pipes`, so the strict signature pre-pass and the dry-run sweep see every sibling pipe. Inputs/output pass `dry_run_pipe_codes=[pipe_code]`, which `_pipes_to_dry_run` uses to narrow **both** the dry-run **and** the signature pre-pass to just the requested pipe.

**Concrete divergence:** a 2-pipe bundle where the requested pipe `echo` is valid and a sibling `broken` is an unimplemented `PipeSignature`:
- `POST /build/inputs  {pipe_code: "echo"}` → **200** (`broken` is never examined).
- `POST /build/runner  {pipe_code: "echo"}` → **422** `SignaturesNotAllowedError` (pre-pass sees `broken`).

Same bundle, same `pipe_code`, opposite outcomes. The inputs/output docstrings explicitly tout "Scoping the sweep to the requested pipe (`dry_run_pipe_codes`) avoids dry-running unrelated sibling pipes" — runner does not do this.

**Open question for the user:** which is correct? Two coherent positions:
1. **Runner should scope too** — a build-runner request is about one pipe; filter `pipes` to the requested pipe (plus, arguably, its transitive deps) before `validate_pipes`. Most consistent with inputs/output.
2. **Whole-bundle is intended for runner** — generating runnable code arguably wants the whole bundle sound. If so, the inconsistency is acceptable but should be a documented, deliberate choice (and the inputs/output docstrings shouldn't imply runner matches).

Note: runner can't pass `dry_run_pipe_codes` (it calls `validate_pipes` directly, which has no code filter — the filter lives in `validate_bundle._pipes_to_dry_run`). To scope it, either filter the `pipes` list in the route, or migrate runner onto `validate_bundle(dry_run_pipe_codes=[...])` like the others (but runner needs the library to stay open for `generate_runner_code`, which `validate_bundle` does provide on success — worth exploring as a unification).

---

## #4 — `_reject_if_requested_pipe_skipped` lets an `allowed_to_fail` requested pipe emit runner code despite a failed dry-run

**Severity:** correctness / narrow edge. **File:** `api/routes/pipelex/build/runner.py:40-45` (the `match`).

```python
match output.status:
    case DryRunStatus.SKIPPED:
        ... raise_validation_error(...)
    case DryRunStatus.SUCCESS | DryRunStatus.FAILURE:
        return
```

A non-`allowed_to_fail` `FAILURE` never reaches this guard — `validate_pipes._aggregate` already raised `DryRunError` and we never return from the sweep. So the **only** way a `FAILURE` survives into `sweep_result` is if the requested pipe's `pipe_ref` is listed in config's `dry_run_config.allowed_to_fail_pipes`. In that case this arm `return`s and the route emits runner code for a pipe whose own dry-run **failed** — the same "emit code for something that can't run" footgun the `SKIPPED` branch exists to prevent.

**Candidate fix:** treat a requested-pipe `FAILURE` like `SKIPPED` (reject with 422), or at minimum split the arm so `FAILURE` isn't silently bundled with `SUCCESS`. Keep the match exhaustive over `DryRunStatus` (no `case _`).

**Open question:** is "generate runner code for an allowed-to-fail pipe" ever desirable? Probably not for codegen, but confirm with the user whether `allowed_to_fail` is meant to be honored on the build routes at all.

---

## #5 — Bare-code matching in the SKIPPED guard can match the wrong entry (multi-domain)

**Severity:** low / edge. **File:** `api/routes/pipelex/build/runner.py:38`.

`if pipe_code not in (output.pipe_code, output.pipe_ref): continue` matches on the **bare** code, while `sweep_result` is keyed by namespaced `pipe_ref`. In a bundle spanning two domains that share a bare code (e.g. `a.echo` and `b.echo`), the loop returns on whichever entry is iterated first, possibly reading the wrong pipe's status.

Self-limiting: a later `get_required_pipe(bare_code)` raises an ambiguity error anyway, so the user gets *an* error — just a confusing "ambiguous pipe" 500 instead of the intended SKIPPED 422. Worth aligning the guard's matching with `get_required_pipe`'s disambiguation, or requiring a namespaced `pipe_code` on these routes.

**Open question:** do the build routes intend to accept bare codes at all, or should they require `domain.code`? That decision resolves both this and the ambiguity at `get_required_pipe`.

---

## #6 — inputs/output silently skip teardown if the loaded-on-success contract is ever violated

**Severity:** low / latent. **Files:** `api/routes/pipelex/build/inputs.py` and `output.py` (the post-`validate_bundle` `finally`).

```python
library_id = get_current_library_id_or_none()
try:
    ...
finally:
    clear_current_library()
    if library_id is not None:
        library_manager.teardown(library_id=library_id)
```

The route reads a ContextVar it doesn't own and tears down only `if library_id is not None`. The loaded-on-success contract currently guarantees a current library, so this holds today. But if `validate_bundle` ever returns success without leaving a current library (a future pipelex change, or a nested call that cleared it), teardown becomes a silent no-op and the library leaks — with no assertion guarding the assumption.

**Candidate fix:** assert the contract (`library_id is not None` after a successful `validate_bundle`) and raise a server error if violated, rather than silently skipping cleanup. Low priority; document the dependency at minimum.

---

## #7 — `allow_signatures` is undocumented

**Severity:** docs (workspace rule: document in the same change). **Files:** `docs/pipe-builder.md`, `docs/pipe-validate.md`.

`allow_signatures` (bool, default `false` = strict) is a new public request field on `/validate`, `/build/inputs`, `/build/output`, `/build/runner`. It appears **nowhere** under `docs/` (verified: `grep -rln allow_signatures docs/` → none). Its OpenAPI description lives only on `MthdsContentsRequest` in `api/schemas/models.py`. The "Request Fields" sections of `docs/pipe-builder.md` and `docs/pipe-validate.md` list only `mthds_contents` (+ `pipe_code`/`format`).

**Action:** add `allow_signatures` to the request-field tables on both doc pages, explaining strict (default) vs lenient (tolerates unimplemented `PipeSignature` placeholders, which dry-run trivially via a mock mint).

**Note for the next session:** also sanity-check whether `/validate`'s graph step honors the flag — `dry_run_pipeline` (the best-effort `graph_spec` builder at `validate.py:117`) does **not** accept `allow_signatures`, so an opted-in signature bundle validates 200 but silently returns `graph_spec = None`. Decide whether that's an acceptable documented limitation or a gap to thread through upstream.

---

## #8 — No CHANGELOG entry for this PR

**Severity:** docs. **File:** `CHANGELOG.md` `[Unreleased]`.

The `[Unreleased]` section documents the earlier RFC 7807 / request-id work but has **nothing** for this PR's user-visible changes: the `allow_signatures` request field across four endpoints, and the build-route library-lifecycle refactor. Add an `### Added` entry for `allow_signatures` and (if user-visible) a `### Changed` note for the validation/library-reuse change. Downstream consumers (`pipelex-api-deploy`, `n8n-nodes-pipelex`) rely on the changelog.

---

## #9 — opt-in test asserts only `status_code == 200`, no body

**Severity:** test coverage. **File:** `tests/unit/test_allow_signatures.py` (`test_signatures_accepted_when_opted_in`, the parametrized 200 test).

For `/build/inputs` and `/build/output`, the test only asserts `response.status_code == 200`. A regression returning 200 with an empty/`{}`/wrong-pipe body would pass green. Only `/build/runner` has a body-level assertion (`test_build_runner_generates_code_for_bundle_with_signatures_when_opted_in`).

**Action:** add body assertions for inputs/output (e.g. the rendered inputs JSON contains the expected input var; the output JSON has the expected concept/schema shape).

---

## #10 — SKIPPED-rejection test fully mocks `validate_pipes` and hardcodes the `error_message` shape

**Severity:** test fragility / coverage gap. **File:** `tests/unit/test_build_and_agent_routes.py` (`test_build_runner_rejects_when_requested_pipe_is_skipped`, ~line 667).

The test `mocker.patch(...BundleValidator.validate_pipes, AsyncMock(return_value=skipped_result))` and constructs a `DryRunOutput` with a hand-written `error_message` string. It never exercises the **real** cross-package SKIPPED path, so:
- drift between the hardcoded `DryRunOutput` shape (`pipe_code`/`pipe_ref` population, `error_message` wording) and what production `validate_pipes` actually emits won't be caught here;
- the route's bare-code matching (#5) breaking against real refs won't be caught here.

The `generate_spy.assert_not_called()` assertion **is** load-bearing and good. The gap is the absence of an integration-level test that drives a genuine cross-package-unresolved bundle through the real sweep.

**Action:** add a non-mocked test with a bundle whose requested pipe references a sub-pipe in an unincluded package, asserting the real 422.

---

## #11 — `make run-wip` re-runs a full `uv sync --all-extras` every invocation

**Severity:** dev-experience / efficiency. **File:** `Makefiles/Makefile.local.mk:45` (`run-wip` → `install-wip-pipelex` → `install`).

`run-wip` depends on `install-wip-pipelex`, which depends on `install` (a full `uv sync --all-extras`) before overlaying editable pipelex. So every `make run-wip` re-resolves and re-syncs (including re-fetching the git-pinned pipelex the developer is trying to override) and then re-installs editable — on every call. The header comment claims edits are "picked up on the next API restart (no reinstall)", but `run-wip` itself always reinstalls. pipelex-worker's equivalent avoids the `install` dependency.

**Open question:** intended? Options: (a) split a `run-wip-fast` that skips the `install` step for tight loops; (b) make `install-wip-pipelex` not depend on `install` and document that you run `make install` once first (matching the worker); (c) leave as-is and fix the comment. Confirm the desired dev loop with the user.

---

## #12 — Stale runner docstring

**Severity:** comment accuracy. **File:** `api/routes/pipelex/build/runner.py:57`.

The `build_runner` docstring still says: *"Matches the pattern in `build/inputs.py` and `build/output.py`."* That is no longer true — inputs/output were rewritten to **not** open their own library (they reuse `validate_bundle`'s loaded-on-success library), while runner still opens its own library and uses the `validate_pipes` inner sweep. Update the sentence to describe runner's actual (now distinct) pattern. (The rest of that docstring — `try`/`finally` teardown, `library_id` stays `None` until `open_library` returns — is still accurate for runner.)

---

## Suggested triage order

1. **#3** — decide runner sweep scope (drives whether #12's wording and possibly #4 change). Behavioral; needs a product call.
2. **#7 + #8** — docs + changelog; cheap, ship with the PR.
3. **#9 + #10** — test hardening; cheap.
4. **#4, #5, #6** — correctness edges; small, do once #3 is decided (the scope decision may absorb #4).
5. **#11, #12** — DX + comment; trivial.
