---
name: postman-run-bundle
description: >-
  Turn a Pipelex MTHDS bundle into an API request you can run — push it as a ready-to-run query into
  the live "Pipelex FastAPI" Postman collection, emit a curl command, or execute it directly. Use this
  whenever the user points at a bundle directory or a .mthds file (often with an inputs.json) and wants
  to run or test it via the API — e.g. "make a Postman query for this bundle", "add a Postman request to
  run cv_batch_screening.mthds", "run the fashion_moodboard bundle against the API", "give me the curl
  for this bundle", or just "postman" said alongside a bundle path. It resolves the bundle exactly like
  `pipelex run bundle <path>` (auto-detects bundle.mthds / the single .mthds, reads main_pipe, loads the
  sibling inputs.json) and targets /api/v1/pipeline/execute and /start. Trigger it even when the user
  does not name the endpoint or the word "skill".
---

# Postman Run Bundle

Create a runnable Postman request for one Pipelex MTHDS bundle and push it into the live **Pipelex FastAPI** collection, so the user can hit Send and run that bundle against a Pipelex API server. The query resolves the bundle the same way the CLI's `pipelex run bundle <path>` does.

This is a focused, one-bundle-at-a-time tool. It is **not** the full collection sync — for diffing the whole API surface against the code, that's the separate `/update-postman` command.

## What it produces

For the bundle the user names, it inserts requests into the collection under:

```
Run Bundle/
  <bundle>/            (named by the bundle's `domain`, or the filename)
    Execute (sync)     POST {{base_url}}/api/v1/pipeline/execute
    Start (async)      POST {{base_url}}/api/v1/pipeline/start
```

Each request body is:

```json
{
  "pipe_code": "<the bundle's main_pipe>",
  "mthds_contents": ["<full text of the .mthds file(s)>"],
  "inputs": { ...inputs.json copied verbatim... }
}
```

Requests use the collection variable `{{base_url}}` and inherit the collection's `{{auth_token}}` bearer auth — so the same query works against a local server or the hosted one by switching the Postman environment.

## How to run it

From the `pipelex-api` repo root, the make targets are the convenient wrapper (they run the script with the project venv):

```bash
make bundle-run     BUNDLE=<dir|.mthds>   # POST to a running API, print the response
make bundle-curl    BUNDLE=<dir|.mthds>   # emit a ready-to-run curl
make bundle-postman BUNDLE=<dir|.mthds>   # push Execute/Start into the Postman collection
make bundle-dry     BUNDLE=<dir|.mthds>   # print the request body only
# optional: ENDPOINT=execute|start|both  PIPE=<code>  INPUTS=<path>  NAME=<folder>  BASE_URL=<url>  TOKEN=<bearer>  ARGS=<extra>
```

Under the hood it's one script that resolves the bundle once and sends the request to one of four sinks. `<bundle-path>` is what the user pointed at — a bundle **directory** (preferred) or a single `.mthds` file. Call the script directly when you need a path outside the repo or finer control:

**Push into the Postman collection** (default). Source the env first so the Postman key is present (it lives in `~/.zshenv`):

```bash
source ~/.zshenv 2>/dev/null
python3 .claude/skills/postman-run-bundle/scripts/build_postman_query.py <bundle-path>
```

After a successful push, tell the user it's in Postman (it auto-syncs), and that they should set `base_url` and `auth_token` in their Postman environment, then Send.

**Run it yourself from Claude Code** — no Postman, no curl-by-hand. This POSTs the exact same body directly to a running API and prints the response:

```bash
python3 .claude/skills/postman-run-bundle/scripts/build_postman_query.py <bundle-path> --run
# defaults to http://127.0.0.1:8081 and no auth (AUTH_MODE=none). Override:
#   --base-url https://api.pipelex.com/runner   --token <bearer>
```

`--run` uses `Execute (sync)` so it waits for and prints the result; pass `--endpoint start` to fire-and-forget and get a `pipeline_run_id` back. The API must be up — start it locally with `make run` first. Heads-up: this triggers real inference (cost + latency), so for an image-gen bundle like `fashion_moodboard` confirm the user wants a live run.

**Emit a curl command** (to share, or to run in a separate terminal). It writes the body to a temp file and prints a `curl --data @file` so the multi-line `mthds_contents` never has to be shell-escaped:

```bash
python3 .claude/skills/postman-run-bundle/scripts/build_postman_query.py <bundle-path> --curl
```

**Preview only** — print the request body, touch nothing:

```bash
python3 .claude/skills/postman-run-bundle/scripts/build_postman_query.py <bundle-path> --dry-run
```

Useful options (apply across modes):

| Option | When to use |
|---|---|
| `--endpoint execute` / `start` / `both` | Pick the endpoint. Default `both` for Postman/curl; `--run both` runs `execute`. |
| `--inputs <path>` | Inputs JSON lives elsewhere, or you point at a `.mthds` file directly (file mode does not auto-detect inputs, matching the CLI). |
| `--pipe <pipe_code>` | The bundle has no `main_pipe`, or you want a different entry pipe. |
| `--base-url` / `--token` | Target server + bearer for `--run`/`--curl`. |
| `--name <folder>` | Override the per-bundle Postman subfolder name. |

### Which mode should I use?

- The user wants it **in Postman** to click around → default (push).
- The user wants **Claude Code to actually run it** → `--run` (this is the answer to "run the same query yourself").
- The user wants a **curl to paste elsewhere / share** → `--curl`.
- Just **confirming resolution** → `--dry-run`.

## Resolution rules (mirrors `pipelex run bundle`)

- **Directory** → use `bundle.mthds` if present, else the single `*.mthds` in the dir. Multiple `.mthds` and no `bundle.mthds` is ambiguous → ask the user to pass the file directly or use `--pipe`. The sibling `inputs.json` is auto-detected. Every `*.mthds` in the directory is sent (main one first), so multi-file bundles resolve.
- **`.mthds` file** → used as-is; inputs only come from `--inputs`.
- **`pipe_code`** comes from the bundle's `main_pipe` (override with `--pipe`).
- **`inputs`** is the inputs.json content copied verbatim — whatever concept/content shape it uses is exactly what the API receives, same as the CLI.

## Important: file/document inputs are out of scope

We are not doing uploads. If inputs.json references a **local file** (e.g. `"url": "inputs/cv.pdf"`), the script copies it verbatim and prints a warning listing those URLs. Pass that warning on to the user: those entries must be replaced with real `https://` URLs in Postman before the request will run. Prefer demoing this on a self-contained, text-input bundle (like `fashion_moodboard`).

A related limit: the API receives only the inline `mthds_contents`, not the bundle directory. Bundles that depend on Python structure classes in a `structures/` folder (rather than inline `[concept.X.structure]` blocks) won't be fully self-contained over the API — flag that if you see a `structures/` dir doing real work.

## If something's missing

- **No `POSTMAN_API_KEY`** → the script exits asking you to `source ~/.zshenv`. Do that and retry. If it's still unset, the user needs to add it to `~/.zshenv` (Postman → Settings → API keys → Generate).
- **No `main_pipe` and no `--pipe`** → the script exits; ask the user which pipe to run and pass `--pipe`.

## Constants

- Collection UID: `35082494-559c5753-885c-409a-af63-7647fe28d301` (Pipelex FastAPI). Override with `--collection-uid` for a different collection.
- Postman API base: `https://api.getpostman.com` (the script handles GET + PUT).
