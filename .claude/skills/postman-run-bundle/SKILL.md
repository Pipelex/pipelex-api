---
name: postman-run-bundle
description: >-
  Turn a Pipelex MTHDS bundle into an API request you can run — push it as a ready-to-run query into
  the live "Pipelex FastAPI" Postman collection, emit a curl command, execute it directly, or just
  dry-run validate it (no inference, no cost). Use this whenever the user points at a bundle directory
  or a .mthds file (often with an inputs.json) and wants to run, validate, or test it via the API —
  e.g. "make a Postman query for this bundle", "add a Postman request to run cv_batch_screening.mthds",
  "run the fashion_moodboard bundle against the API", "validate this bundle via the API", "dry-run this
  method against the API", "give me the curl for this bundle", or just "postman" said alongside a bundle
  path. It resolves the bundle exactly like `pipelex run bundle <path>` (auto-detects bundle.mthds / the
  single .mthds, reads main_pipe, loads the sibling inputs.json) and targets /api/v1/pipeline/execute,
  /start, and /api/v1/validate. Trigger it even when the user does not name the endpoint or the word
  "skill".
---

# Postman Run Bundle

Create a runnable Postman request for one Pipelex MTHDS bundle and push it into the live **Pipelex FastAPI** collection, so the user can hit Send and run that bundle against a Pipelex API server. The query resolves the bundle the same way the CLI's `pipelex run bundle <path>` does.

This is a focused, one-bundle-at-a-time tool. It is **not** the full collection sync — for diffing the whole API surface against the code, that's the separate `/update-postman` command.

## What it produces

For the bundle the user names, it inserts requests into the collection under:

```
Run Bundle/
  <bundle>/            (named by the bundle's `domain`, or the filename)
    Execute (sync)      POST {{base_url}}/api/v1/pipeline/execute
    Start (async)       POST {{base_url}}/api/v1/pipeline/start
    Validate (dry-run)  POST {{base_url}}/api/v1/validate
```

Which of these appears depends on `--endpoint`: the default `both` is `execute` + `start`; `validate` is its own choice and is never bundled with the run endpoints (different body, see below).

The **execute** (sync) body is:

```json
{
  "pipe_code": "<the bundle's main_pipe>",
  "mthds_contents": ["<full text of the .mthds file(s)>"],
  "inputs": { ...inputs.json copied verbatim... }
}
```

The **start** (async) body is the same plus a `callback_urls` array — the webhook(s) the runner POSTs the finished result to (single URL supported, see [Callback URLs](#callback-urls-the-async-start-endpoint) below):

```json
{
  "pipe_code": "<the bundle's main_pipe>",
  "mthds_contents": ["<full text of the .mthds file(s)>"],
  "inputs": { ...inputs.json copied verbatim... },
  "callback_urls": ["https://webhook.site/<your-endpoint>"]
}
```

The **validate** body is different — `/validate` takes **no `pipe_code` and no `inputs`**, only the bundle text:

```json
{
  "mthds_contents": ["<full text of the .mthds file(s)>"],
  "allow_signatures": false
}
```

(`allow_signatures` is emitted only when you opt in via `--allow-signatures`; the default is strict, and it's omitted from the body.)

Requests use the collection variable `{{base_url}}` and inherit the collection's `{{auth_token}}` bearer auth — so the same query works against a local server or the hosted one by switching the Postman environment.

## Validate = the API's dry-run

The API has **no separate "dry-run" endpoint that takes inputs**. `POST /api/v1/validate` *is* the dry-run: it parses, loads, and dry-runs every pipe with mock inputs and **zero inference** (no LLM calls, no cost, no latency), then returns the validated bundle blueprint, a `graph_spec`, and per-pipe input/output JSON-Schema `pipe_structures`. It confirms the whole pipeline wires up — concepts resolve, pipe inputs/outputs match, controllers find their sub-pipes — without ever running it. That makes it the safe, free counterpart to `execute`/`start`: reach for it to "just check" or "dry-run" a bundle.

Two things to know:

- **The bundle must declare a `main_pipe`.** `/validate` derives everything from the bundle text and rejects a bundle with no `main_pipe` (clean 422: *"Bundle does not declare a main_pipe, which is required for validation"*). Unlike the run endpoints, you cannot substitute `--pipe` — the endpoint takes no `pipe_code`. If the user's bundle has no `main_pipe`, tell them to declare one.
- **`allow_signatures`** (`--allow-signatures`) loosens one thing: by default the validation sweep rejects unimplemented pipe signatures; opt in and it tolerates them (each signature dry-runs trivially by minting a mock). Useful for an in-progress bundle whose pipes aren't all fleshed out yet.

## Callback URLs (the async `start` endpoint)

`/api/v1/pipeline/start` is fire-and-forget: it returns a `pipeline_run_id` immediately and POSTs the finished result to a webhook later. So a `start` request **must** carry `callback_urls`. The skill resolves it in this order:

1. **`--callback-url <url>`** if you pass one (repeatable — the field is a JSON list, but a single URL is the common case).
2. **`CALLBACK_URL`** in the environment or the repo `.env` (e.g. `CALLBACK_URL=https://webhook.site/<id>`). `make` exports `.env`, so the make targets pick it up automatically; the script also reads `.env` directly when invoked outside `make`.
3. **Ask the user.** If neither is set, the script stops with a clear error — ask the user for a callback URL (a `https://webhook.site/...` endpoint is the easy way to watch the result land) and re-run with `--callback-url`.

This only affects `start`. `execute` (sync) returns the result inline and `validate` is a dry-run — neither takes `callback_urls`, so the default `both`/`execute`/`validate` flows never need one. Note the API's SSRF guard rejects loopback/private/metadata hosts, so a `localhost`/`127.0.0.1` callback won't validate — use a public `https://` URL.

## How to run it

From the `pipelex-api` repo root, the make targets are the convenient wrapper (they run the script with the project venv):

```bash
make bundle-run      BUNDLE=<dir|.mthds>   # POST to a running API, print the response
make bundle-validate BUNDLE=<dir|.mthds>   # dry-run validate via /api/v1/validate (no inference, no cost)
make bundle-curl     BUNDLE=<dir|.mthds>   # emit a ready-to-run curl
make bundle-postman  BUNDLE=<dir|.mthds>   # push Execute/Start into the Postman collection
make bundle-dry      BUNDLE=<dir|.mthds>   # print the request body only
# optional: ENDPOINT=execute|start|validate|both  PIPE=<code>  INPUTS=<path>  NAME=<folder>  ALLOW_SIGNATURES=1  CALLBACK_URL=<url>  BASE_URL=<url>  TOKEN=<bearer>  ARGS=<extra>
```

`make bundle-validate` is the convenient way to dry-run validate a bundle live (it hardcodes `--endpoint validate --run`, since validate is free and safe to run). The other modes accept `ENDPOINT=validate` too — e.g. `make bundle-postman ENDPOINT=validate` pushes a Validate request into Postman, `make bundle-curl ENDPOINT=validate` emits the validate curl.

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

`--run` uses `Execute (sync)` so it waits for and prints the result; pass `--endpoint start` to fire-and-forget and get a `pipeline_run_id` back (a `start` run needs a `--callback-url` or `CALLBACK_URL` — see [Callback URLs](#callback-urls-the-async-start-endpoint)). The API must be up — start it locally with `make run` first. Heads-up: `execute`/`start` trigger real inference (cost + latency), so for an image-gen bundle like `fashion_moodboard` confirm the user wants a live run.

**Dry-run validate it** — `--endpoint validate` hits `/api/v1/validate`, which parses, loads, and dry-runs every pipe with **no inference** (free, no cost, no latency). Safe to run live without confirmation:

```bash
python3 .claude/skills/postman-run-bundle/scripts/build_postman_query.py <bundle-path> --run --endpoint validate
# add --allow-signatures to tolerate unimplemented pipe signatures (in-progress bundles)
```

On success it returns the validated bundle blueprint, a graph spec, and per-pipe input/output structures. On a wiring problem it returns an RFC 7807 `422` naming the offending pipe — that's the dry-run doing its job. Validate ignores `--pipe`/`--inputs`, but the bundle must declare a `main_pipe` (the endpoint takes no `pipe_code`).

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
| `--endpoint execute` / `start` / `validate` / `both` | Pick the endpoint. Default `both` (= execute + start) for Postman/curl; `--run both` runs `execute`. `validate` is the inference-free dry-run. |
| `--allow-signatures` | (validate only) Tolerate unimplemented pipe signatures instead of rejecting the bundle. Default strict. |
| `--inputs <path>` | Inputs JSON lives elsewhere, or you point at a `.mthds` file directly (file mode does not auto-detect inputs, matching the CLI). Ignored by `validate`. |
| `--pipe <pipe_code>` | The bundle has no `main_pipe`, or you want a different entry pipe. Ignored by `validate` (the endpoint takes no `pipe_code`). |
| `--callback-url <url>` | (start only) Webhook the async result is POSTed to. Repeatable. Falls back to `CALLBACK_URL` in env/`.env`; required when `start` is built or run. Ignored by `execute`/`validate`. |
| `--base-url` / `--token` | Target server + bearer for `--run`/`--curl`. |
| `--name <folder>` | Override the per-bundle Postman subfolder name. |

### Which mode should I use?

- The user wants it **in Postman** to click around → default (push).
- The user wants **Claude Code to actually run it** → `--run` (this is the answer to "run the same query yourself").
- The user wants to **validate / dry-run it without running it** (no cost) → `--run --endpoint validate` (or `make bundle-validate`). This is the answer to "validate this bundle" / "dry-run this method via the API".
- The user wants a **curl to paste elsewhere / share** → `--curl`.
- Just **confirming resolution** (print the body locally) → `--dry-run`.

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
- **No `main_pipe` and no `--pipe`** (run endpoints) → the script exits; ask the user which pipe to run and pass `--pipe`.
- **`start` selected and no callback URL** → the script exits ("the async /pipeline/start endpoint requires callback_urls"). Resolve it from `--callback-url`, `CALLBACK_URL` in `.env`, or ask the user for one (e.g. a `https://webhook.site/...` endpoint), then re-run. `execute`/`validate` never hit this.
- **No `main_pipe`, validating** → the script proceeds (validate needs no `pipe_code`), but the API returns a `422` *"Bundle does not declare a main_pipe"*. `--pipe` can't help here — `/validate` takes no `pipe_code`; the bundle itself must declare a `main_pipe`.

## Constants

- Collection UID: `35082494-559c5753-885c-409a-af63-7647fe28d301` (Pipelex FastAPI). Override with `--collection-uid` for a different collection.
- Postman API base: `https://api.getpostman.com` (the script handles GET + PUT).
