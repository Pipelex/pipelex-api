# Kajson deserialization of untrusted bodies — design flag

> **Status:** flagged, not actioned. Filed during the `pipelex-api` Phase 3 review (2026-05-23) — `TODOS.md` question 10, part 2. Pre-existing — not introduced by Phase 3.

## What

`api/routes/pipelex/pipeline.py::_decode_body` calls `kajson.loads(body.decode("utf-8"))` on the raw, untrusted request body to `/pipeline/execute` and `/pipeline/start`. `kajson`'s decoder treats `{"__class__": "...", "__module__": "..."}` as instructions to:

1. `importlib.import_module(module_name)` — runs the module's top-level code on first import,
2. `getattr(module, class_name)` — resolves a class object,
3. `the_class(**the_dict)` (or `the_class.model_validate(...)` for Pydantic models, or `the_class()` + `obj.__dict__ = the_dict`) — instantiates it from the rest of the marker dict.

The module name and class name come from the caller's body. The caller therefore controls *which* module is imported and *which* class is instantiated on a process that already trusts pipelex's stack.

## Why this is intentional

The kajson round-trip is the feature that lets a caller send pipeline `inputs` containing typed pydantic objects (a `WorkingMemory`, a domain model, an enum) and have them survive deserialization as actual instances. Plain `json.loads` would deliver dicts and force the runtime to do per-field re-coercion. The `inputs` field is the entire point.

## Realistic attack surface

Assume an authenticated caller (or, in `AUTH_MODE=none`, any external caller reachable behind whatever gateway is in front):

- **Forced module import — REAL.** Any module under the running process's `sys.path` can be force-imported by sending `{"__class__": "X", "__module__": "<target>"}` — the `getattr` fails afterwards but the import already happened. Module-level side effects (a top-level `os.environ.get(...)`, a top-level DB connection, a top-level network call, a top-level `print`) fire. Most pipelex / pipelex-api modules are already imported at process start, so the practical reach is "the long tail of site-packages we haven't imported yet." For a tightly pinned production image, that tail is short — but it is not empty.
- **Arbitrary class instantiation — LIMITED.** `the_class(**the_dict)` only works if the class accepts those kwargs without raising. `the_class()` + `obj.__dict__ = the_dict` works for any class with a no-arg `__init__`. The attacker controls the resulting instance's attribute dict and the kwargs that flow into `__init__` / `__post_init__` / `model_validate`, but does **not** control which methods get called on the resulting object afterwards. Downstream code (`PipelineRequest.from_body`, the pipeline runner) does call methods on the resolved `inputs` objects — those methods belong to whatever class kajson resolved, and on a pinned production image that resolves to a `pipelex` / `pydantic` / `mthds` type whose method surface is ours, not the attacker's. The exposure is therefore: whatever side effects fire during construction of an arbitrary class on attacker-controlled kwargs, plus any side effect inside a method our own downstream code legitimately calls on a type the attacker picked.
- **Arbitrary code execution via kajson's `class_source_code` — NOT IN SCOPE.** `kajson.loads` also accepts a `class_source_code: str` argument that gets `exec()`'d. `_decode_body` does **not** pass that argument; the `exec` path is unreachable from the API surface. (Confirmed in source: `_decode_body` in `api/routes/pipelex/pipeline.py` calls `kajson.loads(body.decode("utf-8"))` with no kwargs, and `kajson.loads` gates the `exec` path on `class_source_code is not None`.) **Do not introduce a code path that lets a request body populate `class_source_code`.**

## What this is not

This is not a known live exploit. It is a flagged design surface that warrants its own decision once Phase 5 ships and the error-handling track is closed. It sits adjacent to — but distinct from — `pipelex-changes.md` #15 (kajson wrapping unwrapped `KeyError` / `AttributeError` / `TypeError` in `KajsonDecoderError`), which is purely an error-shape question with no security implications.

## Options worth designing

1. **Allowlist `__module__` prefixes.** A `KAJSON_ALLOWED_MODULES` env var defaulting to `"pipelex,mthds,pydantic"` (or similar). The decoder rejects any `__module__` not under an allowed prefix with a `KajsonDecoderError`. Pros: minimal surface change; preserves the inputs-fidelity feature for the actual pipelex types. Cons: needs maintenance as the surface evolves; an inadvertently allowed prefix re-opens the gap.
2. **Disable kajson decode entirely; require plain JSON inputs.** Migrate `PipelineRequest.from_body` to read `inputs` as a plain dict and let the runtime coerce per field via pydantic. Pros: removes the deserialization-trust concern entirely; aligns with how every other JSON API works. Cons: callers that today send typed `inputs` via kajson break; a migration window is needed.
3. **Gate by `AUTH_MODE`.** Allow kajson in `jwt` / `api_key` modes, disable it in `none`. Pros: simple knob. Cons: `none` is the open-source default and the hosted gateway mode — exactly the configurations where this matters most.
4. **Keep as-is and document.** The risk is contained (no `exec`, limited constructor exploitation, all modules already imported in a pinned production image), and the feature is load-bearing for the inputs surface. Pros: no work. Cons: any future site-packages addition or `from-source` build re-opens the gap.

A decision needs `pipelex-app` (the main `inputs` consumer) and `pipelex-api-deploy` (production runtime) in the conversation — neither lives in this repo.

## What changed for Q10

Nothing security-side. Q10 only widened `_decode_body`'s `except` tuple so kajson's documented and bare-protocol-leak failures land as 422 problem documents instead of opaque 500s. The trust boundary at `kajson.loads` is unchanged. Filing this document is the deliverable.

## Pointers

- `api/routes/pipelex/pipeline.py::_decode_body` and `::_parse_request` — the consumer side; `_decode_body` calls `kajson.loads(body.decode("utf-8"))` with no kwargs, and `_parse_request` hands the result to `PipelineRequest.from_body(...)`.
- `kajson/kajson.py::loads` — the entry point.
- `kajson/json_decoder.py::UniversalJSONDecoder.universal_decoder` — the marker handling.
- `wip/pipelex-changes.md` #15 — the error-shape companion item (separate concern).
- `TODOS.md` question 10 — the trigger.
