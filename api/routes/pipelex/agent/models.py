"""Models endpoint — the MTHDS Protocol model deck this runner routes to."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from mthds.protocol.models import ModelCategory
from pipelex.pipeline.runner import PipelexModelDeck

from api.error_types import ErrorType
from api.errors import raise_validation_error
from api.routes.pipelex.pipeline import ApiRunner

router = APIRouter(tags=["agent"])


@router.get("/models", openapi_extra={"x-mthds-protocol": True})
async def get_models(
    request: Request,
    model_type: Annotated[
        str | None,
        Query(alias="type", description="Filter by model category: llm, extract, img_gen, search. Single value (protocol arity)."),
    ] = None,
) -> PipelexModelDeck:
    """List the model deck this runner can route to (MTHDS Protocol `GET /models`).

    Answers the protocol `ModelDeck` as produced by `PipelexMTHDSProtocol.models` —
    the flat `models` list (`{name, type}` entries) plus this implementation's routing
    extensions (`aliases`, `waterfalls`). The `type` query param is a SINGLE protocol
    `ModelCategory` value: repeated `?type=` values and unknown categories are both
    422s (RFC 7807).
    """
    # Protocol arity: `type` is a plain single-value enum. FastAPI silently keeps one of
    # several repeated scalar query params, so the multi-value rejection must be explicit.
    if len(request.query_params.getlist("type")) > 1:
        raise_validation_error(
            message="The `type` query parameter accepts a single value",
            error_type=ErrorType.INVALID_MODEL_CATEGORY,
        )
    category: ModelCategory | None = None
    if model_type:
        try:
            category = ModelCategory(model_type)
        except ValueError:
            valid = ", ".join(sorted(member.value for member in ModelCategory))
            raise_validation_error(
                message=f"Invalid model category. Valid values: {valid}",
                error_type=ErrorType.INVALID_MODEL_CATEGORY,
            )
    return await ApiRunner().models(category=category)
