# `/v1/execute` accepts `callback_urls` and silently drops them

**Status:** open question, deferred from the PR #60 review. Needs a contract decision before any code moves.

**Raised by:** discovered while verifying a Codex / cubic finding on PR #60. Both bots pointed at `api/schemas/models.py:142` and diagnosed the problem as living in the source-less request validator. It does not — that validator is correct (see "What is *not* the problem" below). The real accept-and-ignore is one layer up, in the `/execute` route handler, and it only shows on requests that **succeed**.

## The asymmetry

`_parse_request` (`api/routes/pipelex/pipeline.py:526-556`) is shared by both run routes. It calls `_validate_extras` (`:478-497`), which validates all four API-server-only fields — `pipeline_run_id`, `callback_urls`, `orchestration_mode`, `storage_scope` — for `/execute` and `/start` alike.

The two handlers then diverge:

- `/start` (`:706-748`) forwards all four: `pipeline_run_id=extras.pipeline_run_id` (`:744`) and `callback_urls=extras.callback_urls` (`:745`) reach `ApiRunner.start`, where `pipeline_run_id` drives `pipeline_run_setup` and `callback_urls` drives the completion webhooks.
- `/execute` (`:641-672`) forwards only two: `storage_scope` onto the `ApiRunner` constructor (`:654`) and `requested_orchestration_mode` into the run (`:664`). `ApiRunner.execute` (`:200-252`) has no parameter that could receive the other two.

So on `/execute`:

- **`callback_urls` is validated, then dropped.** A caller sending `{"pipe_code": "...", "inputs": {...}, "callback_urls": ["https://example.com/hook"]}` gets a `200` and a completed run. The webhook never fires, and nothing in the response says so. Worse than plain indifference: `_validate_extras` runs the full SSRF / scheme / count gate first, so the server will answer `422 InvalidCallbackUrls` for a *bad* callback URL it would have ignored anyway. A caller reasonably reads "my URL was validated" as "my URL will be used".
- **`pipeline_run_id` is nearly dropped.** It is bound onto `request.state` at `:548` and rides every error log line via `_pipeline_run_id_of` (`api/exception_handlers.py:95-125`), so it is not *entirely* inert — but it never reaches the run, and the id the caller sees in the response is server-generated. On `/start` the same field is honoured as the run's actual id.

## What the published contract already says

The OpenAPI artifact draws this line correctly, and has all along. `PipelexApiExecuteRequest` (`api/schemas/models.py:309-318`) documents only `orchestration_mode` and `storage_scope` on top of `RunRequest`; `PipelexApiStartRequest` (`:288-306`) documents `callback_urls` as well and inherits `pipeline_run_id` from `StartRequest` (`:181-190`). `PipelineApiExtras`'s own docstring (`:236`) says it validates "the API-server-only fields on `/start` requests" — already narrower than how the class is actually wired.

The gap is therefore between the documented contract (`/execute` does not take these) and the runtime (`/execute` validates them, then discards them). The documentation is right; the wire handling is looser than what it promises.

## The open question

Three candidate remedies, and picking among them is a contract decision, not a review-pass call:

1. **Refuse.** `/execute` answers `422` when the body carries `callback_urls` (or a `pipeline_run_id`). Honest and loud, and consistent with how `/start` refuses a mode it cannot honour. But it breaks any caller currently sending one field to both endpoints from shared client code, and Rule 1 of the layered extension policy makes every layer extension-open — a hard refusal on a key this same server owns sits awkwardly with that.
2. **Warn.** Return the run, plus something in the response (or a header) naming the ignored args. Preserves the extension-open posture, costs a response-shape addition, and needs a decision about where such a warning lives — there is no precedent for one on the run routes today.
3. **Document and leave it.** Make `PipelineApiExtras`'s per-route scope explicit in the descriptions that reach the artifact, and accept the silent drop. Cheapest; leaves the trap in place for anyone who does not read the schema.

Worth deciding alongside it: if `/execute` is never going to use `callback_urls`, should `_validate_extras` still run the SSRF gate on it there? Validating a field you will discard is what makes the current behaviour actively misleading rather than merely quiet.

## What is *not* the problem

The bots proposed making the `handled_keys` allowlist in `RunRequest._refuse_source_less_extension_body` (`api/schemas/models.py:142`) endpoint-specific. That should not be done, for three reasons:

- The diagnostic only fires on **source-less** bodies, which are refused with a `422` either way. Both arms raise the same `PipelineRequestError` through the same `raise_validation_error` at `pipeline.py:556` — same status, same `error_type`, same `error_domain`. The only difference is the wording of `detail`; no request is silently accepted.
- The message it would produce is factually wrong for these keys. It says the args are "not handled by this deployment" and points a hosted-only selector at the hosted API — but this deployment *does* handle `callback_urls` and `pipeline_run_id`, on `/start`, and validates the former even on `/execute`.
- "Handled" is defined at the layer, not the route, and that is normative. The Pipelex workspace spec `docs/specs/pipelex-platform-api.md` Rule 1 names `callback_urls` / `orchestration_mode` / `storage_scope` as layer-2 extensions; Rule 4 says a source-less body with no unhandled keys keeps the base guidance. The verifying conformance test parametrizes over both routes identically (`Pipelex/conformance` → `tests/pipelex_api/test_run_source_precondition.py`; its route-level twin in this repo is `tests/unit/test_run_source_precondition.py`). Changing this would be a three-repo spec edit in service of a worse message.

## Links

- PR: https://github.com/Pipelex/pipelex-api/pull/60
- Codex thread: https://github.com/Pipelex/pipelex-api/pull/60#discussion_r3844335250
- cubic thread: https://github.com/Pipelex/pipelex-api/pull/60#discussion_r3844411778
- Governing spec: `Pipelex/pipelex-workspace` → `docs/specs/pipelex-platform-api.md` → "Layered extension policy", Rules 1 and 4
