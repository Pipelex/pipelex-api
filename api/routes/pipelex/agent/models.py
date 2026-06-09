"""Models endpoint — list available model presets, aliases, waterfalls, and talent mappings."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pipelex.builder.operations.models_ops import ModelCategory, list_models

from api.error_types import ErrorType
from api.errors import raise_validation_error

router = APIRouter(tags=["agent"])


@router.get("/models", summary="List available model presets, aliases, and waterfalls")
async def get_models(
    model_type: Annotated[list[str] | None, Query(alias="type", description="Filter by model category: llm, extract, img_gen, search")] = None,
) -> dict[str, Any]:
    """List available model presets, aliases, waterfalls, and talent mappings."""
    categories: list[ModelCategory] | None = None
    if model_type:
        try:
            categories = [ModelCategory(cat) for cat in model_type]
        except ValueError:
            valid = ", ".join(sorted(member.value for member in ModelCategory))
            raise_validation_error(
                message=f"Invalid model category. Valid values: {valid}",
                error_type=ErrorType.INVALID_MODEL_CATEGORY,
            )

    result = list_models(categories=categories)
    result["success"] = True
    return result
