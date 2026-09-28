# `scripts/sync_vendored_kit.py` — review follow-ups

**Status:** findings deferred by the review rounds of the branch that introduced `make kit-sync` and `make kit-check`, each with the reason it was not fixed then. The first is confirmed and owed before the next release; the others were not reproduced. Round 4 ran at the `freeze` bar and raised the first three again, Codex adversarial rating the dropped enabled backend high, so they are the first to take up.

## The changelog and the bump skill name the wrong `pipelex` release for the stale backend files

Raised by cubic in round 3, as a P3, at `CHANGELOG.md` (the "Own-key backends offer the pinned `pipelex` model roster" entry) and `.claude/skills/bump-pipelex/SKILL.md` (the trap paragraph), and confirmed. Both say the `inference/backends/` files stayed at `pipelex` 0.14.0. Hashing each backend file of this repository's initial commit against `pipelex`'s kit at every release tag matches only the 0.20.x series (0.20.0 to 0.20.13), so the rosters were the `pipelex` 0.20 kit's; the later commits to those files removed `prompting_target` (v0.14.0 of this repository), regenerated the gateway model tables (v0.27.0) and renamed bedrock's SDK (the 0.69.0 bump), and moved no roster. Both texts should say "the `pipelex` 0.20 kit's" before the entry is released. It was deferred because round 3's bar admitted only a defect of round 2's own changes or one that could not ship, and the entry is still under `[Unreleased]`.

## An enabled backend the kit drops is only noted

Raised by cubic in round 3, as a P3, at the `Note:` line in `main`. When the kit removes or renames a backend this image has switched on, the sync prints a note, exits 0, and the successor arrives switched off; `make kit-check` then passes. Refusing when a dropped switch was `true` would hand the decision to the person doing the bump, who would carry the switch over by adding the successor's table with `enabled = true` to the vendored `backends.toml` before running again. Deferred because no kit has renamed an enabled backend and the note already names the backend.

## A sync that refuses an unhandled kit entry has already written the rest

Raised by Codex adversarial in round 3, as medium, at `main`. When the kit ships an `inference/` entry no rule covers, sync mode applies the plan for the files it does handle and only then exits 1, so the working tree holds a half-adopted kit. Checking `unhandled_kit_entries` before `apply_plan` would leave the tree untouched. Deferred because the tree is tracked, so the partial write is visible in `git status` and undone with `git checkout`.

## The configuration page does not mention `backends_override.toml`

Raised by cubic in round 4, as a P3, at `docs/configuration.md`. The new paragraph tells an operator who mounts their own `backends.toml` to take a fresh copy with each newer image, while pipelex 0.69.0 also layers `inference/backends_override.toml` over the shipped file (`backends_file_paths` in `pipelex/system/configuration/config_loader.py`), so an override holding only `[openai]` and `enabled = true` would survive every re-sync. The same page is said to state elsewhere that `backends.toml` is not layered. Deferred at the `freeze` bar.

## The test fixture copies all of `.pipelex/`

Raised by cubic in round 4, as a P3, at the `vendored_copy` fixture in `tests/unit/test_sync_vendored_kit.py`. The fixture copies the whole directory once per test, and in a developer checkout that includes the untracked `.pipelex/storage/` run data; `plan_sync` reads only `inference/`, so copying that alone would keep the tests fast. Deferred at the `freeze` bar.

## `scripts/` is outside the type checkers' scope

Raised by cubic in round 2, as a P3, at `render_backends_toml`. `pyproject.toml` points pyright at `api` and `tests` and mypy at the same two packages, so neither checks `scripts/sync_vendored_kit.py` or `scripts/export_openapi.py` on `make agent-check`. The sync script was clean under pyright when checked by hand, and its switch logic is covered by `tests/unit/test_sync_vendored_kit.py`, which loads it from its path. Bringing `scripts/` into both checkers is a repo-wide configuration change, larger than the review's `defects` bar admitted.

## Fixed in the same branch

A missing, malformed or empty vendored `backends.toml` used to sync every backend off, `pipelex_gateway` and `internal` included, after which `make kit-check` passed; `make cleanall` reached that state, because its `cleanconfig` step deleted the tracked `.pipelex/` directory. Round 2 made both modes stop without writing in those cases and took `cleanconfig` out of `make cleanall`. Round 3 closed the partial cases the round-2 guard left open: a table with no `enabled` key is now carried as on, which is how pipelex reads it, and a backend whose file is already vendored but whose table is missing stops the sync instead of arriving switched off as if it were new.
