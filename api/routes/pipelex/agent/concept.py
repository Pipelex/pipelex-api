"""Concept endpoint — convert JSON concept spec to TOML."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pipelex.builder.operations.concept_ops import concept_spec_to_toml, parse_concept_spec
from pydantic import BaseModel, Field, ValidationError, field_validator

from api.error_types import ErrorType
from api.errors import ENDPOINT_HANDLED_EXCEPTIONS, raise_internal_error
from api.limits import MAX_AGENT_SPEC_BYTES

router = APIRouter(tags=["agent"])


class BuildConceptRequest(BaseModel):
    spec: dict[str, Any] = Field(..., description="JSON concept specification.")

    @field_validator("spec")
    @classmethod
    def _bound_spec_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value).encode("utf-8")) > MAX_AGENT_SPEC_BYTES:
            msg = f"spec exceeds {MAX_AGENT_SPEC_BYTES // 1024} KiB limit"
            raise ValueError(msg)
        return value


class BuildConceptResponse(BaseModel):
    success: bool = Field(default=True, description="Whether the operation was successful")
    concept_code: str = Field(..., description="The concept code that was generated")
    toml: str = Field(..., description="Generated TOML content for the concept")


@router.post("/build/concept", summary="Convert a JSON concept spec to TOML")
async def build_concept(request_data: BuildConceptRequest) -> BuildConceptResponse:
    """Convert a JSON concept spec to TOML format."""
    try:
        concept_spec = parse_concept_spec(request_data.spec)
        toml_content = concept_spec_to_toml(concept_spec)

        return BuildConceptResponse(
            success=True,
            concept_code=concept_spec.concept_code,
            toml=toml_content,
        )

    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": ErrorType.VALIDATION_ERROR,
                "message": str(exc),
            },
        ) from exc

    except ENDPOINT_HANDLED_EXCEPTIONS as exc:
        raise_internal_error(exc, context="build_concept failed")
