# Handoff — fail loud when an invalid `/validate` verdict carries no structured diagnostics

> Raised by greptile on PR #20 (`feature/validation-errors-source`). Triaged, confirmed, deferred — not fixed in that PR.

## Context (cold start)

`/validate` is a 200-diagnostic endpoint: an invalid bundle is a *produced verdict* that rides a 200 `InvalidReport` (`is_valid: false`) carrying a structured `validation_errors[]` list — the per-error diagnostics the VS Code extension maps to per-line problems. The contract, stated in both the `InvalidReport.validation_errors` field description and the `_invalid_report_response` docstring (`api/routes/pipelex/validate.py`), is that this list is **non-empty on every invalid verdict that reaches the wire**.

The route builds the invalid arm in `_invalid_report_response(error_report)`:

```python
invalid_report = InvalidReport(
    validation_errors=error_report.validation_errors or [],
    message=error_report.message,
)
```

The `or []` exists because `ErrorReport.validation_errors` is typed `list[ValidationErrorItem] | None` (pipelex `base_exceptions.py`) and `InvalidReport.validation_errors` is a non-optional `list` — so a `None` has to be coerced to satisfy the model. The problem: if `validation_errors` is ever `None`/empty, the coercion silently produces a 200 `is_valid:false` arm with an **empty** `validation_errors[]` — an editor client sees "invalid" but has nothing to place. That directly contradicts the documented invariant, silently.

**Is it reachable today? No.** Verified against the pinned pipelex source:

- `ValidateBundleError.to_error_report()` (`pipelex/pipeline/exceptions.py`) calls the one shared builder `build_validation_error_items(...)` with `fallback_message=self.message`. The builder (`pipelex/pipeline/validation_errors.py`) appends a last-resort residual item from that message when no categorized/dry-run item exists, so it always returns ≥1 item for a real validation failure. The method then stores `validation_error_items or None` — which is `None` only when the builder returned empty, which it never does for a raised `ValidateBundleError`.
- The Temporal arm preserves the field verbatim: `WorkflowExecutionError.to_error_report()` (`pipelex/temporal/exceptions.py`) returns the recovered `ErrorReport` as-is, and `ErrorReport.from_dict` is a strict Pydantic round-trip — a non-empty list crosses the activity boundary intact.

So the `or []` is dead code today. It is still a **latent silent-failure**: it makes the route's behavior on an upstream invariant break be "emit a contract-violating empty 200" rather than "fail loudly." Worth closing per the project's no-silent-failure stance, even though no current path triggers it.

## The change (fail-loud — decided)

In `_invalid_report_response`, guard before constructing `InvalidReport`:

```python
if not error_report.validation_errors:
    msg = "Invalid /validate verdict carried no structured diagnostics (upstream invariant violation)."
    raise_internal_server_error(message=msg, error_type=ErrorType.INTERNAL_SERVER_ERROR)
invalid_report = InvalidReport(
    validation_errors=error_report.validation_errors,
    message=error_report.message,
)
```

`raise_internal_server_error` is the existing helper in `api/errors.py` (→ RFC 7807 500, `CONFIG` domain); `ErrorType.INTERNAL_SERVER_ERROR` is the only fitting fixed symbol in `api/error_types.py`. After the `if not …: raise`, pyright narrows `list | None` → `list`, so the `or []` is dropped and the value passed directly.

Rationale for fail-loud over synthesizing a residual item from `error_report.message`: a no-verdict 500 surfaces the upstream regression to us instead of hiding it, and it avoids re-implementing pipelex's message→item fallback in the API layer (the "one shared builder, two surfaces" principle — the API must not grow its own diagnostic-synthesis path).

## Semantics to preserve

- Both invalid-arm call sites route through `_invalid_report_response` — the `ValidateBundleError` direct catch and the `WorkflowExecutionError`-recovered catch — so the single guard covers both backends. Don't duplicate it into the route body.
- This guard is a backstop for an upstream invariant break, NOT a new caller-facing condition. The empty-`mthds_contents` edge case is already a request-shape 422 (via `min_length=1`), so a legitimate caller never lands here with no diagnostics.
- The 500 is correct, not a regression of the 200-diagnostic contract: an invalid verdict with zero machine-readable diagnostics is a *no-verdict* condition (we cannot produce the documented invalid arm), which is exactly what non-2xx is reserved for.

## Tests

Route-level, added to `TestValidateErrors` in `tests/unit/test_validate_errors.py` (one TestClass per module — extend the existing one). Force the unreachable empty state with the existing `_multi_category_error()` helper:

```python
def test_empty_diagnostics_invalid_verdict_fails_loud(self, mocker: MockerFixture):
    report_without_items = _multi_category_error().to_error_report().model_copy(update={"validation_errors": None})
    mocker.patch.object(ValidateBundleError, "to_error_report", return_value=report_without_items)
    mocker.patch.object(ApiRunner, "validate", new=mocker.AsyncMock(side_effect=ValidateBundleError(message="boom")))
    client = _build_client()
    response = client.post("/v1/validate", json={"mthds_contents": [VALID_MTHDS]})
    assert response.status_code >= 500, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
```

This currently passes through the `or []` and returns a 200 with `validation_errors: []` — it should fail before the change and pass after. Pairs with the existing `test_dry_run_residual_becomes_single_dry_run_item`, which pins the non-empty happy path.
