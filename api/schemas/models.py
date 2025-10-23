from typing import Any

from pydantic import BaseModel, Field


class File(BaseModel):
    name: str = Field(..., description="Name of the file")


class PresignPostUrlsRequestBody(BaseModel):
    files: list[File] = Field(
        ...,
        description="List of files to be uploaded. Each file must have a name.",
    )


class PresignPostUrlsResponseBody(BaseModel):
    presigned_post_urls: dict[str, Any] = Field(
        ...,
        description="List of presigned URLs for uploading the files. Each URL corresponds to a file in the request.",
    )
    success: bool = Field(
        True,
        description="Indicates whether the presigned URLs were successfully generated.",
    )
    message: str = Field(
        "Presigned URLs generated successfully.",
        description="A message providing additional information about the response.",
    )
