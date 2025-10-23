import os
import traceback

from fastapi import APIRouter, HTTPException
from pipelex import log
from pipelex.hub import get_storage_provider
from pipelex.system.environment import get_optional_env

from api.schemas.models import PresignPostUrlsRequestBody, PresignPostUrlsResponseBody

router = APIRouter(tags=["uploader"])


@router.post("/presigned-post-urls", response_model=PresignPostUrlsResponseBody)
async def get_presigned_post_urls(body: PresignPostUrlsRequestBody):
    """Get presigned URLs for uploading files to Storage."""
    try:
        # TODO: Make bucket name configurable
        bucket_name = f"pipelex-user-files-{os.getenv('ENV')}"

        # TODO: Get actual user ID from authentication context
        user_id = "here_will_be_user_id"

        # Generate presigned URLs
        storage_provider = get_storage_provider()

        presigned_post_urls = storage_provider.generate_presigned_post_urls(  # type: ignore[attr-defined] # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
            bucket=bucket_name,
            files=[file.name for file in body.files],
            prefix=user_id,
            expire_in=int(get_optional_env("STORAGE_PRESIGNED_POST_URL_EXPIRE_IN") or 300),
        )

        return PresignPostUrlsResponseBody(
            success=True,
            presigned_post_urls=presigned_post_urls,  # pyright: ignore[reportUnknownArgumentType]
            message="Presigned URLs generated successfully.",
        )

    except Exception as exc:
        log.error("Error generating presigned URLs:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
