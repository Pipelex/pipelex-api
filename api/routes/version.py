from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.error_types import ErrorType
from api.errors import raise_internal_server_error

router = APIRouter(tags=["version"])


class VersionResponse(BaseModel):
    version: str = Field(..., description="Semantic version string")


@router.get("/pipelex_version")
async def pipelex_version() -> VersionResponse:
    try:
        return VersionResponse(version=version("pipelex"))
    except PackageNotFoundError:
        raise_internal_server_error("pipelex package metadata is not available", error_type=ErrorType.PACKAGE_NOT_FOUND)


@router.get("/api_version")
async def api_version() -> VersionResponse:
    try:
        return VersionResponse(version=version("pipelex-api"))
    except PackageNotFoundError:
        raise_internal_server_error("pipelex-api package metadata is not available", error_type=ErrorType.PACKAGE_NOT_FOUND)
