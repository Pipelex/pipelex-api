# Findings deferred on the runner's structured-logs member

The per-round traces for L-260916-b7759f, the pipelex-api member of sprint L-260916-a8cdd2; the campaign documents live at the workspace root under `wip/structured-logs/`. A finding lands here when a round read it as real and did not act on it at that pass's bar. An entry marked *unverified* rests on the reviewer's word alone: nobody reproduced it. Findings owned by another repo become ledger items instead, and those are named here rather than described twice.

## L-260916-b7759f, round 1 (bar `open`)

Three reviewers ran against `origin/dev`: cubic, `codex:review` and `code-review` at level medium. Codex returned no findings. Eight distinct findings merged; seven were verified in one batch and all seven confirmed, and they were fixed in this round. What follows is the one deferral and the two findings routed to `pipelex`.

- **The `[tool.uv.sources]` pin names a bare commit, so a rebase or a deletion upstream breaks every job at once** (*unverified*, code-review). CI runs `uv lock --check` and `uv sync --frozen` on every pull request and the Docker build resolves the same rev, so an unreachable commit would fail all of them together with nothing in this repository to fix. Deferred because the exposure is the sprint's own and already has an owner: the pin is temporary by construction, the sprint's pin table records the collapse — replace the git source with the shipped version and re-lock — and the upstream branch is a member of the same sprint rather than a stranger's. Pinning a tag instead would mean cutting one per runtime commit the runner needs, which costs more than the window it closes. Left unverified because it describes a future state of the remote rather than a property of the code.

Two findings were confirmed here but belong to the runtime, and both carry the verification in their notes:

- The console sink renders only the message and ignores every carried attribute, so the fields this member moved out of the message are invisible on it — `L-260918-f7062d`. Its own documentation was corrected in this round to say the fields are structured-sink-only.
- The json sink writes Rich markup verbatim into the message, so a structured line carries console styling — `L-260918-83431f`, found on the sibling `pipelex-server` member and visible in this repository's own test output.

### What the round fixed, for the record

The image no longer copies any of the runtime's per-developer configuration tiers, verified by building a throwaway context and listing what survived; the configuration test reads the shipped file instead of the merged one, so a developer's local `sink = "console"` no longer reds a suite that CI keeps green; three places claiming a Rich-free boot were corrected, Rich being installed unconditionally through `typer` and `instructor`; `detail` is documented as riding the API-authored path only; the documented whole-directory config mount now warns that it drops the logging keys; and the boot refuses a `pipelex` without `log.context` by name, since the branch and the published release declare the same version and no specifier can tell them apart.

## L-260916-b7759f, round 2 (bar `defects`)

Two reviewers ran against `origin/dev`: cubic and `code-review` at level medium; Codex is disabled in this machine's review policy. Three findings; two were verified in one batch, both confirmed and fixed in this round. One is deferred.

- **The release and bump-pipelex skills do not know about the git source** (*unverified*, cubic). `[tool.uv.sources]` overrides the `pipelex` specifier, but `.claude/skills/release/SKILL.md` still asserts an exact `==A.B.C` pin and `.claude/skills/bump-pipelex/SKILL.md` edits only that specifier, so neither removes the source, and no CI check refuses a release whose runtime resolves from git. Deferred as an improvement rather than a defect at this bar: the collapse is a step of the sprint's own train — the merge gesture replaces the git source with the shipped version and re-locks before this branch reaches `dev` — and a sprint pin found on a base branch is refused by the sprint frontier until it comes off. A CI guard or a skill step would make the collapse independent of the sprint, which is worth doing once this repository's pins are no longer only a sprint's.

### What the round fixed, for the record

The middleware test meant to prove the per-request log binding is released could not fail: `TestClient` runs the app in a portal thread whose context never reaches the test's, and a mutation that never released the binding left the whole unit suite green. It now drives the middleware on the test's own event loop and goes red under that mutation. The runtime-contract check was unreachable on the install it exists for: `api.main` validates the config at import, and a published `pipelex` refuses this server's `sink` key there, before `lifespan` ever ran the check. The check now runs at import, above the first config read, a structural test pins its place, and the docstrings, the comment and the changelog entry say what actually happens.

## L-260916-b7759f, round 3 (bar `necessity`)

cubic and `code-review` at level medium ran against `origin/dev`; code-review returned no findings. One finding, deferred at this bar because the previous pass did not introduce it and shipping without it loses no data.

- **The body-size middleware's 413 writes no `api_error` record** (*unverified*, cubic). `api/middleware.py::_too_large_response` builds the problem document and returns it without going through `api.exception_handlers._emit_api_error`, so an oversized-body rejection never reaches the structured error stream, while `docs/logging.md` says an error response produces exactly one line. The cure is either to emit the record from the middleware — it holds the `Request` for `route`, and `request_id` comes from the bound context — or to qualify the doc so an operator does not query for a line that is never written.
