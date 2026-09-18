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
