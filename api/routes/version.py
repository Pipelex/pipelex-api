from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.error_types import ErrorType

router = APIRouter(tags=["version"])


class VersionResponse(BaseModel):
    version: str = Field(..., description="Semantic version string")


@router.get("/pipelex_version", summary="Pipelex library version")
async def pipelex_version() -> VersionResponse:
    try:
        return VersionResponse(version=version("pipelex"))
    except PackageNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": ErrorType.PACKAGE_NOT_FOUND,
                "message": "pipelex package metadata is not available",
            },
        ) from exc


@router.get("/api_version", summary="API server version")
async def api_version() -> VersionResponse:
    try:
        return VersionResponse(version=version("pipelex-api"))
    except PackageNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": ErrorType.PACKAGE_NOT_FOUND,
                "message": "pipelex-api package metadata is not available",
            },
        ) from exc
